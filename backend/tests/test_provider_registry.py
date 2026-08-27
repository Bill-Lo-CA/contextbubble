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


if __name__ == "__main__": unittest.main()
