import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
from db import init_db
import translation_jobs


class TranslationJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()
        while not translation_jobs.TRANSLATION_QUEUE.empty():
            translation_jobs.TRANSLATION_QUEUE.get_nowait()
            translation_jobs.TRANSLATION_QUEUE.task_done()

    async def asyncTearDown(self):
        await translation_jobs.stop_translation_worker()
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    async def test_duplicate_active_job_is_reused_and_persisted(self):
        payload = {"id": "segment-1", "source_text": "hello", "target_language": "zh-TW"}
        first = await translation_jobs.create_translation_job(payload)
        second = await translation_jobs.create_translation_job(payload)
        self.assertEqual(first["translation_job_id"], second["translation_job_id"])
        self.assertEqual(translation_jobs.get_translation_job(first["translation_job_id"])["status"], "queued")

    async def test_stale_processing_job_resumes_after_restart(self):
        payload = {"id": "segment-2", "source_text": "hello", "target_language": "zh-TW"}
        job = await translation_jobs.create_translation_job(payload)
        translation_jobs.update_job(job["translation_job_id"], status="processing", updated_at=0)
        with mock.patch.object(translation_jobs, "translate_segment", return_value={"status": "translated", "translated_text": "哈囉"}):
            await translation_jobs.start_translation_worker()
            await asyncio.wait_for(translation_jobs.TRANSLATION_QUEUE.join(), 2)
        self.assertEqual(translation_jobs.get_translation_job(job["translation_job_id"])["status"], "translated")

    async def test_full_queue_rejects_new_job(self):
        original = translation_jobs.TRANSLATION_QUEUE
        translation_jobs.TRANSLATION_QUEUE = asyncio.Queue(maxsize=1)
        try:
            await translation_jobs.create_translation_job({"id": "one", "source_text": "one"})
            with self.assertRaises(translation_jobs.TranslationQueueFull):
                await translation_jobs.create_translation_job({"id": "two", "source_text": "two"})
        finally:
            translation_jobs.TRANSLATION_QUEUE = original


if __name__ == "__main__":
    unittest.main()
