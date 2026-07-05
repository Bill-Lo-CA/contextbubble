import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "docker" / "bootstrap-model.sh"


class ModelBootstrapTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.bin"
        self.source.write_bytes(b"contextbubble test model\n")
        self.target = self.root / "models" / "model.bin"
        self.checksum = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def run_bootstrap(self, checksum=None, target=None, cwd=None, command=(), env_extra=None):
        env = os.environ.copy()
        env.update(
            {
                "WHISPER_MODEL": str(target or self.target),
                "WHISPER_MODEL_URL": self.source.as_uri(),
                "WHISPER_MODEL_SHA256": checksum or self.checksum,
            }
        )
        env.update(env_extra or {})
        return subprocess.run(
            [str(BOOTSTRAP), *command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            cwd=cwd,
        )

    def test_downloads_valid_model_atomically(self):
        result = self.run_bootstrap()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertIn("downloaded", result.stdout)
        self.assertEqual(list(self.target.parent.glob("*.partial.*")), [])

    def test_existing_valid_model_is_not_replaced(self):
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        original_mtime = self.target.stat().st_mtime_ns

        second = self.run_bootstrap()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already valid", second.stdout)
        self.assertEqual(self.target.stat().st_mtime_ns, original_mtime)

    def test_bad_checksum_does_not_install_model(self):
        result = self.run_bootstrap("0" * 64)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob("*.partial.*")), [])

    def test_invalid_existing_model_is_safely_replaced(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"invalid model")

        result = self.run_bootstrap()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertIn("downloaded", result.stdout)
        self.assertEqual(list(self.target.parent.glob("*.partial.*")), [])

    def test_successful_bootstrap_executes_backend_with_original_arguments(self):
        arguments_file = self.root / "backend-arguments"
        backend = self.root / "backend"
        backend.write_text(
            "#!/bin/sh\n"
            'for argument in "$@"; do printf "%s\\n" "$argument"; done > "$TEST_ARGUMENTS_FILE"\n'
        )
        backend.chmod(0o755)

        result = self.run_bootstrap(
            command=(str(backend), "argument with spaces", "--flag"),
            env_extra={"TEST_ARGUMENTS_FILE": str(arguments_file)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(arguments_file.read_text().splitlines(), ["argument with spaces", "--flag"])

    def test_relative_models_target_is_rejected(self):
        relative_target = Path("tmp/models/model.bin")

        result = self.run_bootstrap(target=relative_target, cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / relative_target).exists())

    def test_models_directory_target_is_rejected_before_download_or_backend(self):
        models_dir = self.root / "nested" / "models"
        models_dir.mkdir(parents=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        curl_marker = self.root / "curl-ran"
        backend_marker = self.root / "backend-ran"
        curl = bin_dir / "curl"
        curl.write_text("#!/bin/sh\nprintf ran > \"$TEST_CURL_MARKER\"\nexit 1\n")
        curl.chmod(0o755)
        backend = bin_dir / "backend"
        backend.write_text("#!/bin/sh\nprintf ran > \"$TEST_BACKEND_MARKER\"\n")
        backend.chmod(0o755)

        result = self.run_bootstrap(
            target=f"{models_dir}/",
            command=(str(backend),),
            env_extra={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TEST_CURL_MARKER": str(curl_marker),
                "TEST_BACKEND_MARKER": str(backend_marker),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("filename", result.stderr)
        self.assertFalse(curl_marker.exists())
        self.assertFalse(backend_marker.exists())
        self.assertEqual(list(models_dir.iterdir()), [])

    def test_filename_directory_target_is_rejected_without_orphans(self):
        self.target.mkdir(parents=True)
        sentinel = self.target / "existing-file"
        sentinel.write_text("unchanged")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        curl_marker = self.root / "curl-ran"
        backend_marker = self.root / "backend-ran"
        curl = bin_dir / "curl"
        curl.write_text("#!/bin/sh\nprintf ran > \"$TEST_CURL_MARKER\"\nexit 1\n")
        curl.chmod(0o755)
        backend = bin_dir / "backend"
        backend.write_text("#!/bin/sh\nprintf ran > \"$TEST_BACKEND_MARKER\"\n")
        backend.chmod(0o755)

        result = self.run_bootstrap(
            command=(str(backend),),
            env_extra={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "TEST_CURL_MARKER": str(curl_marker),
                "TEST_BACKEND_MARKER": str(backend_marker),
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory", result.stderr)
        self.assertFalse(curl_marker.exists())
        self.assertFalse(backend_marker.exists())
        self.assertEqual(list(self.target.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "unchanged")
        self.assertEqual(list(self.target.parent.glob("*.partial.*")), [])

    def test_install_disallows_directory_targets_until_final_validation(self):
        script = BOOTSTRAP.read_text()

        self.assertIn('mv -fT "$partial" "$WHISPER_MODEL"', script)
        move = script.index('mv -fT "$partial" "$WHISPER_MODEL"')
        final_validation = script.index(
            'if ! model_valid "$WHISPER_MODEL"',
            move,
        )
        clear_partial = script.index("partial=\n", final_validation)

        self.assertLess(move, final_validation)
        self.assertLess(final_validation, clear_partial)

    def test_signal_during_download_cleans_partial_and_does_not_start_backend(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        ready = self.root / "curl-ready"
        backend_marker = self.root / "backend-ran"
        curl = bin_dir / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --output) output=$2; shift 2 ;;\n"
            "    *) shift ;;\n"
            "  esac\n"
            "done\n"
            "printf partial > \"$output\"\n"
            "printf ready > \"$TEST_CURL_READY\"\n"
            "trap 'exit 143' TERM\n"
            "trap 'exit 130' INT\n"
            "trap 'exit 129' HUP\n"
            "while :; do sleep 1; done\n"
        )
        curl.chmod(0o755)
        backend = bin_dir / "backend"
        backend.write_text("#!/bin/sh\nprintf ran > \"$TEST_BACKEND_MARKER\"\n")
        backend.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "TEST_CURL_READY": str(ready),
                "TEST_BACKEND_MARKER": str(backend_marker),
                "WHISPER_MODEL": str(self.target),
                "WHISPER_MODEL_URL": "https://example.invalid/model.bin",
                "WHISPER_MODEL_SHA256": self.checksum,
            }
        )
        process = subprocess.Popen(
            [str(BOOTSTRAP), str(backend)],
            env=env,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "curl stub did not start")

            process.terminate()
            returncode = process.wait(timeout=5)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise

        self.assertNotEqual(returncode, 0)
        self.assertEqual(list(self.target.parent.glob("*.partial.*")), [])
        self.assertFalse(self.target.exists())
        self.assertFalse(backend_marker.exists())


if __name__ == "__main__":
    unittest.main()
