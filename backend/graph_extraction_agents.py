from db import short_hash_id


KEYWORDS = (
    "embedding", "embeddings", "cosine similarity", "retrieval augmented generation",
    "retrieval", "generation", "vector database", "vector", "vectors", "transcript",
    "model", "reviewer", "learner level",
)


def node_id_for(video_id, canonical_name):
    return short_hash_id("node", video_id, canonical_name.lower())


def edge_id_for(source_node_id, target_node_id, relation_type):
    return short_hash_id("edge", source_node_id, target_node_id, relation_type)


def heuristic_window_candidates(video_id, window):
    candidates = []
    used = set()
    for segment in window:
        text = segment["text"]
        lowered = text.lower()
        concept = next((keyword for keyword in KEYWORDS if keyword in lowered), "")
        if not concept and len(text.split()) >= 4:
            concept = " ".join(text.split()[:3]).strip(".,:;!?").lower()
        if not concept or concept in used:
            continue
        used.add(concept)
        candidates.append({
            "node_id": node_id_for(video_id, concept),
            "canonical_name": concept,
            "node_type": "concept",
            "short_summary": text[:160],
            "confidence": 0.6,
            "sources": [{
                "source_id": segment["id"],
                "segment_ids": [segment["id"]],
                "start_seconds": segment["start_seconds"],
                "end_seconds": segment["end_seconds"],
                "evidence_text": text,
            }],
        })
    return candidates


def heuristic_extract_graph(video_id, windows):
    """Deterministic, non-LLM placeholder extractor.

    Exists so the graph job lifecycle (create/run/persist/resume) is testable
    end-to-end before the real LLM candidate-extraction/relation-classification
    agents (schemas #1/#2) land in a follow-up change.
    """
    nodes_by_id = {}
    edges_by_key = {}
    for window in windows:
        window_candidates = heuristic_window_candidates(video_id, window)
        for candidate in window_candidates:
            existing = nodes_by_id.get(candidate["node_id"])
            if existing:
                existing["sources"].extend(candidate["sources"])
            else:
                nodes_by_id[candidate["node_id"]] = candidate
        for previous, current in zip(window_candidates, window_candidates[1:]):
            if previous["node_id"] == current["node_id"]:
                continue
            edge_id = edge_id_for(previous["node_id"], current["node_id"], "related_to")
            edges_by_key[edge_id] = {
                "edge_id": edge_id,
                "source_node_id": previous["node_id"],
                "target_node_id": current["node_id"],
                "relation_type": "related_to",
                "confidence": 0.5,
                "evidence_source_ids": [],
            }
    return list(nodes_by_id.values()), list(edges_by_key.values())
