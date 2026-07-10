from analysis_store import run_analysis_for_transcript
from auth import redact_secret_text
from caption_pipeline import transcript_for_job
from db import connect_db
from job_events import add_preparation_event
from media import ExternalCommandError, command_error
from preparation_jobs import finish_preparation_thread, update_job


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

        transcript, source, duration = transcript_for_job(job_id, video_id, source_policy)
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
        finish_preparation_thread(job_id)
