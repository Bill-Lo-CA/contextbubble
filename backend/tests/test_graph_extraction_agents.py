from pathlib import Path
import sys
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import graph_extraction_agents
from graph_extraction_agents import edge_id_for, heuristic_window_candidates


def segment(segment_id, text, start, end):
    return {"id": segment_id, "text": text, "start_seconds": start, "end_seconds": end}


class HeuristicWindowCandidatesTests(unittest.TestCase):
    def test_repeated_concept_within_a_window_keeps_every_occurrences_evidence(self):
        window = [
            segment("segment-001", "Embedding models turn text into vectors.", 0, 5),
            segment("segment-002", "Unrelated filler sentence goes here now.", 5, 10),
            segment("segment-003", "The embedding step runs before retrieval.", 10, 15),
        ]
        candidates = heuristic_window_candidates("video-1", window)
        embedding_candidate = next(c for c in candidates if c["canonical_name"] == "embedding")
        self.assertEqual(
            [source["source_id"] for source in embedding_candidate["sources"]],
            ["segment-001", "segment-003"],
        )

    def test_distinct_concepts_in_one_window_stay_separate(self):
        window = [
            segment("segment-001", "Embedding models turn text into vectors.", 0, 5),
            segment("segment-002", "Retrieval augmented generation retrieves context.", 5, 10),
        ]
        candidates = heuristic_window_candidates("video-1", window)
        self.assertEqual(
            sorted(c["canonical_name"] for c in candidates),
            ["embedding", "retrieval augmented generation"],
        )


class EdgeIdForUndirectedNormalizationTests(unittest.TestCase):
    def test_any_undirected_relation_type_normalizes_pair_order_not_just_related_to(self):
        # Regression test for a bug where edge_id_for only special-cased the literal
        # string "related_to" for undirected sorting, while the DB's
        # idx_kg_edges_undirected_unique index normalizes *any* directional=0 row via
        # min/max - a second undirected type classified in the opposite order would
        # get a different edge_id in memory (no in-process dedupe) but collide on
        # that DB index. Patching the set (rather than waiting for a real second
        # undirected type to be wired in) proves the fix checks set membership.
        with mock.patch.object(graph_extraction_agents, "UNDIRECTED_RELATION_TYPES", {"related_to", "contrasts_with"}):
            self.assertEqual(
                edge_id_for("node-a", "node-b", "contrasts_with"),
                edge_id_for("node-b", "node-a", "contrasts_with"),
            )

    def test_directed_relation_type_is_not_normalized(self):
        self.assertNotEqual(
            edge_id_for("node-a", "node-b", "prerequisite_for"),
            edge_id_for("node-b", "node-a", "prerequisite_for"),
        )


if __name__ == "__main__":
    unittest.main()
