from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import config
from db import init_db
from translation_cache import save_translation_cache, translation_decision


class TranslationCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def test_cached_translation_is_reused_and_changed_text_retranslates(self):
        decision = translation_decision("segment-1", "hello", "", "", "zh-TW")
        save_translation_cache(decision["cache_key"], "segment-1", decision["source_hash"], decision["context_hash"], "zh-TW", decision["provider"], decision["model"], {"translated_text": "哈囉", "confidence": 0.9, "status": "translated", "decision": "translate", "reason": ""})
        self.assertEqual(translation_decision("segment-1", "hello", "", "", "zh-TW")["decision"], "use_cache")
        self.assertEqual(translation_decision("segment-1", "changed", "", "", "zh-TW")["decision"], "retranslate")


if __name__ == "__main__": unittest.main()
