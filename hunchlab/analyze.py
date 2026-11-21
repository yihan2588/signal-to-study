"""Analyze a Flashpoint export: clean, score, compare versions, check robustness, project sample needs.

    python3 -m hunchlab analyze path/to/export.csv [--key fixtures/survey-key.json]
        [--out build/analysis] [--publish build/aggregate-results.json]

`--out` holds respondent-level files (pseudonymized) and stays out of Git.
`--publish` writes aggregates only, for review.
"""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from . import stats
from .survey import KEY, clean, data_kind, funnel, load_export, load_key, score


ROOT = Path(__file__).resolve().parents[1]
PRIMARY = "perf_strict"
OUTCOMES = [
    ("perf_strict", "Performance known? (answer key)", "higher", "primary"),
    ("perf_lenient", "Performance known? (lenient key)", "higher", "exploratory"),
    ("perf_overclaim", "Wrongly says 'independently tested'", "lower", "exploratory"),
    ("comprehension", "Comprehension correct", "higher", "secondary"),
    ("trust", "Trust (1-5)", "context", "secondary"),
    ("open", "Intent to open (1-5)", "higher", "secondary"),
]
RATINGS = {"trust", "open"}
PILOT_MINIMUM = 20
NONINFERIORITY_MARGIN = 0.10
OPEN_MARGIN = 0.25  # points on the 1-5 scale; set on synthetic data before any wave-2 responses
PLAN_SIZES = [10, 20, 30, 40, 60, 80, 120]
ANSWERS = ("independent", "claimed", "doesnt_say", "not_sure")


def decide(n, effects):
    perf, comp, opening = effects["perf_strict"], effects["comprehension"], effects["open"]
    if n < PILOT_MINIMUM:
        return "pilot", f"Only {n} usable completes (the rule needs {PILOT_MINIMUM}). Report as a pilot and run a follow-up."
    if perf["ci95"][0] > 0 and comp["ci95"][0] > -NONINFERIORITY_MARGIN:
        if opening["ci95"][1] < -OPEN_MARGIN:
            return "optional", "B improves understanding of uncertainty but lowers intent to open."
        return "default", "B improves understanding of uncertainty without hurting comprehension."
    return "no clear difference", "The interval for B - A on 'performance known?' includes zero, or comprehension may drop."


SUBGROUPS = [("reader", "AI-news reading"), ("background", "Background"), ("age_group", "Age")]


def scorecard(n, effects):
    """The four checks behind 'which version is better', each pass / leaning / unclear / fail."""
    perf, over, comp, opening = (effects[k] for k in ("perf_strict", "perf_overclaim", "comprehension", "open"))

    def grade(lo, hi, mean_value, p_good, good_if_positive=True):
        if good_if_positive:
            if lo > 0:
                return "pass"
            if hi < 0:
                return "fail"
        else:
            if hi < 0:
                return "pass"
            if lo > 0:
                return "fail"
        return "leaning" if p_good is not None and p_good >= 0.9 else "unclear"

    checks = [
        {"id": "understanding", "question": "Do more people judge the claim correctly with B?",
         "grade": grade(*perf["ci95"], perf["mean_diff_B_minus_A"], perf["bayes"]["p_above_zero"])},
        {"id": "serious_mistakes", "question": "Do fewer people wrongly think the claim was independently tested?",
         "grade": grade(*over["ci95"], over["mean_diff_B_minus_A"], over["bayes"]["p_below_zero"], good_if_positive=False)},
        {"id": "comprehension", "question": f"Does comprehension hold (no drop worse than {NONINFERIORITY_MARGIN * 100:.0f} points)?",
         "grade": "pass" if comp["ci95"][0] > -NONINFERIORITY_MARGIN else "fail" if comp["ci95"][1] < -NONINFERIORITY_MARGIN else "unclear"},
        {"id": "interest", "question": f"Does intent to open hold (no drop worse than {OPEN_MARGIN} on the 1-5 scale)?",
         "grade": "pass" if opening["ci95"][0] > -OPEN_MARGIN else "fail" if opening["ci95"][1] < -OPEN_MARGIN else "unclear"},
    ]
    return {"n": n, "checks": checks}


def analyze(export_path, key_path=KEY, exclude_low_quality=None, keep_speeders=False):
    key = load_key(key_path)
    records = load_export(export_path)
    kind = data_kind(records)
    kept, exclusions = clean(records, exclude_low_quality, keep_speeders)
    responses = score(kept, key)
    roles = sorted({r.role for r in responses}, key=lambda role: role != "treated")
    effects = {role: {name: stats.paired_effect(responses, name, role) for name, *_ in OUTCOMES} for role in roles}
    answers = {}
    for r in responses:
        cell = answers.setdefault(f"{r.product_name} {r.version}", Counter())
        cell[r.performance_answer] += 1
    pooled = {}
    for r in responses:
        if r.role == "treated":
            pooled.setdefault(r.version, Counter())[r.performance_answer] += 1
    decision, why = decide(len(kept), effects.get("treated", effects[roles[0]]))
    result = {
        "survey": key["survey"],
        "wave": key.get("wave", "unspecified"),
        "data_kind": kind,
        "export_sha256": hashlib.sha256(Path(export_path).read_bytes()).hexdigest(),
        "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funnel": funnel(records),
        "n_analyzed": len(kept),
        "exclusions": [vars(e) for e in exclusions],
        "outcomes": {name: {"label": label, "good_direction": d, "status": s} for name, label, d, s in OUTCOMES},
        "effects": effects,
        "per_product": {name: stats.per_product(responses, name) for name, *_ in OUTCOMES},
        "exposure_balance": {role: stats.exposure_balance(responses, role) for role in roles},
        "exposure_effect": {role: stats.exposure_effect(responses, PRIMARY, role) for role in roles},
        "leave_one_out": {
            role: {name: stats.leave_one_out(responses, name, role) for name in ("perf_strict", "perf_overclaim")}
            for role in roles
        },
        "performance_answers": {k: {a: v.get(a, 0) for a in ANSWERS} for k, v in answers.items()},
        "performance_answers_treated_by_version": {k: {a: v.get(a, 0) for a in ANSWERS} for k, v in sorted(pooled.items())},
        "trust_by_answer": {role: stats.trust_by_answer(responses, role) for role in roles},
        "trust_calibration": {role: stats.trust_calibration(responses, role) for role in roles},
        "subgroups": {
            attribute: stats.subgroup_effects(responses, PRIMARY, "treated", attribute) for attribute, _ in SUBGROUPS
        } if "treated" in roles else {},
        "scorecard": scorecard(len(kept), effects.get("treated", effects[roles[0]])),
        "decision": {"status": decision, "reason": why},
        "role_notes": {p["role"]: p["role_note"] for p in key["products"].values() if p.get("role_note")},
    }
    return result, responses


def plan(responses):
    """Planning projections from the pilot. Labeled as projections everywhere they appear."""
    diffs = list(stats.person_differences(responses, PRIMARY, "treated").values())
    p_a = stats._mean_by(responses, PRIMARY, "treated", "A")
    return {
        "label": "PROJECTION from pilot data, not observed results",
        "baseline_A_accuracy": p_a,
        "ci_halfwidth_if_future_respondents_resemble_pilot": stats.projected_ci_halfwidth(diffs, PLAN_SIZES),
        "power_sign_test_by_true_effect": stats.simulated_power(min(p_a, 0.9), [0.10, 0.20, 0.30], PLAN_SIZES, sims=1000),
    }


def public_payload(result, sensitivity, projection):
    """Aggregates only: no respondent IDs, row numbers or comment text."""
    reasons = Counter(e["reason"].split(" (")[0] for e in result["exclusions"])
    loo = {
        role: {name: (None if v is None else {k: v[k] for k in ("full", "min", "max", "without_most_influential")})
               for name, v in by_outcome.items()}
        for role, by_outcome in result["leave_one_out"].items()
    }
    payload = {k: v for k, v in result.items() if k not in ("exclusions", "leave_one_out")}
    payload["exclusion_counts"] = dict(reasons)
    payload["leave_one_out"] = loo
    payload["sensitivity"] = {
        label: {"n": r["n_analyzed"], **{name: r["effects"]["treated"][name]["mean_diff_B_minus_A"] for name in ("perf_strict", "perf_overclaim")}}
        for label, r in sensitivity.items()
    }
    payload["projection"] = projection
    return payload


# ---------- Markdown summary ----------

def _show(name, v):
    if v is None:
        return "—"
    return f"{v:.2f}" if name in RATINGS else f"{v * 100:.0f}%"


def _diff(name, v):
    if v is None:
        return "—"
    return f"{v:+.2f}" if name in RATINGS else f"{v * 100:+.0f} pp"


def _effect_row(name, label, direction, status, e):
    lo, hi = e["ci95"]
    bayes = e["bayes"]
    if direction == "lower":
        p_good = bayes["p_below_zero"]
    elif direction == "higher":
        p_good = bayes["p_above_zero"]
    else:
        p_good = None
    return (
        f"| {label}{' **(primary)**' if status == 'primary' else ''} | {_show(name, e['mean_A'])} | {_show(name, e['mean_B'])} "
        f"| {_diff(name, e['mean_diff_B_minus_A'])} | {_diff(name, lo)} to {_diff(name, hi)} "
        f"| {e['better_with_B']}/{e['worse_with_B']}/{e['no_change']} | {e['p_sign_flip']:.2f} "
        f"| {'—' if p_good is None else f'{p_good * 100:.0f}%'} |"
    )


ROLE_TITLES = {
    "treated": "Treated products (ElevenLabs, Google TPU)",
    "control": "Control product (Claris Harbor)",
}


def summary_markdown(result, sensitivity, projection):
    lines = [
        ("# SYNTHETIC DATA, NOT REAL RESPONSES. " if result["data_kind"] == "synthetic" else "# ")
        + f"{result['survey']}: {result['wave']} analysis",
        "",
        f"Export SHA-256 `{result['export_sha256'][:16]}…`, analyzed {result['analyzed_at']}.",
        "",
        "## Sample",
        "",
        f"Started {result['funnel']['started']}, consented {result['funnel']['consented']}, "
        f"completed {result['funnel']['complete']}, analyzed **{result['n_analyzed']}**.",
        "",
        "| Row | Reason excluded |",
        "|---|---|",
    ]
    lines += [f"| {e['row']} | {e['reason']} |" for e in result["exclusions"] if e["reason"] != "incomplete"][:25]
    lines += [f"| {sum(e['reason'] == 'incomplete' for e in result['exclusions'])} rows | incomplete |", ""]
    if result["data_kind"] == "synthetic":
        lines[1:1] = ["", "> Every row was generated by `tools/make_synthetic.py`. Use it to test the pipeline only; never cite it as a finding."]
    for role, by_outcome in result["effects"].items():
        lines += [
            f"## {ROLE_TITLES.get(role, role)}: B vs A within person",
            "",
            *([f"_{result['role_notes'][role]}_", ""] if role in result.get("role_notes", {}) else []),
            "| Outcome | A | B | B − A | 95% bootstrap CI | people better/worse/same | p (sign-flip) | P(B better) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        lines += [_effect_row(n, l, d, s, by_outcome[n]) for n, l, d, s in OUTCOMES]
        lines += ["", "\"better/worse\" counts people whose score went up/down with B; for the overclaim row, up is worse. P(B better) is a Bayesian-bootstrap posterior; trust has no good direction.", ""]
    lines += ["## Per product, 'performance known?' (answer key)", "", "| Product | B shown | A | B | B − A |", "|---|---|---|---|---|"]
    for name, row in result["per_product"][PRIMARY].items():
        lines.append(f"| {name} | {row['B_shown']} | {_show(PRIMARY, row['mean_A'])} | {_show(PRIMARY, row['mean_B'])} | {_diff(PRIMARY, row['mean_diff_B_minus_A'])} |")
    bal = result["exposure_balance"].get("treated")
    exp = result["exposure_effect"].get("treated")
    if bal and exp:
        lo, hi = exp["ci95"]
        lines += [
            "",
            f"Order check (treated): B was the first exposure {bal['B_first']} times and the second {bal['B_second']} times"
            f" ({'balanced, so a constant learning effect cancels out of B − A' if bal['balanced'] else 'NOT balanced: B − A may include learning'})."
            f" Second minus first exposure: {_diff(PRIMARY, exp['mean_second_minus_first'])} ({_diff(PRIMARY, lo)} to {_diff(PRIMARY, hi)}).",
        ]
    lines += ["", "## Influence check: leave one person out (treated)", "", "| Outcome | full sample | range when dropping one person | without the most influential person |", "|---|---|---|---|"]
    for name, v in result["leave_one_out"].get("treated", {}).items():
        if v:
            lines.append(f"| {result['outcomes'][name]['label']} | {_diff(name, v['full'])} | {_diff(name, v['min'])} to {_diff(name, v['max'])} | {_diff(name, v['without_most_influential'])} |")
    lines += ["", "## 'How well is performance known?' answers", "", "| Alert | independent | claimed | doesn't say | not sure |", "|---|---|---|---|---|"]
    for alert, counts in result["performance_answers"].items():
        lines.append(f"| {alert} | " + " | ".join(str(counts[a]) for a in ANSWERS) + " |")
    for version, counts in result["performance_answers_treated_by_version"].items():
        lines.append(f"| **Treated, all {version}** | " + " | ".join(str(counts[a]) for a in ANSWERS) + " |")
    lines += ["", "## Trust by answer (treated)", "", "| Answer | A: n, mean trust | B: n, mean trust |", "|---|---|---|"]
    for answer, by_version in result["trust_by_answer"].get("treated", {}).items():
        cells = [f"{by_version[v]['n']}, {by_version[v]['mean_trust']:.2f}" if v in by_version else "—" for v in ("A", "B")]
        lines.append(f"| {answer} | {cells[0]} | {cells[1]} |")
    lines += ["", "## Trust calibration (treated)", "", "| Version | mean trust | trust when judged correctly (n) | trust when overclaimed (n) |", "|---|---|---|---|"]
    for version, row in result["trust_calibration"].get("treated", {}).items():
        def t(v):
            return "—" if v is None else f"{v:.2f}"
        lines.append(f"| {version} | {t(row['mean_trust'])} | {t(row['correct_mean_trust'])} ({row['correct_n']}) | {t(row['overclaim_mean_trust'])} ({row['overclaim_n']}) |")
    lines += ["", "## Who it helps (exploratory, primary outcome)", "", "| Split | Group | people | B − A | 95% CI | P(B better) |", "|---|---|---|---|---|---|"]
    for attribute, label in SUBGROUPS:
        for group, row in result["subgroups"].get(attribute, {}).items():
            if row.get("too_few"):
                lines.append(f"| {label} | {group} | {row['n_people']} | too few | | |")
            else:
                lo, hi = row["ci95"]
                p_better = "—" if row["p_B_better"] is None else f"{row['p_B_better'] * 100:.0f}%"
                lines.append(f"| {label} | {group} | {row['n_people']} | {_diff(PRIMARY, row['mean_diff_B_minus_A'])} | {_diff(PRIMARY, lo)} to {_diff(PRIMARY, hi)} | {p_better} |")
    lines += ["", "## Scorecard: which version is better?", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {c['question']} | **{c['grade']}** |" for c in result["scorecard"]["checks"]]
    lines += ["", "## Sensitivity (treated)", "", "| Sample | n | strict B − A | overclaim B − A |", "|---|---|---|---|"]
    for label, res in sensitivity.items():
        e = res["effects"]["treated"]
        lines.append(f"| {label} | {res['n_analyzed']} | {_diff('x', e['perf_strict']['mean_diff_B_minus_A'])} | {_diff('x', e['perf_overclaim']['mean_diff_B_minus_A'])} |")
    lines += [
        "",
        f"## Decision rule: **{result['decision']['status']}**",
        "",
        result["decision"]["reason"],
        "",
        f"## {projection['label']}",
        "",
        "95% CI half-width for B − A (primary) if new respondents resemble the pilot:",
        "",
        "| n | " + " | ".join(str(n) for n in PLAN_SIZES) + " |",
        "|---|" + "---|" * len(PLAN_SIZES),
        "| ± | " + " | ".join(f"{v * 100:.0f} pp" for v in projection["ci_halfwidth_if_future_respondents_resemble_pilot"].values()) + " |",
        "",
        f"Simulated power (exact sign test, α = 0.05) with A accuracy {projection['baseline_A_accuracy'] * 100:.0f}% (capped at 90%):",
        "",
        "| true effect | " + " | ".join(str(n) for n in PLAN_SIZES) + " |",
        "|---|" + "---|" * len(PLAN_SIZES),
    ]
    for effect, row in projection["power_sign_test_by_true_effect"].items():
        lines.append(f"| +{effect * 100:.0f} pp | " + " | ".join(f"{v * 100:.0f}%" for v in row.values()) + " |")
    return "\n".join(lines) + "\n"


def write_outputs(out, result, responses, sensitivity, projection):
    out.mkdir(parents=True, exist_ok=True)
    full = dict(result, sensitivity={k: {"n": v["n_analyzed"], "treated": v["effects"]["treated"]} for k, v in sensitivity.items()}, projection=projection)
    (out / "results.json").write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    with open(out / "alerts-long.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(responses[0].row()))
        writer.writeheader()
        writer.writerows(r.row() for r in responses)
    with open(out / "comments.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["respondent", "position", "product", "version", "comment"])
        writer.writerows([r.respondent, r.position, r.product_name, r.version, r.comment] for r in responses if r.comment)
    (out / "summary.md").write_text(summary_markdown(result, sensitivity, projection), encoding="utf-8")


def main(argv):
    parser = argparse.ArgumentParser(prog="python3 -m hunchlab analyze")
    parser.add_argument("export", type=Path)
    parser.add_argument("--key", type=Path, default=KEY)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "analysis")
    parser.add_argument("--publish", type=Path, help="write aggregate-only results (safe to commit)")
    args = parser.parse_args(argv)
    result, responses = analyze(args.export, args.key)
    if not responses:
        raise ValueError("No usable completes in this export")
    sensitivity = {
        "Main (pre-set exclusions)": result,
        "Keep Flashpoint speeders": analyze(args.export, args.key, keep_speeders=True)[0],
        "Also drop quality score < 50": analyze(args.export, args.key, exclude_low_quality=50)[0],
    }
    projection = plan(responses)
    write_outputs(args.out, result, responses, sensitivity, projection)
    print((args.out / "summary.md").read_text(encoding="utf-8"))
    print(f"Wrote {args.out}/ (results.json, alerts-long.csv, comments.csv, summary.md); keep it out of Git.")
    if args.publish:
        args.publish.parent.mkdir(parents=True, exist_ok=True)
        args.publish.write_text(json.dumps(public_payload(result, sensitivity, projection), indent=2, default=str) + "\n", encoding="utf-8")
        print(f"Published aggregates to {args.publish}")
    return 0
