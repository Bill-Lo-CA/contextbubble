from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re


ANALYSES = {}


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
        if self.path != "/api/analyze":
            return self.send_json({"error": "not found"}, 404)

        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        video_id = body.get("video_id", "unknown")
        analysis_id = f"analysis-{video_id}"
        ANALYSES[analysis_id] = {"video_id": video_id, "bubbles": demo_bubbles()}
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
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("ContextBubble backend on http://127.0.0.1:8000")
    server.serve_forever()
