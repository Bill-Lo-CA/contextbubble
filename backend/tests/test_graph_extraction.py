from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
from db import connect_db, init_db
import graph_extraction_agents
import graph_runner
import graph_store
import preparation_jobs
from graph_runner import run_graph_extraction_for_transcript
from graph_extraction_agents import edge_id_for
from graph_store import extraction_job_payload
from transcripts import store_transcript


class GraphExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def make_job(self, video_id, force_refresh=False):
        with mock.patch.object(preparation_jobs, "start_preparation_thread"):
            job = preparation_jobs.create_or_reuse_job(
                video_id,
                preparation_jobs.GRAPH_PLACEHOLDER_LEARNER_LEVEL,
                force_refresh=force_refresh,
                job_kind="graph_extraction",
            )
        return job["job_id"]

    def test_extraction_persists_job_scoped_nodes_edges_and_sources(self):
        transcript = store_transcript(
            "kg-video-1",
            "video.vtt",
            segments=[
                {"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."},
                {"id": "segment-002", "start_seconds": 5, "end_seconds": 10, "text": "Retrieval augmented generation uses that vector database."},
            ],
            source="test",
        )
        job_id = self.make_job("kg-video-1")
        result = run_graph_extraction_for_transcript("kg-video-1", transcript["transcript_id"], job_id)

        self.assertEqual(result["status"], "ready")
        with connect_db() as conn:
            node_rows = conn.execute("select * from kg_nodes where extraction_job_id = ?", (job_id,)).fetchall()
            source_rows = conn.execute("select * from kg_node_sources where extraction_job_id = ?", (job_id,)).fetchall()
            edge_rows = conn.execute("select * from kg_edges where extraction_job_id = ?", (job_id,)).fetchall()
        self.assertEqual(len(node_rows), result["node_count"])
        self.assertGreater(len(source_rows), 0)
        self.assertEqual(len(edge_rows), result["edge_count"])

    def test_cache_hit_clones_complete_snapshot_to_new_job(self):
        transcript = store_transcript(
            "kg-video-2",
            "video.vtt",
            segments=[
                {"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."},
                {"id": "segment-002", "start_seconds": 5, "end_seconds": 10, "text": "Retrieval augmented generation uses that vector database."},
            ],
            source="test",
        )
        source_job = self.make_job("kg-video-2")
        source_result = run_graph_extraction_for_transcript("kg-video-2", transcript["transcript_id"], source_job)
        target_job = self.make_job("kg-video-2", force_refresh=True)
        target_result = run_graph_extraction_for_transcript("kg-video-2", transcript["transcript_id"], target_job)

        self.assertEqual(target_result["node_count"], source_result["node_count"])
        with connect_db() as conn:
            target_nodes = conn.execute("select count(*) from kg_nodes where extraction_job_id = ?", (target_job,)).fetchone()[0]
            target_sources = conn.execute("select count(*) from kg_node_sources where extraction_job_id = ?", (target_job,)).fetchone()[0]
            target_edges = conn.execute("select count(*) from kg_edges where extraction_job_id = ?", (target_job,)).fetchone()[0]
        self.assertEqual(target_nodes, source_result["node_count"])
        self.assertGreater(target_sources, 0)
        self.assertEqual(target_edges, source_result["edge_count"])

    def test_cache_hit_clone_rolls_back_job_and_snapshot_on_failure(self):
        transcript = store_transcript(
            "kg-video-rollback", "video.vtt",
            segments=[
                {"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."},
                {"id": "segment-002", "start_seconds": 5, "end_seconds": 10, "text": "Retrieval augmented generation uses that vector database."},
            ],
            source="test",
        )
        source_job = self.make_job("kg-video-rollback")
        run_graph_extraction_for_transcript("kg-video-rollback", transcript["transcript_id"], source_job)
        target_job = self.make_job("kg-video-rollback", force_refresh=True)
        with connect_db() as conn:
            conn.execute(
                f"""create trigger fail_clone_edges before insert on kg_edges
                when new.extraction_job_id = '{target_job}'
                begin select raise(abort, 'forced clone failure'); end"""
            )

        with self.assertRaises(sqlite3.IntegrityError):
            run_graph_extraction_for_transcript("kg-video-rollback", transcript["transcript_id"], target_job)
        with connect_db() as conn:
            target_row = conn.execute("select status, stage, error_code from kg_extraction_jobs where job_id = ?", (target_job,)).fetchone()
            counts = [
                conn.execute(f"select count(*) from {table} where extraction_job_id = ?", (target_job,)).fetchone()[0]
                for table in ("kg_nodes", "kg_node_sources", "kg_edges", "kg_node_embeddings", "kg_node_detail_cache", "kg_user_knowledge")
            ]
        self.assertEqual(dict(target_row), {"status": "failed", "stage": "failed", "error_code": "GRAPH_EXTRACTION_FAILED"})
        self.assertEqual(counts, [0] * 6)

    def test_graph_payload_parent_failure_overrides_child(self):
        transcript = store_transcript(
            "kg-parent-fail", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text."}],
            source="test",
        )
        job_id = self.make_job("kg-parent-fail")
        graph_store.upsert_extraction_job(
            job_id, "kg-parent-fail", transcript["transcript_id"], "cache", status="ready", stage="ready", node_count=1, edge_count=1,
        )
        preparation_jobs.update_job(job_id, status="failed", stage="failed", error_code="PARENT_FAILED", message="parent failed")

        self.assertEqual(
            extraction_job_payload(job_id),
            {
                "job_id": job_id,
                "video_id": "kg-parent-fail",
                "status": "failed",
                "stage": "failed",
                "node_count": 1,
                "edge_count": 1,
                "error_code": "PARENT_FAILED",
                "message": "parent failed",
            },
        )

    def test_graph_payload_missing_child_uses_parent_fields(self):
        job_id = self.make_job("kg-video-parent-only")
        preparation_jobs.update_job(job_id, status="failed", stage="failed", error_code="CAPTION_FAILED", message="caption failed")

        payload = extraction_job_payload(job_id)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["stage"], "failed")
        self.assertEqual(payload["error_code"], "CAPTION_FAILED")
        self.assertEqual(payload["message"], "caption failed")

    def test_related_to_edges_are_normalized_and_undirected(self):
        transcript = store_transcript(
            "kg-video-undirected", "video.vtt",
            segments=[
                {"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."},
                {"id": "segment-002", "start_seconds": 5, "end_seconds": 10, "text": "Retrieval augmented generation uses that vector database."},
            ],
            source="test",
        )
        job_id = self.make_job("kg-video-undirected")
        run_graph_extraction_for_transcript("kg-video-undirected", transcript["transcript_id"], job_id)

        with connect_db() as conn:
            edge = conn.execute(
                "select edge_id, source_node_id, target_node_id, relation_type, directional from kg_edges where extraction_job_id = ?",
                (job_id,),
            ).fetchone()
        self.assertEqual(edge["relation_type"], "related_to")
        self.assertEqual(edge["directional"], 0)
        self.assertLess(edge["source_node_id"], edge["target_node_id"])
        self.assertEqual(edge["edge_id"], edge_id_for(edge["target_node_id"], edge["source_node_id"], "related_to"))

    def test_changed_transcript_keeps_previous_snapshot_and_segment_ids(self):
        first = store_transcript(
            "kg-video-3", "first.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text."}],
            source="test",
        )
        second = store_transcript(
            "kg-video-3", "second.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "The reviewer checks grounding."}],
            source="test",
        )
        first_job = self.make_job("kg-video-3")
        second_job = self.make_job("kg-video-3", force_refresh=True)
        run_graph_extraction_for_transcript("kg-video-3", first["transcript_id"], first_job)
        run_graph_extraction_for_transcript("kg-video-3", second["transcript_id"], second_job, force_refresh=True)

        with connect_db() as conn:
            source_rows = conn.execute(
                "select extraction_job_id, transcript_id, source_id from kg_node_sources where video_id = ? order by extraction_job_id",
                ("kg-video-3",),
            ).fetchall()
        self.assertEqual({row["extraction_job_id"] for row in source_rows}, {first_job, second_job})
        self.assertEqual({row["source_id"] for row in source_rows}, {"segment-001"})
        self.assertEqual({row["transcript_id"] for row in source_rows}, {first["transcript_id"], second["transcript_id"]})

    def test_different_extraction_modes_never_share_a_cached_snapshot(self):
        transcript = store_transcript(
            "kg-video-modes", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."}],
            source="test",
        )
        heuristic_job = self.make_job("kg-video-modes")
        run_graph_extraction_for_transcript("kg-video-modes", transcript["transcript_id"], heuristic_job)

        llm_nodes = [{
            "node_id": "node-llm", "canonical_name": "llm concept", "node_type": "concept",
            "short_summary": "s", "confidence": 0.9, "sources": [],
        }]
        ollama_job = self.make_job("kg-video-modes", force_refresh=True)
        # Real config changes are seen consistently by every module that snapshots
        # GRAPH_EXTRACTION_MODE at import time (graph_store, graph_extraction_agents,
        # graph_runner) - patch all three so this test reflects an actual mode switch.
        with mock.patch.object(graph_store, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_runner, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "llm_extract_graph", return_value=(llm_nodes, [])):
            run_graph_extraction_for_transcript("kg-video-modes", transcript["transcript_id"], ollama_job)

        with connect_db() as conn:
            cache_keys = {
                row["job_id"]: row["cache_key"]
                for row in conn.execute(
                    "select job_id, cache_key from kg_extraction_jobs where job_id in (?, ?)", (heuristic_job, ollama_job)
                )
            }
            ollama_names = [
                row["canonical_name"] for row in
                conn.execute("select canonical_name from kg_nodes where extraction_job_id = ?", (ollama_job,)).fetchall()
            ]
            heuristic_names = [
                row["canonical_name"] for row in
                conn.execute("select canonical_name from kg_nodes where extraction_job_id = ?", (heuristic_job,)).fetchall()
            ]
        self.assertNotEqual(cache_keys[heuristic_job], cache_keys[ollama_job])
        self.assertEqual(ollama_names, ["llm concept"])
        self.assertNotIn("llm concept", heuristic_names)

    def test_job_edge_count_matches_edges_left_after_rejected_types_are_filtered(self):
        transcript = store_transcript(
            "kg-rejected-count", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "A influences B."}],
            source="test",
        )
        job_id = self.make_job("kg-rejected-count")
        with connect_db() as conn:
            conn.execute(
                "insert into kg_relation_types (relation_type, description, status, created_at) "
                "values ('influences', 'influences', 'rejected', 'now')"
            )
        nodes = [make_node("node-a", "a"), make_node("node-b", "b")]
        edges = [{
            "edge_id": "edge-rejected", "source_node_id": "node-a", "target_node_id": "node-b",
            "relation_type": "influences", "relation_status": "proposed", "confidence": 0.7,
            "evidence_source_ids": [], "directional": 1,
        }]

        with mock.patch.object(graph_runner, "extract_graph_for_video", return_value=(nodes, edges, "heuristic")):
            result = run_graph_extraction_for_transcript("kg-rejected-count", transcript["transcript_id"], job_id)

        self.assertEqual(result["edge_count"], 0)
        with connect_db() as conn:
            persisted = conn.execute("select count(*) from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()[0]
        self.assertEqual(persisted, 0)

    def test_extraction_failure_marks_graph_job_failed(self):
        transcript = store_transcript(
            "kg-video-4", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text."}],
            source="test",
        )
        job_id = self.make_job("kg-video-4")
        with mock.patch.object(graph_runner, "extract_graph_for_video", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                run_graph_extraction_for_transcript("kg-video-4", transcript["transcript_id"], job_id)
        with connect_db() as conn:
            row = conn.execute("select status, stage, error_code from kg_extraction_jobs where job_id = ?", (job_id,)).fetchone()
        self.assertEqual(dict(row), {"status": "failed", "stage": "failed", "error_code": "GRAPH_EXTRACTION_FAILED"})
        self.assertEqual(extraction_job_payload(job_id)["status"], "failed")

    def test_graph_payload_rejects_bubble_job(self):
        job_id = self.make_job("kg-video-5")
        with mock.patch.object(preparation_jobs, "start_preparation_thread"):
            bubble = preparation_jobs.create_or_reuse_job("kg-video-5", "beginner")
        self.assertNotEqual(job_id, bubble["job_id"])
        self.assertIsNone(extraction_job_payload(bubble["job_id"]))


def make_node(node_id, name):
    return {"node_id": node_id, "canonical_name": name, "node_type": "concept", "short_summary": name, "confidence": 0.6, "sources": []}


class SaveNodesAndEdgesRelationStatusTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()
        with connect_db() as conn:
            conn.execute("insert into videos (video_id, created_at, updated_at) values ('video-rs', 'now', 'now')")
            conn.execute(
                "insert into transcript_sources (transcript_id, video_id, filename, source, content_hash, segment_count, created_at) "
                "values ('transcript-rs', 'video-rs', 'x', 'test', 'hash', 0, 'now')"
            )

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def make_job(self, job_id):
        with connect_db() as conn:
            conn.execute(
                "insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, job_kind, created_at, updated_at) "
                "values (?, 'video-rs', 'intermediate', 'live', 'processing', 'extracting_graph', 'graph_extraction', 'now', 'now')",
                (job_id,),
            )
        graph_store.upsert_extraction_job(job_id, "video-rs", "transcript-rs", f"cache-{job_id}", status="processing", stage="extracting_graph")

    def edge(self, edge_id, relation_type, **overrides):
        base = {
            "edge_id": edge_id, "source_node_id": "node-a", "target_node_id": "node-b",
            "relation_type": relation_type, "confidence": 0.7, "evidence_source_ids": [], "directional": 1,
        }
        base.update(overrides)
        return base

    def test_edge_without_relation_status_defaults_to_accepted_and_approved(self):
        # Regression guard: heuristic-path edges never set relation_status at all.
        job_id = "job-rs-default"
        self.make_job(job_id)
        graph_store.save_nodes_and_edges(
            job_id, "video-rs", "transcript-rs",
            [make_node("node-a", "a"), make_node("node-b", "b")],
            [self.edge("edge-1", "related_to")],
        )
        with connect_db() as conn:
            edge_row = conn.execute("select relation_status from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()
            type_row = conn.execute("select status from kg_relation_types where relation_type = 'related_to'").fetchone()
        self.assertEqual(edge_row["relation_status"], "accepted")
        self.assertEqual(type_row["status"], "approved")

    def test_new_proposed_relation_type_persists_as_proposed_with_description_and_proposer(self):
        job_id = "job-rs-proposed"
        self.make_job(job_id)
        graph_store.save_nodes_and_edges(
            job_id, "video-rs", "transcript-rs",
            [make_node("node-a", "a"), make_node("node-b", "b")],
            [self.edge("edge-1", "influences", relation_status="proposed", proposed_relation_description="A shapes B")],
        )
        with connect_db() as conn:
            edge_row = conn.execute("select relation_status from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()
            type_row = conn.execute(
                "select status, description, proposed_by_job_id from kg_relation_types where relation_type = 'influences'"
            ).fetchone()
        self.assertEqual(edge_row["relation_status"], "proposed")
        self.assertEqual(dict(type_row), {"status": "proposed", "description": "A shapes B", "proposed_by_job_id": job_id})

    def test_edge_for_already_approved_type_is_upgraded_to_accepted(self):
        with connect_db() as conn:
            conn.execute(
                "insert into kg_relation_types (relation_type, description, status, created_at) values ('reviewed_ok', 'reviewed_ok', 'approved', 'now')"
            )
        job_id = "job-rs-approved"
        self.make_job(job_id)
        graph_store.save_nodes_and_edges(
            job_id, "video-rs", "transcript-rs",
            [make_node("node-a", "a"), make_node("node-b", "b")],
            [self.edge("edge-1", "reviewed_ok", relation_status="proposed", proposed_relation_description="x")],
        )
        with connect_db() as conn:
            edge_row = conn.execute("select relation_status from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()
        self.assertEqual(edge_row["relation_status"], "accepted")

    def test_edge_for_already_rejected_type_is_not_created(self):
        with connect_db() as conn:
            conn.execute(
                "insert into kg_relation_types (relation_type, description, status, created_at) values ('reviewed_bad', 'reviewed_bad', 'rejected', 'now')"
            )
        job_id = "job-rs-rejected"
        self.make_job(job_id)
        persisted_edge_count = graph_store.save_nodes_and_edges(
            job_id, "video-rs", "transcript-rs",
            [make_node("node-a", "a"), make_node("node-b", "b")],
            [self.edge("edge-1", "reviewed_bad", relation_status="proposed", proposed_relation_description="x")],
        )
        with connect_db() as conn:
            count = conn.execute("select count(*) from kg_edges where extraction_job_id = ?", (job_id,)).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(persisted_edge_count, 0)

    def test_built_in_type_is_repaired_to_approved_and_edge_accepted(self):
        with connect_db() as conn:
            conn.execute(
                "insert into kg_relation_types (relation_type, description, status, proposed_by_job_id, created_at) "
                "values ('causes', 'causes', 'rejected', 'old-job', 'now')"
            )
        job_id = "job-rs-built-in"
        self.make_job(job_id)
        persisted_edge_count = graph_store.save_nodes_and_edges(
            job_id, "video-rs", "transcript-rs",
            [make_node("node-a", "a"), make_node("node-b", "b")],
            [self.edge("edge-1", "causes", relation_status="proposed")],
        )
        with connect_db() as conn:
            type_row = conn.execute(
                "select status, proposed_by_job_id from kg_relation_types where relation_type = 'causes'"
            ).fetchone()
            edge_row = conn.execute(
                "select relation_status from kg_edges where extraction_job_id = ?", (job_id,)
            ).fetchone()
        self.assertEqual(dict(type_row), {"status": "approved", "proposed_by_job_id": None})
        self.assertEqual(edge_row["relation_status"], "accepted")
        self.assertEqual(persisted_edge_count, 1)

    def test_save_nodes_and_edges_rolls_back_entirely_on_edge_insert_failure(self):
        job_id = "job-rs-rollback"
        self.make_job(job_id)
        with connect_db() as conn:
            conn.execute(
                f"""create trigger fail_edges_{job_id.replace('-', '_')} before insert on kg_edges
                when new.extraction_job_id = '{job_id}'
                begin select raise(abort, 'forced failure'); end"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            graph_store.save_nodes_and_edges(
                job_id, "video-rs", "transcript-rs",
                [make_node("node-a", "a"), make_node("node-b", "b")],
                [self.edge("edge-1", "related_to")],
            )
        with connect_db() as conn:
            counts = [
                conn.execute(f"select count(*) from {table} where extraction_job_id = ?", (job_id,)).fetchone()[0]
                for table in ("kg_nodes", "kg_node_sources", "kg_edges")
            ]
        self.assertEqual(counts, [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
