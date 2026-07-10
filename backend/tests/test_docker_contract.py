import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
COMPOSE = ROOT / "compose.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_DOCKER_EXAMPLE = ROOT / ".env.docker.example"
GITIGNORE = ROOT / ".gitignore"
CHECK_COMPOSE = ROOT / "scripts" / "check-compose.sh"
README = ROOT / "README.md"
DOCS = ROOT / "docs"


class DockerReadmeContractTest(unittest.TestCase):
    def setUp(self):
        self.readme = README.read_text()
        self.docker = (DOCS / "setup-docker.md").read_text()
        self.native = (DOCS / "setup-native.md").read_text()

    def section(self, heading):
        match = re.search(
            rf"(?ms)^## {re.escape(heading)}\n.*?(?=^## |\Z)",
            self.readme,
        )
        self.assertIsNotNone(match, f"README must contain the {heading!r} section")
        return match.group(0)

    def test_docker_workflow_precedes_native_setup(self):
        docker_heading = self.readme.index("[Docker Setup]")
        native_heading = self.readme.index("[Native Developer Setup]")

        self.assertLess(docker_heading, native_heading)
        self.assertIn("uv sync --locked", self.native)

    def test_docker_quick_start_keeps_logs_in_another_terminal(self):
        self.assertIn("docker compose up --build", self.docker)
        self.assertRegex(
            self.docker,
            r"(?is)another terminal.{0,200}docker compose logs backend",
        )
        self.assertRegex(self.docker, r"(?is)pairing code.{0,200}docker compose logs backend")
        self.assertIn("admin token is never written to logs", self.docker.lower())

    def test_docker_api_is_loopback_only_and_not_lan_exposed(self):
        self.assertRegex(
            self.docker,
            r"(?is)http://127\.0\.0\.1:8000.{0,150}(?:only|does not expose.{0,30}LAN)",
        )
        self.assertNotIn("0.0.0.0:8000", self.docker)
        self.assertNotRegex(
            self.docker,
            r"(?i)(?:available|accessible|exposed) (?:on|to) (?:the )?LAN",
        )

    def test_docker_dotenv_is_optional_interpolation_not_image_content(self):
        self.assertRegex(
            self.docker,
            r"(?is)No `.env` file is needed.{0,200}interpolation.{0,200}"
            r"\.dockerignore.{0,100}excludes",
        )
        self.assertRegex(self.docker, r"(?is)defaults.{0,300}cp \.env\.docker\.example \.env")
        self.assertIn("WHISPER_CPP_REF", self.docker)

    def test_docker_workflow_documents_persistent_state_and_restarts(self):
        for detail in (
            "contextbubble-data",
            "contextbubble-models",
            "contextbubble.sqlite3-wal",
            "contextbubble.sqlite3-shm",
            "jobs.log",
            "/data/media",
            "contextbubble.token",
            "fresh pairing code",
        ):
            self.assertIn(detail, self.docker)
        self.assertRegex(
            self.docker,
            r"(?is)generated admin token.{0,100}reused.{0,100}"
            r"unexpired browser\s+session.{0,100}remains valid.{0,150}fresh pairing code",
        )
        for command in (
            "docker compose stop",
            "docker compose start",
            "docker compose restart",
            "docker compose down",
            "docker compose up",
        ):
            self.assertIn(command, self.docker)
        self.assertRegex(
            self.docker,
            r"(?is)docker compose stop.{0,300}all preserve named volumes",
        )

    def test_docker_workflow_warns_that_only_down_v_destroys_data(self):
        self.assertRegex(
            self.docker,
            r"(?is)docker compose down`?.{0,300}preserve.{0,500}"
            r"docker compose down -v`?.{0,100}(?:destructive|deletes)",
        )
        destructive = self.docker[self.docker.index("docker compose down -v") :]
        for lost_state in (
            "analyses",
            "transcripts",
            "logs",
            "ASR resume files",
            "generated token",
            "model",
        ):
            self.assertIn(lost_state, destructive)
        self.assertRegex(destructive, r"session\s+database")
        normal_down = self.docker[: self.docker.index("docker compose down -v")]
        self.assertNotRegex(
            normal_down,
            r"(?is)docker compose down`?.{0,100}(?:deletes|removes) (?:the )?"
            r"(?:named )?volumes",
        )

    def test_docker_workflow_documents_model_configuration_as_a_tuple(self):
        self.assertIn("English-only", self.docker)
        for setting in (
            "WHISPER_MODEL",
            "WHISPER_MODEL_URL",
            "WHISPER_MODEL_SHA256",
            "WHISPER_LANGUAGE",
        ):
            self.assertIn(setting, self.docker)
        self.assertRegex(
            self.docker,
            r"(?is)multilingual transcription.{0,100}all four.{0,300}"
            r"WHISPER_LANGUAGE.{0,300}WHISPER_LANGUAGE=zh.{0,200}"
            r"WHISPER_LANGUAGE=auto",
        )
        self.assertIn("WHISPER_NO_GPU", self.docker)
        self.assertIn("-ng", self.docker)

    def test_docker_workflow_documents_asr_resume_and_cleanup_lifecycle(self):
        self.assertRegex(
            self.docker,
            r"(?is)interrupted (?:queued|processing).{0,100}jobs resume",
        )
        self.assertRegex(
            self.docker,
            r"(?is)failed and interrupted jobs.{0,150}/data/media/<job-id>.{0,150}"
            r"diagnosis.{0,50}resume inputs",
        )
        self.assertRegex(
            self.docker,
            r"(?is)failed jobs\s+do\s+not automatically retry.{0,150}"
            r"only successful ASR (?:work|paths|transcriptions).{0,100}"
            r"(?:removes?|clean up) (?:(?:its|their) )?media",
        )

    def test_docker_workflow_documents_validation(self):
        self.assertIn("scripts/check-compose.sh", self.docker)
        self.assertIn("requires Docker Compose", self.docker)
        self.assertRegex(
            self.docker,
            r"(?is)POSIX shell.{0,100}(?:Git Bash|WSL).{0,300}"
            r"docker compose config --quiet.{0,300}"
            r"docker compose --env-file \.env\.docker\.example config --quiet",
        )
        self.assertRegex(
            self.docker,
            r"(?is)(?:does not|not) run.{0,150}external YouTube/browser smoke tests",
        )
        self.assertRegex(
            self.docker,
            r"(?is)fail(?:s)? fast.{0,200}(?:yt-dlp|ASR).{0,300}before.{0,100}backend",
        )

    def test_native_workflow_documents_lazy_asr_validation_default(self):
        self.assertRegex(
            self.native,
            r"(?is)(?:lazy|on demand).{0,200}ASR.{0,200}(?:caption|caption-only)",
        )

    def test_check_script_validation_description_is_complete(self):
        validation = self.section("Validate")

        self.assertRegex(validation, r"(?is)unit.{0,30}contract tests")
        self.assertIn("backend self-check", validation)
        self.assertIn("JavaScript syntax checks", validation)


class DockerComposeContractTest(unittest.TestCase):
    EXPECTED_ENVIRONMENT = {
        "CONTEXTBUBBLE_TOKEN": "${CONTEXTBUBBLE_TOKEN:-}",
        "CONTEXTBUBBLE_HOST": "0.0.0.0",
        "CONTEXTBUBBLE_PORT": "8000",
        "CONTEXTBUBBLE_DATA_DIR": "/data",
        "CONTEXTBUBBLE_VALIDATE_ASR_ON_START": "1",
        "YTDLP_CMD": "yt-dlp",
        "FFMPEG_CMD": "ffmpeg",
        "FFPROBE_CMD": "ffprobe",
        "WHISPER_CMD": "/opt/whisper/bin/whisper-cli",
        "WHISPER_MODEL": "${DOCKER_WHISPER_MODEL:-/models/ggml-base.en.bin}",
        "WHISPER_MODEL_URL": (
            "${DOCKER_WHISPER_MODEL_URL:-https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.en.bin}"
        ),
        "WHISPER_MODEL_SHA256": (
            "${DOCKER_WHISPER_MODEL_SHA256:-a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002}"
        ),
        "WHISPER_LANGUAGE": "${DOCKER_WHISPER_LANGUAGE:-en}",
        "WHISPER_NO_GPU": "1",
        "AGENT_MODE": "${AGENT_MODE:-heuristic}",
        "GEMINI_API_KEY": "${GEMINI_API_KEY:-}",
        "GEMINI_MODEL": "${GEMINI_MODEL:-gemini-2.5-flash}",
        "OLLAMA_BASE_URL": "${DOCKER_OLLAMA_BASE_URL:-http://host.docker.internal:11434}",
        "OLLAMA_MODEL": "${OLLAMA_MODEL:-qwen3:8b}",
        "TRANSLATION_MODE": "${TRANSLATION_MODE:-ollama}",
        "TRANSLATION_MODEL": "${TRANSLATION_MODEL:-qwen3:8b}",
        "TRANSCRIPT_BLOCK_SPLITTER_MODE": "${TRANSCRIPT_BLOCK_SPLITTER_MODE:-ollama}",
        "TRANSCRIPT_BLOCK_SPLITTER_MODEL": "${TRANSCRIPT_BLOCK_SPLITTER_MODEL:-llama3.2:3b}",
        "DEMO_VIDEO_IDS": "${DEMO_VIDEO_IDS:-}",
    }

    def test_compose_declares_single_loopback_backend_service(self):
        compose = self.read_compose()

        self.assertTrue(compose.startswith("services:\n  backend:\n"))
        self.assertIn("    image: contextbubble-backend:cpu\n", compose)
        self.assertIn("    restart: unless-stopped\n", compose)
        self.assertIn('      - "127.0.0.1:8000:8000"\n', compose)
        without_loopback_binding = compose.replace(
            '"127.0.0.1:8000:8000"', ""
        )
        self.assertNotIn("8000:8000", without_loopback_binding)

    def test_compose_build_arguments_are_pinned_with_overridable_defaults(self):
        compose = self.read_compose()

        self.assertIn("    build:\n      context: .\n      args:\n", compose)
        self.assertIn('        WHISPER_CPP_REF: "${WHISPER_CPP_REF:-v1.8.6}"\n', compose)
        self.assertNotIn("YT_DLP_VERSION", compose)

    def test_compose_defines_runtime_and_provider_defaults(self):
        compose = self.read_compose()

        self.assertIn("    environment:\n", compose)
        for name, value in self.EXPECTED_ENVIRONMENT.items():
            self.assertIn(f'      {name}: "{value}"\n', compose)

    def test_compose_connects_host_and_persistent_data(self):
        compose = self.read_compose()

        self.assertIn('      - "host.docker.internal:host-gateway"\n', compose)
        self.assertIn('      - "contextbubble-data:/data"\n', compose)
        self.assertIn('      - "contextbubble-models:/models"\n', compose)
        self.assertIn(
            "\nvolumes:\n"
            "  contextbubble-data:\n"
            "    name: contextbubble-data\n"
            "  contextbubble-models:\n"
            "    name: contextbubble-models\n",
            compose,
        )

    def test_docker_env_example_is_secret_free_and_documents_model_choices(self):
        self.assertTrue(ENV_DOCKER_EXAMPLE.is_file(), ".env.docker.example must exist")
        example = ENV_DOCKER_EXAMPLE.read_text()

        self.assertRegex(example, r"(?m)^CONTEXTBUBBLE_TOKEN=$")
        self.assertIn("/data/contextbubble.token", example)
        for default in (
            "WHISPER_CPP_REF=v1.8.6",
            "DOCKER_OLLAMA_BASE_URL=http://host.docker.internal:11434",
            "AGENT_MODE=heuristic",
            "GEMINI_API_KEY=",
            "GEMINI_MODEL=gemini-2.5-flash",
            "OLLAMA_MODEL=qwen3:8b",
            "TRANSLATION_MODE=ollama",
            "TRANSLATION_MODEL=qwen3:8b",
            "TRANSCRIPT_BLOCK_SPLITTER_MODE=ollama",
            "TRANSCRIPT_BLOCK_SPLITTER_MODEL=llama3.2:3b",
            "DEMO_VIDEO_IDS=",
            "DOCKER_WHISPER_MODEL=/models/ggml-base.en.bin",
            "DOCKER_WHISPER_LANGUAGE=en",
        ):
            self.assertIn(default, example)
        self.assertNotIn("WHISPER_NO_GPU", example)
        self.assertRegex(example, r"(?i)english[- ]only")
        for multilingual_override in (
            "# DOCKER_WHISPER_MODEL=/models/ggml-base.bin",
            "# DOCKER_WHISPER_MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/"
            "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.bin",
            "# DOCKER_WHISPER_MODEL_SHA256=60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
            "# DOCKER_WHISPER_LANGUAGE=zh",
        ):
            self.assertIn(multilingual_override, example)
        self.assertNotRegex(example, r"(?m)^CONTEXTBUBBLE_TOKEN=\S+")
        self.assertNotRegex(example, r"(?m)^GEMINI_API_KEY=\S+")
        self.assertNotIn("AIza", example)

    def test_gitignore_ignores_local_env_but_tracks_example(self):
        rules = GITIGNORE.read_text().splitlines()

        self.assertIn(".env", rules)
        self.assertIn(".env.*", rules)
        self.assertIn("!.env.example", rules)
        self.assertIn("!.env.docker.example", rules)

    def test_compose_interpolates_with_no_env_file(self):
        rendered = self.render_compose()

        self.assertIn('WHISPER_CPP_REF: "v1.8.6"', rendered)
        self.assertIn('CONTEXTBUBBLE_TOKEN: ""', rendered)
        self.assertIn('AGENT_MODE: "heuristic"', rendered)

    def test_compose_fixes_cpu_mode_without_dotenv_override(self):
        compose = self.read_compose()

        self.assertIn("CPU-only invariant", compose)
        self.assertIn('      WHISPER_NO_GPU: "1"\n', compose)
        self.assertNotRegex(compose, r"WHISPER_NO_GPU.*\$\{")
        self.assertNotIn("WHISPER_NO_GPU", ENV_DOCKER_EXAMPLE.read_text())

    def test_compose_interpolates_with_copied_example_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            shutil.copyfile(ENV_DOCKER_EXAMPLE, env_file)
            rendered = self.render_compose(self.read_env(env_file))

        self.assertIn('CONTEXTBUBBLE_TOKEN: ""', rendered)
        self.assertIn('WHISPER_MODEL: "/models/ggml-base.en.bin"', rendered)
        self.assertIn('OLLAMA_BASE_URL: "http://host.docker.internal:11434"', rendered)
        self.assertIn('AGENT_MODE: "heuristic"', rendered)
        self.assertIn('OLLAMA_MODEL: "qwen3:8b"', rendered)
        self.assertIn('TRANSLATION_MODE: "ollama"', rendered)
        self.assertIn('TRANSLATION_MODEL: "qwen3:8b"', rendered)
        self.assertIn('TRANSCRIPT_BLOCK_SPLITTER_MODE: "ollama"', rendered)
        self.assertIn('TRANSCRIPT_BLOCK_SPLITTER_MODEL: "llama3.2:3b"', rendered)

    def test_multilingual_override_renders_as_one_coherent_tuple(self):
        expected = {
            "WHISPER_MODEL": "/models/ggml-base.bin",
            "WHISPER_MODEL_URL": (
                "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                "80da2d8bfee42b0e836fc3a9890373e5defc00a6/ggml-base.bin"
            ),
            "WHISPER_MODEL_SHA256": (
                "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
            ),
            "WHISPER_LANGUAGE": "zh",
        }
        docker_override_names = {
            "WHISPER_MODEL": "DOCKER_WHISPER_MODEL",
            "WHISPER_MODEL_URL": "DOCKER_WHISPER_MODEL_URL",
            "WHISPER_MODEL_SHA256": "DOCKER_WHISPER_MODEL_SHA256",
            "WHISPER_LANGUAGE": "DOCKER_WHISPER_LANGUAGE",
        }
        overrides = {}
        for line in ENV_DOCKER_EXAMPLE.read_text().splitlines():
            uncommented = line.removeprefix("# ")
            if "=" in uncommented:
                name, value = uncommented.split("=", 1)
                for runtime_name, docker_name in docker_override_names.items():
                    if name == docker_name:
                        overrides[docker_name] = value

        self.assertEqual(
            {runtime_name: overrides[docker_name] for runtime_name, docker_name in docker_override_names.items()},
            expected,
        )
        rendered = self.render_compose(overrides)
        for name, value in expected.items():
            self.assertIn(f'{name}: "{value}"', rendered)

    def test_compose_validator_checks_both_env_modes_without_mutating_dotenv(self):
        self.assertTrue(CHECK_COMPOSE.is_file(), "scripts/check-compose.sh must exist")
        script = CHECK_COMPOSE.read_text()

        self.assertTrue(script.startswith("#!/usr/bin/env sh\nset -eu\n"))
        self.assertIn("docker compose --env-file /dev/null config --quiet", script)
        self.assertIn("docker compose --env-file .env.docker.example config --quiet", script)
        self.assertIn("docker compose is required", script)
        for mutation in ("cp .env", "rm .env", "> .env", "mv .env"):
            self.assertNotIn(mutation, script)

    def read_compose(self):
        self.assertTrue(COMPOSE.is_file(), "compose.yaml must exist")
        return COMPOSE.read_text()

    def render_compose(self, environment=None):
        environment = environment or {}
        def interpolate(match):
            name, default = match.groups()
            return environment.get(name) or default

        rendered = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}",
            interpolate,
            COMPOSE.read_text(),
        )
        self.assertNotIn("${", rendered)
        return rendered

    @staticmethod
    def read_env(env_file):
        environment = {}
        for line in env_file.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                environment[name] = value
        return environment


class DockerImageContractTest(unittest.TestCase):
    def test_dockerfile_pins_whisper_and_owns_yt_dlp_version_in_lock(self):
        self.assertTrue(DOCKERFILE.is_file(), "Dockerfile must exist")
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("ARG WHISPER_CPP_REF=v1.8.6", dockerfile)
        self.assertNotIn("YT_DLP_VERSION", dockerfile)

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

    def test_dockerfile_installs_locked_uv_project(self):
        dockerfile = DOCKERFILE.read_text()

        self.assertIn("COPY pyproject.toml uv.lock ./", dockerfile)
        self.assertIn("uv sync --locked --no-dev", dockerfile)
        self.assertIn("yt-dlp[default,deno,pin,pin-deno]", PYPROJECT.read_text())
        self.assertTrue(UV_LOCK.is_file())

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

        lock_copy_instruction = "COPY pyproject.toml uv.lock ./"
        lock_install_instruction = "RUN uv sync --locked --no-dev"
        self.assertIn(lock_copy_instruction, dockerfile)
        self.assertIn(lock_install_instruction, dockerfile)
        lock_copy = dockerfile.index(lock_copy_instruction)
        requirements_install = dockerfile.index(lock_install_instruction)
        backend_copy = dockerfile.index("COPY backend ./backend")
        self.assertLess(lock_copy, requirements_install)
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
            "!pyproject.toml",
            "!uv.lock",
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
