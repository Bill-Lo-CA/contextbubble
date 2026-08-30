from pathlib import Path
import sys
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import graph_extraction_agents
from graph_extraction_agents import edge_id_for, heuristic_window_candidates, resolve_relation_type, valid_node_candidate
from providers import AgentProviderError


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


class ValidNodeCandidateTests(unittest.TestCase):
    def base_candidate(self, **overrides):
        candidate = {
            "canonical_name": "SQL injection", "node_type": "vulnerability", "short_summary": "Injecting SQL via input.",
            "confidence": 0.8, "evidence_segment_ids": ["segment-001"],
        }
        candidate.update(overrides)
        return candidate

    def test_valid_candidate_passes(self):
        self.assertTrue(valid_node_candidate(self.base_candidate(), {"segment-001"}))

    def test_rejection_cases(self):
        cases = {
            "missing evidence": self.base_candidate(evidence_segment_ids=[]),
            "evidence outside window": self.base_candidate(evidence_segment_ids=["segment-999"]),
            "unknown node_type": self.base_candidate(node_type="not-a-type"),
            "confidence too high": self.base_candidate(confidence=1.5),
            "confidence too low": self.base_candidate(confidence=-0.1),
            "boolean confidence": self.base_candidate(confidence=True),
            "empty canonical_name": self.base_candidate(canonical_name="   "),
            "name too many words": self.base_candidate(canonical_name="a b c d e f g"),
            "name too many characters": self.base_candidate(canonical_name="x" * (graph_extraction_agents.MAX_CANONICAL_NAME_CHARS + 1)),
            "blank summary": self.base_candidate(short_summary="   "),
            "summary too long": self.base_candidate(short_summary=" ".join(["word"] * 41)),
            "summary too many characters": self.base_candidate(short_summary="x" * (graph_extraction_agents.MAX_NODE_SUMMARY_CHARS + 1)),
            "not a dict": "not a dict",
        }
        for label, candidate in cases.items():
            with self.subTest(label):
                self.assertFalse(valid_node_candidate(candidate, {"segment-001"}))


class ResolveRelationTypeTests(unittest.TestCase):
    def test_cases(self):
        cases = {
            "fixed vocabulary": ("causes", ("causes", "accepted")),
            "redundantly proposed fixed vocabulary": ("propose:causes", ("causes", "accepted")),
            "valid propose slug": ("propose:influences", ("influences", "proposed")),
            "propose uppercase rejected": ("propose:Influences", None),
            "propose missing colon": ("proposeinfluences", None),
            "propose empty slug": ("propose:", None),
            "unrecognized non-propose string": ("made_up_type", None),
            "not a string": (42, None),
        }
        for label, (raw, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(resolve_relation_type(raw), expected)


class LlmNodeCandidateAgentTests(unittest.TestCase):
    def test_caps_at_eight_nodes_in_deterministic_confidence_order(self):
        window = [segment("segment-001", "t", 0, 5)]
        candidates = [
            {
                "canonical_name": f"concept-{index}", "node_type": "concept", "short_summary": "s",
                "confidence": index / 10, "evidence_segment_ids": ["segment-001"],
            }
            for index in range(10)
        ]
        with mock.patch.object(graph_extraction_agents, "llm_generate", return_value={"nodes": candidates}):
            result = graph_extraction_agents.llm_node_candidate_agent(window, [])
        self.assertEqual(len(result), graph_extraction_agents.MAX_NODES_PER_WINDOW)
        self.assertEqual([c["canonical_name"] for c in result], [f"concept-{i}" for i in range(9, 1, -1)])


class LlmExtractGraphNodeMergeTests(unittest.TestCase):
    def test_cross_window_merge_is_case_and_whitespace_insensitive(self):
        window1 = [segment("segment-001", "t1", 0, 5)]
        window2 = [segment("segment-002", "t2", 5, 10)]

        def fake_node_agent(window, known_nodes):
            segment_id = window[0]["id"]
            name = "Embedding" if segment_id == "segment-001" else "  embedding  "
            return [{"canonical_name": name, "node_type": "concept", "short_summary": "s", "confidence": 0.7, "evidence_segment_ids": [segment_id]}]

        with mock.patch.object(graph_extraction_agents, "llm_node_candidate_agent", side_effect=fake_node_agent):
            nodes, edges = graph_extraction_agents.llm_extract_graph("video-1", [window1, window2])

        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["canonical_name"], "Embedding")
        self.assertEqual(nodes[0]["node_id"], graph_extraction_agents.node_id_for("video-1", "Embedding"))
        self.assertEqual([s["source_id"] for s in nodes[0]["sources"]], ["segment-001", "segment-002"])
        self.assertEqual(edges, [])  # only one distinct node -> no pair has >=2 candidate nodes

    def test_known_nodes_context_is_capped_and_projected_to_name_and_type(self):
        window_count = graph_extraction_agents.MAX_KNOWN_NODES_IN_PROMPT + 5
        windows = [[segment(f"segment-{i:03d}", "t", i, i + 1)] for i in range(window_count)]
        captured_known = []

        def fake_node_agent(window, known_nodes):
            captured_known.append(known_nodes)
            segment_id = window[0]["id"]
            return [{
                "canonical_name": f"concept-{segment_id}", "node_type": "concept",
                "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id],
            }]

        with mock.patch.object(graph_extraction_agents, "llm_node_candidate_agent", side_effect=fake_node_agent), \
             mock.patch.object(graph_extraction_agents, "llm_relation_agent", return_value=[]):
            graph_extraction_agents.llm_extract_graph("video-1", windows)

        last_known = captured_known[-1]
        self.assertLessEqual(len(last_known), graph_extraction_agents.MAX_KNOWN_NODES_IN_PROMPT)
        for entry in last_known:
            self.assertEqual(set(entry.keys()), {"canonical_name", "node_type"})

    def test_relation_classification_is_scoped_to_adjacent_window_pairs(self):
        windows = [
            [segment("segment-000", "t", 0, 1)],
            [segment("segment-001", "t", 1, 2)],
            [segment("segment-002", "t", 2, 3)],
        ]

        def fake_node_agent(window, known_nodes):
            segment_id = window[0]["id"]
            name = {"segment-000": "A", "segment-001": "B", "segment-002": "C"}[segment_id]
            return [{"canonical_name": name, "node_type": "concept", "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id]}]

        seen_pairs = []

        def fake_relation_agent(candidate_nodes, segments):
            seen_pairs.append({node["canonical_name"] for node in candidate_nodes})
            return []

        with mock.patch.object(graph_extraction_agents, "llm_node_candidate_agent", side_effect=fake_node_agent), \
             mock.patch.object(graph_extraction_agents, "llm_relation_agent", side_effect=fake_relation_agent):
            graph_extraction_agents.llm_extract_graph("video-1", windows)

        self.assertEqual(seen_pairs, [{"A", "B"}, {"B", "C"}])

    def test_duplicate_edge_across_adjacent_pairs_merges_evidence_and_keeps_max_confidence(self):
        windows = [[segment(f"segment-{i:03d}", "t", i, i + 1)] for i in range(3)]
        node_a_id = graph_extraction_agents.node_id_for("video-1", "A")
        node_b_id = graph_extraction_agents.node_id_for("video-1", "B")

        def fake_node_agent(window, known_nodes):
            segment_id = window[0]["id"]
            return [
                {"canonical_name": "A", "node_type": "concept", "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id]},
                {"canonical_name": "B", "node_type": "concept", "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id]},
            ]

        calls = {"count": 0}

        def fake_relation_agent(candidate_nodes, segments):
            confidence, evidence = (0.4, ["segment-000"]) if calls["count"] == 0 else (0.9, ["segment-002"])
            calls["count"] += 1
            return [{
                "source_node_id": node_a_id, "target_node_id": node_b_id, "relation_type": "causes",
                "relation_status": "accepted", "confidence": confidence, "evidence_source_ids": evidence,
            }]

        with mock.patch.object(graph_extraction_agents, "llm_node_candidate_agent", side_effect=fake_node_agent), \
             mock.patch.object(graph_extraction_agents, "llm_relation_agent", side_effect=fake_relation_agent):
            nodes, edges = graph_extraction_agents.llm_extract_graph("video-1", windows)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["confidence"], 0.9)
        self.assertEqual(edges[0]["evidence_source_ids"], ["segment-000", "segment-002"])

    def test_opposite_directions_for_same_relation_type_drop_both_edges(self):
        windows = [[segment(f"segment-{i:03d}", "t", i, i + 1)] for i in range(4)]
        node_a_id = graph_extraction_agents.node_id_for("video-1", "A")
        node_b_id = graph_extraction_agents.node_id_for("video-1", "B")

        def fake_node_agent(window, known_nodes):
            segment_id = window[0]["id"]
            return [
                {"canonical_name": "A", "node_type": "concept", "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id]},
                {"canonical_name": "B", "node_type": "concept", "short_summary": "s", "confidence": 0.5, "evidence_segment_ids": [segment_id]},
            ]

        directions = [(node_a_id, node_b_id), (node_b_id, node_a_id), (node_a_id, node_b_id)]

        def fake_relation_agent(candidate_nodes, segments):
            source_id, target_id = directions.pop(0)
            return [{
                "source_node_id": source_id, "target_node_id": target_id, "relation_type": "prerequisite_for",
                "relation_status": "accepted", "confidence": 0.8, "evidence_source_ids": [segments[0]["id"]],
            }]

        with mock.patch.object(graph_extraction_agents, "llm_node_candidate_agent", side_effect=fake_node_agent), \
             mock.patch.object(graph_extraction_agents, "llm_relation_agent", side_effect=fake_relation_agent):
            _, edges = graph_extraction_agents.llm_extract_graph("video-1", windows)

        self.assertEqual(edges, [])


class LlmRelationAgentValidationTests(unittest.TestCase):
    def setUp(self):
        self.node_a = {"node_id": "node-a", "canonical_name": "A", "node_type": "concept", "sources": [{"source_id": "segment-001"}]}
        self.node_b = {"node_id": "node-b", "canonical_name": "B", "node_type": "concept", "sources": [{"source_id": "segment-002"}]}
        self.segments = [
            segment("segment-001", "a", 0, 5), segment("segment-002", "b", 5, 10), segment("segment-003", "c", 10, 15),
        ]

    def relation(self, **overrides):
        base = {"source_node": "A", "target_node": "B", "relation_type": "related_to", "confidence": 0.6, "evidence_source_ids": ["segment-001"]}
        base.update(overrides)
        return base

    def run_with(self, relation):
        with mock.patch.object(graph_extraction_agents, "llm_generate", return_value={"relations": [relation]}):
            return graph_extraction_agents.llm_relation_agent([self.node_a, self.node_b], self.segments)

    def test_valid_relation_is_accepted(self):
        self.assertEqual(len(self.run_with(self.relation())), 1)

    def test_endpoint_not_in_known_nodes_is_dropped(self):
        self.assertEqual(self.run_with(self.relation(source_node="Unknown")), [])

    def test_self_loop_is_dropped(self):
        self.assertEqual(self.run_with(self.relation(target_node="A")), [])

    def test_evidence_not_belonging_to_either_endpoint_is_dropped(self):
        # segment-003 is a real segment in this window (passes the shape check)
        # but isn't cited as a source by either A or B (fails the tighter check).
        self.assertEqual(self.run_with(self.relation(evidence_source_ids=["segment-003"])), [])

    def test_propose_without_description_is_dropped(self):
        self.assertEqual(self.run_with(self.relation(relation_type="propose:influences")), [])

    def test_propose_with_description_is_accepted_as_proposed(self):
        edges = self.run_with(self.relation(relation_type="propose:influences", proposed_relation_description="A shapes B"))
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["relation_status"], "proposed")
        self.assertEqual(edges[0]["relation_type"], "influences")

    def test_proposed_description_over_character_limit_is_dropped(self):
        description = "x" * (graph_extraction_agents.MAX_PROPOSED_RELATION_DESCRIPTION_CHARS + 1)
        self.assertEqual(self.run_with(self.relation(relation_type="propose:influences", proposed_relation_description=description)), [])

    def test_boolean_confidence_is_dropped(self):
        self.assertEqual(self.run_with(self.relation(confidence=True)), [])


class ExtractGraphForVideoTests(unittest.TestCase):
    def test_empty_windows_returns_empty_result_without_calling_any_llm(self):
        with mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "llm_extract_graph") as llm_extract:
            nodes, edges, mode = graph_extraction_agents.extract_graph_for_video("video-1", [])
        self.assertEqual((nodes, edges), ([], []))
        llm_extract.assert_not_called()

    def test_default_mode_matches_heuristic_extractor_exactly(self):
        windows = [[segment("segment-001", "Embedding models turn text into vectors.", 0, 5)]]
        with mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "heuristic"):
            nodes, edges, mode = graph_extraction_agents.extract_graph_for_video("video-1", windows)
        self.assertEqual((nodes, edges), graph_extraction_agents.heuristic_extract_graph("video-1", windows))
        self.assertEqual(mode, "heuristic")

    def test_llm_mode_returns_llm_result_and_reports_actual_mode(self):
        fixed = ([{"node_id": "n"}], [{"edge_id": "e"}])
        with mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "llm_extract_graph", return_value=fixed):
            nodes, edges, mode = graph_extraction_agents.extract_graph_for_video("video-1", [["window"]])
        self.assertEqual((nodes, edges), fixed)
        self.assertEqual(mode, "ollama")

    def test_provider_error_propagates_without_falling_back(self):
        with mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "llm_extract_graph", side_effect=AgentProviderError("OLLAMA_TIMEOUT")):
            with self.assertRaises(AgentProviderError):
                graph_extraction_agents.extract_graph_for_video("video-1", [["window"]])

    def test_zero_valid_nodes_falls_back_to_heuristic(self):
        windows = [[segment("segment-001", "Embedding models turn text into vectors.", 0, 5)]]
        with mock.patch.object(graph_extraction_agents, "GRAPH_EXTRACTION_MODE", "ollama"), \
             mock.patch.object(graph_extraction_agents, "llm_extract_graph", return_value=([], [])):
            nodes, edges, mode = graph_extraction_agents.extract_graph_for_video("video-1", windows)
        self.assertEqual((nodes, edges), graph_extraction_agents.heuristic_extract_graph("video-1", windows))
        self.assertEqual(mode, "heuristic")


class SchemaForwardingTests(unittest.TestCase):
    def test_node_agent_forwards_node_candidate_schema(self):
        fake_provider = mock.Mock()
        fake_provider.generate_json.return_value = {"nodes": []}
        with mock.patch.object(graph_extraction_agents, "resolve_provider", return_value=fake_provider):
            graph_extraction_agents.llm_node_candidate_agent([segment("segment-001", "t", 0, 5)], [])
        self.assertEqual(fake_provider.generate_json.call_args.kwargs["schema"], graph_extraction_agents.NODE_CANDIDATE_SCHEMA)

    def test_relation_agent_forwards_relation_schema(self):
        fake_provider = mock.Mock()
        fake_provider.generate_json.return_value = {"relations": []}
        node_a = {"node_id": "a", "canonical_name": "A", "node_type": "concept", "sources": []}
        node_b = {"node_id": "b", "canonical_name": "B", "node_type": "concept", "sources": []}
        with mock.patch.object(graph_extraction_agents, "resolve_provider", return_value=fake_provider):
            graph_extraction_agents.llm_relation_agent([node_a, node_b], [segment("segment-001", "t", 0, 5)])
        self.assertEqual(fake_provider.generate_json.call_args.kwargs["schema"], graph_extraction_agents.RELATION_SCHEMA)


if __name__ == "__main__":
    unittest.main()
