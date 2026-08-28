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

    def test_extraction_failure_marks_graph_job_failed(self):
        transcript = store_transcript(
            "kg-video-4", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text."}],
            source="test",
        )
        job_id = self.make_job("kg-video-4")
        with mock.patch.object(graph_runner, "heuristic_extract_graph", side_effect=RuntimeError("boom")):
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


if __name__ == "__main__":
    unittest.main()
