from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import config
from asr_pipeline import mark_asr_chunk_completed
from db import connect_db, init_db
from preparation_jobs import job_payload


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
        payload = job_payload("job", include_ready=False, include_transcript=True, include_sentence_entries=True)
        self.assertTrue(payload["partial_transcript"])
        self.assertEqual(payload["transcript_source"], "whisper_partial")


if __name__ == "__main__": unittest.main()
