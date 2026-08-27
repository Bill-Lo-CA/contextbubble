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

    def test_existing_v3_graph_schema_is_upgraded_without_losing_graph_rows(self):
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
                insert into kg_extraction_jobs values ('job-old', 'video-old', 'transcript-old', 'same-cache', 'ready', 'ready', 1, 0, null, null, 'now', 'now');
                insert into kg_nodes values ('node-old', 'embedding', 'concept', 'summary', null, 'pending', 0.6, 'unknown', '[]', 'now', 'now');
                insert into kg_node_sources values ('node-old', 'segment-001', 'video-old', 'transcript-old', '[\"segment-001\"]', 0, 5, 'job-old', 'evidence', 'now');
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
            row = conn.execute("select extraction_job_id, evidence_text from kg_node_sources").fetchone()
            self.assertEqual(dict(row), {"extraction_job_id": "job-old", "evidence_text": "evidence"})
            self.assertEqual(conn.execute("select count(*) from kg_nodes where extraction_job_id = 'job-old'").fetchone()[0], 1)

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
