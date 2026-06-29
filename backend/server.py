from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import re
import sys
from urllib.parse import parse_qs, urlparse


ANALYSIS_VERSION = "phase2-placeholder"
ANALYSES = {}
ANALYSIS_CACHE = {}
TRANSCRIPTS = {}


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


def parse_subtitles(content):
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
                "start_seconds": parse_time(start_text),
                "end_seconds": parse_time(end_text),
                "text": text,
            })

    return segments


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
    assert len(transcript_bubbles(parse_subtitles(vtt + "\n" + srt))) >= 1
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
            content = body.get("content", "")
            segments = parse_subtitles(content)
            if not segments:
                return self.send_json({"error": "no subtitle segments found"}, 400)
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            transcript_id = f"transcript-{content_hash[:12]}"
            TRANSCRIPTS[transcript_id] = {
                "video_id": video_id,
                "filename": body.get("filename", ""),
                "segments": segments,
                "content_hash": content_hash,
            }
            return self.send_json({
                "transcript_id": transcript_id,
                "video_id": video_id,
                "segment_count": len(segments),
                "content_hash": content_hash,
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
