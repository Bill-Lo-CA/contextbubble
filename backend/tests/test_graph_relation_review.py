from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config
import api_routes
from api_models import RelationTypeReviewRequest
from db import connect_db, init_db
from graph_store import list_relation_types, review_relation_type


class RelationTypeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.context = config.settings_override(replace(config.get_settings(), data_dir=Path(self.tempdir.name)))
        self.context.__enter__()
        init_db()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def seed_relation_type(self, relation_type, status, proposed_by_job_id=None):
        with connect_db() as conn:
            conn.execute(
                "insert into kg_relation_types (relation_type, description, status, proposed_by_job_id, created_at) "
                "values (?, ?, ?, ?, 'now')",
                (relation_type, relation_type, status, proposed_by_job_id),
            )

    def seed_edge(self, relation_type, relation_status, edge_id="edge-1", job_id="job-1"):
        # Minimal fixture: only the columns review_relation_type's UPDATE touches
        # need to be meaningful, but kg_edges' FKs still require real parent rows.
        with connect_db() as conn:
            conn.execute(
                "insert or ignore into videos (video_id, created_at, updated_at) values ('video-1', 'now', 'now')"
            )
            conn.execute(
                "insert or ignore into transcript_sources (transcript_id, video_id, filename, source, content_hash, segment_count, created_at) "
                "values ('transcript-1', 'video-1', 'x', 'test', 'hash', 0, 'now')"
            )
            conn.execute(
                "insert into preparation_jobs (job_id, video_id, learner_level, source_policy, status, stage, job_kind, created_at, updated_at) "
                "values (?, 'video-1', 'intermediate', 'live', 'processing', 'extracting_graph', 'graph_extraction', 'now', 'now')",
                (job_id,),
            )
            conn.execute(
                "insert into kg_extraction_jobs (job_id, video_id, transcript_id, cache_key, status, stage, created_at, updated_at) "
                "values (?, 'video-1', 'transcript-1', 'cache-1', 'processing', 'extracting_graph', 'now', 'now')",
                (job_id,),
            )
            for node_id in ("node-a", "node-b"):
                conn.execute(
                    "insert into kg_nodes (extraction_job_id, node_id, canonical_name, node_type, short_summary, confidence, created_at, updated_at) "
                    "values (?, ?, ?, 'concept', 's', 0.5, 'now', 'now')",
                    (job_id, node_id, node_id),
                )
            conn.execute(
                "insert into kg_edges (extraction_job_id, edge_id, source_node_id, target_node_id, relation_type, relation_status, "
                "confidence, directional, created_at, updated_at) values (?, ?, 'node-a', 'node-b', ?, ?, 0.5, 1, 'now', 'now')",
                (job_id, edge_id, relation_type, relation_status),
            )

    def test_list_relation_types_reports_edge_and_job_counts(self):
        self.seed_relation_type("influences", "proposed", proposed_by_job_id="job-1")
        self.seed_edge("influences", "proposed")
        result = list_relation_types("proposed")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["relation_type"], "influences")
        self.assertEqual(result[0]["edge_count"], 1)
        self.assertEqual(result[0]["job_count"], 1)

    def test_list_relation_types_filters_by_status(self):
        self.seed_relation_type("influences", "proposed")
        self.seed_relation_type("related_to", "approved")
        self.assertEqual([r["relation_type"] for r in list_relation_types("approved")], ["related_to"])

    def test_state_transition_table(self):
        cases = {
            "proposed+approve -> approved, edges accepted": ("proposed", "approve", "approved", "accepted", 1),
            "proposed+reject -> rejected, edges rejected": ("proposed", "reject", "rejected", "rejected", 1),
            "approved+approve -> idempotent no-op": ("approved", "approve", "approved", "proposed", 0),
            "rejected+reject -> idempotent no-op": ("rejected", "reject", "rejected", "proposed", 0),
        }
        for label, (initial_status, decision, expected_status, expected_edge_status_if_untouched, expected_affected) in cases.items():
            with self.subTest(label):
                with connect_db() as conn:
                    conn.execute("delete from kg_edges")
                    conn.execute("delete from kg_nodes")
                    conn.execute("delete from kg_extraction_jobs")
                    conn.execute("delete from preparation_jobs")
                    conn.execute("delete from transcript_sources")
                    conn.execute("delete from videos")
                    conn.execute("delete from kg_relation_types")
                self.seed_relation_type("influences", initial_status)
                self.seed_edge("influences", "proposed")

                result = review_relation_type("influences", decision)

                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["affected_edge_count"], expected_affected)
                with connect_db() as conn:
                    type_row = conn.execute("select status from kg_relation_types where relation_type = 'influences'").fetchone()
                    edge_row = conn.execute("select relation_status from kg_edges where edge_id = 'edge-1'").fetchone()
                self.assertEqual(type_row["status"], expected_status)
                if expected_affected:
                    self.assertEqual(edge_row["relation_status"], "accepted" if decision == "approve" else "rejected")
                else:
                    self.assertEqual(edge_row["relation_status"], expected_edge_status_if_untouched)

    def test_reversing_an_approved_decision_returns_conflict_without_changing_anything(self):
        self.seed_relation_type("influences", "approved")
        self.seed_edge("influences", "accepted")
        self.assertEqual(review_relation_type("influences", "reject"), "conflict")
        with connect_db() as conn:
            self.assertEqual(conn.execute("select status from kg_relation_types where relation_type = 'influences'").fetchone()["status"], "approved")

    def test_reversing_a_rejected_decision_returns_conflict(self):
        self.seed_relation_type("influences", "rejected")
        self.assertEqual(review_relation_type("influences", "approve"), "conflict")

    def test_built_in_relation_type_cannot_be_rejected_even_if_legacy_state_is_proposed(self):
        self.seed_relation_type("causes", "proposed")
        self.assertEqual(review_relation_type("causes", "reject"), "conflict")

    def test_missing_relation_type_returns_none(self):
        self.assertIsNone(review_relation_type("does_not_exist", "approve"))

    def test_description_is_updated_even_on_idempotent_repeat(self):
        self.seed_relation_type("influences", "approved", proposed_by_job_id=None)
        result = review_relation_type("influences", "approve", description="updated wording")
        self.assertEqual(result["affected_edge_count"], 0)
        with connect_db() as conn:
            row = conn.execute("select description from kg_relation_types where relation_type = 'influences'").fetchone()
        self.assertEqual(row["description"], "updated wording")

    def test_approve_transaction_rolls_back_type_change_if_edge_update_fails(self):
        self.seed_relation_type("influences", "proposed")
        self.seed_edge("influences", "proposed")
        with connect_db() as conn:
            conn.execute(
                "create trigger fail_edge_update before update on kg_edges "
                "when new.relation_type = 'influences' "
                "begin select raise(abort, 'forced failure'); end"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            review_relation_type("influences", "approve")
        with connect_db() as conn:
            type_row = conn.execute("select status from kg_relation_types where relation_type = 'influences'").fetchone()
            edge_row = conn.execute("select relation_status from kg_edges where edge_id = 'edge-1'").fetchone()
        # Both statements share one transaction - the type flip must not survive
        # if the edge update it was paired with never committed.
        self.assertEqual(type_row["status"], "proposed")
        self.assertEqual(edge_row["relation_status"], "proposed")

    def test_reject_marks_previously_proposed_edges_rejected(self):
        self.seed_relation_type("influences", "proposed")
        self.seed_edge("influences", "proposed")
        review_relation_type("influences", "reject")
        with connect_db() as conn:
            status = conn.execute("select relation_status from kg_edges where edge_id = 'edge-1'").fetchone()[0]
        self.assertEqual(status, "rejected")


class RelationTypeReviewRouteTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def payload(response):
        return json.loads(response.body)

    async def test_review_requires_bearer_auth(self):
        with mock.patch.object(api_routes, "valid_bearer_token", return_value=False):
            response = await api_routes.review_relation_type_route("influences", mock.Mock(), authorization="bad")
        self.assertEqual(response.status_code, 401)

    async def test_relation_type_routes_reject_invalid_input(self):
        with mock.patch.object(api_routes, "valid_bearer_token", return_value=True):
            invalid_status = await api_routes.relation_types(status="unknown", authorization="token")
            invalid_slug = await api_routes.review_relation_type_route("Invalid!", mock.Mock(), authorization="token")
        self.assertEqual(invalid_status.status_code, 400)
        self.assertEqual(invalid_slug.status_code, 400)

    async def test_review_maps_missing_and_conflicting_types(self):
        body = RelationTypeReviewRequest(decision="approve")
        cases = ((None, 404, "NOT_FOUND"), ("conflict", 409, "RELATION_TYPE_REVIEW_CONFLICT"))
        for result, expected_status, expected_code in cases:
            with self.subTest(result=result), \
                 mock.patch.object(api_routes, "valid_bearer_token", return_value=True), \
                 mock.patch.object(api_routes, "read_model", new=mock.AsyncMock(return_value=body)), \
                 mock.patch.object(api_routes, "run_in_threadpool", new=mock.AsyncMock(return_value=result)):
                response = await api_routes.review_relation_type_route("influences", mock.Mock(), authorization="token")
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(self.payload(response)["error_code"], expected_code)


if __name__ == "__main__":
    unittest.main()
