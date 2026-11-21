"""Check what a two-arm study can establish from a reviewed instrument and export."""

import csv
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path


VERIFIER_VERSION = 1
RESPONSE_FIELDS = {
    "study_id", "revision", "sample", "respondent_id", "story_id", "arm_id",
    "question_id", "answer_id",
}


def require_fields(value, fields, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    for name in fields:
        if not isinstance(value.get(name), str) or not value[name].strip():
            raise ValueError(f"{label}.{name} must be a nonempty string")


def check_question(question, label):
    require_fields(question, ("id", "text"), label)
    options = question.get("options")
    if not isinstance(options, dict) or len(options) < 2:
        raise ValueError(f"{label}.options must contain at least two choices")
    require_fields(options, options, f"{label}.options")
    if any(not key.strip() for key in options) or len(set(options.values())) != len(options):
        raise ValueError(f"{label}.options requires distinct choices and nonempty IDs")


def validate_inputs(claim, instrument):
    require_fields(
        claim, ("id", "statement", "story_id", "control", "treatment", "treatment_addition"),
        "claim",
    )
    if claim["control"] == claim["treatment"]:
        raise ValueError("claim.control and claim.treatment must be distinct arm IDs")
    question = claim.get("question")
    check_question(question, "claim.question")
    require_fields(question, ("correct",), "claim.question")
    if question["correct"] not in question["options"]:
        raise ValueError("claim.question.correct must name an approved choice")

    require_fields(instrument, ("study_id", "revision"), "instrument")
    source = instrument.get("source")
    require_fields(source, ("kind", "reference"), "instrument.source")
    if source["kind"] not in {"fixture", "flashpoint", "proposal"}:
        raise ValueError("instrument.source.kind must be fixture, flashpoint, or proposal")
    assignment = instrument.get("assignment")
    require_fields(assignment, ("method", "unit"), "instrument.assignment")
    exposures = assignment.get("exposures_per_unit")
    if type(exposures) is not int or exposures < 1:
        raise ValueError("instrument.assignment.exposures_per_unit must be a positive integer")
    arms = instrument.get("arms")
    if not isinstance(arms, dict) or not arms:
        raise ValueError("instrument.arms must be a nonempty object keyed by arm ID")
    for arm_id, arm in arms.items():
        label = f"instrument.arms.{arm_id}"
        require_fields(arm, ("story_id", "content"), label)
        if "addition" not in arm or (
            arm["addition"] is not None
            and (not isinstance(arm["addition"], str) or not arm["addition"].strip())
        ):
            raise ValueError(f"{label}.addition must be null or a nonempty string")
        check_question(arm.get("question"), f"{label}.question")


def blocker(code, message):
    return {"code": code, "message": message}


def design_blockers(claim, instrument):
    arms = instrument["arms"]
    control = claim["control"]
    treatment = claim["treatment"]
    problems = []
    if set(arms) != {control, treatment}:
        problems.append(blocker(
            "arm_set_mismatch", f"The comparison requires exactly arms {control} and {treatment}."
        ))
    else:
        left, right = arms[control], arms[treatment]
        if left["story_id"] != right["story_id"]:
            problems.append(blocker(
                "no_same_story_pair",
                f"Zero stories appear in both formats. {control} shows {left['story_id']}. "
                f"{treatment} shows {right['story_id']}.",
            ))
        elif left["story_id"] != claim["story_id"]:
            problems.append(blocker(
                "target_story_missing", f"Both arms must show the approved story {claim['story_id']}."
            ))
        if left["content"] != right["content"]:
            problems.append(blocker(
                "base_content_changed", "The base content differs across the two arms."
            ))
        if left["addition"] is not None or right["addition"] != claim["treatment_addition"]:
            problems.append(blocker(
                "addition_changed", f"Only {treatment} should display the approved addition."
            ))

        approved = claim["question"]
        for arm_id, arm in arms.items():
            question = arm["question"]
            if (
                question["id"] != approved["id"]
                or question["text"] != approved["text"]
                or list(question["options"].items()) != list(approved["options"].items())
            ):
                problems.append(blocker(
                    "question_changed", f"{arm_id} differs from the approved question or choice order."
                ))

    assignment = instrument["assignment"]
    if assignment["method"] != "randomized" or assignment["unit"] != "respondent":
        problems.append(blocker(
            "assignment_not_randomized", "The comparison needs random assignment by respondent."
        ))
    else:
        weights = assignment.get("weights")
        if (
            not isinstance(weights, dict)
            or set(weights) != {control, treatment}
            or any(type(weight) is not int or weight <= 0 for weight in weights.values())
        ):
            problems.append(blocker(
                "assignment_weights_missing",
                "Record positive randomization weights for both approved arms.",
            ))
    if assignment["exposures_per_unit"] != 1:
        problems.append(blocker(
            "multiple_exposures", "Each respondent must see exactly one version in this comparison."
        ))
    return problems


def repair_instruction(claim):
    return (
        f"Randomly show each respondent either {claim['control']} or {claim['treatment']} "
        f"for {claim['story_id']}. Keep the base content, approved question, and ordered choices "
        f"identical. Show the approved addition only in {claim['treatment']}."
    )


def propose_paths(claim_path, instrument_path, revision):
    inputs = read_inputs(claim_path, instrument_path)
    claim, instrument = load_design(inputs)
    if not design_blockers(claim, instrument):
        raise ValueError("This instrument already passes the design checks")
    if not revision.strip() or revision == instrument["revision"]:
        raise ValueError("A proposed revision needs a new, nonempty revision ID")
    arms = instrument["arms"]
    matching = [arm for arm in arms.values() if arm["story_id"] == claim["story_id"]]
    target = arms.get(claim["control"])
    if not target or target["story_id"] != claim["story_id"]:
        if len({arm["content"] for arm in matching}) != 1:
            raise ValueError("Identify a base version of the approved story before proposing a revision")
        target = matching[0]

    question = {field: claim["question"][field] for field in ("id", "text", "options")}
    candidate = {
        "story_id": claim["story_id"],
        "content": target["content"],
        "question": question,
        "addition": None,
    }
    return {
        "study_id": instrument["study_id"],
        "revision": revision,
        "source": {
            "kind": "proposal",
            "reference": f"Local proposal based on {instrument['study_id']} / {instrument['revision']}",
        },
        "assignment": {
            "method": "randomized",
            "unit": "respondent",
            "exposures_per_unit": 1,
            "weights": {claim["control"]: 1, claim["treatment"]: 1},
        },
        "arms": {
            claim["control"]: candidate,
            claim["treatment"]: {**deepcopy(candidate), "addition": claim["treatment_addition"]},
        },
    }


def read_responses(data):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    header = reader.fieldnames or []
    if len(header) != len(RESPONSE_FIELDS) or set(header) != RESPONSE_FIELDS:
        raise ValueError("Response CSV headers must be " + ", ".join(sorted(RESPONSE_FIELDS)))
    rows = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Invalid response CSV width near line {reader.line_num}")
        rows.append(row)
    return rows


def response_comparison(result, rows, claim, instrument):
    evidence = result["evidence"]
    evidence["response_rows"] = len(rows)
    if not rows:
        return

    if instrument["source"]["kind"] == "proposal":
        result["blockers"].append(blocker(
            "unfielded_proposal", "Use a reviewed fielded instrument to score responses."
        ))
        return

    matching_revisions = sum(row["revision"] == result["revision"] for row in rows)
    evidence["matching_revision_rows"] = matching_revisions
    if {row["study_id"] for row in rows} != {result["study_id"]}:
        result["blockers"].append(blocker(
            "response_study_mismatch", "The export contains rows from a different or missing study ID."
        ))
    if matching_revisions != len(rows):
        result["blockers"].append(blocker(
            "response_revision_mismatch",
            f"{len(rows) - matching_revisions} row(s) have a different or missing revision. "
            f"Use the export for {result['study_id']} / {result['revision']}.",
        ))
    expected_sample = "fixture" if instrument["source"]["kind"] == "fixture" else "first_party"
    if {row["sample"] for row in rows} != {expected_sample}:
        result["blockers"].append(blocker(
            "response_sample_mismatch", f"Every export row must come from the {expected_sample} sample."
        ))
    if result["blockers"]:
        return

    approved = claim["question"]
    arm_ids = (claim["control"], claim["treatment"])
    counts = {arm_id: {"correct": 0, "responses": 0} for arm_id in arm_ids}
    respondent_ids = {row["respondent_id"] for row in rows if row["respondent_id"].strip()}
    bad_exposures = 0
    bad_answers = 0
    for row in rows:
        if row["arm_id"] not in counts or row["story_id"] != claim["story_id"]:
            bad_exposures += 1
        elif row["question_id"] != approved["id"] or row["answer_id"] not in approved["options"]:
            bad_answers += 1
        else:
            arm = counts[row["arm_id"]]
            arm["responses"] += 1
            arm["correct"] += row["answer_id"] == approved["correct"]

    checks = [
        (len(respondent_ids) != len(rows), "response_respondent_mismatch",
         "Each respondent needs a unique, nonempty ID and exactly one row."),
        (bad_exposures, "response_exposure_mismatch",
         f"{bad_exposures} row(s) do not document exposure to an approved arm and story."),
        (bad_answers, "response_answer_mismatch",
         f"{bad_answers} row(s) lack a valid answer to the approved question."),
    ]
    for failed, code, message in checks:
        if failed:
            result["blockers"].append(blocker(code, message))
    if result["blockers"]:
        return
    if any(arm["responses"] == 0 for arm in counts.values()):
        result["blockers"].append(blocker(
            "response_arm_missing", "The export needs at least one valid response in each arm."
        ))
        return

    for arm in counts.values():
        arm["rate"] = arm["correct"] / arm["responses"]
    difference = counts[claim["treatment"]]["rate"] - counts[claim["control"]]["rate"]
    result["comparison"] = {
        "arms": counts,
        "difference_percentage_points": round(100 * difference, 10),
        "interpretation": "Observed difference in correct-answer rates (treatment minus control).",
    }


def read_inputs(claim_path, instrument_path, response_path=None):
    return {
        "claim": Path(claim_path).read_bytes(),
        "instrument": Path(instrument_path).read_bytes(),
        "responses": None if response_path is None else Path(response_path).read_bytes(),
    }


def fingerprints(inputs):
    return {
        name: None if content is None else sha256(content).hexdigest()
        for name, content in inputs.items()
    }


def load_design(inputs):
    claim = json.loads(inputs["claim"])
    instrument = json.loads(inputs["instrument"])
    validate_inputs(claim, instrument)
    return claim, instrument


def verify(inputs):
    claim, instrument = load_design(inputs)
    problems = design_blockers(claim, instrument)
    result = {
        "verifier_version": VERIFIER_VERSION,
        "status": None,
        "claim": claim,
        "study_id": instrument["study_id"],
        "revision": instrument["revision"],
        "source": instrument["source"],
        "inputs_sha256": fingerprints(inputs),
        "blockers": problems,
        "repair": repair_instruction(claim) if problems else None,
        "comparison": None,
        "evidence": {
            "assignment": instrument["assignment"],
            "arm_stories": {
                arm_id: arm["story_id"] for arm_id, arm in instrument["arms"].items()
            },
        },
    }
    if not problems and inputs["responses"] is not None:
        response_comparison(result, read_responses(inputs["responses"]), claim, instrument)

    result["status"] = "awaiting_responses"
    if result["blockers"]:
        result["status"] = "blocked"
    elif result["comparison"] is not None:
        result["status"] = "scored"
    return result


def verify_paths(claim_path, instrument_path, response_path=None):
    return verify(read_inputs(claim_path, instrument_path, response_path))


def replay_paths(record_path, claim_path, instrument_path, response_path=None):
    record = json.loads(Path(record_path).read_bytes())
    saved = record.get("inputs_sha256") if isinstance(record, dict) else None
    if not isinstance(saved, dict) or set(saved) != {"claim", "instrument", "responses"}:
        raise ValueError("Replay requires a saved verifier record with input fingerprints")

    inputs = read_inputs(claim_path, instrument_path, response_path)
    current = fingerprints(inputs)
    changed = [name for name in current if current[name] != saved[name]]
    if changed:
        return {"status": "inputs_changed", "changed_inputs": changed}

    verdict = verify(inputs)
    status = "reproduced" if verdict == record else "verdict_changed"
    return {"status": status, "report": verdict}
