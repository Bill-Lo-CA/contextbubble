import hashlib
import json

from analysis_agents import concept_candidates, reviewer_agent, validate_bubbles
from auth import redact_secret_text
from config import AGENT_MODE, ANALYSIS_VERSION, now_iso
from db import connect_db
from providers import AgentProviderError
from transcripts import load_transcript


ANALYSES = {}


def analysis_result(analysis_id):
    with connect_db() as conn:
        analysis = conn.execute("select * from analyses where analysis_id = ?", (analysis_id,)).fetchone()
        if not analysis:
            return None
        rows = conn.execute(
            "select * from bubbles where analysis_id = ? order by start_seconds, bubble_id",
            (analysis_id,),
        ).fetchall()
    result = {
        "analysis_id": analysis["analysis_id"],
        "status": analysis["status"],
        "stage": analysis["stage"],
        "video_id": analysis["video_id"],
        "learner_level": analysis["learner_level"],
        "error_code": analysis["error_code"],
        "message": analysis["message"],
        "bubbles": [
            {
                "id": row["bubble_id"],
                "concept": row["concept"],
                "anchor_segment_id": row["anchor_segment_id"],
                "source_segment_ids": json.loads(row["source_segment_ids"]),
                "start_seconds": row["start_seconds"],
                "short_explanation": row["short_explanation"],
                "expanded_explanation": row["expanded_explanation"],
                "confidence": row["confidence"],
                "review_status": row["review_status"],
                "review_reason": row["review_reason"],
            }
            for row in rows
        ],
    }
    if analysis["status"] == "completed" and analysis["message"]:
        try:
            result["analysis_metrics"] = json.loads(analysis["message"])
        except json.JSONDecodeError:
            pass
    ANALYSES[analysis_id] = result
    return result

def run_analysis_for_transcript(video_id, learner_level, transcript_id, force_refresh=False):
    transcript = load_transcript(transcript_id)
    if not transcript:
        raise FileNotFoundError("transcript not found")
    content_hash = transcript.get("content_hash", "fixture")
    cache_key = f"{video_id}:{content_hash}:{learner_level}:{ANALYSIS_VERSION}"
    analysis_id = f"analysis-{hashlib.sha256(cache_key.encode()).hexdigest()[:12]}"
    existing = analysis_result(analysis_id)
    if existing and existing["status"] == "completed" and not force_refresh:
        return existing

    timestamp = now_iso()
    with connect_db() as conn:
        conn.execute(
            """insert into analyses (analysis_id, video_id, learner_level, transcript_id, cache_key, status, stage, error_code, message, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(analysis_id) do update set status=excluded.status, stage=excluded.stage, error_code=excluded.error_code, message=excluded.message, updated_at=excluded.updated_at""",
            (analysis_id, video_id, learner_level, transcript_id, cache_key, "processing", "concept_agent", None, None, timestamp, timestamp),
        )
        conn.execute("delete from bubbles where analysis_id = ?", (analysis_id,))

    try:
        segments = transcript.get("segments", [])
        candidates, metrics = concept_candidates(segments, learner_level)
        with connect_db() as conn:
            conn.execute("update analyses set stage = ?, updated_at = ? where analysis_id = ?", ("reviewing", now_iso(), analysis_id))
        reviewed = [reviewer_agent(candidate, segments, learner_level) for candidate in candidates]
        with connect_db() as conn:
            conn.execute("update analyses set stage = ?, updated_at = ? where analysis_id = ?", ("validating", now_iso(), analysis_id))
        bubbles = validate_bubbles(reviewed, segments)
        metrics["accepted_bubble_count"] = len(bubbles)
        result = {
            "analysis_id": analysis_id,
            "status": "completed",
            "stage": "ready",
            "video_id": video_id,
            "learner_level": learner_level,
            "bubbles": bubbles,
            "analysis_metrics": metrics,
        }
        with connect_db() as conn:
            conn.execute(
                "update analyses set status = ?, stage = ?, error_code = null, message = ?, updated_at = ? where analysis_id = ?",
                ("completed", "ready", json.dumps(metrics), now_iso(), analysis_id),
            )
            conn.executemany(
                "insert into bubbles (analysis_id, bubble_id, concept, anchor_segment_id, source_segment_ids, start_seconds, short_explanation, expanded_explanation, confidence, review_status, review_reason) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        analysis_id,
                        bubble["id"],
                        bubble["concept"],
                        bubble["anchor_segment_id"],
                        json.dumps(bubble["source_segment_ids"]),
                        bubble["start_seconds"],
                        bubble["short_explanation"],
                        bubble["expanded_explanation"],
                        bubble["confidence"],
                        bubble["review_status"],
                        bubble.get("review_reason", ""),
                    )
                    for bubble in bubbles
                ],
            )
        ANALYSES[analysis_id] = result
        return result
    except AgentProviderError as error:
        with connect_db() as conn:
            conn.execute(
                "update analyses set status = ?, stage = ?, error_code = ?, message = ?, updated_at = ? where analysis_id = ?",
                ("failed", "failed", error.error_code, redact_secret_text(str(error)), now_iso(), analysis_id),
            )
        raise
    except Exception as error:
        with connect_db() as conn:
            conn.execute(
                "update analyses set status = ?, stage = ?, error_code = ?, message = ?, updated_at = ? where analysis_id = ?",
                ("failed", "failed", "GEMINI_UNAVAILABLE" if AGENT_MODE == "gemini" else "ANALYSIS_FAILED", redact_secret_text(str(error)), now_iso(), analysis_id),
            )
        raise
