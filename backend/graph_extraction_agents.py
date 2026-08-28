import hashlib


# Relation types whose (source, target) pair order carries no meaning - kept
# in sync with the set used to seed/validate LLM-classified relations later
# in this module. "related_to" is the only member today (heuristic path).
UNDIRECTED_RELATION_TYPES = {"related_to"}


KEYWORDS = (
    "embedding", "embeddings", "cosine similarity", "retrieval augmented generation",
    "retrieval", "generation", "vector database", "vector", "vectors", "transcript",
    "model", "reviewer", "learner level",
)


def node_id_for(video_id, canonical_name):
    digest = hashlib.sha256(f"{video_id}:{canonical_name.lower()}".encode()).hexdigest()
    return f"node-{digest[:12]}"


def edge_id_for(source_node_id, target_node_id, relation_type):
    # Any undirected relation type must normalize pair order the same way the
    # DB's idx_kg_edges_undirected_unique index does (min/max), not just the
    # single literal "related_to" - otherwise two edges classified in opposite
    # orders get different edge_ids in memory but collide on that unique index.
    if relation_type in UNDIRECTED_RELATION_TYPES:
        source_node_id, target_node_id = sorted((source_node_id, target_node_id))
    digest = hashlib.sha256(f"{source_node_id}:{target_node_id}:{relation_type}".encode()).hexdigest()
    return f"edge-{digest[:12]}"


def heuristic_window_candidates(video_id, window):
    candidates_by_concept = {}
    for segment in window:
        text = segment["text"]
        lowered = text.lower()
        concept = next((keyword for keyword in KEYWORDS if keyword in lowered), "")
        if not concept and len(text.split()) >= 4:
            concept = " ".join(text.split()[:3]).strip(".,:;!?").lower()
        if not concept:
            continue
        source = {
            "source_id": segment["id"],
            "segment_ids": [segment["id"]],
            "start_seconds": segment["start_seconds"],
            "end_seconds": segment["end_seconds"],
            "evidence_text": text,
        }
        existing = candidates_by_concept.get(concept)
        if existing:
            # Same concept recurring within one window - keep every occurrence's
            # evidence/timestamp instead of dropping all but the first (heuristic_extract_graph
            # already does this same merge across windows; this mirrors it within a window).
            existing["sources"].append(source)
        else:
            candidates_by_concept[concept] = {
                "node_id": node_id_for(video_id, concept),
                "canonical_name": concept,
                "node_type": "concept",
                "short_summary": text[:160],
                "confidence": 0.6,
                "sources": [source],
            }
    return list(candidates_by_concept.values())


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
            source_node_id, target_node_id = sorted((previous["node_id"], current["node_id"]))
            edge_id = edge_id_for(source_node_id, target_node_id, "related_to")
            edges_by_key[edge_id] = {
                "edge_id": edge_id,
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "relation_type": "related_to",
                "directional": 0,
                "confidence": 0.5,
                "evidence_source_ids": [],
            }
    return list(nodes_by_id.values()), list(edges_by_key.values())
