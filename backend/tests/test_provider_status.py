import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import auth
import config
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
    "last_invalid_response_length": None,
    "last_invalid_response_sha256": None,
    "last_json_error": None,
}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"candidates":[{"content":{"parts":[{"text":"{\\"ok\\": true}"}]}}]}'


class RawResponse(FakeResponse):
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body


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

    def test_gemini_status_counters_are_thread_safe(self):
        def increment(_):
            for _ in range(1000):
                providers.update_gemini_status(request_count=1)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(increment, range(8)))

        self.assertEqual(providers.gemini_status("test-key", "gemini-test")["total_requests"], 8000)

    def test_gemini_status_reports_success(self):
        with mock.patch.object(providers, "urlopen", return_value=FakeResponse()):
            result = providers.gemini_generate("{}", "test-key", "gemini-test")

        self.assertEqual(result, {"ok": True})
        status = providers.gemini_status("test-key", "gemini-test")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["last_error_code"], None)
        self.assertEqual(status["total_requests"], 1)

    def test_gemini_generate_includes_response_schema_when_provided(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return FakeResponse()

        with mock.patch.object(providers, "urlopen", side_effect=fake_urlopen):
            providers.gemini_generate("{}", "test-key", "gemini-test", schema={"type": "object"})

        self.assertEqual(captured["body"]["generationConfig"]["responseSchema"], {"type": "object"})

    def test_gemini_generate_omits_response_schema_when_not_provided(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return FakeResponse()

        with mock.patch.object(providers, "urlopen", side_effect=fake_urlopen):
            providers.gemini_generate("{}", "test-key", "gemini-test")

        self.assertNotIn("responseSchema", captured["body"]["generationConfig"])

    def test_ollama_generate_sends_schema_as_format_when_provided(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return OllamaFakeResponse()

        with mock.patch.object(providers, "urlopen", side_effect=fake_urlopen):
            providers.ollama_generate("prompt", "http://example.invalid", "qwen-test", schema={"type": "object"})

        self.assertEqual(captured["body"]["format"], {"type": "object"})

    def test_ollama_generate_defaults_format_to_json_string(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode())
            return OllamaFakeResponse()

        with mock.patch.object(providers, "urlopen", side_effect=fake_urlopen):
            providers.ollama_generate("prompt", "http://example.invalid", "qwen-test")

        self.assertEqual(captured["body"]["format"], "json")


class BrokenInnerJsonResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"candidates":[{"content":{"parts":[{"text":"{not valid json"}]}}]}'


class BrokenEnvelopeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b"not json at all"


class GeminiErrorClassificationTests(unittest.TestCase):
    def setUp(self):
        providers.GEMINI_STATUS.update(INITIAL_GEMINI_STATUS)

    def test_5xx_is_classified_as_server_error(self):
        error = HTTPError("https://example.invalid", 503, "Service Unavailable", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_SERVER_ERROR")

    def test_408_is_classified_as_timeout(self):
        error = HTTPError("https://example.invalid", 408, "Request Timeout", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_TIMEOUT")

    def test_4xx_other_than_429_401_403_stays_http_error(self):
        error = HTTPError("https://example.invalid", 404, "Not Found", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_HTTP_ERROR")


class OllamaErrorClassificationTests(unittest.TestCase):
    def test_5xx_is_classified_as_server_error(self):
        error = HTTPError("https://example.invalid", 503, "Service Unavailable", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.ollama_generate("prompt", "http://example.invalid", "qwen-test")
        self.assertEqual(raised.exception.error_code, "OLLAMA_SERVER_ERROR")

    def test_4xx_stays_http_error_and_is_not_retryable(self):
        error = HTTPError("https://example.invalid", 404, "Not Found", {}, None)
        with mock.patch.object(providers, "urlopen", side_effect=error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.ollama_generate("prompt", "http://example.invalid", "qwen-test")
        self.assertEqual(raised.exception.error_code, "OLLAMA_HTTP_ERROR")


class GeminiInvalidResponseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        providers.GEMINI_STATUS.update(INITIAL_GEMINI_STATUS)

    def test_gemini_status_records_invalid_json_diagnostics_without_raw_content(self):
        with mock.patch.object(providers, "urlopen", return_value=BrokenInnerJsonResponse()):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_INVALID_JSON")

        status = providers.gemini_status("test-key", "gemini-test")
        self.assertIsInstance(status["last_invalid_response_length"], int)
        self.assertEqual(len(status["last_invalid_response_sha256"]), 64)
        self.assertIn("line", status["last_json_error"])
        self.assertNotIn("not valid json", json.dumps(status))

        with mock.patch.object(providers, "urlopen", return_value=FakeResponse()):
            providers.gemini_generate("{}", "test-key", "gemini-test")
        status = providers.gemini_status("test-key", "gemini-test")
        self.assertIsNone(status["last_invalid_response_length"])
        self.assertIsNone(status["last_invalid_response_sha256"])
        self.assertIsNone(status["last_json_error"])

    def test_invalid_json_diagnostics_do_not_survive_a_later_unrelated_failure(self):
        # Regression guard: a retry sequence of "invalid JSON, then timeout"
        # must not leave the invalid-JSON diagnostics attached to a status
        # snapshot that now reports a completely different error.
        with mock.patch.object(providers, "urlopen", return_value=BrokenInnerJsonResponse()):
            with self.assertRaises(providers.AgentProviderError):
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertIsNotNone(providers.gemini_status("test-key", "gemini-test")["last_invalid_response_sha256"])

        timeout_error = TimeoutError()
        with mock.patch.object(providers, "urlopen", side_effect=timeout_error):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_TIMEOUT")

        status = providers.gemini_status("test-key", "gemini-test")
        self.assertEqual(status["last_error_code"], "GEMINI_TIMEOUT")
        self.assertIsNone(status["last_invalid_response_length"])
        self.assertIsNone(status["last_invalid_response_sha256"])
        self.assertIsNone(status["last_json_error"])

    def test_gemini_outer_envelope_decode_failure_raises_agent_provider_error(self):
        with mock.patch.object(providers, "urlopen", return_value=BrokenEnvelopeResponse()):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_INVALID_RESPONSE")

    def test_gemini_blocked_prompt_raises_non_transient_error(self):
        response = RawResponse(b'{"promptFeedback":{"blockReason":"SAFETY"}}')
        with mock.patch.object(providers, "urlopen", return_value=response):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_BLOCKED")

    def test_gemini_empty_candidates_is_invalid_response(self):
        with mock.patch.object(providers, "urlopen", return_value=RawResponse(b'{"candidates":[]}')):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_INVALID_RESPONSE")

    def test_gemini_non_object_envelope_is_invalid_response(self):
        with mock.patch.object(providers, "urlopen", return_value=RawResponse(b'[]')):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.gemini_generate("{}", "test-key", "gemini-test")
        self.assertEqual(raised.exception.error_code, "GEMINI_INVALID_RESPONSE")

    def test_ollama_non_object_envelope_is_invalid_response(self):
        with mock.patch.object(providers, "urlopen", return_value=RawResponse(b'[]')):
            with self.assertRaises(providers.AgentProviderError) as raised:
                providers.ollama_generate("prompt", "http://example.invalid", "qwen-test")
        self.assertEqual(raised.exception.error_code, "OLLAMA_INVALID_RESPONSE")


class DebugLogGatingTests(unittest.TestCase):
    def setUp(self):
        providers.GEMINI_STATUS.update(INITIAL_GEMINI_STATUS)

    def test_debug_log_writes_only_when_flag_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "llm_debug.log"

            with mock.patch.object(config, "DEBUG_LLM_RESPONSES", False), \
                 mock.patch.object(config, "LLM_DEBUG_LOG_FILE", log_path), \
                 mock.patch.object(providers, "urlopen", return_value=BrokenInnerJsonResponse()):
                with self.assertRaises(providers.AgentProviderError):
                    providers.gemini_generate("{}", "test-key", "gemini-test")
            self.assertFalse(log_path.exists())

            with mock.patch.object(config, "DEBUG_LLM_RESPONSES", True), \
                 mock.patch.object(config, "LLM_DEBUG_LOG_FILE", log_path), \
                 mock.patch.object(providers, "urlopen", return_value=BrokenInnerJsonResponse()):
                with self.assertRaises(providers.AgentProviderError):
                    providers.gemini_generate("{}", "test-key", "gemini-test")
            self.assertTrue(log_path.exists())
            self.assertIn("GEMINI_INVALID_JSON", log_path.read_text(encoding="utf-8"))

    def test_debug_log_overwrites_previous_failure_instead_of_growing(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "llm_debug.log"
            with mock.patch.object(config, "DEBUG_LLM_RESPONSES", True), \
                 mock.patch.object(config, "LLM_DEBUG_LOG_FILE", log_path):
                for _ in range(3):
                    with mock.patch.object(providers, "urlopen", return_value=BrokenInnerJsonResponse()):
                        with self.assertRaises(providers.AgentProviderError):
                            providers.gemini_generate("{}", "test-key", "gemini-test")
            content = log_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("GEMINI_INVALID_JSON"), 1)

    def test_debug_log_redacts_known_secrets(self):
        class SecretLeakingResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"candidates":[{"content":{"parts":[{"text":"{not valid json super-secret-token"}]}}]}'

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "llm_debug.log"
            with mock.patch.object(auth, "API_TOKEN", "super-secret-token"), \
                 mock.patch.object(config, "DEBUG_LLM_RESPONSES", True), \
                 mock.patch.object(config, "LLM_DEBUG_LOG_FILE", log_path), \
                 mock.patch.object(providers, "urlopen", return_value=SecretLeakingResponse()):
                with self.assertRaises(providers.AgentProviderError):
                    providers.gemini_generate("{}", "test-key", "gemini-test")
            content = log_path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret-token", content)
            self.assertIn("[redacted]", content)


class OllamaFakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"response": "{\\"ok\\": true}"}'


if __name__ == "__main__":
    unittest.main()
