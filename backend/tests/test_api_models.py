from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api_models import PrepareVideoRequest, TranslationRequest
from api_routes import routers


class ApiModelTests(unittest.TestCase):
    def test_prepare_video_rejects_unknown_learner_level(self):
        with self.assertRaises(ValidationError):
            PrepareVideoRequest(learner_level="unknown")

    def test_translation_rejects_oversized_language(self):
        with self.assertRaises(ValidationError):
            TranslationRequest(target_language="x" * 33)

    def test_expected_routes_are_registered(self):
        paths = {route.path for router in routers for route in router.routes}
        self.assertEqual(
            paths,
            {
                "/api/pair", "/api/pair/resend", "/api/videos/{video_id}/prepare",
                "/api/subtitles", "/api/demo-transcript", "/api/youtube-subtitles",
                "/api/preparations/{job_id}", "/api/preparations/{job_id}/events",
                "/api/translations", "/api/translations/{translation_job_id}",
                "/api/analyze", "/api/videos/{video_id}/analysis",
                "/api/analysis/{analysis_id}", "/api/health",
            },
        )


if __name__ == "__main__":
    unittest.main()
