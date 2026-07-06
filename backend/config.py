import os
from pathlib import Path
import re
import shutil
import sys
import threading
from datetime import datetime, timezone


API_VERSION = "2026-07-prepare-v1"
ANALYSIS_VERSION = "agent-mvp-gemini-v2"
HOME = Path.home()
DATA_DIR = Path(os.environ.get("CONTEXTBUBBLE_DATA_DIR", Path(__file__).resolve().parent / ".contextbubble"))
DB_FILE = DATA_DIR / "contextbubble.sqlite3"
JOB_LOG_FILE = DATA_DIR / "jobs.log"
MEDIA_DIR = DATA_DIR / "media"
LOCAL_YTDLP_CMD = HOME / ".local/bin/yt-dlp"
DEFAULT_YTDLP_CMD = str(LOCAL_YTDLP_CMD) if LOCAL_YTDLP_CMD.exists() else "yt-dlp"
YTDLP_CMD = os.environ.get("YTDLP_CMD", DEFAULT_YTDLP_CMD)
FFMPEG_CMD = os.environ.get("FFMPEG_CMD", "ffmpeg")
FFPROBE_CMD = os.environ.get("FFPROBE_CMD", "ffprobe")
WHISPER_CMD = os.environ.get("WHISPER_CMD", str(HOME / "tools/whisper.cpp/build/bin/whisper-cli"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", str(HOME / "tools/whisper.cpp/models/ggml-base.en.bin"))
WHISPER_NO_GPU = os.environ.get("WHISPER_NO_GPU", "").lower() in ("1", "true", "yes")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
AGENT_MODE = os.environ.get("AGENT_MODE", "heuristic").lower()
TRANSLATION_MODE = os.environ.get("TRANSLATION_MODE", "ollama").lower()
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL", "qwen3:8b")
TRANSCRIPT_BLOCK_SPLITTER_MODE = os.environ.get("TRANSCRIPT_BLOCK_SPLITTER_MODE", "ollama").lower()
TRANSCRIPT_BLOCK_SPLITTER_MODEL = os.environ.get("TRANSCRIPT_BLOCK_SPLITTER_MODEL", "llama3.2:3b")
DEMO_VIDEO_IDS = {item.strip() for item in os.environ.get("DEMO_VIDEO_IDS", "").split(",") if item.strip()}
DEMO_FIXTURES = {
    "fNk_zzaMoSs": "fNk_zzaMoSs.vtt",
}
LEARNER_LEVELS = {"beginner", "intermediate", "advanced"}
AGENT_MODES = {"heuristic", "gemini", "ollama"}
TRANSLATION_MODES = {"gemini", "ollama"}
TRANSCRIPT_BLOCK_SPLITTER_MODES = {"heuristic", "gemini", "ollama"}
TRANSLATION_PROMPT_VERSION = "translation-v2"
TRANSCRIPT_BLOCK_SPLITTER_PROMPT_VERSION = "block-splitter-v1"
DEFAULT_CHUNK_SECONDS = 30
CHUNK_OVERLAP_SECONDS = 2
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024
MAX_BEARER_TOKEN_BYTES = 512


def set_data_dir(path):
    global DATA_DIR, DB_FILE, JOB_LOG_FILE, MEDIA_DIR
    DATA_DIR = Path(path)
    DB_FILE = DATA_DIR / "contextbubble.sqlite3"
    JOB_LOG_FILE = DATA_DIR / "jobs.log"
    MEDIA_DIR = DATA_DIR / "media"
    for module_name in ("db", "media", "jobs", "transcripts", "agents", "checks", "server"):
        module = sys.modules.get(module_name)
        if module:
            module.DATA_DIR = DATA_DIR
            module.DB_FILE = DB_FILE
            module.JOB_LOG_FILE = JOB_LOG_FILE
            module.MEDIA_DIR = MEDIA_DIR
def now_iso():
    return datetime.now(timezone.utc).isoformat()
def iso_from_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
def validate_config():
    if AGENT_MODE not in AGENT_MODES:
        raise ValueError(f"AGENT_MODE must be one of: {', '.join(sorted(AGENT_MODES))}")
    if TRANSLATION_MODE not in TRANSLATION_MODES:
        raise ValueError(f"TRANSLATION_MODE must be one of: {', '.join(sorted(TRANSLATION_MODES))}")
    if TRANSCRIPT_BLOCK_SPLITTER_MODE not in TRANSCRIPT_BLOCK_SPLITTER_MODES:
        raise ValueError(f"TRANSCRIPT_BLOCK_SPLITTER_MODE must be one of: {', '.join(sorted(TRANSCRIPT_BLOCK_SPLITTER_MODES))}")
def validate_runtime_for_asr():
    if not shutil.which(YTDLP_CMD) and not Path(YTDLP_CMD).exists():
        raise FileNotFoundError("YTDLP_AUDIO_FAILED")
    if not shutil.which(FFMPEG_CMD):
        raise FileNotFoundError("AUDIO_NORMALIZATION_FAILED")
    if not shutil.which(FFPROBE_CMD):
        raise FileNotFoundError("FFPROBE_NOT_FOUND")
    if not Path(WHISPER_CMD).exists() and not shutil.which(WHISPER_CMD):
        raise FileNotFoundError("WHISPER_NOT_FOUND")
    if not Path(WHISPER_MODEL).exists():
        raise FileNotFoundError("WHISPER_MODEL_NOT_FOUND")
def validate_video_id(video_id):
    if not re.fullmatch(r"[-_A-Za-z0-9]{6,20}", video_id):
        raise ValueError("invalid YouTube video id")
def demo_fixture_path(video_id):
    return Path(__file__).resolve().parent / "fixtures" / DEMO_FIXTURES.get(video_id, "demo.vtt")
