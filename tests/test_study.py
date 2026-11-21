import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hunchlab.study import propose_paths, read_inputs, replay_paths, verify, verify_paths


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "study"
CLAIM = FIXTURES / "claim.json"
CONFOUNDED = FIXTURES / "confounded.json"
REPAIRED = FIXTURES / "repaired.json"


def codes(report):
    return {blocker["code"] for blocker in report["blockers"]}


class StudyVerifierTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.responses = Path(tmp.name) / "input.csv"
        self.stale = Path(tmp.name) / "stale.csv"
        header = "study_id,revision,sample,respondent_id,story_id,arm_id,question_id,answer_id"
        rows = [header]
        for n in range(1, 9):
            arm = "plain" if n <= 4 else "boxed"
            answer = {2: "general-release", 4: "unsure", 7: "benchmarks"}.get(n, "limited-pilot")
            rows.append(f"alert-format-demo,draft-2,fixture,r{n:02d},northline,{arm},comprehension,{answer}")
        contents = "\n".join(rows) + "\n"
        self.responses.write_text(contents)
        self.stale.write_text(contents.replace("draft-2", "draft-1"))

    def test_confounded_design_and_generated_repair(self):
        blocked = verify_paths(CLAIM, CONFOUNDED)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn("no_same_story_pair", codes(blocked))
        self.assertIn("multiple_exposures", codes(blocked))
        self.assertIn("northline", blocked["repair"])

        proposal = propose_paths(CLAIM, CONFOUNDED, "proposal-2")
        fielded_fixture = json.loads(REPAIRED.read_bytes())
        self.assertEqual(proposal["assignment"], fielded_fixture["assignment"])
        self.assertEqual(proposal["arms"], fielded_fixture["arms"])
        inputs = read_inputs(CLAIM, REPAIRED)
        inputs["instrument"] = json.dumps(proposal).encode()
        self.assertEqual(verify(inputs)["status"], "awaiting_responses")

    def test_same_story_alone_does_not_make_an_eligible_comparison(self):
        inputs = read_inputs(CLAIM, REPAIRED)
        design = json.loads(inputs["instrument"])
        design["arms"]["boxed"]["content"] += " Pricing is yet to be announced."
        design["arms"]["boxed"]["question"]["text"] += " Select the best answer."
        design["assignment"]["weights"]["boxed"] = 0
        inputs["instrument"] = json.dumps(design).encode()
        self.assertEqual(codes(verify(inputs)), {
            "base_content_changed", "question_changed", "assignment_weights_missing",
        })

    def test_score_matching_rows_and_reject_a_stale_export(self):
        score = verify_paths(CLAIM, REPAIRED, self.responses)
        self.assertEqual(score["status"], "scored")
        self.assertEqual(score["comparison"]["arms"]["plain"], {
            "correct": 2, "responses": 4, "rate": 0.5,
        })
        self.assertEqual(score["comparison"]["arms"]["boxed"], {
            "correct": 3, "responses": 4, "rate": 0.75,
        })
        self.assertEqual(score["comparison"]["difference_percentage_points"], 25.0)
        stale = verify_paths(CLAIM, REPAIRED, self.stale)
        self.assertEqual(stale["status"], "blocked")
        self.assertEqual(codes(stale), {"response_revision_mismatch"})
        self.assertIsNone(stale["comparison"])

    def test_duplicate_reader_missing_outcome_and_wrong_exposure_block_scoring(self):
        inputs = read_inputs(CLAIM, REPAIRED, self.responses)
        inputs["responses"] = (
            inputs["responses"].replace(b",r02,", b",r01,")
            .replace(b",comprehension,benchmarks", b",comprehension,")
            .replace(b",r06,northline,", b",r06,calder,")
        )
        report = verify(inputs)
        self.assertEqual(codes(report), {
            "response_respondent_mismatch", "response_answer_mismatch", "response_exposure_mismatch",
        })
        self.assertIsNone(report["comparison"])
        self.assertNotIn("r01", json.dumps(report))

    def test_only_matching_fielded_first_party_data_can_score_a_flashpoint_study(self):
        inputs = read_inputs(CLAIM, REPAIRED, self.responses)
        design = json.loads(inputs["instrument"])
        design["source"]["kind"] = "flashpoint"
        inputs["instrument"] = json.dumps(design).encode()
        self.assertEqual(codes(verify(inputs)), {"response_sample_mismatch"})
        inputs["responses"] = inputs["responses"].replace(b",fixture,", b",first_party,")
        self.assertEqual(verify(inputs)["status"], "scored")
        design["source"]["kind"] = "proposal"
        inputs["instrument"] = json.dumps(design).encode()
        self.assertEqual(codes(verify(inputs)), {"unfielded_proposal"})

    def test_replay_detects_changes_even_if_revision_label_is_unchanged(self):
        score = verify_paths(CLAIM, REPAIRED, self.responses)
        with TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            record.write_text(json.dumps(score), encoding="utf-8")
            self.assertEqual(
                replay_paths(record, CLAIM, REPAIRED, self.responses),
                {"status": "reproduced", "report": score},
            )
            changed = Path(tmp) / "changed.json"
            changed.write_bytes(REPAIRED.read_bytes() + b"\n")
            self.assertEqual(
                replay_paths(record, CLAIM, changed, self.responses),
                {"status": "inputs_changed", "changed_inputs": ["instrument"]},
            )
            record.write_text(json.dumps({**score, "status": "blocked"}), encoding="utf-8")
            self.assertEqual(
                replay_paths(record, CLAIM, REPAIRED, self.responses)["status"], "verdict_changed",
            )


if __name__ == "__main__":
    unittest.main()
