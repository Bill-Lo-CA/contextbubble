import contextvars
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import analysis_store
import config
from db import init_db
from transcripts import store_transcript


class ConcurrentAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()
        self.transcript = store_transcript(
            "concurrent-video", "video.vtt",
            segments=[{"id": "segment-001", "start_seconds": 0, "end_seconds": 5, "text": "Concurrent analysis test."}],
            source="test",
        )

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_concurrent_requests_for_same_analysis_run_pipeline_once_and_stay_completed(self):
        call_count = 0
        call_count_lock = threading.Lock()
        entered_pipeline = threading.Event()
        release_pipeline = threading.Event()

        def fake_concept_candidates(segments, learner_level, window_seconds):
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            entered_pipeline.set()
            # Widen the race window: without the keyed lock, a second concurrent
            # caller would reach this same point and both would go on to insert
            # bubbles for the same analysis_id, tripping the bubbles primary key.
            self.assertTrue(release_pipeline.wait(timeout=2))
            return [{"concept": "concept-a"}], {"window_count": 1}

        bubble = {
            "id": "bubble-1", "concept": "concept-a", "anchor_segment_id": "segment-001",
            "source_segment_ids": ["segment-001"], "start_seconds": 0.0,
            "short_explanation": "short", "expanded_explanation": "expanded", "confidence": 0.9,
            "review_status": "accepted", "review_reason": "",
        }

        results = {}

        def run(name):
            results[name] = analysis_store.run_analysis_for_transcript(
                "concurrent-video", "beginner", self.transcript["transcript_id"], False,
            )

        # config.get_settings() reads a ContextVar, which a plain new thread does
        # not inherit (it starts from the default, real on-disk data_dir). Run
        # both worker threads inside their own copy of this test's context so
        # they see the tempdir override instead of touching the real local
        # database. A Context object can't be entered concurrently by two
        # threads, so each thread needs its own copy (both copied while the
        # override is active, so both see the same overridden settings).
        context_a = contextvars.copy_context()
        context_b = contextvars.copy_context()

        with mock.patch.object(analysis_store, "concept_candidates", side_effect=fake_concept_candidates), \
             mock.patch.object(analysis_store, "reviewer_agent", side_effect=lambda candidate, segments, learner_level: candidate), \
             mock.patch.object(analysis_store, "validate_bubbles", return_value=[bubble]):
            thread_a = threading.Thread(target=context_a.run, args=(run, "a"))
            thread_a.start()
            self.assertTrue(entered_pipeline.wait(timeout=2), "thread A should have entered the locked pipeline")

            thread_b = threading.Thread(target=context_b.run, args=(run, "b"))
            thread_b.start()
            # Give thread B a chance to reach (and block on) the keyed lock
            # before releasing thread A, so both are genuinely in flight.
            time.sleep(0.1)

            release_pipeline.set()
            thread_a.join(timeout=2)
            thread_b.join(timeout=2)

        self.assertEqual(call_count, 1, "the pipeline should run exactly once for a shared analysis_id")
        self.assertEqual(results["a"]["status"], "completed")
        self.assertEqual(results["b"]["status"], "completed")
        self.assertEqual(results["a"]["analysis_id"], results["b"]["analysis_id"])

        final = analysis_store.analysis_result(results["a"]["analysis_id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(final["bubbles"]), 1)


if __name__ == "__main__":
    unittest.main()
