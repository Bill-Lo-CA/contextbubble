from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import config
from db import connect_db, init_db
import graph_store
from graph_runner import run_graph_extraction_for_transcript
import preparation_jobs


TRANSCRIPT_SEGMENTS = [
    {"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "An embedding maps text into a vector space."},
    {"id": "segment-002", "start_seconds": 5, "end_seconds": 10, "text": "Retrieval augmented generation uses that vector database."},
    {"id": "segment-003", "start_seconds": 90, "end_seconds": 95, "text": "The reviewer checks the transcript for grounding."},
]


class GraphExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def seed_transcript(self, video_id, transcript_id, content_hash):
        timestamp = config.now_iso()
        with connect_db() as conn:
            conn.execute("insert into videos (video_id, created_at, updated_at) values (?, ?, ?) on conflict(video_id) do update set updated_at = excluded.updated_at", (video_id, timestamp, timestamp))
            conn.execute("insert into transcript_sources (transcript_id, video_id, filename, source, content_hash, segment_count, metadata, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (transcript_id, video_id, "video.vtt", "youtube_caption", content_hash, len(TRANSCRIPT_SEGMENTS), "{}", timestamp))
            conn.executemany(
                "insert into transcript_segments (transcript_id, segment_id, start_seconds, end_seconds, text) values (?, ?, ?, ?, ?)",
                [(transcript_id, s["id"], s["start_seconds"], s["end_seconds"], s["text"]) for s in TRANSCRIPT_SEGMENTS],
            )

    def make_graph_job(self, video_id, force_refresh=False):
        with mock.patch.object(preparation_jobs, "start_preparation_thread"):
            job = preparation_jobs.create_or_reuse_job(video_id, preparation_jobs.GRAPH_PLACEHOLDER_LEARNER_LEVEL, force_refresh=force_refresh, job_kind="graph_extraction")
        return job["job_id"]

    def test_create_or_reuse_job_keeps_graph_and_bubble_jobs_separate(self):
        with mock.patch.object(preparation_jobs, "start_preparation_thread"):
            bubble_job = preparation_jobs.create_or_reuse_job("kg-video", "beginner", job_kind="bubble_analysis")
            graph_job = preparation_jobs.create_or_reuse_job("kg-video", preparation_jobs.GRAPH_PLACEHOLDER_LEARNER_LEVEL, job_kind="graph_extraction")
        self.assertNotEqual(bubble_job["job_id"], graph_job["job_id"])
        self.assertEqual(bubble_job["job_kind"], "bubble_analysis")
        self.assertEqual(graph_job["job_kind"], "graph_extraction")
        self.assertEqual(graph_job["learner_level"], preparation_jobs.GRAPH_PLACEHOLDER_LEARNER_LEVEL)

        graph_job_id = self.make_graph_job("kg-video")
        self.assertEqual(graph_job_id, graph_job["job_id"])

    def test_extraction_persists_nodes_edges_and_job_status(self):
        self.seed_transcript("kg-video-2", "transcript-kg-2", "hash-1")
        job_id = self.make_graph_job("kg-video-2")
        result = run_graph_extraction_for_transcript("kg-video-2", "transcript-kg-2", job_id)

        self.assertEqual(result["status"], "ready")
        self.assertGreater(result["node_count"], 0)
        with connect_db() as conn:
            node_rows = conn.execute("select * from kg_nodes").fetchall()
            source_rows = conn.execute("select * from kg_node_sources").fetchall()
            job_row = conn.execute("select * from kg_extraction_jobs where job_id = ?", (job_id,)).fetchone()
        self.assertEqual(len(node_rows), result["node_count"])
        self.assertGreater(len(source_rows), 0)
        self.assertEqual(job_row["status"], "ready")
        self.assertTrue(job_row["cache_key"].endswith(f":{config.GRAPH_VERSION}"))

    def test_rerunning_same_job_id_is_idempotent(self):
        self.seed_transcript("kg-video-3", "transcript-kg-3", "hash-3")
        job_b = self.make_graph_job("kg-video-3")
        first = run_graph_extraction_for_transcript("kg-video-3", "transcript-kg-3", job_b)
        second = run_graph_extraction_for_transcript("kg-video-3", "transcript-kg-3", job_b)

        self.assertEqual(second["node_count"], first["node_count"])
        with connect_db() as conn:
            rows = conn.execute("select job_id from kg_extraction_jobs where video_id = 'kg-video-3'").fetchall()
        self.assertEqual([row["job_id"] for row in rows], [job_b])

    def test_force_refresh_creates_its_own_row_alongside_the_original(self):
        self.seed_transcript("kg-video-4", "transcript-kg-4", "hash-4")
        job_d = self.make_graph_job("kg-video-4")
        first = run_graph_extraction_for_transcript("kg-video-4", "transcript-kg-4", job_d)
        job_e = self.make_graph_job("kg-video-4", force_refresh=True)
        second = run_graph_extraction_for_transcript("kg-video-4", "transcript-kg-4", job_e, force_refresh=True)

        self.assertEqual(second["job_id"], job_e)
        self.assertEqual(second["status"], "ready")
        self.assertEqual(second["node_count"], first["node_count"])
        # kg_extraction_jobs is a 1:1 child of preparation_jobs: a force-refresh rerun
        # over unchanged content gets its own row rather than repointing job_d's,
        # so job_d's own payload keeps reporting correctly instead of degrading.
        self.assertEqual(graph_store.extraction_job_payload(job_d)["status"], "ready")
        with connect_db() as conn:
            rows = conn.execute("select job_id from kg_extraction_jobs where video_id = 'kg-video-4' order by job_id").fetchall()
        self.assertEqual(sorted(row["job_id"] for row in rows), sorted([job_d, job_e]))


if __name__ == "__main__": unittest.main()
