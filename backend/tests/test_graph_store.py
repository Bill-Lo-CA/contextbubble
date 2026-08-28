from pathlib import Path
import sys
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import graph_store
from graph_store import graph_extraction_cache_key, graph_extraction_pipeline_signature


class GraphExtractionCacheIdentityTests(unittest.TestCase):
    def test_different_modes_never_share_a_pipeline_signature(self):
        signatures = {
            mode: graph_extraction_pipeline_signature(mode, model)
            for mode, model in (("heuristic", None), ("gemini", "gemini-2.5-flash"), ("ollama", "qwen3:8b"))
        }
        self.assertEqual(len(set(signatures.values())), 3)

    def test_different_models_within_the_same_mode_never_share_a_signature(self):
        self.assertNotEqual(
            graph_extraction_pipeline_signature("ollama", "qwen3:8b"),
            graph_extraction_pipeline_signature("ollama", "llama3.2:3b"),
        )

    def test_model_containing_a_colon_does_not_collide_with_a_different_split(self):
        # Ollama model tags like "qwen3:8b" already contain ":" - if the cache key
        # were a naive ":"-joined string instead of a hashed token, a model of
        # "8b" for mode "qwen3" could theoretically collide with model "qwen3:8b"
        # for some other mode string. The signature must not be derived by simple
        # string concatenation in a way that makes this ambiguous.
        self.assertNotEqual(
            graph_extraction_pipeline_signature("ollama", "qwen3:8b"),
            graph_extraction_pipeline_signature("ollama:qwen3", "8b"),
        )

    def test_signature_reads_active_config_constants_when_not_given_explicitly(self):
        # Mirrors this repo's established mode-testing convention (e.g.
        # test_analysis_agents.py patching analysis_agents.AGENT_MODE directly):
        # GRAPH_EXTRACTION_MODE/GEMINI_MODEL are imported once into graph_store's
        # namespace at module load, so tests patch that namespace directly rather
        # than expecting config.settings_override to propagate into it.
        with mock.patch.object(graph_store, "GRAPH_EXTRACTION_MODE", "gemini"), \
             mock.patch.object(graph_store, "GEMINI_MODEL", "gemini-test"):
            self.assertEqual(
                graph_extraction_pipeline_signature(),
                graph_extraction_pipeline_signature("gemini", "gemini-test"),
            )

    def test_cache_key_embeds_the_pipeline_signature_after_video_and_content_hash(self):
        key = graph_extraction_cache_key("video-1", "hash-1", "ollama", "qwen3:8b")
        self.assertEqual(key, f"video-1:hash-1:{graph_extraction_pipeline_signature('ollama', 'qwen3:8b')}")


if __name__ == "__main__":
    unittest.main()
