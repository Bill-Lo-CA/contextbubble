import hashlib
import json
from pathlib import Path
import shutil
import threading
import time

from agents import analysis_result, run_analysis_for_transcript
from auth import redact_secret_text
from config import *
from db import connect_db
from media import ExternalCommandError, command_error, create_chunks, download_full_audio, fetch_youtube_subtitles, get_youtube_duration, media_duration, merge_transcript_segments, normalize_audio, transcribe_audio_chunk
from transcript_quality import asr_tools_available, caption_source_qc, route_transcript_source
from transcripts import load_transcript, sentence_entries, store_transcript


STATE_LOCK = threading.Lock()
ASR_LOCK = threading.Lock()
ACTIVE_PREPARATIONS = set()


def add_preparation_event(job_id, event_type, stage=None, metadata=None):
    safe_metadata = json.loads(redact_secret_text(json.dumps(metadata or {}, sort_keys=True)))
    with connect_db() as conn:
        conn.execute(
            "insert into preparation_events (job_id, event_type, stage, metadata, created_at) values (?, ?, ?, ?, ?)",
            (job_id, event_type, stage, json.dumps(safe_metadata, sort_keys=True), now_iso()),
        )


def preparation_events(job_id):
    with connect_db() as conn:
        rows = conn.execute(
            "select * from preparation_events where job_id = ? order by created_at, event_id",
            (job_id,),
        ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "job_id": row["job_id"],
            "event_type": row["event_type"],
            "stage": row["stage"],
            "metadata": json.loads(row["metadata"] or "{}"),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


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


def mark_asr_chunk_processing(job_id, chunk_index):
    with connect_db() as conn:
        conn.execute(
            """
            update asr_chunks
            set status = ?, attempt_count = attempt_count + 1, error_code = null, updated_at = ?
            where job_id = ? and chunk_index = ?
            """,
            ("processing", now_iso(), job_id, chunk_index),
        )


def mark_asr_chunk_completed(job_id, chunk_index, segments):
    with connect_db() as conn:
        conn.execute("delete from asr_chunk_segments where job_id = ? and chunk_index = ?", (job_id, chunk_index))
        conn.executemany(
            "insert into asr_chunk_segments values (?, ?, ?, ?, ?, ?)",
            [
                (job_id, chunk_index, index, segment["start_seconds"], segment["end_seconds"], segment["text"])
                for index, segment in enumerate(segments)
            ],
        )
        conn.execute(
            """
            update asr_chunks
            set status = ?, segment_count = ?, error_code = null, updated_at = ?
            where job_id = ? and chunk_index = ?
            """,
            ("completed", len(segments), now_iso(), job_id, chunk_index),
        )
        return conn.execute(
            "select count(*) as count from asr_chunks where job_id = ? and status = 'completed'",
            (job_id,),
        ).fetchone()["count"]


def mark_asr_chunk_failed(job_id, chunk_index, error_code):
    with connect_db() as conn:
        conn.execute(
            """
            update asr_chunks
            set status = ?, error_code = ?, updated_at = ?
            where job_id = ? and chunk_index = ?
            """,
            ("failed", error_code, now_iso(), job_id, chunk_index),
        )


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
    if include_transcript and segments:
        payload["segments"] = segments
    if include_sentence_entries and segments:
        payload["sentence_entries"] = sentence_entries(segments)
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
                start_preparation_thread(existing["job_id"])
                return job_payload(existing["job_id"], include_ready=existing["status"] == "ready")

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
    add_preparation_event(job_id, "job_queued", "queued", {"source_policy": source_policy})
    start_preparation_thread(job_id)
    return job_payload(job_id, include_ready=False)


def start_preparation_thread(job_id):
    with STATE_LOCK:
        if job_id in ACTIVE_PREPARATIONS:
            return
        ACTIVE_PREPARATIONS.add(job_id)
    threading.Thread(target=run_preparation_job, args=(job_id,), daemon=True).start()


def run_preparation_job(job_id):
    try:
        with connect_db() as conn:
            job = conn.execute("select * from preparation_jobs where job_id = ?", (job_id,)).fetchone()
        if not job or job["status"] == "ready":
            return
        video_id = job["video_id"]
        learner_level = job["learner_level"]
        force_refresh = bool(job["force_refresh"])
        source_policy = job["source_policy"]
        update_job(job_id, status="processing", stage="fetching_captions", progress=0.02)
        add_preparation_event(job_id, "captions_attempt_started", "fetching_captions")

        try:
            filename, content, segments = fetch_youtube_subtitles(video_id, job_id)
            duration = segments[-1]["end_seconds"] if segments else None
            caption_kind = "auto" if "auto" in filename.lower() else "unknown"
            caption_transcript = store_transcript(video_id, filename, content, "youtube_caption", segments, {
                "caption_kind": caption_kind,
                "caption_language": "en",
                "provisional": True,
            })
            update_job(job_id, stage="caption_available", transcript_id=caption_transcript["transcript_id"], transcript_source="youtube_caption", duration_seconds=duration, progress=0.18)
            add_preparation_event(job_id, "captions_found", "caption_available", {"segment_count": len(segments), "transcript_id": caption_transcript["transcript_id"]})
            qc_result = caption_source_qc(segments, None, caption_kind)
            add_preparation_event(job_id, "caption_qc_completed", "caption_available", qc_result)
            if qc_result["source_quality"] == "good":
                route = {"decision": "use_cc", "reason": "Caption QC passed.", "windows_to_check": [], "confidence": 0.92}
            else:
                route = route_transcript_source(
                    video_id,
                    duration,
                    "youtube_caption",
                    "en",
                    caption_kind,
                    qc_result,
                    segments[:3],
                    asr_tools_available(),
                )
            add_preparation_event(job_id, "transcript_route_selected", "caption_available", route)
            if route["decision"] == "run_whole_video_whisper":
                add_preparation_event(job_id, "asr_triggered_by_caption_quality", "caption_available", {"issues": qc_result["issues"]})
                transcript, source, duration = run_whole_video_asr(job_id, video_id)
            else:
                source = "youtube_caption" if route["decision"] == "use_cc" else "youtube_caption_with_warnings"
                transcript = store_transcript(video_id, filename, content, source, segments, {
                    "caption_qc": qc_result,
                    "routing_decision": route,
                    "caption_kind": caption_kind,
                    "caption_language": "en",
                })
        except FileNotFoundError:
            add_preparation_event(job_id, "captions_unavailable", "fetching_captions")
            transcript, source, duration = fallback_transcript_for_missing_captions(job_id, video_id, source_policy)
        except ExternalCommandError as error:
            add_preparation_event(job_id, "captions_command_failed", "fetching_captions", {"error_code": error.error_code})
            transcript, source, duration = fallback_transcript_for_missing_captions(job_id, video_id, source_policy)
        except Exception:
            add_preparation_event(job_id, "unexpected_caption_pipeline_failure", "fetching_captions")
            raise

        update_job(
            job_id,
            stage="concept_agent",
            transcript_id=transcript["transcript_id"],
            transcript_source=source,
            duration_seconds=duration,
            progress=0.92,
        )
        add_preparation_event(job_id, "analysis_started", "concept_agent")
        analysis = run_analysis_for_transcript(video_id, learner_level, transcript["transcript_id"], force_refresh)
        add_preparation_event(job_id, "analysis_completed", "ready", {"bubble_count": len(analysis.get("bubbles", []))})
        update_job(job_id, status="ready", stage="ready", analysis_id=analysis["analysis_id"], progress=1.0, message=None, error_code=None)
        add_preparation_event(job_id, "job_ready", "ready")
    except FileNotFoundError as error:
        update_job(job_id, status="failed", stage="failed", error_code=str(error), message=str(error))
        add_preparation_event(job_id, "job_failed", "failed", {"error_code": str(error)})
    except ExternalCommandError as error:
        prefix = "External tool timed out" if error.timeout else "External tool failed"
        update_job(job_id, status="failed", stage="failed", error_code=error.error_code, message=command_error(prefix, error))
        add_preparation_event(job_id, "job_failed", "failed", {"error_code": error.error_code})
    except Exception as error:
        update_job(job_id, status="failed", stage="failed", error_code="PREPARATION_FAILED", message=redact_secret_text(str(error)))
        add_preparation_event(job_id, "job_failed", "failed", {"error_code": "PREPARATION_FAILED"})
    finally:
        with STATE_LOCK:
            ACTIVE_PREPARATIONS.discard(job_id)


def fallback_transcript_for_missing_captions(job_id, video_id, source_policy):
    if source_policy == "demo" or video_id in DEMO_VIDEO_IDS:
        update_job(job_id, stage="loading_demo", progress=0.1)
        fixture = demo_fixture_path(video_id)
        with open(fixture, encoding="utf-8") as file:
            content = file.read()
        transcript = store_transcript(video_id, fixture.name, content, "demo_fixture", metadata={"provenance": "demo_fixture"})
        source = "demo_fixture"
        duration = load_transcript(transcript["transcript_id"])["segments"][-1]["end_seconds"]
        add_preparation_event(job_id, "demo_fixture_selected", "loading_demo")
        return transcript, source, duration
    add_preparation_event(job_id, "asr_fallback_started", "fetching_metadata")
    return run_whole_video_asr(job_id, video_id)


def run_whole_video_asr(job_id, video_id):
    validate_runtime_for_asr()
    with ASR_LOCK:
        update_job(job_id, stage="fetching_metadata", progress=0.05)
        MEDIA_DIR.mkdir(exist_ok=True)
        job_media_dir = MEDIA_DIR / job_id
        job_media_dir.mkdir(exist_ok=True)
        try:
            duration = get_youtube_duration(video_id, job_id)
        except (RuntimeError, ExternalCommandError):
            add_preparation_event(job_id, "metadata_unavailable_fallback_attempted", "fetching_metadata")
            duration = None
        update_job(job_id, stage="downloading_audio", duration_seconds=duration, progress=0.1)
        raw_audio = next((str(path) for path in job_media_dir.glob("*.wav") if path.name != "audio-16k-mono.wav" and not path.name.startswith("chunk-")), "")
        if not raw_audio:
            raw_audio = download_full_audio(video_id, str(job_media_dir), job_id)
        add_preparation_event(job_id, "audio_downloaded", "downloading_audio")
        update_job(job_id, stage="normalizing_audio", progress=0.18)
        normalized_audio = str(job_media_dir / "audio-16k-mono.wav")
        if not Path(normalized_audio).exists():
            normalize_audio(raw_audio, normalized_audio, job_id)
        if not duration:
            duration = media_duration(normalized_audio, job_id)

        chunks = create_chunks(duration)
        timestamp = now_iso()
        with connect_db() as conn:
            conn.executemany(
                "insert or ignore into asr_chunks values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (job_id, chunk["chunk_index"], chunk["start_seconds"], chunk["end_seconds"], "pending", 0, 0, None, timestamp)
                    for chunk in chunks
                ],
            )
            conn.execute(
                "update asr_chunks set status = ?, error_code = ?, updated_at = ? where job_id = ? and status = ?",
                ("pending", "STALE_PROCESSING_RESET", timestamp, job_id, "processing"),
            )
            completed = conn.execute(
                "select count(*) as count from asr_chunks where job_id = ? and status = 'completed'",
                (job_id,),
            ).fetchone()["count"]
        update_job(job_id, stage="transcribing", chunks_total=len(chunks), chunks_completed=completed, progress=0.2)

        all_segments = load_asr_chunk_segments(job_id)
        for chunk in chunks:
            with connect_db() as conn:
                row = conn.execute(
                    "select * from asr_chunks where job_id = ? and chunk_index = ?",
                    (job_id, chunk["chunk_index"]),
                ).fetchone()
            if row and row["status"] == "completed":
                continue
            mark_asr_chunk_processing(job_id, chunk["chunk_index"])
            update_job(job_id, stage="transcribing", progress=0.2 + 0.55 * (completed / max(1, len(chunks))))
            add_preparation_event(job_id, "chunk_started", "transcribing", {"chunk_index": chunk["chunk_index"]})
            try:
                segments = transcribe_audio_chunk(normalized_audio, chunk, str(job_media_dir), job_id)
            except ExternalCommandError as error:
                mark_asr_chunk_failed(job_id, chunk["chunk_index"], error.error_code)
                add_preparation_event(job_id, "chunk_failed", "transcribing", {"chunk_index": chunk["chunk_index"], "error_code": error.error_code})
                raise
            except Exception:
                mark_asr_chunk_failed(job_id, chunk["chunk_index"], "ASR_CHUNK_FAILED")
                add_preparation_event(job_id, "chunk_failed", "transcribing", {"chunk_index": chunk["chunk_index"], "error_code": "ASR_CHUNK_FAILED"})
                raise
            all_segments.extend(segments)
            completed = mark_asr_chunk_completed(job_id, chunk["chunk_index"], segments)
            add_preparation_event(job_id, "chunk_completed", "transcribing", {"chunk_index": chunk["chunk_index"], "segment_count": len(segments)})
            update_job(job_id, chunks_completed=completed, progress=0.2 + 0.55 * (completed / max(1, len(chunks))))

        update_job(job_id, stage="merging_transcript", progress=0.82)
        merged = merge_transcript_segments(all_segments, duration)
        if not merged:
            raise RuntimeError("TRANSCRIPT_MERGE_FAILED")
        transcript = store_transcript(video_id, f"{video_id}.whole-video.whisper.vtt", source="whisper_asr", segments=merged, metadata={"provenance": "whisper_asr"})
        add_preparation_event(job_id, "transcript_merged", "merging_transcript", {"segment_count": len(merged)})
        shutil.rmtree(job_media_dir, ignore_errors=True)
        return transcript, "whisper", duration


def load_asr_chunk_segments(job_id):
    with connect_db() as conn:
        rows = conn.execute(
            """
            select * from asr_chunk_segments
            where job_id = ?
            order by chunk_index, segment_index
            """,
            (job_id,),
        ).fetchall()
    return [
        {
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "text": row["text"],
        }
        for row in rows
    ]


def resume_preparations():
    with connect_db() as conn:
        rows = conn.execute("select job_id from preparation_jobs where status in ('queued', 'processing')").fetchall()
    for row in rows:
        start_preparation_thread(row["job_id"])
