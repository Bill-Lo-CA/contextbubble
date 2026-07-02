from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse


ANALYSIS_VERSION = "phase2-placeholder"
HOME = Path.home()
LOCAL_YTDLP_CMD = HOME / ".local/bin/yt-dlp"
DEFAULT_YTDLP_CMD = str(LOCAL_YTDLP_CMD) if LOCAL_YTDLP_CMD.exists() else "yt-dlp"
YTDLP_CMD = os.environ.get("YTDLP_CMD", DEFAULT_YTDLP_CMD)
WHISPER_CMD = os.environ.get("WHISPER_CMD", str(HOME / "tools/whisper.cpp/build/bin/whisper-cli"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", str(HOME / "tools/whisper.cpp/models/ggml-base.en.bin"))
ANALYSES = {}
ANALYSIS_CACHE = {}
TRANSCRIPTS = {}
DEFAULT_CHUNK_SECONDS = 30


def parse_time(value):
    value = value.replace(",", ".")
    parts = value.split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) > 1 else 0
    hours = int(parts[-3]) if len(parts) > 2 else 0
    return hours * 3600 + minutes * 60 + seconds


def clean_caption_text(lines):
    text = " ".join(line.strip() for line in lines if line.strip())
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_subtitles(content, offset_seconds=0):
    segments = []
    lines = content.replace("\ufeff", "").splitlines()
    index = 0

    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            continue
        if "-->" not in line and index + 1 < len(lines) and "-->" in lines[index + 1]:
            index += 1
            line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue

        start_text, end_text = [part.strip().split()[0] for part in line.split("-->", 1)]
        index += 1
        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index])
            index += 1

        text = clean_caption_text(text_lines)
        if text:
            segments.append({
                "start_seconds": parse_time(start_text) + offset_seconds,
                "end_seconds": parse_time(end_text) + offset_seconds,
                "text": text,
            })

    return segments


def store_transcript(video_id, filename, content):
    segments = parse_subtitles(content)
    if not segments:
        return None
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    transcript_id = f"transcript-{content_hash[:12]}"
    TRANSCRIPTS[transcript_id] = {
        "video_id": video_id,
        "filename": filename,
        "segments": segments,
        "content_hash": content_hash,
    }
    return {
        "transcript_id": transcript_id,
        "video_id": video_id,
        "segment_count": len(segments),
        "content_hash": content_hash,
    }


def validate_video_id(video_id):
    if not re.fullmatch(r"[-_A-Za-z0-9]{6,20}", video_id):
        raise ValueError("invalid YouTube video id")


def command_error(prefix, error):
    output = "\n".join(part for part in (error.stderr, error.stdout) if part).strip()
    if len(output) > 1200:
        output = f"{output[:1200]}..."
    return f"{prefix}: {output or error}"


def format_section_time(seconds):
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = seconds % 3600 // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def transcribe_youtube_audio(video_id, start_seconds=0, chunk_seconds=60):
    validate_video_id(video_id)
    start_seconds = max(0, int(float(start_seconds)))
    chunk_seconds = min(180, max(15, int(float(chunk_seconds))))
    end_seconds = start_seconds + chunk_seconds

    with tempfile.TemporaryDirectory(prefix="contextbubble-") as tmpdir:
        audio_base = os.path.join(tmpdir, "%(id)s")
        subprocess.run([
            YTDLP_CMD,
            "-f", "bestaudio/best",
            "--download-sections", f"*{format_section_time(start_seconds)}-{format_section_time(end_seconds)}",
            "-x",
            "--audio-format", "wav",
            "-o", f"{audio_base}.%(ext)s",
            f"https://www.youtube.com/watch?v={video_id}",
        ], check=True, capture_output=True, text=True, timeout=600)

        audio_path = ""
        for filename in os.listdir(tmpdir):
            if filename.endswith(".wav"):
                audio_path = os.path.join(tmpdir, filename)
                break

        if not audio_path:
            raise FileNotFoundError("yt-dlp did not produce a wav audio file")

        transcript_base = os.path.join(tmpdir, "transcript")
        subprocess.run([
            WHISPER_CMD,
            "-m", WHISPER_MODEL,
            "-f", audio_path,
            "-l", "en",
            "-ovtt",
            "-of", transcript_base,
            "-ng",
            "-np",
        ], check=True, capture_output=True, text=True, timeout=900)

        with open(f"{transcript_base}.vtt", encoding="utf-8") as file:
            return f"{video_id}.{start_seconds}-{end_seconds}.whisper.vtt", file.read(), start_seconds, end_seconds


def transcript_bubbles(segments):
    bubbles = []
    used_text = set()
    for index, segment in enumerate(segments, 1):
        text = segment["text"]
        if text in used_text or len(text.split()) < 4:
            continue
        used_text.add(text)
        concept = " ".join(text.split()[:4]).rstrip(".,:;!?")
        bubbles.append({
            "id": f"bubble-{index:03d}",
            "concept": concept,
            "start_seconds": segment["start_seconds"],
            "short_explanation": f"This moment mentions: {text[:120]}",
            "expanded_explanation": "This placeholder will be replaced by concept detection, generation, and review.",
            "confidence": 0.5,
            "review_status": "accepted",
        })
        if len(bubbles) == 3:
            break
    return bubbles or demo_bubbles()


def demo_bubbles():
    return [
        {
            "id": "bubble-001",
            "concept": "ContextBubble demo",
            "start_seconds": 5,
            "short_explanation": "This backend-provided bubble proves the extension can receive timestamped analysis.",
            "expanded_explanation": "The next slice can replace this fixture with subtitle parsing and the agent workflow.",
            "confidence": 1.0,
            "review_status": "accepted",
        }
    ]


def self_check():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.500
Embeddings are numeric representations of text.
"""
    srt = """1
00:00:04,000 --> 00:00:06,250
Cosine similarity compares vector direction.
"""
    assert parse_subtitles(vtt) == [{
        "start_seconds": 1.0,
        "end_seconds": 3.5,
        "text": "Embeddings are numeric representations of text.",
    }]
    assert parse_subtitles(srt)[0]["start_seconds"] == 4.0
    assert parse_subtitles(vtt, 10)[0]["start_seconds"] == 11.0
    assert parse_subtitles(srt, 120)[0]["end_seconds"] == 126.25
    assert format_section_time(65) == "00:01:05"
    assert len(transcript_bubbles(parse_subtitles(vtt + "\n" + srt))) >= 1
    assert store_transcript("demo", "demo.vtt", vtt)["segment_count"] == 1
    try:
        transcribe_youtube_audio("../../bad")
        raise AssertionError("invalid video id accepted")
    except ValueError:
        pass
    assert hashlib.sha256(b"demo").hexdigest()


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/subtitles":
            video_id = body.get("video_id", "unknown")
            transcript = store_transcript(video_id, body.get("filename", ""), body.get("content", ""))
            if not transcript:
                return self.send_json({"error": "no subtitle segments found"}, 400)
            return self.send_json(transcript)

        if self.path == "/api/youtube-subtitles":
            video_id = body.get("video_id", "unknown")
            try:
                request_time = float(body.get("current_time", 0))
                chunk_seconds = float(body.get("chunk_seconds", DEFAULT_CHUNK_SECONDS))
                chunk_start = int(request_time // chunk_seconds * chunk_seconds)
                filename, content, start_seconds, end_seconds = transcribe_youtube_audio(video_id, chunk_start, chunk_seconds)
            except ValueError as error:
                return self.send_json({"error": str(error)}, 400)
            except FileNotFoundError as error:
                return self.send_json({"error": f"YouTube audio transcription failed: {error}"}, 500)
            except subprocess.CalledProcessError as error:
                return self.send_json({"error": command_error("YouTube audio transcription failed", error)}, 500)
            except subprocess.TimeoutExpired as error:
                return self.send_json({"error": f"YouTube audio transcription timed out: {error}"}, 500)

            segments = parse_subtitles(content, start_seconds)
            transcript = store_transcript(video_id, filename, content)
            if not transcript:
                return self.send_json({"error": "Whisper returned no usable subtitle segments"}, 500)
            TRANSCRIPTS[transcript["transcript_id"]]["segments"] = segments
            return self.send_json({
                **transcript,
                "request_time_seconds": request_time,
                "chunk_start_seconds": start_seconds,
                "chunk_end_seconds": end_seconds,
                "segments": segments,
            })

        if self.path not in ("/api/analyses", "/api/analyze"):
            return self.send_json({"error": "not found"}, 404)

        video_id = body.get("video_id", "unknown")
        learner_level = body.get("learner_level", "beginner")
        transcript_id = body.get("transcript_id", "")
        transcript = TRANSCRIPTS.get(transcript_id, {})
        content_hash = transcript.get("content_hash", "fixture")
        cache_key = f"{video_id}:{content_hash}:{learner_level}:{ANALYSIS_VERSION}"
        analysis_id = f"analysis-{hashlib.sha256(cache_key.encode()).hexdigest()[:12]}"

        cached = ANALYSIS_CACHE.get(cache_key)
        if cached and not body.get("force_refresh"):
            ANALYSES[analysis_id] = cached
        else:
            ANALYSES[analysis_id] = {
                "analysis_id": analysis_id,
                "status": "completed",
                "video_id": video_id,
                "learner_level": learner_level,
                "bubbles": transcript_bubbles(transcript.get("segments", [])),
            }
            ANALYSIS_CACHE[cache_key] = ANALYSES[analysis_id]

        self.send_json({"analysis_id": analysis_id, "status": "processing"})

    def do_GET(self):
        url = urlparse(self.path)
        video_match = re.fullmatch(r"/api/videos/([^/]+)/analysis", url.path)
        if video_match:
            learner_level = parse_qs(url.query).get("learner_level", ["beginner"])[0]
            for analysis in reversed(list(ANALYSIS_CACHE.values())):
                if analysis["video_id"] == video_match.group(1) and analysis["learner_level"] == learner_level:
                    return self.send_json(analysis)
            return self.send_json({"status": "missing"}, 404)

        match = re.fullmatch(r"/api/analyses/([^/]+)", url.path) or re.fullmatch(r"/api/analysis/([^/]+)", url.path)
        if not match:
            return self.send_json({"error": "not found"}, 404)

        analysis = ANALYSES.get(match.group(1))
        if not analysis:
            return self.send_json({"status": "missing"}, 404)

        self.send_json(analysis)

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if "--check" in sys.argv:
        self_check()
        print("ok")
        raise SystemExit(0)
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("ContextBubble backend on http://127.0.0.1:8000")
    server.serve_forever()
