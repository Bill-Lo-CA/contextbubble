from asr_pipeline import run_whole_video_asr
from config import *
from job_events import add_preparation_event
from media import ExternalCommandError, fetch_youtube_subtitles
from preparation_jobs import update_job
from transcript_quality import asr_tools_available, caption_source_qc, route_transcript_source
from transcripts import load_transcript, store_transcript


def transcript_for_job(job_id, video_id, source_policy):
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
            return run_whole_video_asr(job_id, video_id)
        source = "youtube_caption" if route["decision"] == "use_cc" else "youtube_caption_with_warnings"
        transcript = store_transcript(video_id, filename, content, source, segments, {
            "caption_qc": qc_result,
            "routing_decision": route,
            "caption_kind": caption_kind,
            "caption_language": "en",
        })
        return transcript, source, duration
    except FileNotFoundError:
        add_preparation_event(job_id, "captions_unavailable", "fetching_captions")
        return fallback_transcript_for_missing_captions(job_id, video_id, source_policy)
    except ExternalCommandError as error:
        add_preparation_event(job_id, "captions_command_failed", "fetching_captions", {"error_code": error.error_code})
        return fallback_transcript_for_missing_captions(job_id, video_id, source_policy)
    except Exception:
        add_preparation_event(job_id, "unexpected_caption_pipeline_failure", "fetching_captions")
        raise


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
