from pathlib import Path
import sys
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import provider_registry
from providers import AgentProviderError


class ResolveProviderTests(unittest.TestCase):
    def test_gemini_mode_resolves_to_gemini_provider_with_requested_model(self):
        provider = provider_registry.resolve_provider("gemini", gemini_model="gemini-test")
        self.assertIsInstance(provider, provider_registry.GeminiProvider)
        self.assertEqual(provider.model, "gemini-test")

    def test_ollama_mode_resolves_to_ollama_provider_with_requested_model(self):
        provider = provider_registry.resolve_provider("ollama", ollama_model="qwen-test")
        self.assertIsInstance(provider, provider_registry.OllamaProvider)
        self.assertEqual(provider.model, "qwen-test")

    def test_unknown_mode_raises_caller_supplied_error_code(self):
        with self.assertRaises(AgentProviderError) as raised:
            provider_registry.resolve_provider("heuristic", disabled_error_code="ANALYSIS_FAILED", disabled_message="no LLM provider selected")
        self.assertEqual(raised.exception.error_code, "ANALYSIS_FAILED")
        self.assertEqual(str(raised.exception), "no LLM provider selected")

    def test_unknown_mode_without_message_uses_generic_default(self):
        with self.assertRaises(AgentProviderError) as raised:
            provider_registry.resolve_provider("bogus")
        self.assertEqual(raised.exception.error_code, "PROVIDER_DISABLED")


class ProviderTransportTests(unittest.TestCase):
    def test_gemini_provider_forwards_schema_to_transport(self):
        provider = provider_registry.GeminiProvider(model="gemini-test", api_key="test-key")
        with mock.patch.object(provider_registry, "gemini_generate", return_value={"ok": True}) as generate:
            result = provider.generate_json("prompt", schema={"type": "object"})
        generate.assert_called_once_with("prompt", "test-key", "gemini-test", schema={"type": "object"})
        self.assertEqual(result, {"ok": True})

    def test_ollama_provider_forwards_schema_to_transport(self):
        provider = provider_registry.OllamaProvider(model="qwen-test", base_url="http://example.invalid")
        with mock.patch.object(provider_registry, "ollama_generate", return_value={"ok": True}) as generate:
            result = provider.generate_json("prompt", schema={"type": "object"})
        generate.assert_called_once_with("prompt", "http://example.invalid", "qwen-test", schema={"type": "object"})
        self.assertEqual(result, {"ok": True})


class GenerateJsonWithRetryTests(unittest.TestCase):
    def setUp(self):
        self.sleep_patcher = mock.patch.object(provider_registry.time, "sleep")
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def test_retries_transient_error_then_succeeds(self):
        provider = mock.Mock()
        provider.generate_json.side_effect = [AgentProviderError("GEMINI_RATE_LIMITED"), {"ok": True}]
        result = provider_registry.generate_json_with_retry(provider, "prompt")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.generate_json.call_count, 2)

    def test_retry_exhausted_raises_last_error(self):
        provider = mock.Mock()
        provider.generate_json.side_effect = AgentProviderError("GEMINI_TIMEOUT")
        with self.assertRaises(AgentProviderError) as raised:
            provider_registry.generate_json_with_retry(provider, "prompt")
        self.assertEqual(raised.exception.error_code, "GEMINI_TIMEOUT")
        self.assertEqual(provider.generate_json.call_count, provider_registry.MAX_PROVIDER_ATTEMPTS)

    def test_non_retryable_error_fails_immediately(self):
        for error_code in ("GEMINI_AUTH_FAILED", "GEMINI_NOT_CONFIGURED", "OLLAMA_HTTP_ERROR"):
            with self.subTest(error_code):
                provider = mock.Mock()
                provider.generate_json.side_effect = AgentProviderError(error_code)
                with self.assertRaises(AgentProviderError):
                    provider_registry.generate_json_with_retry(provider, "prompt")
                self.assertEqual(provider.generate_json.call_count, 1)

    def test_http_400_only_retried_once(self):
        provider = mock.Mock()
        provider.generate_json.side_effect = AgentProviderError("GEMINI_HTTP_ERROR", "HTTP 400")
        with self.assertRaises(AgentProviderError):
            provider_registry.generate_json_with_retry(provider, "prompt")
        self.assertEqual(provider.generate_json.call_count, 1)

    def test_gemini_server_error_is_retried(self):
        provider = mock.Mock()
        provider.generate_json.side_effect = [AgentProviderError("GEMINI_SERVER_ERROR"), {"ok": True}]
        result = provider_registry.generate_json_with_retry(provider, "prompt")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.generate_json.call_count, 2)

    def test_ollama_error_codes_are_all_retryable(self):
        for error_code in ("OLLAMA_TIMEOUT", "OLLAMA_UNAVAILABLE", "OLLAMA_SERVER_ERROR", "OLLAMA_INVALID_RESPONSE", "OLLAMA_INVALID_JSON"):
            with self.subTest(error_code):
                provider = mock.Mock()
                provider.generate_json.side_effect = [AgentProviderError(error_code), {"ok": True}]
                result = provider_registry.generate_json_with_retry(provider, "prompt")
                self.assertEqual(result, {"ok": True})
                self.assertEqual(provider.generate_json.call_count, 2)


if __name__ == "__main__": unittest.main()
