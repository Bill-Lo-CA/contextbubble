import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from auth import redact_secret_text
from config import *
from db import connect_db
from transcripts import add_segment_ids, parse_subtitles


def command_error(prefix, error):
    return redact_secret_text(f"{prefix}. Check {JOB_LOG_FILE} for details.")
class ExternalCommandError(RuntimeError):
    def __init__(self, stage, command, error, chunk_index=None):
        self.stage = stage
        self.command = command
        self.original = error
        self.chunk_index = chunk_index
        self.timeout = isinstance(error, subprocess.TimeoutExpired)
        self.returncode = getattr(error, "returncode", None)
        self.stderr = getattr(error, "stderr", "") or getattr(error, "output", "") or str(error)
        super().__init__(self.error_code)

    @property
    def tool(self):
        name = Path(str(self.command[0])).name.lower() if self.command else ""
        if "yt-dlp" in name:
            return "yt-dlp"
        if "ffmpeg" in name:
            return "ffmpeg"
        if "ffprobe" in name:
            return "ffprobe"
        if "whisper" in name:
            return "whisper"
        return name

    @property
    def error_code(self):
        if self.timeout:
            return {
                "yt-dlp": "YTDLP_TIMEOUT",
                "ffmpeg": "FFMPEG_TIMEOUT",
                "ffprobe": "FFPROBE_TIMEOUT",
                "whisper": "WHISPER_TIMEOUT",
            }.get(self.tool, "EXTERNAL_TOOL_TIMEOUT")
        if self.stage == "fetching_captions":
            return "YOUTUBE_CAPTIONS_FAILED"
        if self.stage == "fetching_metadata":
            return "VIDEO_METADATA_FAILED"
        if self.stage == "downloading_audio":
            return "YTDLP_AUDIO_FAILED"
        if self.stage == "normalizing_audio":
            return "AUDIO_NORMALIZATION_FAILED"
        if self.stage == "transcribing" and self.tool == "ffmpeg":
            return "AUDIO_CHUNK_FAILED"
        if self.stage == "transcribing":
            return "WHISPER_FAILED"
        return "EXTERNAL_TOOL_FAILED"
def log_job(job_id, stage, command, error=None, chunk_index=None, retry_count=0):
    ensure_private_dir(DATA_DIR)
    tail = ""
    code = None
    if error is not None:
        code = getattr(error, "returncode", None)
        stderr = getattr(error, "stderr", "") or getattr(error, "output", "") or str(error)
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        tail = redact_secret_text(str(stderr))[-2000:]
    entry = {
        "time": now_iso(),
        "job_id": job_id,
        "stage": stage,
        "chunk_index": chunk_index,
        "command": command[:4],
        "exit_code": code,
        "retry_count": retry_count,
        "stderr_tail": tail,
    }
    with open(JOB_LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")
def run_command(args, job_id, stage, timeout, chunk_index=None):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        log_job(job_id, stage, args, error, chunk_index)
        raise ExternalCommandError(stage, args, error, chunk_index) from error
def format_section_time(seconds):
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
def fetch_youtube_subtitles(video_id, job_id="caption"):
    validate_video_id(video_id)
    with tempfile.TemporaryDirectory(prefix="contextbubble-subs-") as tmpdir:
        run_command([
            YTDLP_CMD,
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "en.*",
            "--sub-format", "vtt",
            "--skip-download",
            "-o", os.path.join(tmpdir, "%(id)s.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}",
        ], job_id, "fetching_captions", 120)

        candidates = []
        for filename in os.listdir(tmpdir):
            if not filename.endswith(".vtt"):
                continue
            path = os.path.join(tmpdir, filename)
            with open(path, encoding="utf-8") as file:
                content = file.read()
            segments = parse_subtitles(content)
            if segments:
                generated_penalty = 1 if "auto" in filename.lower() else 0
                candidates.append((generated_penalty, filename, content, segments))
        if candidates:
            _, filename, content, segments = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
            return filename, content, segments

    raise FileNotFoundError("NO_USABLE_CAPTIONS")
def get_youtube_duration(video_id, job_id):
    result = run_command([
        YTDLP_CMD,
        "--no-download",
        "--print", "duration",
        f"https://www.youtube.com/watch?v={video_id}",
    ], job_id, "fetching_metadata", 120)
    return parse_duration_output(result.stdout)
def parse_duration_output(stdout):
    try:
        return float(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise RuntimeError("VIDEO_METADATA_FAILED") from error
def media_duration(path, job_id):
    result = run_command([
        FFPROBE_CMD,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ], job_id, "fetching_metadata", 60)
    try:
        return float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError("VIDEO_METADATA_FAILED") from error
def download_full_audio(video_id, directory, job_id):
    audio_base = os.path.join(directory, "%(id)s")
    run_command([
        YTDLP_CMD,
        "-f", "bestaudio/best",
        "-x",
        "--audio-format", "wav",
        "-o", f"{audio_base}.%(ext)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ], job_id, "downloading_audio", 1800)
    for filename in os.listdir(directory):
        if filename.endswith(".wav"):
            return os.path.join(directory, filename)
    raise FileNotFoundError("YTDLP_AUDIO_FAILED")
def normalize_audio(audio_path, output_path, job_id):
    run_command([
        FFMPEG_CMD,
        "-y",
        "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_path,
    ], job_id, "normalizing_audio", 600)
def create_chunks(duration_seconds, chunk_seconds=DEFAULT_CHUNK_SECONDS, overlap_seconds=CHUNK_OVERLAP_SECONDS):
    chunks = []
    step = max(1, chunk_seconds - overlap_seconds)
    start = 0.0
    index = 0
    while start < duration_seconds:
        end = min(start + chunk_seconds, duration_seconds)
        chunks.append({"chunk_index": index, "start_seconds": start, "end_seconds": end})
        index += 1
        start += step
    return chunks
def transcribe_audio_chunk(audio_path, chunk, tmpdir, job_id):
    chunk_path = os.path.join(tmpdir, f"chunk-{chunk['chunk_index']:04d}.wav")
    run_command([
        FFMPEG_CMD,
        "-y",
        "-ss", str(chunk["start_seconds"]),
        "-to", str(chunk["end_seconds"]),
        "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        chunk_path,
    ], job_id, "transcribing", 120, chunk["chunk_index"])
    transcript_base = os.path.join(tmpdir, f"chunk-{chunk['chunk_index']:04d}")
    whisper_args = [
        WHISPER_CMD,
        "-m", WHISPER_MODEL,
        "-f", chunk_path,
        "-l", WHISPER_LANGUAGE,
        "-ovtt",
        "-of", transcript_base,
        "-np",
    ]
    if WHISPER_NO_GPU:
        whisper_args.append("-ng")
    run_command(whisper_args, job_id, "transcribing", 900, chunk["chunk_index"])
    with open(f"{transcript_base}.vtt", encoding="utf-8") as file:
        return parse_subtitles(file.read(), chunk["start_seconds"])
def merge_token_overlap(left, right):
    left_tokens = left.split()
    right_tokens = right.split()
    max_size = min(len(left_tokens), len(right_tokens), 12)
    for size in range(max_size, 0, -1):
        if [token.lower() for token in left_tokens[-size:]] == [token.lower() for token in right_tokens[:size]]:
            return " ".join(left_tokens + right_tokens[size:])
    return ""
def merge_transcript_segments(segments, duration_seconds=None):
    merged = []
    for segment in sorted(segments, key=lambda item: (item["start_seconds"], item["end_seconds"])):
        start = max(0, segment["start_seconds"])
        end = max(start, segment["end_seconds"])
        if duration_seconds is not None:
            start = min(start, duration_seconds)
            end = min(end, duration_seconds)
        text = re.sub(r"\s+", " ", segment["text"]).strip(" ,")
        if not text:
            continue
        item = {"start_seconds": start, "end_seconds": end, "text": text}
        if merged:
            previous = merged[-1]
            if item["start_seconds"] <= previous["end_seconds"] + CHUNK_OVERLAP_SECONDS:
                if item["text"].lower() == previous["text"].lower():
                    previous["end_seconds"] = max(previous["end_seconds"], item["end_seconds"])
                    continue
                overlapped = merge_token_overlap(previous["text"], item["text"])
                if overlapped:
                    previous["text"] = overlapped
                    previous["end_seconds"] = max(previous["end_seconds"], item["end_seconds"])
                    continue
        merged.append(item)
    return add_segment_ids(merged)
