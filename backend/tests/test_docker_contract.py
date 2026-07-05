import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
REQUIREMENTS = ROOT / "requirements.txt"
REQUIREMENTS_LOCK = ROOT / "requirements.lock"


class DockerImageContractTest(unittest.TestCase):
    def test_dockerfile_pins_whisper_and_yt_dlp(self):
        self.assertTrue(DOCKERFILE.is_file(), "Dockerfile must exist")
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("ARG WHISPER_CPP_REF=v1.8.6", dockerfile)
        self.assertIn("ARG YT_DLP_VERSION=2026.7.4", dockerfile)

    def test_dockerfile_builds_static_cpu_only_whisper_cli(self):
        self.assertTrue(DOCKERFILE.is_file(), "Dockerfile must exist")
        dockerfile = DOCKERFILE.read_text()

        for option in (
            "-DBUILD_SHARED_LIBS=OFF",
            "-DGGML_CUDA=OFF",
            "-DGGML_VULKAN=OFF",
            "-DGGML_METAL=OFF",
        ):
            self.assertIn(option, dockerfile)

    def test_dockerfile_disables_native_cpu_tuning_with_portable_architectures(self):
        dockerfile = DOCKERFILE.read_text()

        architecture_detection = dockerfile.index('arch="$(dpkg --print-architecture)"')
        cmake_invocation = dockerfile.index("&& cmake -S /src/whisper.cpp", architecture_detection)
        cmake_build = dockerfile.index("&& cmake --build", cmake_invocation)
        architecture_options = dockerfile[architecture_detection:cmake_invocation]
        cmake_options = dockerfile[cmake_invocation:cmake_build]

        self.assertIn("-DGGML_CPU_ARM_ARCH=armv8-a", architecture_options)
        for option in (
            "-DGGML_SSE42=OFF",
            "-DGGML_AVX=OFF",
            "-DGGML_AVX2=OFF",
            "-DGGML_BMI2=OFF",
            "-DGGML_FMA=OFF",
            "-DGGML_F16C=OFF",
        ):
            self.assertIn(option, architecture_options)
        self.assertIn("x86-64 baseline", dockerfile)
        self.assertIn("-DGGML_NATIVE=OFF", cmake_options)
        self.assertIn('"$@"', cmake_options)

    def test_dockerfile_installs_pinned_full_youtube_runtime(self):
        dockerfile = DOCKERFILE.read_text()

        self.assertIn(
            '"yt-dlp[default,deno,pin,pin-deno]==${YT_DLP_VERSION}"',
            dockerfile,
        )

    def test_application_dependencies_are_pinned(self):
        requirements = REQUIREMENTS.read_text().splitlines()

        self.assertIn("fastapi==0.139.0", requirements)
        self.assertIn("uvicorn==0.50.0", requirements)

    def test_application_lock_pins_transitives_with_sha256_hashes(self):
        self.assertTrue(REQUIREMENTS_LOCK.is_file(), "requirements.lock must exist")
        lock = REQUIREMENTS_LOCK.read_text()

        self.assertIn("fastapi==0.139.0", lock)
        self.assertIn("uvicorn==0.50.0", lock)
        self.assertRegex(lock, r"(?m)^#    uv \d+\.\d+\.\d+ pip compile .+$")
        package_lines = [
            line for line in lock.splitlines() if line and not line[0].isspace() and not line.startswith("#")
        ]
        self.assertGreater(len(package_lines), 2)
        self.assertTrue(all("==" in line for line in package_lines))
        package_blocks = re.split(r"(?m)(?=^[a-zA-Z0-9])", lock)[1:]
        self.assertEqual(len(package_blocks), len(package_lines))
        self.assertTrue(
            all(re.search(r"(?m)^    --hash=sha256:[0-9a-f]{64}(?: \\)?$", block) for block in package_blocks)
        )

    def test_dockerfile_uses_supported_patch_updateable_base_tags(self):
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("FROM debian:bookworm-slim AS whisper-builder", dockerfile)
        self.assertIn("FROM python:3.12-slim-bookworm AS runtime", dockerfile)

    def test_dockerfile_defines_app_user_and_entrypoint(self):
        self.assertTrue(DOCKERFILE.is_file(), "Dockerfile must exist")
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("groupadd --gid 10001 contextbubble", dockerfile)
        self.assertIn("useradd --uid 10001 --gid contextbubble", dockerfile)
        self.assertIn("--create-home", dockerfile)
        self.assertIn("--home-dir /home/contextbubble", dockerfile)
        self.assertIn("HOME=/home/contextbubble", dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/contextbubble-entrypoint"]', dockerfile)

    def test_dockerfile_copies_application_and_executables_to_runtime_paths(self):
        dockerfile = DOCKERFILE.read_text()

        lock_copy_instruction = "COPY requirements.lock ./requirements.lock"
        lock_install_instruction = (
            "RUN python -m pip install --no-cache-dir --require-hashes "
            "-r requirements.lock"
        )
        self.assertIn(lock_copy_instruction, dockerfile)
        self.assertIn(lock_install_instruction, dockerfile)
        requirements_copy = dockerfile.index("COPY requirements.txt ./requirements.txt")
        lock_copy = dockerfile.index(lock_copy_instruction)
        requirements_install = dockerfile.index(lock_install_instruction)
        backend_copy = dockerfile.index("COPY backend ./backend")
        self.assertLess(requirements_copy, lock_copy)
        self.assertLess(lock_copy, requirements_install)
        self.assertLess(requirements_copy, requirements_install)
        self.assertLess(requirements_install, backend_copy)
        self.assertIn(
            "COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-cli /opt/whisper/bin/whisper-cli",
            dockerfile,
        )

    def test_dockerfile_keeps_runtime_caches_in_writable_tmp(self):
        dockerfile = DOCKERFILE.read_text()
        env = dockerfile[dockerfile.index("ENV PYTHONUNBUFFERED=1") : dockerfile.index("EXPOSE 8000")]

        self.assertIn("XDG_CACHE_HOME=/tmp/contextbubble/cache", env)
        self.assertIn("DENO_DIR=/tmp/contextbubble/deno", env)
        self.assertNotIn("XDG_CACHE_HOME=/app", env)
        self.assertNotIn("DENO_DIR=/app", env)
        self.assertNotIn("XDG_CACHE_HOME=/home/contextbubble", env)
        self.assertNotIn("DENO_DIR=/home/contextbubble", env)
        self.assertIn(
            "COPY docker/bootstrap-model.sh /usr/local/bin/contextbubble-bootstrap-model",
            dockerfile,
        )
        self.assertIn(
            "COPY docker/entrypoint.sh /usr/local/bin/contextbubble-entrypoint",
            dockerfile,
        )

    def test_dockerignore_excludes_local_secrets_state_and_git(self):
        self.assertTrue(DOCKERIGNORE.is_file(), ".dockerignore must exist")
        exclusions = set(DOCKERIGNORE.read_text().splitlines())

        for exclusion in (".env", "backend/.contextbubble", ".git"):
            self.assertIn(exclusion, exclusions)

    def test_dockerignore_default_denies_then_allows_only_image_inputs(self):
        lines = [
            line
            for line in DOCKERIGNORE.read_text().splitlines()
            if line and not line.startswith("#")
        ]

        self.assertEqual(lines[0], "*")
        expected_inclusions = {
            "!Dockerfile",
            "!requirements.txt",
            "!requirements.lock",
            "!backend/",
            "!backend/**",
            "!docker/",
            "!docker/**",
        }
        self.assertEqual(
            {line for line in lines if line.startswith("!")},
            expected_inclusions,
        )
        self.assertIn("backend/tests", lines)
        self.assertIn("**/.env*", lines)
        self.assertNotIn("!.env", lines)


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
