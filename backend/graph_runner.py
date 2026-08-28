from analysis_agents import time_windows
from auth import redact_secret_text
from config import GRAPH_EXTRACTION_MODE
from graph_extraction_agents import extract_graph_for_video
from graph_store import (
    clone_graph_snapshot,
    extraction_job_payload,
    graph_extraction_cache_key,
    latest_ready_extraction_by_cache_key,
    save_nodes_and_edges,
    update_extraction_job,
    upsert_extraction_job,
)
from transcripts import load_transcript


GRAPH_WINDOW_SECONDS = 75


def run_graph_extraction_for_transcript(video_id, transcript_id, job_id, force_refresh=False):
    cache_key = None
    try:
        transcript = load_transcript(transcript_id)
        if not transcript:
            raise FileNotFoundError("transcript not found")
        content_hash = transcript.get("content_hash", "fixture")
        cache_key = graph_extraction_cache_key(video_id, content_hash)
        existing = latest_ready_extraction_by_cache_key(cache_key)
        if existing and not force_refresh:
            clone_graph_snapshot(existing["job_id"], job_id, video_id, transcript_id, cache_key)
            return extraction_job_payload(job_id)

        upsert_extraction_job(job_id, video_id, transcript_id, cache_key, status="processing", stage="extracting_graph")
        segments = transcript.get("segments", [])
        windows = time_windows(segments, seconds=GRAPH_WINDOW_SECONDS)
        nodes, edges, actual_mode = extract_graph_for_video(video_id, windows)
        if actual_mode != GRAPH_EXTRACTION_MODE:
            # The configured LLM mode fell back to heuristic (a validated-empty
            # result, not a provider error) - persist under the signature that
            # actually produced this snapshot, not the one initially checked,
            # so a later request under the same configured mode doesn't
            # mistake this heuristic result for an LLM one.
            cache_key = graph_extraction_cache_key(video_id, content_hash, actual_mode)
        save_nodes_and_edges(job_id, video_id, transcript_id, nodes, edges)
        update_extraction_job(job_id, cache_key=cache_key, status="ready", stage="ready", node_count=len(nodes), edge_count=len(edges), error_code=None, message=None)
        return extraction_job_payload(job_id)
    except Exception as error:
        values = {
            "status": "failed",
            "stage": "failed",
            "error_code": "GRAPH_EXTRACTION_FAILED",
            "message": redact_secret_text(str(error)),
        }
        if cache_key is None:
            update_extraction_job(job_id, **values)
        else:
            upsert_extraction_job(job_id, video_id, transcript_id, cache_key, **values)
        raise
