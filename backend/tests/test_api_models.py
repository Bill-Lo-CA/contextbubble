from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api_models import PrepareVideoRequest


class ApiModelTests(unittest.TestCase):
    def test_prepare_video_rejects_unknown_learner_level(self):
        with self.assertRaises(ValidationError):
            PrepareVideoRequest(learner_level="unknown")


if __name__ == "__main__":
    unittest.main()
