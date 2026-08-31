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


class ValidReviewerResultTests(unittest.TestCase):
    def test_revised_status_requires_a_candidate_object(self):
        cases = (
            ({"review_status": "revised", "review_reason": "r", "candidate": {"concept": "x"}}, True),
            ({"review_status": "revised", "review_reason": "r"}, False),
            ({"review_status": "revised", "review_reason": "r", "candidate": "not-a-dict"}, False),
            ({"review_status": "accepted", "review_reason": "r"}, True),
            ({"review_status": "rejected", "review_reason": "r"}, True),
        )
        for result, expected in cases:
            with self.subTest(result=result):
                self.assertEqual(analysis_agents.valid_reviewer_result(result), expected)


class TimeWindowsTests(unittest.TestCase):
    def test_explicit_seconds_buckets_correctly(self):
        segments = [
            {"id": "s1", "start_seconds": 0, "end_seconds": 10, "text": "a"},
            {"id": "s2", "start_seconds": 50, "end_seconds": 60, "text": "b"},
            {"id": "s3", "start_seconds": 100, "end_seconds": 110, "text": "c"},
        ]
        windows = analysis_agents.time_windows(segments, seconds=60)
        self.assertEqual([[segment["id"] for segment in window] for window in windows], [["s1", "s2"], ["s3"]])


class ConceptCandidatesWindowSecondsTests(unittest.TestCase):
    def test_window_seconds_is_forwarded_to_time_windows(self):
        segments = [{"id": "s1", "start_seconds": 0, "end_seconds": 5, "text": "t"}]
        with mock.patch.object(analysis_agents, "time_windows", return_value=[]) as time_windows_mock:
            analysis_agents.concept_candidates(segments, "beginner", 45)
        time_windows_mock.assert_called_once_with(segments, seconds=45)


class SchemaForwardingTests(unittest.TestCase):
    def test_llm_concept_agent_forwards_concept_schema(self):
        fake_provider = mock.Mock()
        fake_provider.generate_json.return_value = {"bubbles": []}
        window = [{"id": "segment-1", "start_seconds": 0, "end_seconds": 5, "text": "t"}]
        with mock.patch.object(analysis_agents, "resolve_provider", return_value=fake_provider):
            analysis_agents.llm_concept_agent(window, "beginner")
        self.assertEqual(fake_provider.generate_json.call_args.kwargs["schema"], analysis_agents.CONCEPT_SCHEMA)

    def test_revised_without_candidate_is_rejected_not_silently_accepted(self):
        fake_provider = mock.Mock()
        fake_provider.generate_json.return_value = {"review_status": "revised", "review_reason": "needs a fix"}
        candidate = {
            "concept": "x", "anchor_segment_id": "segment-1", "source_segment_ids": ["segment-1"],
            "start_seconds": 0, "short_explanation": "s", "expanded_explanation": "e", "confidence": 0.5,
        }
        segments = [{"id": "segment-1", "start_seconds": 0, "end_seconds": 5, "text": "t"}]
        with mock.patch.object(analysis_agents, "resolve_provider", return_value=fake_provider):
            reviewed = analysis_agents.llm_reviewer_agent(candidate, segments, "beginner")
        self.assertEqual(reviewed["review_status"], "rejected")
        self.assertEqual(reviewed["concept"], "x")

    def test_llm_reviewer_agent_forwards_review_schema(self):
        fake_provider = mock.Mock()
        fake_provider.generate_json.return_value = {"review_status": "accepted", "review_reason": "ok"}
        candidate = {
            "concept": "x", "anchor_segment_id": "segment-1", "source_segment_ids": ["segment-1"],
            "start_seconds": 0, "short_explanation": "s", "expanded_explanation": "e", "confidence": 0.5,
        }
        segments = [{"id": "segment-1", "start_seconds": 0, "end_seconds": 5, "text": "t"}]
        with mock.patch.object(analysis_agents, "resolve_provider", return_value=fake_provider):
            analysis_agents.llm_reviewer_agent(candidate, segments, "beginner")
        self.assertEqual(fake_provider.generate_json.call_args.kwargs["schema"], analysis_agents.REVIEW_SCHEMA)


if __name__ == "__main__": unittest.main()
