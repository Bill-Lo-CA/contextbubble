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

    async def test_retryable_provider_error_from_threadpool_still_maps_to_502(self):
        with mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=self.body)), \
             mock.patch.object(api_routes, "load_transcript", return_value=self.transcript), \
             mock.patch.object(api_routes, "run_in_threadpool", new=mock.AsyncMock(side_effect=AgentProviderError("GEMINI_TIMEOUT"))), \
             mock.patch.object(api_routes, "require_auth", return_value=None):
            response = await api_routes.create_analysis(mock.Mock(), authorization="token")

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
