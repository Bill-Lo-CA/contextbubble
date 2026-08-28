from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph_extraction_agents import heuristic_window_candidates


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


if __name__ == "__main__":
    unittest.main()
