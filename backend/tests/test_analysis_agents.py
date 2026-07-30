from pathlib import Path
import sys
import unittest
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path: sys.path.insert(0, str(BACKEND_DIR))

import analysis_agents


class AnalysisAgentTests(unittest.TestCase):
    def test_empty_llm_candidates_fall_back_to_heuristic(self):
        window = [{"id": "segment-1", "start_seconds": 0, "end_seconds": 5, "text": "The Supreme Leader funeral became a display of regime power."}]
        with mock.patch.object(analysis_agents, "AGENT_MODE", "ollama"), mock.patch.object(analysis_agents, "llm_concept_agent", return_value=[]):
            self.assertTrue(analysis_agents.window_note(window, "beginner")["candidate_concepts"])

    def test_validate_bubbles_uses_absolute_spacing_and_chronological_ids(self):
        def candidate(concept, start_seconds, confidence):
            return {
                "concept": concept,
                "anchor_segment_id": f"segment-{concept}",
                "source_segment_ids": [f"segment-{concept}"],
                "start_seconds": start_seconds,
                "short_explanation": "Brief explanation.",
                "expanded_explanation": "Expanded explanation.",
                "confidence": confidence,
                "review_status": "accepted",
            }

        reviewed = [
            candidate("late", 100, 0.99),
            candidate("boundary", 70, 0.98),
            candidate("early", 10, 0.97),
            candidate("too-close", 71, 0.96),
            candidate("later", 130, 0.95),
        ]
        segments = [
            {"id": candidate["anchor_segment_id"], "start_seconds": candidate["start_seconds"], "end_seconds": candidate["start_seconds"] + 1, "text": candidate["concept"]}
            for candidate in reviewed
        ]

        bubbles = analysis_agents.validate_bubbles(reviewed, segments)

        self.assertEqual(
            [(bubble["id"], bubble["concept"], bubble["start_seconds"]) for bubble in bubbles],
            [("bubble-001", "early", 10), ("bubble-002", "boundary", 70), ("bubble-003", "late", 100), ("bubble-004", "later", 130)],
        )


if __name__ == "__main__": unittest.main()
