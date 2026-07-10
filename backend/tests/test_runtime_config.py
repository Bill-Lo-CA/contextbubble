import asyncio
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
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


def env_without_dotenv(extra=None):
    environment = {"CONTEXTBUBBLE_SKIP_DOTENV": "1"}
    environment.update(extra or {})
    return environment


class FakeFastAPI:
    def __init__(self, **kwargs):
        self.lifespan = kwargs.get("lifespan")

    def route(self, *args, **kwargs):
        return lambda function: function

    middleware = route
    exception_handler = route
    post = route
    get = route


def server_dependency_modules():
    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI = FakeFastAPI
    fastapi.Header = lambda default="": default
    fastapi.Request = type("Request", (), {})

    responses = types.ModuleType("fastapi.responses")
    responses.JSONResponse = type("JSONResponse", (), {})
    responses.Response = type("Response", (), {})

    exceptions = types.ModuleType("starlette.exceptions")
    exceptions.HTTPException = type("HTTPException", (Exception,), {})

    concurrency = types.ModuleType("starlette.concurrency")
    concurrency.run_in_threadpool = mock.AsyncMock()

    uvicorn = types.ModuleType("uvicorn")
    uvicorn.run = mock.Mock()
    return {
        "fastapi": fastapi,
        "fastapi.responses": responses,
        "starlette.concurrency": concurrency,
        "starlette.exceptions": exceptions,
        "uvicorn": uvicorn,
    }


def run_lifespan(server):
    async def exercise_lifespan():
        async with server.lifespan(server.app):
            pass

    asyncio.run(exercise_lifespan())


class RuntimeConfigTests(unittest.TestCase):
    def test_backend_bind_defaults_with_clear_environment(self):
        with mock.patch.dict(os.environ, env_without_dotenv(), clear=True):
            config = load_config()

        self.assertEqual(config.BACKEND_HOST, "127.0.0.1")
        self.assertEqual(config.BACKEND_PORT, 8000)
        self.assertFalse(config.VALIDATE_ASR_ON_START)

    def test_dotenv_loads_home_paths_and_keeps_env_file_private(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir, "home")
            home.mkdir()
            env_file = Path(tmpdir, ".env")
            env_file.write_text(
                "\n".join(
                    [
                        "CONTEXTBUBBLE_DATA_DIR=${HOME}/.local/share/contextbubble",
                        'GEMINI_API_KEY="from-dotenv"',
                    ]
                ),
                encoding="utf-8",
            )
            os.chmod(env_file, 0o644)

            with mock.patch.dict(
                os.environ,
                {"CONTEXTBUBBLE_ENV_FILE": str(env_file), "HOME": str(home)},
                clear=True,
            ):
                config = load_config()

            self.assertEqual(config.DATA_DIR, home / ".local/share/contextbubble")
            self.assertEqual(config.GEMINI_API_KEY, "from-dotenv")
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

    def test_shell_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir, ".env")
            env_file.write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {"CONTEXTBUBBLE_ENV_FILE": str(env_file), "GEMINI_API_KEY": "from-shell"},
                clear=True,
            ):
                config = load_config()

        self.assertEqual(config.GEMINI_API_KEY, "from-shell")

    def test_startup_asr_validation_accepts_documented_true_values(self):
        for value in ("1", "true", "TRUE", "yes", "YES"):
            with self.subTest(value=value):
                with mock.patch.dict(
                    os.environ,
                    env_without_dotenv({"CONTEXTBUBBLE_VALIDATE_ASR_ON_START": value}),
                    clear=True,
                ):
                    config = load_config()

                self.assertTrue(config.VALIDATE_ASR_ON_START)

    def test_backend_bind_environment_overrides(self):
        environment = {
            "CONTEXTBUBBLE_HOST": "0.0.0.0",
            "CONTEXTBUBBLE_PORT": "9000",
            "WHISPER_LANGUAGE": "zh",
        }
        with mock.patch.dict(os.environ, env_without_dotenv(environment), clear=True):
            config = load_config()

        self.assertEqual(config.BACKEND_HOST, "0.0.0.0")
        self.assertEqual(config.BACKEND_PORT, 9000)
        self.assertEqual(config.WHISPER_LANGUAGE, "zh")

    def test_backend_port_rejects_out_of_range_value(self):
        with mock.patch.dict(os.environ, env_without_dotenv({"CONTEXTBUBBLE_PORT": "70000"}), clear=True):
            with self.assertRaisesRegex(ValueError, "CONTEXTBUBBLE_PORT"):
                load_config()

    def test_backend_port_rejects_non_integer_value(self):
        with mock.patch.dict(os.environ, env_without_dotenv({"CONTEXTBUBBLE_PORT": "invalid"}), clear=True):
            with self.assertRaisesRegex(ValueError, "CONTEXTBUBBLE_PORT must be an integer"):
                load_config()

    def test_transcription_forwards_cpu_and_language_settings(self):
        from asr_provider import whisper_cpp
        import asr_provider
        import config

        chunk = {"chunk_index": 0, "start_seconds": 0, "end_seconds": 10}
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "chunk-0000.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(asr_provider, "run_command") as run_command,
            ):
                settings = config.get_settings()
                with config.settings_override(__import__("dataclasses").replace(settings, whisper_no_gpu=True, whisper_language="zh")):
                    whisper_cpp.transcribe("audio.wav", chunk, tmpdir, "job-1")

        whisper_command = next(
            call.args[0]
            for call in run_command.call_args_list
            if call.args[0][0] == config.get_settings().whisper_cmd
        )
        self.assertIn("-ng", whisper_command)
        language_index = whisper_command.index("-l")
        self.assertEqual(whisper_command[language_index + 1], "zh")

    def test_server_main_binds_to_configured_address(self):
        sys.modules.pop("server", None)
        try:
            with mock.patch.dict(sys.modules, server_dependency_modules()):
                import server

                with (
                    mock.patch.object(server, "BACKEND_HOST", "0.0.0.0"),
                    mock.patch.object(server, "BACKEND_PORT", 9000),
                    mock.patch.object(server, "validate_config"),
                    mock.patch.object(server, "init_db"),
                    mock.patch.object(server.auth, "initialize_auth"),
                    mock.patch.object(server, "resume_preparations"),
                    mock.patch("builtins.print"),
                ):
                    server.main()

                server.uvicorn.run.assert_called_once_with(
                    server.app,
                    host="0.0.0.0",
                    port=9000,
                )
        finally:
            sys.modules.pop("server", None)

    def test_lifespan_initializes_runtime_before_serving(self):
        sys.modules.pop("server", None)
        events = []
        modules = server_dependency_modules()
        try:
            with mock.patch.dict(sys.modules, modules):
                import server

                with (
                    mock.patch.object(server, "VALIDATE_ASR_ON_START", True),
                    mock.patch.object(
                        server,
                        "validate_config",
                        side_effect=lambda: events.append("config"),
                    ),
                    mock.patch.object(
                        server,
                        "init_db",
                        side_effect=lambda: events.append("database"),
                    ),
                    mock.patch.object(
                        server.auth,
                        "initialize_auth",
                        side_effect=lambda: events.append("auth"),
                    ),
                    mock.patch.object(server.auth, "API_TOKEN", "secret-api-token"),
                    mock.patch.object(
                        server,
                        "validate_runtime_for_asr",
                        side_effect=lambda: events.append("validate"),
                    ),
                    mock.patch.object(
                        server,
                        "resume_preparations",
                        side_effect=lambda: events.append("resume"),
                    ),
                    mock.patch("builtins.print") as print_output,
                ):
                    run_lifespan(server)

            self.assertEqual(
                events,
                ["config", "database", "auth", "validate", "resume"],
            )
            output = " ".join(
                str(argument)
                for call in print_output.call_args_list
                for argument in call.args
            )
            self.assertNotIn("secret-api-token", output)
        finally:
            sys.modules.pop("server", None)

    def test_lifespan_validation_failure_prevents_resume(self):
        sys.modules.pop("server", None)
        modules = server_dependency_modules()
        try:
            with mock.patch.dict(sys.modules, modules):
                import server

                with (
                    mock.patch.object(server, "VALIDATE_ASR_ON_START", True),
                    mock.patch.object(server, "validate_config"),
                    mock.patch.object(server, "init_db"),
                    mock.patch.object(server.auth, "initialize_auth"),
                    mock.patch.object(
                        server,
                        "validate_runtime_for_asr",
                        side_effect=FileNotFoundError("WHISPER_NOT_FOUND"),
                    ),
                    mock.patch.object(server, "resume_preparations") as resume,
                    mock.patch("builtins.print"),
                ):
                    with self.assertRaisesRegex(FileNotFoundError, "WHISPER_NOT_FOUND"):
                        run_lifespan(server)

            resume.assert_not_called()
        finally:
            sys.modules.pop("server", None)


if __name__ == "__main__":
    unittest.main()
