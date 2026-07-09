from pathlib import Path
import shutil
import threading

from config import *
from db import connect_db
from job_events import add_preparation_event
from media import ExternalCommandError, create_chunks, download_full_audio, get_youtube_duration, media_duration, merge_transcript_segments, normalize_audio, transcribe_audio_chunk
from preparation_jobs import update_job
from transcripts import store_transcript


ASR_LOCK = threading.Lock()


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


def run_whole_video_asr(job_id, video_id):
    validate_runtime_for_asr()
    with ASR_LOCK:
        update_job(job_id, stage="fetching_metadata", progress=0.05)
        ensure_private_dir(MEDIA_DIR)
        job_media_dir = MEDIA_DIR / job_id
        ensure_private_dir(job_media_dir)
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
