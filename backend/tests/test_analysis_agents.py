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


if __name__ == "__main__": unittest.main()
