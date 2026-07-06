import json
from pathlib import Path
import sys
import tempfile

from checks import self_check
from config import API_VERSION, AGENT_MODE, DEMO_VIDEO_IDS, GEMINI_MODEL, LEARNER_LEVELS, MAX_JSON_BYTES, MAX_SUBTITLE_BYTES, TRANSLATION_MODE, TRANSLATION_MODEL, iso_from_timestamp, set_data_dir, validate_config, validate_video_id


if "--check" in sys.argv:
    validate_config()
    with tempfile.TemporaryDirectory(prefix="contextbubble-check-") as tmpdir:
        set_data_dir(tmpdir)
        self_check()
    print("ok")
    raise SystemExit(0)


from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

import auth
from agents import AgentProviderError, analysis_result, run_analysis_for_transcript, translate_segment
from auth import allowed_origin, pair_session, redact_secret_text, reset_pairing_code, valid_bearer_token
from db import connect_db, init_db
from jobs import create_or_reuse_job, job_payload, preparation_events, resume_preparations
from media import ExternalCommandError, fetch_youtube_subtitles
from transcript_quality import caption_source_qc
from transcripts import load_transcript, store_transcript


app = FastAPI()


def json_response(payload, status=200):
    return JSONResponse(payload, status_code=status)


def ok(payload, status=200):
    return json_response({"api_version": API_VERSION, **payload}, status)


def error(error_code, message, status):
    return json_response({"error": redact_secret_text(message), "error_code": error_code, "api_version": API_VERSION}, status)


def unauthorized():
    return json_response({"error": "unauthorized", "error_code": "UNAUTHORIZED", "api_version": API_VERSION}, 401)


def authorized(authorization=""):
    return valid_bearer_token(authorization)


async def request_json(request, limit):
    length = request.headers.get("content-length")
    if length is not None and int(length) > limit:
        raise ValueError("request body too large")
    body = await request.body()
    if len(body) > limit:
        raise ValueError("request body too large")
    return json.loads(body or b"{}")


async def read_body(request):
    try:
        limit = MAX_SUBTITLE_BYTES if request.url.path == "/api/subtitles" else MAX_JSON_BYTES
        return await request_json(request, limit)
    except (ValueError, json.JSONDecodeError) as exc:
        return error("BAD_REQUEST", str(exc), 400)


def require_auth(authorization):
    if not authorized(authorization):
        return unauthorized()
    return None


@app.middleware("http")
async def cors_middleware(request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        response = await call_next(request)

    origin = request.headers.get("origin", "")
    if allowed_origin(origin, request.url.path):
        response.headers["access-control-allow-origin"] = origin
    response.headers["access-control-allow-methods"] = "GET, POST, OPTIONS"
    response.headers["access-control-allow-headers"] = "authorization, content-type"
    response.headers["access-control-allow-private-network"] = "true"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    if exc.status_code == 404:
        return json_response({"error": "not found", "error_code": "NOT_FOUND"}, 404)
    return error("HTTP_ERROR", str(exc.detail), exc.status_code)


@app.post("/api/pair")
async def pair(request: Request):
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        token, expires_at = pair_session(body.get("pairing_code", ""))
        return ok({
            "session_token": token,
            "expires_at": iso_from_timestamp(expires_at),
        })
    except ValueError as exc:
        return error("PAIRING_EXPIRED", str(exc), 401)
    except PermissionError as exc:
        return error("PAIRING_INVALID", str(exc), 401)
    except RuntimeError as exc:
        return error("PAIRING_RATE_LIMITED", str(exc), 429)


@app.post("/api/pair/resend")
async def pair_resend(request: Request):
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    code, expires_at = reset_pairing_code()
    print(f"ContextBubble pairing code: {code} (expires in 5 minutes)", flush=True)
    return ok({"expires_at": iso_from_timestamp(expires_at)})


@app.post("/api/videos/{video_id}/prepare")
async def prepare_video(video_id: str, request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        job = create_or_reuse_job(
            video_id,
            body.get("learner_level", "beginner"),
            bool(body.get("force_refresh")),
            bool(body.get("demo_mode")),
        )
        return json_response({"api_version": API_VERSION, **job})
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)


@app.post("/api/subtitles")
async def subtitle_upload(request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    video_id = body.get("video_id", "unknown")
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)
    content = body.get("content", "")
    if len(content.encode()) > MAX_SUBTITLE_BYTES:
        return json_response({"error": "subtitle file too large", "error_code": "BAD_REQUEST"}, 400)
    transcript = store_transcript(video_id, body.get("filename", ""), content, "manual_upload", metadata={"provenance": "manual_upload"})
    if not transcript:
        return json_response({"error": "no subtitle segments found", "error_code": "NO_USABLE_CAPTIONS"}, 400)
    return json_response(transcript)


@app.post("/api/demo-transcript")
async def demo_transcript(request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    video_id = body.get("video_id", "demo")
    demo_mode = bool(body.get("demo_mode"))
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)
    if not demo_mode and video_id not in DEMO_VIDEO_IDS:
        return json_response({"error": "demo transcript is not allowed for this video", "error_code": "DEMO_NOT_ALLOWED"}, 403)
    fixture = demo_fixture_path(video_id)
    with open(fixture, encoding="utf-8") as file:
        transcript = store_transcript(video_id, fixture.name, file.read(), "demo_fixture", metadata={"provenance": "demo_fixture"})
    return json_response({**transcript, "segments": load_transcript(transcript["transcript_id"])["segments"]})


@app.post("/api/youtube-subtitles")
async def youtube_subtitles(request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    video_id = body.get("video_id", "unknown")
    try:
        validate_video_id(video_id)
        filename, content, segments = fetch_youtube_subtitles(video_id)
        caption_kind = "auto" if "auto" in filename.lower() else "unknown"
        qc_result = caption_source_qc(segments, None, caption_kind)
        transcript = store_transcript(video_id, filename, content, "youtube_caption", segments, {
            "caption_qc": qc_result,
            "caption_kind": caption_kind,
            "caption_language": "en",
        })
        return json_response({
            **transcript,
            "request_time_seconds": float(body.get("current_time", 0)),
            "subtitle_source": "youtube_caption",
            "caption_qc": qc_result,
            "segments": load_transcript(transcript["transcript_id"])["segments"],
        })
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)
    except ExternalCommandError as exc:
        return json_response({"error": str(exc), "error_code": exc.error_code}, 502)
    except Exception as exc:
        return json_response({"error": str(exc), "error_code": "NO_USABLE_CAPTIONS"}, 404)


@app.post("/api/translations")
async def translations(request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    try:
        result = await run_in_threadpool(
            translate_segment,
            body.get("id", ""),
            body.get("source_text", ""),
            body.get("context_before", ""),
            body.get("context_after", ""),
            body.get("target_language", "zh-TW"),
            bool(body.get("force_refresh")),
        )
        return ok(result)
    except AgentProviderError as exc:
        return error(exc.error_code, str(exc), 502)
    except Exception as exc:
        return error("TRANSLATION_FAILED", str(exc), 500)


@app.post("/api/analyze")
async def create_analysis(request: Request, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    body = await read_body(request)
    if isinstance(body, JSONResponse):
        return body
    video_id = body.get("video_id", "unknown")
    learner_level = body.get("learner_level", "beginner")
    transcript_id = body.get("transcript_id", "")
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)
    if learner_level not in LEARNER_LEVELS:
        return json_response({"error": "invalid learner level", "error_code": "BAD_REQUEST"}, 400)
    transcript = load_transcript(transcript_id)
    if not transcript:
        return json_response({"error": "transcript not found", "error_code": "TRANSCRIPT_NOT_FOUND"}, 404)
    if transcript.get("video_id") != video_id:
        return json_response({"error": "transcript does not belong to this video", "error_code": "BAD_REQUEST"}, 400)

    try:
        analysis = run_analysis_for_transcript(video_id, learner_level, transcript_id, bool(body.get("force_refresh")))
        return json_response({"analysis_id": analysis["analysis_id"], "status": analysis["status"]})
    except AgentProviderError as exc:
        return error(exc.error_code, str(exc), 502)
    except Exception as exc:
        return error("ANALYSIS_FAILED", str(exc), 500)


@app.get("/api/health")
async def health(authorization: str = Header("")):
    if not authorized(authorization):
        return json_response({"status": "unauthorized", "error_code": "UNAUTHORIZED", "api_version": API_VERSION}, 401)
    return json_response({
        "status": "healthy",
        "api_version": API_VERSION,
        "agent_mode": AGENT_MODE,
        "gemini_model": GEMINI_MODEL if AGENT_MODE == "gemini" else None,
        "translation_mode": TRANSLATION_MODE,
        "translation_model": TRANSLATION_MODEL if TRANSLATION_MODE == "ollama" else GEMINI_MODEL,
    })


@app.get("/api/preparations/{job_id}")
async def preparation(job_id: str, include_transcript: str = "false", include_sentence_entries: str = "false", authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    job = await run_in_threadpool(
        job_payload,
        job_id,
        include_transcript=include_transcript == "true",
        include_sentence_entries=include_sentence_entries == "true",
    )
    if not job:
        return json_response({"status": "missing", "error_code": "NOT_FOUND"}, 404)
    return json_response({"api_version": API_VERSION, **job})


@app.get("/api/preparations/{job_id}/events")
async def preparation_event_list(job_id: str, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    return json_response({"api_version": API_VERSION, "events": preparation_events(job_id)})


@app.get("/api/videos/{video_id}/analysis")
async def video_analysis(video_id: str, learner_level: str = "beginner", authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    try:
        validate_video_id(video_id)
    except ValueError as exc:
        return json_response({"error": str(exc), "error_code": "BAD_REQUEST"}, 400)
    if learner_level not in LEARNER_LEVELS:
        return json_response({"error": "invalid learner level", "error_code": "BAD_REQUEST"}, 400)
    with connect_db() as conn:
        row = conn.execute(
            """
            select analysis_id from analyses
            where video_id = ? and learner_level = ? and status = 'completed'
            order by updated_at desc limit 1
            """,
            (video_id, learner_level),
        ).fetchone()
    if row:
        return json_response(analysis_result(row["analysis_id"]))
    return json_response({"status": "missing"}, 404)


@app.get("/api/analysis/{analysis_id}")
async def analysis(analysis_id: str, authorization: str = Header("")):
    auth_error = require_auth(authorization)
    if auth_error:
        return auth_error
    result = analysis_result(analysis_id)
    if not result:
        return json_response({"status": "missing"}, 404)
    return json_response(result)


def main():
    validate_config()
    init_db()
    resume_preparations()
    print("ContextBubble backend on http://127.0.0.1:8000")
    print(f"ContextBubble pairing code: {auth.PAIRING_CODE} (expires in 5 minutes)")
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
