import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from analysis_store import analysis_result, run_analysis_for_transcript
from api_models import AnalysisRequest, PairRequest, PrepareVideoRequest, SubtitleUploadRequest, TranscriptRequest, TranslationRequest
from auth import pair_session, redact_secret_text, reset_pairing_code, valid_bearer_token
from config import API_VERSION, AGENT_MODE, DEMO_VIDEO_IDS, GEMINI_API_KEY, GEMINI_MODEL, LEARNER_LEVELS, MAX_JSON_BYTES, MAX_SUBTITLE_BYTES, TRANSCRIPT_BLOCK_SPLITTER_MODE, TRANSLATION_MODE, TRANSLATION_MODEL, demo_fixture_path, iso_from_timestamp, validate_video_id
from db import connect_db
from job_events import preparation_events
from media import ExternalCommandError, fetch_youtube_subtitles
from preparation_jobs import create_or_reuse_job, job_payload
from providers import AgentProviderError, gemini_status
from transcript_quality import caption_source_qc
from transcripts import load_transcript, store_transcript
from translation_jobs import TranslationQueueFull, create_translation_job, get_translation_job, public_translation_job


pairing_router = APIRouter()
preparations_router = APIRouter()
translations_router = APIRouter()
analyses_router = APIRouter()
health_router = APIRouter()
routers = (pairing_router, preparations_router, translations_router, analyses_router, health_router)


def json_response(payload, status=200):
    return JSONResponse(payload, status_code=status)


def ok(payload, status=200):
    return json_response({"api_version": API_VERSION, **payload}, status)


def error(error_code, message, status):
    return json_response({"error": redact_secret_text(message), "error_code": error_code, "api_version": API_VERSION}, status)


def require_auth(authorization):
    if not valid_bearer_token(authorization):
        return error("UNAUTHORIZED", "unauthorized", 401)
    return None


async def request_json(request, limit):
    length = request.headers.get("content-length")
    if length is not None and int(length) > limit:
        raise ValueError("request body too large")
    body = await request.body()
    if len(body) > limit:
        raise ValueError("request body too large")
    return json.loads(body or b"{}")


async def read_model(request, model=None):
    try:
        limit = MAX_SUBTITLE_BYTES if request.url.path == "/api/subtitles" else MAX_JSON_BYTES
        body = await request_json(request, limit)
        return model.model_validate(body) if model else body
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        return error("BAD_REQUEST", str(exc), 400)


@pairing_router.post("/api/pair")
async def pair(request: Request):
    body = await read_model(request, PairRequest)
    if isinstance(body, JSONResponse): return body
    try:
        token, expires_at = pair_session(body.pairing_code)
        return ok({"session_token": token, "expires_at": iso_from_timestamp(expires_at)})
    except ValueError as exc: return error("PAIRING_EXPIRED", str(exc), 401)
    except PermissionError as exc: return error("PAIRING_INVALID", str(exc), 401)
    except RuntimeError as exc: return error("PAIRING_RATE_LIMITED", str(exc), 429)


@pairing_router.post("/api/pair/resend")
async def pair_resend(request: Request):
    body = await read_model(request)
    if isinstance(body, JSONResponse): return body
    code, expires_at = reset_pairing_code()
    print(f"ContextBubble pairing code: {code} (expires in 5 minutes)", flush=True)
    return ok({"expires_at": iso_from_timestamp(expires_at)})


@preparations_router.post("/api/videos/{video_id}/prepare")
async def prepare_video(video_id: str, request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, PrepareVideoRequest)
    if isinstance(body, JSONResponse): return body
    try:
        return ok(create_or_reuse_job(video_id, body.learner_level, body.force_refresh, body.demo_mode))
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)


@preparations_router.post("/api/subtitles")
async def subtitle_upload(request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, SubtitleUploadRequest)
    if isinstance(body, JSONResponse): return body
    try: validate_video_id(body.video_id)
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)
    if len(body.content.encode()) > MAX_SUBTITLE_BYTES: return error("BAD_REQUEST", "subtitle file too large", 400)
    transcript = store_transcript(body.video_id, body.filename, body.content, "manual_upload", metadata={"provenance": "manual_upload"})
    return json_response(transcript) if transcript else error("NO_USABLE_CAPTIONS", "no subtitle segments found", 400)


@preparations_router.post("/api/demo-transcript")
async def demo_transcript(request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, TranscriptRequest)
    if isinstance(body, JSONResponse): return body
    try: validate_video_id(body.video_id)
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)
    if not body.demo_mode and body.video_id not in DEMO_VIDEO_IDS: return error("DEMO_NOT_ALLOWED", "demo transcript is not allowed for this video", 403)
    fixture = demo_fixture_path(body.video_id)
    transcript = store_transcript(body.video_id, fixture.name, fixture.read_text(encoding="utf-8"), "demo_fixture", metadata={"provenance": "demo_fixture"})
    return json_response({**transcript, "segments": load_transcript(transcript["transcript_id"])["segments"]})


@preparations_router.post("/api/youtube-subtitles")
async def youtube_subtitles(request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, TranscriptRequest)
    if isinstance(body, JSONResponse): return body
    try:
        validate_video_id(body.video_id)
        filename, content, segments = fetch_youtube_subtitles(body.video_id)
        caption_kind = "auto" if "auto" in filename.lower() else "unknown"
        qc = caption_source_qc(segments, None, caption_kind)
        transcript = store_transcript(body.video_id, filename, content, "youtube_caption", segments, {"caption_qc": qc, "caption_kind": caption_kind, "caption_language": "en"})
        return json_response({**transcript, "request_time_seconds": body.current_time, "subtitle_source": "youtube_caption", "caption_qc": qc, "segments": load_transcript(transcript["transcript_id"])["segments"]})
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)
    except ExternalCommandError as exc: return error(exc.error_code, str(exc), 502)
    except Exception as exc: return error("NO_USABLE_CAPTIONS", str(exc), 404)


@preparations_router.get("/api/preparations/{job_id}")
async def preparation(job_id: str, include_transcript: bool = False, include_sentence_entries: bool = False, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    job = await run_in_threadpool(job_payload, job_id, include_transcript=include_transcript, include_sentence_entries=include_sentence_entries)
    return ok(job) if job else error("NOT_FOUND", "missing", 404)


@preparations_router.get("/api/preparations/{job_id}/events")
async def preparation_event_list(job_id: str, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    return ok({"events": preparation_events(job_id)})


@translations_router.post("/api/translations")
async def translations(request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, TranslationRequest)
    if isinstance(body, JSONResponse): return body
    try:
        return ok(public_translation_job(await create_translation_job(body.model_dump())), 202)
    except TranslationQueueFull as exc:
        return error("TRANSLATION_QUEUE_FULL", str(exc), 429)


@translations_router.get("/api/translations/{translation_job_id}")
async def translation_status(translation_job_id: str, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    job = get_translation_job(translation_job_id)
    return ok(public_translation_job(job)) if job else error("NOT_FOUND", "missing", 404)


@analyses_router.post("/api/analyze")
async def create_analysis(request: Request, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    body = await read_model(request, AnalysisRequest)
    if isinstance(body, JSONResponse): return body
    try: validate_video_id(body.video_id)
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)
    transcript = load_transcript(body.transcript_id)
    if not transcript: return error("TRANSCRIPT_NOT_FOUND", "transcript not found", 404)
    if transcript.get("video_id") != body.video_id: return error("BAD_REQUEST", "transcript does not belong to this video", 400)
    try:
        analysis = run_analysis_for_transcript(body.video_id, body.learner_level, body.transcript_id, body.force_refresh)
        return json_response({"analysis_id": analysis["analysis_id"], "status": analysis["status"]})
    except AgentProviderError as exc: return error(exc.error_code, str(exc), 502)
    except Exception as exc: return error("ANALYSIS_FAILED", str(exc), 500)


@analyses_router.get("/api/videos/{video_id}/analysis")
async def video_analysis(video_id: str, learner_level: str = "beginner", authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    try: validate_video_id(video_id)
    except ValueError as exc: return error("BAD_REQUEST", str(exc), 400)
    if learner_level not in LEARNER_LEVELS: return error("BAD_REQUEST", "invalid learner level", 400)
    with connect_db() as conn:
        row = conn.execute("select analysis_id from analyses where video_id = ? and learner_level = ? and status = 'completed' order by updated_at desc limit 1", (video_id, learner_level)).fetchone()
    return json_response(analysis_result(row["analysis_id"])) if row else json_response({"status": "missing"}, 404)


@analyses_router.get("/api/analysis/{analysis_id}")
async def analysis(analysis_id: str, authorization: str = Header("")):
    if auth_error := require_auth(authorization): return auth_error
    result = analysis_result(analysis_id)
    return json_response(result) if result else json_response({"status": "missing"}, 404)


@health_router.get("/api/health")
async def health(authorization: str = Header("")):
    if not valid_bearer_token(authorization): return error("UNAUTHORIZED", "unauthorized", 401)
    return json_response({"status": "healthy", "api_version": API_VERSION, "agent_mode": AGENT_MODE, "gemini_model": GEMINI_MODEL if AGENT_MODE == "gemini" else None, "gemini": {**gemini_status(GEMINI_API_KEY, GEMINI_MODEL), "active_for": {"analysis": AGENT_MODE == "gemini", "translation": TRANSLATION_MODE == "gemini", "block_splitter": TRANSCRIPT_BLOCK_SPLITTER_MODE == "gemini"}}, "translation_mode": TRANSLATION_MODE, "translation_model": TRANSLATION_MODEL if TRANSLATION_MODE == "ollama" else GEMINI_MODEL})
