import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT_DIR / ".env"
TRUE_VALUES = ("1", "true", "yes")


def expand_config_path(value):
    return Path(os.path.expandvars(str(value))).expanduser()


def ensure_private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def ensure_private_file(path):
    path = Path(path)
    if path.exists():
        os.chmod(path, 0o600)
    return path


def env_values(environ=None, env_file=None):
    values = dict(environ or os.environ)
    if values.get("CONTEXTBUBBLE_SKIP_DOTENV", "").lower() in TRUE_VALUES:
        return values
    path = expand_config_path(env_file or values.get("CONTEXTBUBBLE_ENV_FILE", DEFAULT_ENV_FILE))
    if not path.exists():
        return values
    ensure_private_file(path)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not line or line.startswith("#") or not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values.setdefault(key.strip(), os.path.expandvars(value))
    return values


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ytdlp_cmd: str
    ffmpeg_cmd: str
    ffprobe_cmd: str
    whisper_cmd: str
    whisper_model: str
    whisper_no_gpu: bool
    validate_asr_on_start: bool
    whisper_language: str
    backend_host: str
    backend_port: int
    gemini_api_key: str
    gemini_model: str
    ollama_base_url: str
    ollama_model: str
    agent_mode: str
    translation_mode: str
    translation_model: str
    transcript_block_splitter_mode: str
    transcript_block_splitter_model: str
    demo_video_ids: frozenset[str]

    @property
    def db_file(self): return self.data_dir / "contextbubble.sqlite3"
    @property
    def job_log_file(self): return self.data_dir / "jobs.log"
    @property
    def media_dir(self): return self.data_dir / "media"


def load_settings(environ=None, env_file=None):
    values = env_values(environ, env_file)
    home = Path(values.get("HOME", str(Path.home())))
    local_ytdlp = home / ".local/bin/yt-dlp"
    try:
        port = int(values.get("CONTEXTBUBBLE_PORT", "8000"))
    except ValueError as error:
        raise ValueError("CONTEXTBUBBLE_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("CONTEXTBUBBLE_PORT must be between 1 and 65535")
    return Settings(
        data_dir=expand_config_path(values.get("CONTEXTBUBBLE_DATA_DIR", Path(__file__).resolve().parent / ".contextbubble")),
        ytdlp_cmd=values.get("YTDLP_CMD", str(local_ytdlp) if local_ytdlp.exists() else "yt-dlp"),
        ffmpeg_cmd=values.get("FFMPEG_CMD", "ffmpeg"), ffprobe_cmd=values.get("FFPROBE_CMD", "ffprobe"),
        whisper_cmd=values.get("WHISPER_CMD", str(home / "tools/whisper.cpp/build/bin/whisper-cli")),
        whisper_model=values.get("WHISPER_MODEL", str(home / "tools/whisper.cpp/models/ggml-base.en.bin")),
        whisper_no_gpu=values.get("WHISPER_NO_GPU", "").lower() in TRUE_VALUES,
        validate_asr_on_start=values.get("CONTEXTBUBBLE_VALIDATE_ASR_ON_START", "").lower() in TRUE_VALUES,
        whisper_language=values.get("WHISPER_LANGUAGE", "en").strip() or "en",
        backend_host=values.get("CONTEXTBUBBLE_HOST", "127.0.0.1").strip() or "127.0.0.1", backend_port=port,
        gemini_api_key=values.get("GEMINI_API_KEY", ""), gemini_model=values.get("GEMINI_MODEL", "gemini-2.5-flash"),
        ollama_base_url=values.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"), ollama_model=values.get("OLLAMA_MODEL", "qwen3:8b"),
        agent_mode=values.get("AGENT_MODE", "heuristic").lower(), translation_mode=values.get("TRANSLATION_MODE", "ollama").lower(),
        translation_model=values.get("TRANSLATION_MODEL", "qwen3:8b"), transcript_block_splitter_mode=values.get("TRANSCRIPT_BLOCK_SPLITTER_MODE", "ollama").lower(),
        transcript_block_splitter_model=values.get("TRANSCRIPT_BLOCK_SPLITTER_MODEL", "llama3.2:3b"),
        demo_video_ids=frozenset(item.strip() for item in values.get("DEMO_VIDEO_IDS", "").split(",") if item.strip()),
    )


_settings = ContextVar("contextbubble_settings", default=load_settings())


def get_settings(): return _settings.get()


@contextmanager
def settings_override(settings):
    token = _settings.set(settings)
    try:
        yield settings
    finally:
        _settings.reset(token)


def now_iso(): return datetime.now(timezone.utc).isoformat()
def iso_from_timestamp(timestamp): return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
def validate_config(settings=None):
    settings = settings or get_settings()
    if settings.agent_mode not in {"heuristic", "gemini", "ollama"}: raise ValueError("AGENT_MODE must be one of: gemini, heuristic, ollama")
    if settings.translation_mode not in {"gemini", "ollama"}: raise ValueError("TRANSLATION_MODE must be one of: gemini, ollama")
    if settings.transcript_block_splitter_mode not in {"heuristic", "gemini", "ollama"}: raise ValueError("TRANSCRIPT_BLOCK_SPLITTER_MODE must be one of: gemini, heuristic, ollama")
def validate_runtime_for_asr(settings=None):
    settings = settings or get_settings()
    if not shutil.which(settings.ytdlp_cmd) and not Path(settings.ytdlp_cmd).exists(): raise FileNotFoundError("YTDLP_AUDIO_FAILED")
    if not shutil.which(settings.ffmpeg_cmd): raise FileNotFoundError("AUDIO_NORMALIZATION_FAILED")
    if not shutil.which(settings.ffprobe_cmd): raise FileNotFoundError("FFPROBE_NOT_FOUND")
    if not Path(settings.whisper_cmd).exists() and not shutil.which(settings.whisper_cmd): raise FileNotFoundError("WHISPER_NOT_FOUND")
    if not Path(settings.whisper_model).exists(): raise FileNotFoundError("WHISPER_MODEL_NOT_FOUND")
def validate_video_id(video_id):
    if not re.fullmatch(r"[-_A-Za-z0-9]{6,20}", video_id): raise ValueError("invalid YouTube video id")
def demo_fixture_path(video_id): return Path(__file__).resolve().parent / "fixtures" / ("fNk_zzaMoSs.vtt" if video_id == "fNk_zzaMoSs" else "demo.vtt")


def __getattr__(name):
    settings = get_settings()
    aliases = {"DATA_DIR": settings.data_dir, "DB_FILE": settings.db_file, "JOB_LOG_FILE": settings.job_log_file, "MEDIA_DIR": settings.media_dir,
        "YTDLP_CMD": settings.ytdlp_cmd, "FFMPEG_CMD": settings.ffmpeg_cmd, "FFPROBE_CMD": settings.ffprobe_cmd, "WHISPER_CMD": settings.whisper_cmd, "WHISPER_MODEL": settings.whisper_model, "WHISPER_NO_GPU": settings.whisper_no_gpu, "VALIDATE_ASR_ON_START": settings.validate_asr_on_start, "WHISPER_LANGUAGE": settings.whisper_language, "BACKEND_HOST": settings.backend_host, "BACKEND_PORT": settings.backend_port, "GEMINI_API_KEY": settings.gemini_api_key, "GEMINI_MODEL": settings.gemini_model, "OLLAMA_BASE_URL": settings.ollama_base_url, "OLLAMA_MODEL": settings.ollama_model, "AGENT_MODE": settings.agent_mode, "TRANSLATION_MODE": settings.translation_mode, "TRANSLATION_MODEL": settings.translation_model, "TRANSCRIPT_BLOCK_SPLITTER_MODE": settings.transcript_block_splitter_mode, "TRANSCRIPT_BLOCK_SPLITTER_MODEL": settings.transcript_block_splitter_model, "DEMO_VIDEO_IDS": settings.demo_video_ids,
        "API_VERSION": "2026-07-prepare-v1", "ANALYSIS_VERSION": "agent-mvp-gemini-v2", "LEARNER_LEVELS": {"beginner", "intermediate", "advanced"}, "AGENT_MODES": {"heuristic", "gemini", "ollama"}, "TRANSLATION_PROMPT_VERSION": "translation-v2", "TRANSCRIPT_BLOCK_SPLITTER_PROMPT_VERSION": "block-splitter-v1", "DEFAULT_CHUNK_SECONDS": 30, "CHUNK_OVERLAP_SECONDS": 2, "MAX_SUBTITLE_BYTES": 5 * 1024 * 1024, "MAX_JSON_BYTES": 32 * 1024, "MAX_BEARER_TOKEN_BYTES": 512}
    if name in aliases: return aliases[name]
    raise AttributeError(name)


__all__ = [
    "Settings", "expand_config_path", "ensure_private_dir", "ensure_private_file", "load_settings", "get_settings", "settings_override", "now_iso", "iso_from_timestamp", "validate_config", "validate_runtime_for_asr", "validate_video_id", "demo_fixture_path",
    "DATA_DIR", "DB_FILE", "JOB_LOG_FILE", "MEDIA_DIR", "YTDLP_CMD", "FFMPEG_CMD", "FFPROBE_CMD", "WHISPER_CMD", "WHISPER_MODEL", "WHISPER_NO_GPU", "VALIDATE_ASR_ON_START", "WHISPER_LANGUAGE", "BACKEND_HOST", "BACKEND_PORT", "GEMINI_API_KEY", "GEMINI_MODEL", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "AGENT_MODE", "TRANSLATION_MODE", "TRANSLATION_MODEL", "TRANSCRIPT_BLOCK_SPLITTER_MODE", "TRANSCRIPT_BLOCK_SPLITTER_MODEL", "DEMO_VIDEO_IDS", "API_VERSION", "ANALYSIS_VERSION", "LEARNER_LEVELS", "AGENT_MODES", "TRANSLATION_PROMPT_VERSION", "TRANSCRIPT_BLOCK_SPLITTER_PROMPT_VERSION", "DEFAULT_CHUNK_SECONDS", "CHUNK_OVERLAP_SECONDS", "MAX_SUBTITLE_BYTES", "MAX_JSON_BYTES", "MAX_BEARER_TOKEN_BYTES",
]
