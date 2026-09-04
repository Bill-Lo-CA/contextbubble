from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import config
import analysis_store
from asr_pipeline import mark_asr_chunk_completed
from db import connect_db, init_db
import preparation_jobs
import preparation_runner
from transcripts import store_transcript


class PreparationJobTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_partial_asr_payload_survives_completed_chunks(self):
        timestamp = config.now_iso()
        with connect_db() as conn:
            conn.execute("insert into videos (video_id, created_at, updated_at) values (?, ?, ?)", ("partial-asr-demo", timestamp, timestamp))
            conn.execute("insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, duration_seconds, chunks_total, chunks_completed, progress, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("job", "partial-asr-demo", "beginner", "live", "processing", "transcribing", 60, 1, 0, 0.5, timestamp, timestamp))
            conn.execute("insert into asr_chunks (job_id, chunk_index, start_seconds, end_seconds, status, updated_at) values (?, ?, ?, ?, ?, ?)", ("job", 0, 0, 30, "pending", timestamp))
        mark_asr_chunk_completed("job", 0, [{"start_seconds": 0, "end_seconds": 4, "text": "This partial transcript is useful."}])
        payload = preparation_jobs.job_payload("job", include_ready=False, include_transcript=True, include_sentence_entries=True)
        self.assertTrue(payload["partial_transcript"])
        self.assertEqual(payload["transcript_source"], "whisper_partial")

    def test_old_analysis_version_is_not_reused(self):
        timestamp = config.now_iso()
        with connect_db() as conn:
            conn.execute("insert into videos (video_id, created_at, updated_at) values (?, ?, ?)", ("stale-analysis-demo", timestamp, timestamp))
            conn.execute("insert into transcript_sources (transcript_id, video_id, filename, source, content_hash, segment_count, metadata, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)", ("old-transcript", "stale-analysis-demo", "old.vtt", "youtube_caption", "old-hash", 0, "{}", timestamp))
            conn.execute("insert into analyses (analysis_id, video_id, learner_level, transcript_id, cache_key, status, stage, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("old-analysis", "stale-analysis-demo", "beginner", "old-transcript", "stale-analysis-demo:old-hash:beginner:agent-mvp-gemini-v4", "completed", "ready", timestamp, timestamp))
            conn.execute("insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, analysis_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("old-job", "stale-analysis-demo", "beginner", "live", "ready", "ready", "old-analysis", timestamp, timestamp))

        self.assertIsNone(analysis_store.analysis_result("old-analysis"))
        with mock.patch.object(preparation_jobs, "start_preparation_thread") as start:
            job = preparation_jobs.create_or_reuse_job("stale-analysis-demo", "beginner")
        self.assertNotEqual(job["job_id"], "old-job")
        self.assertEqual(job["status"], "queued")
        start.assert_called_once_with(job["job_id"])

    def test_graph_failure_keeps_graph_specific_error_code_on_parent_job(self):
        transcript = store_transcript(
            "graph-failure", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "Graph extraction."}],
            source="test",
        )
        with mock.patch.object(preparation_jobs, "start_preparation_thread"):
            job = preparation_jobs.create_or_reuse_job(
                "graph-failure", "intermediate", job_kind="graph_extraction"
            )
        with mock.patch.object(
            preparation_runner, "transcript_for_job", return_value=(transcript, "test", 5)
        ), mock.patch.object(
            preparation_runner, "run_graph_extraction_for_transcript", side_effect=RuntimeError("boom")
        ), mock.patch.object(preparation_runner, "finish_preparation_thread"):
            preparation_runner.run_preparation_job(job["job_id"])

        result = preparation_jobs.job_payload(job["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "GRAPH_EXTRACTION_FAILED")


if __name__ == "__main__": unittest.main()
