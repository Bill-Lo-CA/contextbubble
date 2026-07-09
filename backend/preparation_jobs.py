import hashlib
import threading
import time

from agents import analysis_result, semantic_sentence_entries
from config import *
from db import connect_db
from job_events import add_preparation_event
from transcripts import load_transcript, sentence_entries


STATE_LOCK = threading.Lock()
JOB_CREATION_LOCK = threading.Lock()
ACTIVE_PREPARATIONS = set()


JOB_UPDATE_COLUMNS = {
    "status", "stage", "transcript_source", "transcript_id", "analysis_id",
    "duration_seconds", "chunks_total", "chunks_completed", "progress",
    "error_code", "message", "force_refresh", "updated_at",
}


def update_job(job_id, **values):
    if not values:
        return
    unknown = set(values) - JOB_UPDATE_COLUMNS
    if unknown:
        raise ValueError(f"invalid preparation job update fields: {', '.join(sorted(unknown))}")
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect_db() as conn:
        conn.execute(f"update preparation_jobs set {assignments} where job_id = ?", (*values.values(), job_id))


def job_payload(job_id, include_ready=True, include_transcript=False, include_sentence_entries=False):
    with connect_db() as conn:
        job = conn.execute("select * from preparation_jobs where job_id = ?", (job_id,)).fetchone()
        if not job:
            return None
    payload = dict(job)
    if payload["chunks_total"]:
        payload["progress"] = payload["chunks_completed"] / payload["chunks_total"]
    transcript = load_transcript(payload["transcript_id"]) if payload.get("transcript_id") else None
    segments = transcript["segments"] if transcript else []
    if transcript and transcript.get("metadata"):
        payload["transcript_metadata"] = transcript["metadata"]
    if not segments and payload.get("stage") in ("transcribing", "merging_transcript"):
        from asr_pipeline import load_asr_chunk_segments
        from media import merge_transcript_segments

        partial_segments = merge_transcript_segments(load_asr_chunk_segments(job_id), payload.get("duration_seconds"))
        if partial_segments:
            segments = partial_segments
            payload["partial_transcript"] = True
            payload["transcript_source"] = payload.get("transcript_source") or "whisper_partial"
    if include_transcript and segments:
        payload["segments"] = segments
    if include_sentence_entries and segments:
        if payload.get("partial_transcript"):
            payload["sentence_entries"] = sentence_entries(segments)
        else:
            payload["sentence_entries"] = semantic_sentence_entries(segments)
    if include_ready and payload["status"] == "ready":
        analysis = analysis_result(payload["analysis_id"])
        payload["bubbles"] = analysis["bubbles"] if analysis else []
        payload["bubble_count"] = len(payload["bubbles"])
    return payload


def create_or_reuse_job(video_id, learner_level, force_refresh=False, demo_mode=False):
    validate_video_id(video_id)
    if learner_level not in LEARNER_LEVELS:
        raise ValueError("invalid learner level")
    source_policy = "demo" if demo_mode else "live"
    with JOB_CREATION_LOCK:
        with connect_db() as conn:
            if not force_refresh:
                existing = conn.execute(
                    """
                    select * from preparation_jobs
                    where video_id = ? and learner_level = ? and source_policy = ? and status in ('queued', 'processing', 'ready')
                    order by created_at desc limit 1
                    """,
                    (video_id, learner_level, source_policy),
                ).fetchone()
                if existing:
                    job_id = existing["job_id"]
                    include_ready = existing["status"] == "ready"
                    created = False
                else:
                    job_id, include_ready, created = create_job_row(conn, video_id, learner_level, source_policy, force_refresh)
            else:
                job_id, include_ready, created = create_job_row(conn, video_id, learner_level, source_policy, force_refresh)
    if created:
        add_preparation_event(job_id, "job_queued", "queued", {"source_policy": source_policy})
    start_preparation_thread(job_id)
    return job_payload(job_id, include_ready=include_ready)


def create_job_row(conn, video_id, learner_level, source_policy, force_refresh):
    seed = f"{video_id}:{learner_level}:{time.time_ns()}:{ANALYSIS_VERSION}"
    job_id = f"prepare-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"
    timestamp = now_iso()
    conn.execute(
        "insert or replace into videos values (?, coalesce((select created_at from videos where video_id = ?), ?), ?)",
        (video_id, video_id, timestamp, timestamp),
    )
    conn.execute(
        "insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, force_refresh, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, video_id, learner_level, source_policy, "queued", "queued", int(force_refresh), timestamp, timestamp),
    )
    return job_id, False, True


def start_preparation_thread(job_id):
    from preparation_runner import run_preparation_job

    with STATE_LOCK:
        if job_id in ACTIVE_PREPARATIONS:
            return
        ACTIVE_PREPARATIONS.add(job_id)
    threading.Thread(target=run_preparation_job, args=(job_id,), daemon=True).start()


def finish_preparation_thread(job_id):
    with STATE_LOCK:
        ACTIVE_PREPARATIONS.discard(job_id)


def resume_preparations():
    with connect_db() as conn:
        rows = conn.execute("select job_id from preparation_jobs where status in ('queued', 'processing')").fetchall()
    for row in rows:
        start_preparation_thread(row["job_id"])
