from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import providers


INITIAL_GEMINI_STATUS = {
    "status": "idle",
    "last_request_at": None,
    "last_success_at": None,
    "last_error_at": None,
    "last_error_code": None,
    "last_http_status": None,
    "last_message": "",
    "total_requests": 0,
    "total_failures": 0,
}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"candidates":[{"content":{"parts":[{"text":"{\\"ok\\": true}"}]}}]}'


class ProviderStatusTests(unittest.TestCase):
    def setUp(self):
        providers.GEMINI_STATUS.update(INITIAL_GEMINI_STATUS)

    def test_gemini_status_reports_missing_key_without_secret(self):
        with self.assertRaises(providers.AgentProviderError) as raised:
            providers.gemini_generate("{}", "", "gemini-test")

        self.assertEqual(raised.exception.error_code, "GEMINI_NOT_CONFIGURED")
        status = providers.gemini_status("", "gemini-test")
        self.assertFalse(status["configured"])
        self.assertEqual(status["status"], "not_configured")
        self.assertEqual(status["last_error_code"], "GEMINI_NOT_CONFIGURED")

    def test_gemini_status_reports_429(self):
        error = HTTPError("https://example.invalid", 429, "Too Many Requests", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")

        self.assertEqual(raised.exception.error_code, "GEMINI_RATE_LIMITED")
        status = providers.gemini_status("test-key", "gemini-test")
        self.assertTrue(status["configured"])
        self.assertEqual(status["status"], "rate_limited")
        self.assertEqual(status["last_http_status"], 429)
        self.assertEqual(status["last_error_code"], "GEMINI_RATE_LIMITED")

    def test_gemini_status_reports_success(self):
        with mock.patch.object(providers, "urlopen", return_value=FakeResponse()):
            result = providers.gemini_generate("{}", "test-key", "gemini-test")

        self.assertEqual(result, {"ok": True})
        status = providers.gemini_status("test-key", "gemini-test")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["last_error_code"], None)
        self.assertEqual(status["total_requests"], 1)


if __name__ == "__main__":
    unittest.main()
