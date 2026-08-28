from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
from db import connect_db, init_db
from migrations import migrate_add_job_kind_and_graph_tables, migrate_graph_snapshot_schema


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_fresh_schema_has_constraints_and_migration_record(self):
        init_db()
        with connect_db() as conn:
            self.assertEqual(conn.execute("select name from schema_migrations where version = 1").fetchone()[0], "initial_schema")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("insert into videos values ('video1', 'now', 'now')")
                conn.execute("insert into preparation_jobs (job_id, video_id, learner_level, status, stage, progress, created_at, updated_at) values ('job1', 'video1', 'beginner', 'queued', 'queued', 2, 'now', 'now')")

    def test_fresh_schema_has_job_scoped_graph_tables(self):
        init_db()
        with connect_db() as conn:
            self.assertEqual(conn.execute("select name from schema_migrations where version = 4").fetchone()[0], "knowledge_graph_job_snapshots")
            self.assertIsNone(conn.execute("select 1 from sqlite_master where type = 'table' and name = 'kg_extraction_events'").fetchone())
            self.assertIsNone(conn.execute("select 1 from sqlite_master where type = 'index' and name = 'idx_kg_extraction_events_job'").fetchone())
            node_columns = {row["name"] for row in conn.execute("pragma table_info(kg_nodes)")}
            edge_columns = {row["name"] for row in conn.execute("pragma table_info(kg_edges)")}
            self.assertIn("extraction_job_id", node_columns)
            self.assertIn("extraction_job_id", edge_columns)
            conn.execute("insert into videos values ('video-kg', 'now', 'now')")
            conn.execute("insert into transcript_sources values ('transcript-kg', 'video-kg', 'x', 'test', 'hash', 1, '{}', 'now')")
            for job_id in ("job-kg-a", "job-kg-b"):
                conn.execute(
                    "insert into preparation_jobs (job_id, video_id, learner_level, status, stage, job_kind, created_at, updated_at) "
                    "values (?, 'video-kg', 'intermediate', 'ready', 'ready', 'graph_extraction', 'now', 'now')",
                    (job_id,),
                )
                conn.execute(
                    "insert into kg_extraction_jobs (job_id, video_id, transcript_id, cache_key, status, stage, created_at, updated_at) "
                    "values (?, 'video-kg', 'transcript-kg', 'same-cache', 'ready', 'ready', 'now', 'now')",
                    (job_id,),
                )
            self.assertEqual(conn.execute("select count(*) from kg_extraction_jobs where cache_key = 'same-cache'").fetchone()[0], 2)

    def test_undirected_edges_reject_the_reverse_pair_but_directed_edges_allow_it(self):
        init_db()
        with connect_db() as conn:
            conn.execute("insert into videos values ('video-edges', 'now', 'now')")
            conn.execute(
                "insert into preparation_jobs (job_id, video_id, learner_level, status, stage, job_kind, created_at, updated_at) "
                "values ('job-edges', 'video-edges', 'intermediate', 'ready', 'ready', 'graph_extraction', 'now', 'now')"
            )
            conn.execute("insert into transcript_sources values ('transcript-x', 'video-edges', 'x', 'test', 'hash', 0, '{}', 'now')")
            conn.execute(
                "insert into kg_extraction_jobs (job_id, video_id, transcript_id, cache_key, status, stage, created_at, updated_at) "
                "values ('job-edges', 'video-edges', 'transcript-x', 'k', 'ready', 'ready', 'now', 'now')"
            )
            conn.execute("insert into kg_relation_types (relation_type, description, created_at) values ('related_to', 'r', 'now')")
            conn.execute("insert into kg_relation_types (relation_type, description, created_at) values ('causes', 'r', 'now')")
            for node_id in ("node-a", "node-b"):
                conn.execute(
                    "insert into kg_nodes (extraction_job_id, node_id, canonical_name, node_type, short_summary, confidence, created_at, updated_at) "
                    "values ('job-edges', ?, ?, 'concept', 's', 0.5, 'now', 'now')",
                    (node_id, node_id),
                )
            conn.execute(
                "insert into kg_edges (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, confidence, directional, created_at, updated_at) "
                "values ('job-edges', 'edge-ab', 'node-a', 'node-b', 'related_to', 0.5, 0, 'now', 'now')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "insert into kg_edges (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, confidence, directional, created_at, updated_at) "
                    "values ('job-edges', 'edge-ba', 'node-b', 'node-a', 'related_to', 0.5, 0, 'now', 'now')"
                )
            # A directed pair in the reverse order is a structurally different edge and must be allowed
            # (uses a different relation_type so it doesn't collide with edge-ab's own unique-index entry).
            conn.execute(
                "insert into kg_edges (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, confidence, directional, created_at, updated_at) "
                "values ('job-edges', 'edge-ab-directed', 'node-a', 'node-b', 'causes', 0.5, 1, 'now', 'now')"
            )
            conn.execute(
                "insert into kg_edges (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, confidence, directional, created_at, updated_at) "
                "values ('job-edges', 'edge-ba-directed', 'node-b', 'node-a', 'causes', 0.5, 1, 'now', 'now')"
            )

    def test_job_kind_migration_is_retryable_after_a_partial_alter(self):
        with connect_db() as conn:
            conn.executescript("""
                create table preparation_jobs (
                    job_id text primary key, video_id text not null, learner_level text not null,
                    source_policy text not null default 'live', status text not null, stage text not null,
                    created_at text not null, updated_at text not null
                );
            """)
            # Simulate a process that was killed after the ALTER TABLE committed but
            # before schema_migrations recorded version 3 - the column already exists.
            conn.execute(
                "alter table preparation_jobs add column job_kind text not null default 'bubble_analysis' "
                "check (job_kind in ('bubble_analysis','graph_extraction'))"
            )
            migrate_add_job_kind_and_graph_tables(conn)  # must not raise "duplicate column name"
            columns = [row[1] for row in conn.execute("pragma table_info(preparation_jobs)")]
            self.assertEqual(columns.count("job_kind"), 1)

    def test_snapshot_migration_resumes_after_interruption_and_normalizes_bad_timestamps(self):
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                create table preparation_jobs (
                    job_id text primary key, video_id text not null, learner_level text not null,
                    status text not null, stage text not null, created_at text not null, updated_at text not null,
                    job_kind text not null default 'bubble_analysis'
                );
                create table transcript_sources (
                    transcript_id text primary key, video_id text not null, filename text not null, source text not null,
                    content_hash text not null, segment_count integer not null, metadata text default '{}', created_at text not null
                );
                create table kg_extraction_jobs (
                    job_id text primary key, video_id text not null, transcript_id text not null,
                    cache_key text not null, status text not null, stage text not null,
                    node_count integer default 0, edge_count integer default 0, error_code text, message text,
                    created_at text not null, updated_at text not null
                );
                create table kg_extraction_events (
                    event_id integer primary key autoincrement, job_id text not null, event_type text not null,
                    stage text, metadata text, created_at text not null
                );
                create table kg_nodes (
                    node_id text primary key, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, aliases text not null default '[]', created_at text not null, updated_at text not null
                );
                create table kg_node_sources (
                    node_id text not null, source_id text not null, video_id text, transcript_id text,
                    segment_ids text not null default '[]', start_seconds real, end_seconds real,
                    extraction_job_id text, evidence_text text, created_at text not null,
                    primary key (node_id, source_id)
                );
                create table kg_relation_types (
                    relation_type text primary key, description text not null,
                    status text not null default 'approved', proposed_by_job_id text, created_at text not null
                );
                create table kg_edges (
                    edge_id text primary key, source_node_id text not null, target_node_id text not null,
                    relation_type text not null, relation_status text not null default 'accepted',
                    confidence real not null, evidence_source_ids text not null default '[]',
                    directional integer not null default 1, extraction_job_id text,
                    created_at text not null, updated_at text not null
                );
                create table kg_node_embeddings (
                    node_id text primary key, model text not null, dims integer not null, vector text not null, created_at text not null
                );
                create table kg_node_detail_cache (
                    cache_key text primary key, node_id text not null, lens_hash text not null, lens_json text not null,
                    provider text not null, model text not null, prompt_version text not null,
                    detail_markdown text, evidence_json text not null default '[]', confidence real,
                    status text not null, created_at text not null, updated_at text not null
                );
                create table kg_user_knowledge (
                    node_id text primary key, status text not null default 'unknown',
                    confidence real, last_reviewed_at text, notes text, updated_at text not null
                );
                insert into videos values ('video-resume', 'now', 'now');
                insert into transcript_sources values ('transcript-resume', 'video-resume', 'x', 'test', 'hash', 1, '{}', 'now');
                insert into preparation_jobs values ('job-resume', 'video-resume', 'intermediate', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into kg_extraction_jobs values ('job-resume', 'video-resume', 'transcript-resume', 'k', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes values ('node-resume', 'embedding', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                -- end_seconds < start_seconds: valid under the old (unchecked) schema, invalid under the new one.
                insert into kg_node_sources values ('node-resume', 'segment-001', 'video-resume', 'transcript-resume', '[]', 10, 5, 'job-resume', 'evidence', 'now');
            """)
            # First call: a full rename+rebuild+copy+drop. Also verifies Codex's P1
            # finding - the legacy row above has end_seconds(5) < start_seconds(10),
            # which the new schema's check constraint would reject unless normalized.
            migrate_graph_snapshot_schema(conn)
            row = conn.execute(
                "select start_seconds, end_seconds from kg_node_sources where extraction_job_id = 'job-resume'"
            ).fetchone()
            self.assertEqual((row["start_seconds"], row["end_seconds"]), (5, 10))
            self.assertEqual(
                conn.execute("select 1 from sqlite_master where type = 'table' and name = 'kg_nodes_legacy'").fetchone(),
                None,
            )

        # Second call: simulate a process that was killed after the rebuild committed
        # (executescript()'s implicit commit) but before the copy+drop finished, by
        # manually recreating a lingering _legacy table with data that was never copied.
        with connect_db() as conn:
            conn.execute(
                "insert into preparation_jobs values ('job-resume-2', 'video-resume', 'intermediate', 'ready', 'ready', 'now', 'now', 'graph_extraction')"
            )
            conn.executescript("""
                create table kg_node_sources_legacy (
                    node_id text not null, source_id text not null, video_id text, transcript_id text,
                    segment_ids text not null default '[]', start_seconds real, end_seconds real,
                    extraction_job_id text, evidence_text text, created_at text not null,
                    primary key (node_id, source_id)
                );
                create table kg_extraction_jobs_legacy (
                    job_id text primary key, video_id text not null, transcript_id text not null,
                    cache_key text not null, status text not null, stage text not null,
                    node_count integer default 0, edge_count integer default 0, error_code text, message text,
                    created_at text not null, updated_at text not null
                );
                create table kg_extraction_events_legacy (
                    event_id integer primary key, job_id text not null, event_type text not null,
                    stage text, metadata text, created_at text not null
                );
                create table kg_edges_legacy (
                    edge_id text primary key, source_node_id text not null, target_node_id text not null,
                    relation_type text not null, relation_status text not null default 'accepted',
                    confidence real not null, evidence_source_ids text not null default '[]',
                    directional integer not null default 1, extraction_job_id text,
                    created_at text not null, updated_at text not null
                );
                create table kg_nodes_legacy (
                    node_id text primary key, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, aliases text not null default '[]', created_at text not null, updated_at text not null
                );
                create table kg_node_embeddings_legacy (
                    node_id text primary key, model text not null, dims integer not null, vector text not null, created_at text not null
                );
                create table kg_node_detail_cache_legacy (
                    cache_key text primary key, node_id text not null, lens_hash text not null, lens_json text not null,
                    provider text not null, model text not null, prompt_version text not null,
                    detail_markdown text, evidence_json text not null default '[]', confidence real,
                    status text not null, created_at text not null, updated_at text not null
                );
                create table kg_user_knowledge_legacy (
                    node_id text primary key, status text not null default 'unknown',
                    confidence real, last_reviewed_at text, notes text, updated_at text not null
                );
                insert into kg_extraction_jobs_legacy values ('job-resume-2', 'video-resume', 'transcript-resume', 'k2', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes_legacy values ('node-resume', 'embedding', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                insert into kg_node_sources_legacy values ('node-resume', 'segment-001', 'video-resume', 'transcript-resume', '[]', 10, 5, 'job-resume-2', 'evidence', 'now');
            """)
            # kg_nodes already has extraction_job_id (rebuilt above) but _legacy tables
            # linger, mimicking an interruption between the rebuild and the copy+drop.
            migrate_graph_snapshot_schema(conn)
            row = conn.execute(
                "select start_seconds, end_seconds from kg_node_sources where extraction_job_id = 'job-resume-2'"
            ).fetchone()
            self.assertEqual((row["start_seconds"], row["end_seconds"]), (5, 10))
            self.assertEqual(
                conn.execute("select 1 from sqlite_master where type = 'table' and name = 'kg_node_sources_legacy'").fetchone(),
                None,
            )
            # Calling it again once fully migrated must be a no-op, not an error.
            migrate_graph_snapshot_schema(conn)

    def test_snapshot_migration_recreates_tables_missing_from_an_interrupted_rebuild(self):
        # Simulates a crash mid-executescript(GRAPH_TABLES_SQL): kg_nodes was already
        # rebuilt (has extraction_job_id, so rebuild_done=True) but kg_node_sources
        # never got (re)created, so it's still the old legacy-shaped table left over
        # from the rename step - both rebuild_done and legacy_present are true, which
        # previously skipped re-running GRAPH_TABLES_SQL entirely (Codex P2).
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                create table preparation_jobs (
                    job_id text primary key, video_id text not null, learner_level text not null,
                    status text not null, stage text not null, created_at text not null, updated_at text not null,
                    job_kind text not null default 'bubble_analysis'
                );
                create table transcript_sources (
                    transcript_id text primary key, video_id text not null, filename text not null, source text not null,
                    content_hash text not null, segment_count integer not null, metadata text default '{}', created_at text not null
                );
                create table kg_nodes (
                    extraction_job_id text not null, node_id text not null, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, aliases text not null default '[]', created_at text not null, updated_at text not null,
                    primary key (extraction_job_id, node_id)
                );
                create table kg_extraction_jobs_legacy (
                    job_id text primary key, video_id text not null, transcript_id text not null,
                    cache_key text not null, status text not null, stage text not null,
                    node_count integer default 0, edge_count integer default 0, error_code text, message text,
                    created_at text not null, updated_at text not null
                );
                create table kg_nodes_legacy (
                    node_id text primary key, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, aliases text not null default '[]', created_at text not null, updated_at text not null
                );
                create table kg_node_sources_legacy (
                    node_id text not null, source_id text not null, video_id text, transcript_id text,
                    segment_ids text not null default '[]', start_seconds real, end_seconds real,
                    extraction_job_id text, evidence_text text, created_at text not null,
                    primary key (node_id, source_id)
                );
                create table kg_edges_legacy (
                    edge_id text primary key, source_node_id text not null, target_node_id text not null,
                    relation_type text not null, relation_status text not null default 'accepted',
                    confidence real not null, evidence_source_ids text not null default '[]',
                    directional integer not null default 1, extraction_job_id text,
                    created_at text not null, updated_at text not null
                );
                create table kg_node_embeddings_legacy (
                    node_id text primary key, model text not null, dims integer not null, vector text not null, created_at text not null
                );
                create table kg_node_detail_cache_legacy (
                    cache_key text primary key, node_id text not null, lens_hash text not null, lens_json text not null,
                    provider text not null, model text not null, prompt_version text not null,
                    detail_markdown text, evidence_json text not null default '[]', confidence real,
                    status text not null, created_at text not null, updated_at text not null
                );
                create table kg_user_knowledge_legacy (
                    node_id text primary key, status text not null default 'unknown',
                    confidence real, last_reviewed_at text, notes text, updated_at text not null
                );
                insert into videos values ('video-partial', 'now', 'now');
                insert into transcript_sources values ('transcript-partial', 'video-partial', 'x', 'test', 'hash', 1, '{}', 'now');
                insert into preparation_jobs values ('job-partial', 'video-partial', 'intermediate', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into kg_extraction_jobs_legacy values ('job-partial', 'video-partial', 'transcript-partial', 'k', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes_legacy values ('node-partial', 'embedding', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                insert into kg_node_sources_legacy values ('node-partial', 'segment-001', 'video-partial', 'transcript-partial', '[]', 0, 5, 'job-partial', 'evidence', 'now');
            """)
            # kg_node_sources/kg_edges/kg_extraction_jobs (the v4 shapes) don't exist
            # yet at all - the fix must (re-)run GRAPH_TABLES_SQL before attempting the copy.
            migrate_graph_snapshot_schema(conn)
            row = conn.execute(
                "select start_seconds, end_seconds from kg_node_sources where extraction_job_id = 'job-partial'"
            ).fetchone()
            self.assertEqual((row["start_seconds"], row["end_seconds"]), (0, 5))
            self.assertIsNone(
                conn.execute("select 1 from sqlite_master where type = 'table' and name = 'kg_nodes_legacy'").fetchone()
            )

    def test_existing_v3_graph_schema_copies_shared_nodes_per_job(self):
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                create table preparation_jobs (
                    job_id text primary key, video_id text not null, learner_level text not null,
                    source_policy text not null default 'live', status text not null, stage text not null,
                    created_at text not null, updated_at text not null, job_kind text not null default 'bubble_analysis'
                );
                create table transcript_sources (
                    transcript_id text primary key, video_id text not null, filename text not null, source text not null,
                    content_hash text not null, segment_count integer not null, metadata text default '{}', created_at text not null
                );
                create table schema_migrations (version integer primary key, name text not null, applied_at text not null);
                insert into schema_migrations values (1, 'initial_schema', 'now');
                insert into schema_migrations values (2, 'persisted_translation_jobs', 'now');
                insert into schema_migrations values (3, 'knowledge_graph_extraction', 'now');
                create table kg_extraction_jobs (
                    job_id text primary key, video_id text not null, transcript_id text not null,
                    cache_key text not null unique, status text not null, stage text not null,
                    node_count integer default 0, edge_count integer default 0, error_code text, message text,
                    created_at text not null, updated_at text not null
                );
                create table kg_extraction_events (
                    event_id integer primary key autoincrement, job_id text not null, event_type text not null,
                    stage text, metadata text, created_at text not null
                );
                create table kg_nodes (
                    node_id text primary key, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, knowledge_status text not null default 'unknown',
                    aliases text not null default '[]', created_at text not null, updated_at text not null
                );
                create table kg_node_sources (
                    node_id text not null, source_id text not null, video_id text, transcript_id text,
                    segment_ids text not null default '[]', start_seconds real, end_seconds real,
                    extraction_job_id text, evidence_text text, created_at text not null,
                    primary key (node_id, source_id)
                );
                create table kg_relation_types (
                    relation_type text primary key, description text not null,
                    status text not null default 'approved', proposed_by_job_id text, created_at text not null
                );
                create table kg_edges (
                    edge_id text primary key, source_node_id text not null, target_node_id text not null,
                    relation_type text not null, relation_status text not null default 'accepted',
                    confidence real not null, evidence_source_ids text not null default '[]',
                    directional integer not null default 1, extraction_job_id text,
                    created_at text not null, updated_at text not null
                );
                create table kg_node_embeddings (
                    node_id text primary key, model text not null, dims integer not null,
                    vector text not null, created_at text not null
                );
                create table kg_node_detail_cache (
                    cache_key text primary key, node_id text not null, lens_hash text not null, lens_json text not null,
                    provider text not null, model text not null, prompt_version text not null,
                    detail_markdown text, evidence_json text not null default '[]', confidence real,
                    status text not null, created_at text not null, updated_at text not null
                );
                create table kg_user_knowledge (
                    node_id text primary key, status text not null default 'unknown',
                    confidence real, last_reviewed_at text, notes text, updated_at text not null
                );
                insert into videos values ('video-old', 'now', 'now');
                insert into transcript_sources values ('transcript-old', 'video-old', 'x', 'test', 'hash', 1, '{}', 'now');
                insert into preparation_jobs values ('job-old', 'video-old', 'intermediate', 'live', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into preparation_jobs values ('job-old-2', 'video-old', 'intermediate', 'live', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into kg_extraction_jobs values ('job-old', 'video-old', 'transcript-old', 'same-cache', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_extraction_jobs values ('job-old-2', 'video-old', 'transcript-old', 'same-cache-2', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes values ('node-old', 'embedding', 'concept', 'summary', null, 'pending', 0.6, 'unknown', '[]', 'now', 'now');
                insert into kg_nodes values ('node-target', 'target', 'entity', 'target summary', null, 'pending', 0.7, 'unknown', '[]', 'now', 'now');
                insert into kg_node_sources values ('node-old', 'segment-001', 'video-old', 'transcript-old', '[\"segment-001\"]', 0, 5, 'job-old', 'evidence', 'now');
                insert into kg_node_sources values ('node-old', 'segment-002', 'video-old', 'transcript-old', '[\"segment-002\"]', 5, 10, 'job-old-2', 'evidence-2', 'now');
                insert into kg_relation_types values ('supports', 'support', 'approved', null, 'now');
                insert into kg_edges values ('edge-old', 'node-old', 'node-target', 'supports', 'accepted', 0.8, '[]', 1, 'job-old', 'now', 'now');
                insert into kg_node_embeddings values ('node-old', 'model', 2, '[0.1,0.2]', 'now');
                insert into kg_node_detail_cache values ('detail-old', 'node-old', 'lens-hash', '{}', 'provider', 'model', 'v1', 'details', '[]', 0.8, 'ready', 'now', 'now');
                insert into kg_user_knowledge values ('node-old', 'understood', 0.8, 'now', 'notes', 'now');
            """)
        init_db()
        with connect_db() as conn:
            conn.execute(
                "insert into preparation_jobs values ('job-new', 'video-old', 'intermediate', 'live', 'ready', 'ready', 'now', 'now', 'graph_extraction')"
            )
            conn.execute(
                "insert into kg_extraction_jobs values ('job-new', 'video-old', 'transcript-old', 'same-cache', 'ready', 'ready', 1, 0, null, null, 'now', 'now')"
            )
            self.assertEqual(conn.execute("select count(*) from kg_extraction_jobs where cache_key = 'same-cache'").fetchone()[0], 2)
            row = conn.execute("select extraction_job_id, evidence_text from kg_node_sources where extraction_job_id = 'job-old'").fetchone()
            self.assertEqual(dict(row), {"extraction_job_id": "job-old", "evidence_text": "evidence"})
            self.assertEqual(conn.execute("select count(*) from kg_nodes where extraction_job_id = 'job-old'").fetchone()[0], 2)
            self.assertEqual(conn.execute("select count(*) from kg_nodes where extraction_job_id = 'job-old-2'").fetchone()[0], 1)
            self.assertEqual(
                {row[0] for row in conn.execute("select extraction_job_id from kg_nodes where node_id = 'node-old'" )},
                {"job-old", "job-old-2"},
            )
            self.assertEqual(conn.execute("select count(*) from kg_edges where extraction_job_id = 'job-old'").fetchone()[0], 1)
            self.assertEqual(tuple(conn.execute("select node_count, edge_count from kg_extraction_jobs where job_id = 'job-old'").fetchone()), (2, 1))
            self.assertEqual(tuple(conn.execute("select node_count, edge_count from kg_extraction_jobs where job_id = 'job-old-2'").fetchone()), (1, 0))
            self.assertEqual(conn.execute("select count(*) from kg_node_embeddings where extraction_job_id = 'job-old'").fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from kg_node_embeddings where extraction_job_id = 'job-old-2'").fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from kg_node_detail_cache where extraction_job_id = 'job-old'").fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from kg_user_knowledge where extraction_job_id = 'job-old'").fetchone()[0], 1)

    def test_unassignable_legacy_job_is_discarded_without_sinking_unrelated_jobs(self):
        # Discard a broken job and an unowned node without affecting a valid job.
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                create table preparation_jobs (
                    job_id text primary key, video_id text not null, learner_level text not null,
                    source_policy text not null default 'live', status text not null, stage text not null,
                    created_at text not null, updated_at text not null, job_kind text not null default 'bubble_analysis'
                );
                create table transcript_sources (
                    transcript_id text primary key, video_id text not null, filename text not null, source text not null,
                    content_hash text not null, segment_count integer not null, metadata text default '{}', created_at text not null
                );
                create table schema_migrations (version integer primary key, name text not null, applied_at text not null);
                insert into schema_migrations values (1, 'initial_schema', 'now');
                insert into schema_migrations values (2, 'persisted_translation_jobs', 'now');
                insert into schema_migrations values (3, 'knowledge_graph_extraction', 'now');
                create table kg_extraction_jobs (
                    job_id text primary key, video_id text not null, transcript_id text not null,
                    cache_key text not null unique, status text not null, stage text not null,
                    node_count integer default 0, edge_count integer default 0, error_code text, message text,
                    created_at text not null, updated_at text not null
                );
                create table kg_extraction_events (
                    event_id integer primary key autoincrement, job_id text not null, event_type text not null,
                    stage text, metadata text, created_at text not null
                );
                create table kg_nodes (
                    node_id text primary key, canonical_name text not null, node_type text not null,
                    short_summary text not null, detailed_explanation text, detail_status text not null default 'pending',
                    confidence real not null, aliases text not null default '[]', created_at text not null, updated_at text not null
                );
                create table kg_node_sources (
                    node_id text not null, source_id text not null, video_id text, transcript_id text,
                    segment_ids text not null default '[]', start_seconds real, end_seconds real,
                    extraction_job_id text, evidence_text text, created_at text not null,
                    primary key (node_id, source_id)
                );
                create table kg_relation_types (
                    relation_type text primary key, description text not null,
                    status text not null default 'approved', proposed_by_job_id text, created_at text not null
                );
                create table kg_edges (
                    edge_id text primary key, source_node_id text not null, target_node_id text not null,
                    relation_type text not null, relation_status text not null default 'accepted',
                    confidence real not null, evidence_source_ids text not null default '[]',
                    directional integer not null default 1, extraction_job_id text,
                    created_at text not null, updated_at text not null
                );
                create table kg_node_embeddings (
                    node_id text primary key, model text not null, dims integer not null, vector text not null, created_at text not null
                );
                create table kg_node_detail_cache (
                    cache_key text primary key, node_id text not null, lens_hash text not null, lens_json text not null,
                    provider text not null, model text not null, prompt_version text not null,
                    detail_markdown text, evidence_json text not null default '[]', confidence real,
                    status text not null, created_at text not null, updated_at text not null
                );
                create table kg_user_knowledge (
                    node_id text primary key, status text not null default 'unknown',
                    confidence real, last_reviewed_at text, notes text, updated_at text not null
                );
                insert into videos values ('video-a', 'now', 'now');
                insert into transcript_sources values ('transcript-a', 'video-a', 'x', 'test', 'hash', 1, '{}', 'now');
                insert into kg_relation_types values ('related_to', 'related_to', 'approved', null, 'now');

                insert into preparation_jobs values ('job-bad', 'video-a', 'intermediate', 'live', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into kg_extraction_jobs values ('job-bad', 'video-a', 'transcript-a', 'bad-cache', 'ready', 'ready', 1, 1, null, null, 'now', 'now');
                insert into kg_extraction_events values (1, 'job-bad', 'started', 'extracting', '{}', 'now');
                insert into kg_nodes values ('node-bad', 'bad concept', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                insert into kg_node_sources values ('node-bad', 'segment-001', 'video-a', 'transcript-a', '[]', 0, 5, 'job-bad', 'evidence', 'now');
                -- dangling target_node_id: 'node-missing' does not exist in kg_nodes -> job-bad is structurally broken.
                insert into kg_edges values ('edge-bad', 'node-bad', 'node-missing', 'related_to', 'accepted', 0.5, '[]', 1, 'job-bad', 'now', 'now');

                insert into kg_nodes values ('node-orphan', 'orphan concept', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                insert into kg_node_sources values ('node-orphan', 'segment-orphan', 'video-a', 'transcript-a', '[]', 5, 10, null, 'orphan evidence', 'now');

                insert into preparation_jobs values ('job-good', 'video-a', 'intermediate', 'live', 'ready', 'ready', 'now', 'now', 'graph_extraction');
                insert into kg_extraction_jobs values ('job-good', 'video-a', 'transcript-a', 'good-cache', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes values ('node-good', 'good concept', 'concept', 'summary', null, 'pending', 0.6, '[]', 'now', 'now');
                insert into kg_node_sources values ('node-good', 'segment-002', 'video-a', 'transcript-a', '[]', 0, 5, 'job-good', 'evidence', 'now');
            """)
        init_db()
        with connect_db() as conn:
            self.assertIsNone(conn.execute("select 1 from kg_extraction_jobs where job_id = 'job-bad'").fetchone())
            self.assertEqual(
                tuple(conn.execute("select status, stage from preparation_jobs where job_id = 'job-bad'").fetchone()),
                ("failed", "failed"),
            )
            self.assertIsNotNone(conn.execute("select 1 from kg_extraction_jobs where job_id = 'job-good'").fetchone())
            self.assertEqual(
                tuple(conn.execute("select status, stage from preparation_jobs where job_id = 'job-good'").fetchone()),
                ("ready", "ready"),
            )
            self.assertEqual(
                conn.execute("select canonical_name from kg_nodes where extraction_job_id = 'job-good'").fetchone()[0],
                "good concept",
            )
            self.assertIsNone(conn.execute("select 1 from kg_nodes where node_id = 'node-orphan'").fetchone())
            self.assertIsNone(conn.execute("select 1 from kg_node_sources where node_id = 'node-orphan'").fetchone())
            for table in (
                "kg_extraction_events_legacy", "kg_node_sources_legacy", "kg_edges_legacy", "kg_node_embeddings_legacy",
                "kg_node_detail_cache_legacy", "kg_user_knowledge_legacy", "kg_nodes_legacy", "kg_extraction_jobs_legacy",
            ):
                self.assertIsNone(conn.execute("select 1 from sqlite_master where type = 'table' and name = ?", (table,)).fetchone())

    def test_legacy_schema_upgrades_without_losing_rows(self):
        with connect_db() as conn:
            conn.executescript("""
                create table videos (video_id text primary key, created_at text not null, updated_at text not null);
                insert into videos values ('legacy', 'then', 'then');
                create table preparation_jobs (job_id text primary key, video_id text not null, learner_level text not null, status text not null, stage text not null, created_at text not null, updated_at text not null);
                create table transcript_sources (transcript_id text primary key, video_id text not null, filename text not null, source text not null, content_hash text not null, segment_count integer not null, created_at text not null);
                create table schema_migrations (name text primary key, applied_at text not null);
            """)
        init_db()
        with connect_db() as conn:
            self.assertEqual(conn.execute("select video_id from videos").fetchone()[0], "legacy")
            self.assertIn("source_policy", {row["name"] for row in conn.execute("pragma table_info(preparation_jobs)")})
            self.assertIn("metadata", {row["name"] for row in conn.execute("pragma table_info(transcript_sources)")})


if __name__ == "__main__":
    unittest.main()
