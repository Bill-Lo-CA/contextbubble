import threading
import time

from analysis_store import analysis_result
from semantic_splitter import semantic_sentence_entries
from config import ANALYSIS_VERSION, GRAPH_VERSION, LEARNER_LEVELS, now_iso, validate_video_id
from db import connect_db, short_hash_id, update_allowed_columns
from job_events import add_preparation_event
from transcripts import load_transcript, sentence_entries


STATE_LOCK = threading.Lock()
JOB_CREATION_LOCK = threading.Lock()
ACTIVE_PREPARATIONS = set()


# graph_extraction jobs ignore learner_level (their personalization axis is
# lens/goal, not learner level) but the column stays NOT NULL, so callers
# creating a graph job should pass this placeholder.
GRAPH_PLACEHOLDER_LEARNER_LEVEL = "intermediate"

# Per-job-kind dedupe strategy for create_or_reuse_job: which cache table to join
# against to check "is there already a fresh, ready result for this job", and
# whether learner_level participates in the dedupe key. Adding a new job kind
# means adding one entry here rather than another branch in create_or_reuse_job.
JOB_KIND_DEDUPE = {
    "bubble_analysis": {
        "cache_join_sql": "left join analyses on analyses.analysis_id = preparation_jobs.analysis_id",
        "cache_key_column": "analyses.cache_key",
        "version": ANALYSIS_VERSION,
        "filter_learner_level": True,
    },
    "graph_extraction": {
        "cache_join_sql": "left join kg_extraction_jobs on kg_extraction_jobs.job_id = preparation_jobs.job_id",
        "cache_key_column": "kg_extraction_jobs.cache_key",
        "version": GRAPH_VERSION,
        "filter_learner_level": False,
    },
}
JOB_KINDS = set(JOB_KIND_DEDUPE)


JOB_UPDATE_COLUMNS = {
    "status", "stage", "transcript_source", "transcript_id", "analysis_id",
    "duration_seconds", "chunks_total", "chunks_completed", "progress",
    "error_code", "message", "force_refresh",
}


def update_job(job_id, **values):
    update_allowed_columns("preparation_jobs", "job_id", job_id, JOB_UPDATE_COLUMNS, **values)


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
    if include_ready and payload["status"] == "ready" and payload["job_kind"] == "bubble_analysis":
        analysis = analysis_result(payload["analysis_id"])
        payload["bubbles"] = analysis["bubbles"] if analysis else []
        payload["bubble_count"] = len(payload["bubbles"])
    return payload


def create_or_reuse_job(video_id, learner_level, force_refresh=False, demo_mode=False, job_kind="bubble_analysis"):
    validate_video_id(video_id)
    if job_kind not in JOB_KINDS:
        raise ValueError("invalid job kind")
    if learner_level not in LEARNER_LEVELS:
        raise ValueError("invalid learner level")
    source_policy = "demo" if demo_mode else "live"
    with JOB_CREATION_LOCK:
        with connect_db() as conn:
            if not force_refresh:
                dedupe = JOB_KIND_DEDUPE[job_kind]
                learner_level_filter = "and preparation_jobs.learner_level = ? " if dedupe["filter_learner_level"] else ""
                params = [video_id]
                if dedupe["filter_learner_level"]:
                    params.append(learner_level)
                params += [job_kind, source_policy, f"%:{dedupe['version']}"]
                existing = conn.execute(
                    f"""
                    select preparation_jobs.* from preparation_jobs
                    {dedupe["cache_join_sql"]}
                    where preparation_jobs.video_id = ? {learner_level_filter}
                    and preparation_jobs.job_kind = ? and preparation_jobs.source_policy = ? and preparation_jobs.status in ('queued', 'processing', 'ready')
                    and (preparation_jobs.status != 'ready' or {dedupe["cache_key_column"]} like ?)
                    order by preparation_jobs.created_at desc limit 1
                    """,
                    params,
                ).fetchone()
                if existing:
                    job_id = existing["job_id"]
                    include_ready = existing["status"] == "ready"
                    created = False
                else:
                    job_id, include_ready, created = create_job_row(conn, video_id, learner_level, source_policy, force_refresh, job_kind)
            else:
                job_id, include_ready, created = create_job_row(conn, video_id, learner_level, source_policy, force_refresh, job_kind)
    if created:
        add_preparation_event(job_id, "job_queued", "queued", {"source_policy": source_policy, "job_kind": job_kind})
    start_preparation_thread(job_id)
    return job_payload(job_id, include_ready=include_ready)


def create_job_row(conn, video_id, learner_level, source_policy, force_refresh, job_kind="bubble_analysis"):
    job_id = short_hash_id("prepare", video_id, learner_level, job_kind, time.time_ns(), ANALYSIS_VERSION)
    timestamp = now_iso()
    conn.execute(
        "insert into videos (video_id, created_at, updated_at) values (?, ?, ?) on conflict(video_id) do update set updated_at = excluded.updated_at",
        (video_id, timestamp, timestamp),
    )
    conn.execute(
        "insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, force_refresh, job_kind, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, video_id, learner_level, source_policy, "queued", "queued", int(force_refresh), job_kind, timestamp, timestamp),
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
