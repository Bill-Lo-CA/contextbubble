from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

import auth
from agents import AgentProviderError, analysis_result, run_analysis_for_transcript
from auth import allowed_origin, pair_session, redact_secret_text, reset_pairing_code, valid_bearer_token
from checks import self_check
from config import API_VERSION, AGENT_MODE, DEMO_VIDEO_IDS, GEMINI_MODEL, LEARNER_LEVELS, MAX_JSON_BYTES, MAX_SUBTITLE_BYTES, iso_from_timestamp, set_data_dir, validate_config, validate_video_id
from db import connect_db, init_db
from jobs import create_or_reuse_job, job_payload, preparation_events, resume_preparations
from media import ExternalCommandError, fetch_youtube_subtitles
from transcripts import load_transcript, store_transcript


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        origin = self.headers.get("origin", "")
        if allowed_origin(origin, urlparse(self.path).path):
            self.send_header("access-control-allow-origin", origin)
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "authorization, content-type")
        self.send_header("access-control-allow-private-network", "true")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def authorized(self):
        return valid_bearer_token(self.headers.get("authorization", ""))

    def send_ok(self, payload, status=200):
        return self.send_json({"api_version": API_VERSION, **payload}, status)

    def send_error(self, error_code, message, status):
        return self.send_json({"error": redact_secret_text(message), "error_code": error_code, "api_version": API_VERSION}, status)

    def bad_request(self, message):
        return self.send_error("BAD_REQUEST", message, 400)

    def unauthorized(self):
        return self.send_json({"error": "unauthorized", "error_code": "UNAUTHORIZED", "api_version": API_VERSION}, 401)

    def read_json_body(self, limit):
        length = int(self.headers.get("content-length", "0"))
        if length > limit:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in ("/api/pair", "/api/pair/resend") and not self.authorized():
            return self.unauthorized()

        try:
            limit = MAX_SUBTITLE_BYTES if url.path == "/api/subtitles" else MAX_JSON_BYTES
            body = self.read_json_body(limit)
        except (ValueError, json.JSONDecodeError) as error:
            return self.bad_request(str(error))

        if url.path == "/api/pair":
            try:
                token, expires_at = pair_session(body.get("pairing_code", ""))
                return self.send_ok({
                    "session_token": token,
                    "expires_at": iso_from_timestamp(expires_at),
                })
            except ValueError as error:
                return self.send_error("PAIRING_EXPIRED", str(error), 401)
            except PermissionError as error:
                return self.send_error("PAIRING_INVALID", str(error), 401)
            except RuntimeError as error:
                return self.send_error("PAIRING_RATE_LIMITED", str(error), 429)

        if url.path == "/api/pair/resend":
            code, expires_at = reset_pairing_code()
            print(f"ContextBubble pairing code: {code} (expires in 5 minutes)", flush=True)
            return self.send_ok({"expires_at": iso_from_timestamp(expires_at)})

        prepare_match = re.fullmatch(r"/api/videos/([^/]+)/prepare", url.path)
        if prepare_match:
            try:
                job = create_or_reuse_job(
                    prepare_match.group(1),
                    body.get("learner_level", "beginner"),
                    bool(body.get("force_refresh")),
                    bool(body.get("demo_mode")),
                )
                return self.send_json({"api_version": API_VERSION, **job})
            except ValueError as error:
                return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)

        if url.path == "/api/subtitles":
            video_id = body.get("video_id", "unknown")
            try:
                validate_video_id(video_id)
            except ValueError as error:
                return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)
            content = body.get("content", "")
            if len(content.encode()) > MAX_SUBTITLE_BYTES:
                return self.send_json({"error": "subtitle file too large", "error_code": "BAD_REQUEST"}, 400)
            transcript = store_transcript(video_id, body.get("filename", ""), content, "upload")
            if not transcript:
                return self.send_json({"error": "no subtitle segments found", "error_code": "NO_USABLE_CAPTIONS"}, 400)
            return self.send_json(transcript)

        if url.path == "/api/demo-transcript":
            video_id = body.get("video_id", "demo")
            demo_mode = bool(body.get("demo_mode"))
            try:
                validate_video_id(video_id)
            except ValueError as error:
                return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)
            if not demo_mode and video_id not in DEMO_VIDEO_IDS:
                return self.send_json({"error": "demo transcript is not allowed for this video", "error_code": "DEMO_NOT_ALLOWED"}, 403)
            fixture = Path(__file__).resolve().parent / "fixtures/demo.vtt"
            with open(fixture, encoding="utf-8") as file:
                transcript = store_transcript(video_id, fixture.name, file.read(), "demo")
            return self.send_json({**transcript, "segments": load_transcript(transcript["transcript_id"])["segments"]})

        if url.path == "/api/youtube-subtitles":
            video_id = body.get("video_id", "unknown")
            try:
                validate_video_id(video_id)
                filename, content, segments = fetch_youtube_subtitles(video_id)
                transcript = store_transcript(video_id, filename, content, "youtube", segments)
                return self.send_json({
                    **transcript,
                    "request_time_seconds": float(body.get("current_time", 0)),
                    "subtitle_source": "youtube",
                    "segments": load_transcript(transcript["transcript_id"])["segments"],
                })
            except ValueError as error:
                return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)
            except ExternalCommandError as error:
                return self.send_json({"error": str(error), "error_code": error.error_code}, 502)
            except Exception as error:
                return self.send_json({"error": str(error), "error_code": "NO_USABLE_CAPTIONS"}, 404)

        if url.path not in ("/api/analyses", "/api/analyze"):
            return self.send_json({"error": "not found", "error_code": "NOT_FOUND"}, 404)

        video_id = body.get("video_id", "unknown")
        learner_level = body.get("learner_level", "beginner")
        transcript_id = body.get("transcript_id", "")
        try:
            validate_video_id(video_id)
        except ValueError as error:
            return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)
        if learner_level not in LEARNER_LEVELS:
            return self.send_json({"error": "invalid learner level", "error_code": "BAD_REQUEST"}, 400)
        transcript = load_transcript(transcript_id)
        if not transcript:
            return self.send_json({"error": "transcript not found", "error_code": "TRANSCRIPT_NOT_FOUND"}, 404)
        if transcript.get("video_id") != video_id:
            return self.send_json({"error": "transcript does not belong to this video", "error_code": "BAD_REQUEST"}, 400)

        try:
            analysis = run_analysis_for_transcript(video_id, learner_level, transcript_id, bool(body.get("force_refresh")))
            return self.send_json({"analysis_id": analysis["analysis_id"], "status": analysis["status"]})
        except AgentProviderError as error:
            return self.send_error(error.error_code, str(error), 502)
        except Exception as error:
            return self.send_error("ANALYSIS_FAILED", str(error), 500)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/health":
            if not self.authorized():
                return self.send_json({"status": "unauthorized", "error_code": "UNAUTHORIZED", "api_version": API_VERSION}, 401)
            return self.send_json({
                "status": "healthy",
                "api_version": API_VERSION,
                "agent_mode": AGENT_MODE,
                "gemini_model": GEMINI_MODEL if AGENT_MODE == "gemini" else None,
            })

        if not self.authorized():
            return self.send_json({"error": "unauthorized", "error_code": "UNAUTHORIZED", "api_version": API_VERSION}, 401)

        prep_match = re.fullmatch(r"/api/preparations/([^/]+)", url.path)
        if prep_match:
            query = parse_qs(url.query)
            job = job_payload(
                prep_match.group(1),
                include_transcript=query.get("include_transcript", ["false"])[0] == "true",
                include_sentence_entries=query.get("include_sentence_entries", ["false"])[0] == "true",
            )
            if not job:
                return self.send_json({"status": "missing", "error_code": "NOT_FOUND"}, 404)
            return self.send_json({"api_version": API_VERSION, **job})

        events_match = re.fullmatch(r"/api/preparations/([^/]+)/events", url.path)
        if events_match:
            return self.send_json({"api_version": API_VERSION, "events": preparation_events(events_match.group(1))})

        video_match = re.fullmatch(r"/api/videos/([^/]+)/analysis", url.path)
        if video_match:
            learner_level = parse_qs(url.query).get("learner_level", ["beginner"])[0]
            try:
                validate_video_id(video_match.group(1))
            except ValueError as error:
                return self.send_json({"error": str(error), "error_code": "BAD_REQUEST"}, 400)
            if learner_level not in LEARNER_LEVELS:
                return self.send_json({"error": "invalid learner level", "error_code": "BAD_REQUEST"}, 400)
            with connect_db() as conn:
                row = conn.execute(
                    """
                    select analysis_id from analyses
                    where video_id = ? and learner_level = ? and status = 'completed'
                    order by updated_at desc limit 1
                    """,
                    (video_match.group(1), learner_level),
                ).fetchone()
            if row:
                return self.send_json(analysis_result(row["analysis_id"]))
            return self.send_json({"status": "missing"}, 404)

        match = re.fullmatch(r"/api/analyses/([^/]+)", url.path) or re.fullmatch(r"/api/analysis/([^/]+)", url.path)
        if not match:
            return self.send_json({"error": "not found", "error_code": "NOT_FOUND"}, 404)

        analysis = analysis_result(match.group(1))
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


def main():
    validate_config()
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory(prefix="contextbubble-check-") as tmpdir:
            set_data_dir(tmpdir)
            self_check()
        print("ok")
        raise SystemExit(0)
    init_db()
    resume_preparations()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("ContextBubble backend on http://127.0.0.1:8000")
    print(f"ContextBubble API token: {auth.API_TOKEN}")
    print(f"ContextBubble pairing code: {auth.PAIRING_CODE} (expires in 5 minutes)")
    server.serve_forever()


if __name__ == "__main__":
    main()
