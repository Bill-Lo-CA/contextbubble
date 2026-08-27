from analysis_agents import time_windows
from graph_extraction_agents import heuristic_extract_graph
from graph_store import (
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
    transcript = load_transcript(transcript_id)
    if not transcript:
        raise FileNotFoundError("transcript not found")
    content_hash = transcript.get("content_hash", "fixture")
    cache_key = graph_extraction_cache_key(video_id, content_hash)

    existing = latest_ready_extraction_by_cache_key(cache_key)
    if existing and not force_refresh:
        upsert_extraction_job(
            job_id, video_id, transcript_id, cache_key,
            status="ready", stage="ready", node_count=existing["node_count"], edge_count=existing["edge_count"],
        )
        return extraction_job_payload(job_id)

    upsert_extraction_job(job_id, video_id, transcript_id, cache_key, status="processing", stage="extracting_graph")
    segments = transcript.get("segments", [])
    windows = time_windows(segments, seconds=GRAPH_WINDOW_SECONDS)
    nodes, edges = heuristic_extract_graph(video_id, windows)
    save_nodes_and_edges(job_id, video_id, transcript_id, nodes, edges)
    update_extraction_job(job_id, status="ready", stage="ready", node_count=len(nodes), edge_count=len(edges), error_code=None, message=None)
    return extraction_job_payload(job_id)
