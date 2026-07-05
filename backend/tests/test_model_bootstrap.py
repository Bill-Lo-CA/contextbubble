import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
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

    def run_bootstrap(self, checksum=None, target=None, cwd=None):
        env = os.environ.copy()
        env.update(
            {
                "WHISPER_MODEL": str(target or self.target),
                "WHISPER_MODEL_URL": self.source.as_uri(),
                "WHISPER_MODEL_SHA256": checksum or self.checksum,
            }
        )
        return subprocess.run(
            [str(BOOTSTRAP)],
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

    def test_relative_models_target_is_rejected(self):
        relative_target = Path("tmp/models/model.bin")

        result = self.run_bootstrap(target=relative_target, cwd=self.root)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / relative_target).exists())


if __name__ == "__main__":
    unittest.main()
