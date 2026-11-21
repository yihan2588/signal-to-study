"""Validate the demo graph before rendering citations and study evidence."""

from collections import defaultdict, deque
from dataclasses import dataclass
import re


EDGE_TYPES = {
    ("source", "supports", "claim", "fixture"),
    ("claim", "motivates", "hypothesis", "proposed"),
    ("hypothesis", "tested by", "study", "proposed"),
    ("study", "has fixture result", "result", "fixture"),
    ("study", "will inform", "decision", "proposed"),
}


@dataclass(frozen=True)
class Edge:
    source: str
    relation: str
    target: str
    status: str


@dataclass(frozen=True)
class SourceSpan:
    source_id: str
    start: int
    end: int
    quote: str


class EvidenceGraph:
    def __init__(self, fixture):
        self.nodes = {}
        for record in fixture["nodes"]:
            node_id = record["id"]
            if not re.fullmatch(r"[a-z][a-z0-9-]*", node_id) or node_id in self.nodes:
                raise ValueError(f"Invalid or duplicate record ID: {node_id!r}")
            self.nodes[node_id] = record

        self.edges = [Edge(**record) for record in fixture["edges"]]
        self.outgoing = defaultdict(list)
        for edge in self.edges:
            source = self.record(edge.source)
            target = self.record(edge.target)
            signature = (source["kind"], edge.relation, target["kind"], edge.status)
            if signature not in EDGE_TYPES:
                raise ValueError(f"Invalid fixture edge: {edge}")
            self.outgoing[edge.source].append(edge)

        self.claim = self.one("claim")
        self.study = self.one("study")
        self.result = self.one("result")
        self.decision = self.one("decision")
        self.span = self.extract_span(self.claim)
        self.validate_study()
        self.citation_paths = self.memo_citation_paths()

    def record(self, node_id, kind=None):
        node = self.nodes.get(node_id)
        if node is None:
            raise ValueError(f"Missing record: {node_id}")
        if kind is not None and node["kind"] != kind:
            raise ValueError(f"Record {node_id} must be a {kind}")
        return node

    def one(self, kind):
        records = [node for node in self.nodes.values() if node["kind"] == kind]
        if len(records) != 1:
            raise ValueError(f"The demo requires one {kind} record")
        return records[0]

    def extract_span(self, claim):
        sources = [
            self.record(edge.source, "source")
            for edge in self.edges
            if edge.relation == "supports" and edge.target == claim["id"]
        ]
        if len(sources) != 1:
            raise ValueError(f"Claim {claim['id']} requires one supporting source")

        source = sources[0]
        quote = claim["quote"]
        excerpt = source["excerpt"]
        start = excerpt.find(quote)
        if not quote.strip() or start < 0 or excerpt.find(quote, start + 1) >= 0:
            raise ValueError(f"Claim {claim['id']} needs an exact, unique source quote")
        return SourceSpan(source["id"], start, start + len(quote), quote)

    def validate_study(self):
        if self.study["status"] != "proposed" or self.result["status"] != "fixture":
            raise ValueError("The demo requires a proposed study and a fixture result")
        if not any(edge.target == self.result["id"] for edge in self.outgoing[self.study["id"]]):
            raise ValueError("The fixture result must link to its study")

        variants = self.study["variants"]
        if [variant["id"] for variant in variants] != ["A", "B"]:
            raise ValueError("The demo requires alert variants A and B")
        source = self.record(variants[1]["source_id"], "source")
        if source["id"] != self.span.source_id:
            raise ValueError("Variant B must cite the fixture's supporting source")
        for variant in variants:
            counts = self.result["counts"][variant["id"]]
            total = counts["responses"]
            if type(total) is not int or total < 0:
                raise ValueError(f"Invalid fixture response total for variant {variant['id']}")
            for question in self.study["questions"]:
                count = counts[question["id"]]
                if type(count) is not int or not 0 <= count <= total:
                    raise ValueError(f"Invalid fixture count for {variant['id']} / {question['id']}")

    def path(self, source_id, target_id):
        seen = {source_id}
        pending = deque([(source_id, ())])
        while pending:
            node_id, path = pending.popleft()
            if node_id == target_id:
                return path
            for edge in self.outgoing.get(node_id, ()):
                if edge.target not in seen:
                    seen.add(edge.target)
                    pending.append((edge.target, (*path, edge)))
        return ()

    def memo_citation_paths(self):
        if self.decision["status"] != "pending" or self.decision["observation_ids"]:
            raise ValueError("A pending fixture memo cannot cite research observations")
        if not self.decision["source_ids"]:
            raise ValueError("The decision memo requires a source citation")

        paths = []
        for source_id in self.decision["source_ids"]:
            self.record(source_id, "source")
            path = self.path(source_id, self.decision["id"])
            if not path:
                raise ValueError(f"No evidence path from {source_id} to the decision memo")
            paths.append(path)
        return paths
