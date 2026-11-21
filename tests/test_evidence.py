import json
from pathlib import Path
import unittest

from hunchlab.evidence import EvidenceGraph


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "market-alert.json"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def node(self, kind):
        return next(node for node in self.fixture["nodes"] if node["kind"] == kind)

    def test_preserves_source_span_and_resolves_the_decision_path(self):
        graph = EvidenceGraph(self.fixture)
        span = graph.span
        excerpt = graph.record(span.source_id, "source")["excerpt"]
        self.assertEqual(excerpt[span.start:span.end], self.node("claim")["quote"])
        self.assertEqual(
            [edge.target for edge in graph.citation_paths[0]],
            ["pilot-claim", "evidence-panel", "alert-comparison", "prototype-decision"],
        )

    def test_rejects_a_claim_with_no_matching_source_span(self):
        self.node("claim")["quote"] = "Northline ran an independent benchmark."
        with self.assertRaisesRegex(ValueError, "source quote"):
            EvidenceGraph(self.fixture)

    def test_rejects_an_edge_with_a_missing_endpoint(self):
        self.fixture["edges"][0]["target"] = "missing-claim"
        with self.assertRaisesRegex(ValueError, "Missing record: missing-claim"):
            EvidenceGraph(self.fixture)

    def test_rejects_a_decision_citation_with_no_source(self):
        self.node("decision")["source_ids"] = ["missing-source"]
        with self.assertRaisesRegex(ValueError, "Missing record: missing-source"):
            EvidenceGraph(self.fixture)

    def test_rejects_a_citation_to_a_disconnected_source(self):
        self.fixture["nodes"].append({**self.node("source"), "id": "unrelated-source"})
        self.node("decision")["source_ids"] = ["unrelated-source"]
        with self.assertRaisesRegex(ValueError, "No evidence path"):
            EvidenceGraph(self.fixture)

    def test_rejects_proposed_studies_and_fixture_results_as_observations(self):
        for kind in ("study", "result"):
            with self.subTest(kind=kind):
                self.node("decision")["observation_ids"] = [self.node(kind)["id"]]
                with self.assertRaisesRegex(ValueError, "cannot cite research observations"):
                    EvidenceGraph(self.fixture)

    def test_rejects_an_observed_label_on_the_fixture_result(self):
        self.node("result")["status"] = "observed"
        with self.assertRaisesRegex(ValueError, "fixture result"):
            EvidenceGraph(self.fixture)

    def test_rejects_counts_above_the_response_total(self):
        self.node("result")["counts"]["A"]["trust"] = 9
        with self.assertRaisesRegex(ValueError, "Invalid fixture count"):
            EvidenceGraph(self.fixture)
