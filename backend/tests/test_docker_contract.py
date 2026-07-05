import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


class DockerEntrypointContractTest(unittest.TestCase):
    def test_root_initializes_runtime_directories_before_privilege_drop(self):
        script = ENTRYPOINT.read_text()

        for directory in ("/data", "/data/media", "/models", "/tmp/contextbubble"):
            self.assertIn(directory, script)
        self.assertIn("install -d", script)
        self.assertIn("-o contextbubble", script)
        self.assertIn("-g contextbubble", script)
        self.assertIn("-m 0750", script)

        privilege_drop = script.index('exec gosu contextbubble "$0" "$@"')
        bootstrap = script.index("/usr/local/bin/contextbubble-bootstrap-model")
        self.assertLess(privilege_drop, bootstrap)

    def test_root_and_app_phases_preserve_command_after_privilege_drop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "events.log"

            self.write_command(
                bin_dir,
                "id",
                'if [ "$1" = -u ]; then printf "%s\\n" "${TEST_UID:-1000}"; fi\n',
            )
            self.write_command(
                bin_dir,
                "install",
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in -d) shift ;; -o|-g|-m) shift 2 ;; *) mkdir -p "$1"; shift ;; esac\n'
                "done\n",
            )
            self.write_command(bin_dir, "chown", ":\n")
            self.write_command(
                bin_dir,
                "gosu",
                'printf "gosu\\n" >> "$TEST_EVENT_LOG"\n'
                "shift\n"
                'TEST_UID=1000 exec "$@"\n',
            )
            bootstrap = self.write_command(
                bin_dir,
                "bootstrap",
                'printf "bootstrap\\n" >> "$TEST_EVENT_LOG"\n'
                'for argument in "$@"; do printf "bootstrap-arg:%s\\n" "$argument" >> "$TEST_EVENT_LOG"; done\n'
                'exec "$@"\n',
            )
            backend = self.write_command(
                bin_dir,
                "backend",
                'printf "backend\\n" >> "$TEST_EVENT_LOG"\n'
                'for argument in "$@"; do printf "backend-arg:%s\\n" "$argument" >> "$TEST_EVENT_LOG"; done\n',
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "TEST_UID": "0",
                    "TEST_EVENT_LOG": str(log),
                    "CONTEXTBUBBLE_DATA_DIR": str(root / "data"),
                    "CONTEXTBUBBLE_MEDIA_DIR": str(root / "data" / "media"),
                    "CONTEXTBUBBLE_MODELS_DIR": str(root / "models"),
                    "CONTEXTBUBBLE_TMP_DIR": str(root / "tmp"),
                    "CONTEXTBUBBLE_BOOTSTRAP": str(bootstrap),
                }
            )
            result = subprocess.run(
                [str(ENTRYPOINT), str(backend), "argument with spaces", "--flag"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = log.read_text().splitlines()
            self.assertEqual(events[0], "gosu")
            self.assertEqual(events[1], "bootstrap")
            self.assertEqual(events[-3:], ["backend", "backend-arg:argument with spaces", "backend-arg:--flag"])
            self.assertIn(f"bootstrap-arg:{backend}", events)
            self.assertIn("bootstrap-arg:argument with spaces", events)
            self.assertIn("bootstrap-arg:--flag", events)

    @staticmethod
    def write_command(bin_dir, name, body):
        command = bin_dir / name
        command.write_text("#!/bin/sh\nset -eu\n" + body)
        command.chmod(0o755)
        return command


if __name__ == "__main__":
    unittest.main()
