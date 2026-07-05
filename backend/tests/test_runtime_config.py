import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def load_config():
    spec = importlib.util.spec_from_file_location("runtime_config_under_test", BACKEND_DIR / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeConfigTests(unittest.TestCase):
    def test_backend_bind_defaults_with_clear_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = load_config()

        self.assertEqual(config.BACKEND_HOST, "127.0.0.1")
        self.assertEqual(config.BACKEND_PORT, 8000)

    def test_backend_bind_environment_overrides(self):
        environment = {
            "CONTEXTBUBBLE_HOST": "0.0.0.0",
            "CONTEXTBUBBLE_PORT": "9000",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            config = load_config()

        self.assertEqual(config.BACKEND_HOST, "0.0.0.0")
        self.assertEqual(config.BACKEND_PORT, 9000)

    def test_backend_port_rejects_out_of_range_value(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_PORT": "70000"}, clear=True):
            with self.assertRaisesRegex(ValueError, "CONTEXTBUBBLE_PORT"):
                load_config()

    def test_backend_port_rejects_non_integer_value(self):
        with mock.patch.dict(os.environ, {"CONTEXTBUBBLE_PORT": "invalid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "CONTEXTBUBBLE_PORT must be an integer"):
                load_config()

    def test_transcription_forwards_cpu_and_language_settings(self):
        import media

        chunk = {"chunk_index": 0, "start_seconds": 0, "end_seconds": 10}
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "chunk-0000.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(media, "WHISPER_NO_GPU", True),
                mock.patch.object(media, "WHISPER_LANGUAGE", "zh"),
                mock.patch.object(media, "run_command") as run_command,
            ):
                media.transcribe_audio_chunk("audio.wav", chunk, tmpdir, "job-1")

        whisper_command = run_command.call_args_list[1].args[0]
        self.assertIn("-ng", whisper_command)
        language_index = whisper_command.index("-l")
        self.assertEqual(whisper_command[language_index + 1], "zh")


if __name__ == "__main__":
    unittest.main()
