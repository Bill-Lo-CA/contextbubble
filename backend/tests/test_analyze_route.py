import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import api_routes
from api_models import AnalysisRequest
from providers import AgentProviderError


class CreateAnalysisRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.body = AnalysisRequest(video_id="demo-video", learner_level="beginner", transcript_id="transcript-1")
        self.transcript = {"video_id": "demo-video"}

    async def test_analysis_is_offloaded_to_threadpool_not_run_on_event_loop(self):
        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=self.body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_analysis_for_transcript", return_value={"analysis_id": "a1", "status": "ready"}) as run_direct, \
             mock.patch.object(api_routes, "run_in_threadpool", new=mock.AsyncMock(return_value={"analysis_id": "a1", "status": "ready"})) as run_offloaded, \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            await api_routes.create_analysis(mock.Mock(), authorization="token")

        run_direct.assert_not_called()
        run_offloaded.assert_awaited_once_with(run_direct, "demo-video", "beginner", "transcript-1", False)

    async def test_concurrent_calls_for_same_analysis_share_one_threadpool_dispatch(self):
        release = asyncio.Event()
        call_count = 0

        async def fake_run_in_threadpool(func, *args):
            nonlocal call_count
            call_count += 1
            await release.wait()
            return {"analysis_id": "shared-id", "status": "completed"}

        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=self.body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_in_threadpool", new=fake_run_in_threadpool), \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            task_a = asyncio.create_task(api_routes.create_analysis(mock.Mock(), authorization="token"))
            task_b = asyncio.create_task(api_routes.create_analysis(mock.Mock(), authorization="token"))
            # Let both requests run up to the point where they'd dispatch to
            # the threadpool before releasing the (single) dispatch.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            response_a, response_b = await asyncio.gather(task_a, task_b)

        self.assertEqual(call_count, 1, "only the first caller should dispatch to the threadpool")
        expected = {"analysis_id": "shared-id", "status": "completed"}
        self.assertEqual(json.loads(response_a.body), expected)
        self.assertEqual(json.loads(response_b.body), expected)

    async def test_force_refresh_bypasses_coalescing(self):
        call_count = 0

        async def fake_run_in_threadpool(func, *args):
            nonlocal call_count
            call_count += 1
            return {"analysis_id": "shared-id", "status": "completed"}

        forced_body = AnalysisRequest(video_id="demo-video", learner_level="beginner", transcript_id="transcript-1", force_refresh=True)
        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=forced_body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_in_threadpool", new=fake_run_in_threadpool), \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            await asyncio.gather(
                api_routes.create_analysis(mock.Mock(), authorization="token"),
                api_routes.create_analysis(mock.Mock(), authorization="token"),
            )

        self.assertEqual(call_count, 2, "force_refresh calls should not be coalesced")

    async def test_concurrent_follower_receives_leaders_error_too(self):
        release = asyncio.Event()

        async def fake_run_in_threadpool(func, *args):
            await release.wait()
            raise AgentProviderError("GEMINI_TIMEOUT")

        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=self.body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_in_threadpool", new=fake_run_in_threadpool), \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            task_a = asyncio.create_task(api_routes.create_analysis(mock.Mock(), authorization="token"))
            task_b = asyncio.create_task(api_routes.create_analysis(mock.Mock(), authorization="token"))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            response_a, response_b = await asyncio.gather(task_a, task_b)

        self.assertEqual(response_a.status_code, 502)
        self.assertEqual(response_b.status_code, 502)

    async def test_retryable_provider_error_from_threadpool_still_maps_to_502(self):
        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=self.body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_in_threadpool", new=mock.AsyncMock(side_effect=AgentProviderError("GEMINI_TIMEOUT"))), \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            response = await api_routes.create_analysis(mock.Mock(), authorization="token")

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
