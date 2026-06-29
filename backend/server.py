from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sys


ANALYSES = {}
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
    for segment in segments:
        text = segment["text"]
        if text in used_text or len(text.split()) < 4:
            continue
        used_text.add(text)
        concept = " ".join(text.split()[:4]).rstrip(".,:;!?")
        bubbles.append({
            "concept": concept,
            "start_seconds": segment["start_seconds"],
            "short_explanation": f"This moment mentions: {text[:120]}",
            "expanded_explanation": "This placeholder will be replaced by concept detection, generation, and review.",
            "confidence": 0.5,
        })
        if len(bubbles) == 3:
            break
    return bubbles or demo_bubbles()


def demo_bubbles():
    return [
        {
            "concept": "ContextBubble demo",
            "start_seconds": 5,
            "short_explanation": "This backend-provided bubble proves the extension can receive timestamped analysis.",
            "expanded_explanation": "The next slice can replace this fixture with subtitle parsing and the agent workflow.",
            "confidence": 1.0,
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
            TRANSCRIPTS[video_id] = {
                "filename": body.get("filename", ""),
                "segments": segments,
            }
            return self.send_json({
                "video_id": video_id,
                "segment_count": len(segments),
                "status": "ready",
            })

        if self.path != "/api/analyze":
            return self.send_json({"error": "not found"}, 404)

        video_id = body.get("video_id", "unknown")
        analysis_id = f"analysis-{video_id}"
        transcript = TRANSCRIPTS.get(video_id, {})
        bubbles = transcript_bubbles(transcript.get("segments", []))
        ANALYSES[analysis_id] = {"video_id": video_id, "bubbles": bubbles}
        self.send_json({"analysis_id": analysis_id, "status": "processing"})

    def do_GET(self):
        match = re.fullmatch(r"/api/analysis/([^/]+)", self.path)
        if not match:
            return self.send_json({"error": "not found"}, 404)

        analysis = ANALYSES.get(match.group(1))
        if not analysis:
            return self.send_json({"status": "missing"}, 404)

        self.send_json({"status": "completed", **analysis})

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
