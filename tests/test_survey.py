"""Tests for the survey pipeline. All data here is synthetic and built in the test."""

import csv
import tempfile
from pathlib import Path
import unittest

from hunchlab import stats
from hunchlab.analyze import analyze, decide
from hunchlab.survey import clean, load_export, load_key, score


# Exercise the fixed-order validation with one of the current instrument's two orders.
instrument = load_key()
cells = {(a["product"], a["version"]): a for a in instrument["alerts"]}
KEY = dict(instrument, fixed_order=True, alerts=[
    dict(cells[tuple(cell)], position=n)
    for n, cell in enumerate(instrument["orders"]["1"], start=1)
])
OPT = KEY["performance_options"]
PRODUCTS = KEY["products"]


def synthetic_record(row, *, status="COMPLETE", flags="", ip="ip-1", perf=None, started="2026-11-18 01:00:00"):
    record = {
        "#": str(row), "ID": f"id-{row}", "Status": status, "Started At": started,
        "Duration (minutes)": "5.0", "IP Hash": ip, "Flashpoint Checks": flags,
        "Quality Score (0-100)": "100.0", "CONSENT": "Yes, I agree",
    }
    for alert in KEY["alerts"]:
        n, product = alert["position"], PRODUCTS[alert["product"]]
        record[f"ALERT{n}_ARTICLE"] = alert["product"]
        record[f"ALERT{n}_VERSION"] = alert["version"]
        record[f"Q{n}A"] = product["comprehension_correct"]
        record[f"Q{n}B"] = OPT[(perf or {}).get(n, product["performance_correct"])]
        record[f"Q{n}C"] = "3 Moderately"
        record[f"Q{n}D"] = "2 Slightly"
        record[f"Q{n}E"] = ""
    return record


def write_export(records, path):
    header = list(records[0])
    grouped = [h if "/" not in h else "" for h in header]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(grouped)
        writer.writerow(header + ["Q2/other_text"])
        for record in records:
            writer.writerow([record[h] for h in header] + [""])


class SurveyPipelineTests(unittest.TestCase):
    def test_reads_flashpoint_two_row_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            write_export([synthetic_record(1)], path)
            records = load_export(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Status"], "COMPLETE")

    def test_excludes_incompletes_speeders_and_duplicate_ips(self):
        records = [
            synthetic_record(1, ip="a"),
            synthetic_record(2, status="INCOMPLETE", ip="b"),
            synthetic_record(3, flags="SPEEDER", ip="c"),
            synthetic_record(4, ip="a", started="2026-11-18 02:00:00"),
        ]
        kept, exclusions = clean(records)
        self.assertEqual([r["#"] for r in kept], ["1"])
        reasons = {e.row: e.reason for e in exclusions}
        self.assertEqual(reasons["2"], "incomplete")
        self.assertIn("speeder", reasons["3"])
        self.assertIn("duplicate", reasons["4"])
        kept, _ = clean(records, keep_speeders=True)
        self.assertEqual(sorted(r["#"] for r in kept), ["1", "3"])

    def test_scores_strict_lenient_and_overclaim(self):
        # Alert 1 is ElevenLabs A, alert 4 is ElevenLabs B.
        record = synthetic_record(1, perf={1: "doesnt_say", 4: "independent"})
        rows = {r.position: r for r in score([record], KEY)}
        self.assertEqual((rows[1].perf_strict, rows[1].perf_lenient, rows[1].perf_overclaim), (0, 1, 0))
        self.assertEqual((rows[4].perf_strict, rows[4].perf_lenient, rows[4].perf_overclaim), (0, 0, 1))
        self.assertEqual(rows[2].perf_strict, 1)
        self.assertEqual(rows[3].role, "control")

    def test_rejects_an_export_whose_version_order_differs_from_the_key(self):
        record = synthetic_record(1)
        record["ALERT1_VERSION"] = "B"
        with self.assertRaises(ValueError):
            score([record], KEY)

    def test_rejects_unknown_answer_text(self):
        record = synthetic_record(1)
        record["Q2B"] = "Something else"
        with self.assertRaises(ValueError):
            score([record], KEY)

    def test_paired_effect_is_within_person(self):
        # Three people: B fixes one treated error for each of them.
        records = [
            synthetic_record(i, ip=f"ip{i}", perf={1: "not_sure"}) for i in range(1, 4)
        ]
        effect = stats.paired_effect(score(clean(records)[0], KEY), "perf_strict", "treated")
        self.assertEqual(effect["better_with_B"], 3)
        self.assertAlmostEqual(effect["mean_diff_B_minus_A"], 0.5)
        self.assertAlmostEqual(effect["p_sign_flip"], 0.25)

    def test_end_to_end_and_small_samples_are_reported_as_pilots(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            write_export([synthetic_record(i, ip=f"ip{i}") for i in range(1, 6)], path)
            result, responses = analyze(path)
        self.assertEqual(result["n_analyzed"], 5)
        self.assertEqual(len(responses), 30)
        self.assertEqual(result["decision"]["status"], "pilot")


class SyntheticTests(unittest.TestCase):
    def test_synthetic_exports_are_labeled_and_cannot_be_mixed_with_real(self):
        from hunchlab.survey import data_kind
        real = synthetic_record(1)
        fake = dict(synthetic_record(2), **{"Sample Source": "SYNTHETIC: generated"})
        self.assertEqual(data_kind([real]), "real")
        self.assertEqual(data_kind([fake]), "synthetic")
        with self.assertRaises(ValueError):
            data_kind([real, fake])

    def test_synthetic_results_label_carries_through_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            rows = [dict(synthetic_record(i, ip=f"ip{i}"), **{"Sample Source": "SYNTHETIC"}) for i in range(1, 4)]
            write_export(rows, path)
            result, _ = analyze(path)
        self.assertEqual(result["data_kind"], "synthetic")


class CounterbalancedTests(unittest.TestCase):
    def test_scores_a_counterbalanced_order_and_tracks_exposure(self):
        key = dict(KEY, fixed_order=False)
        record = synthetic_record(1)
        # Swap alerts 1 and 4 so this person sees ElevenLabs B before A.
        for col in ("ARTICLE", "VERSION"):
            record[f"ALERT1_{col}"], record[f"ALERT4_{col}"] = record[f"ALERT4_{col}"], record[f"ALERT1_{col}"]
        rows = {r.position: r for r in score([record], key)}
        self.assertEqual((rows[1].version, rows[1].exposure), ("B", 1))
        self.assertEqual((rows[4].version, rows[4].exposure), ("A", 2))

    def test_fixed_order_key_rejects_a_swapped_export(self):
        record = synthetic_record(1)
        for col in ("ARTICLE", "VERSION"):
            record[f"ALERT1_{col}"], record[f"ALERT4_{col}"] = record[f"ALERT4_{col}"], record[f"ALERT1_{col}"]
        with self.assertRaises(ValueError):
            score([record], KEY)


class StatsTests(unittest.TestCase):
    def test_bayesian_bootstrap_and_leave_one_out(self):
        self.assertEqual(stats.bayesian_bootstrap([0.5, 0.5, 0.5])["p_above_zero"], 1.0)
        self.assertIsNone(stats.bayesian_bootstrap([0, 0])["p_above_zero"])
        records = [synthetic_record(i, ip=f"ip{i}", perf={1: "not_sure"} if i == 1 else None) for i in range(1, 5)]
        loo = stats.leave_one_out(score(clean(records)[0], KEY), "perf_strict", "treated")
        self.assertAlmostEqual(loo["full"], 0.125)
        self.assertAlmostEqual(loo["without_most_influential"], 0.0)

    def test_sign_test_is_exact(self):
        self.assertAlmostEqual(stats.sign_test_p([1] * 5), 2 / 32)
        self.assertEqual(stats.sign_test_p([0, 0]), 1.0)

    def test_scorecard_grades_each_check(self):
        from hunchlab.analyze import scorecard
        def eff(lo, hi, p_up=0.5, p_down=0.5):
            return {"ci95": [lo, hi], "mean_diff_B_minus_A": (lo + hi) / 2, "bayes": {"p_above_zero": p_up, "p_below_zero": p_down}}
        effects = {"perf_strict": eff(0.05, 0.3), "perf_overclaim": eff(-0.2, 0.02, p_down=0.95),
                   "comprehension": eff(-0.05, 0.05), "open": eff(-0.6, -0.3)}
        grades = {c["id"]: c["grade"] for c in scorecard(40, effects)["checks"]}
        self.assertEqual(grades, {"understanding": "pass", "serious_mistakes": "leaning", "comprehension": "pass", "interest": "fail"})

    def test_subgroups_report_small_groups_as_too_few(self):
        records = [dict(synthetic_record(i, ip=f"ip{i}"), Q4="Daily" if i < 7 else "Rarely") for i in range(1, 9)]
        groups = stats.subgroup_effects(score(clean(records)[0], KEY), "perf_strict", "treated", "reader")
        self.assertEqual(groups["frequent"]["n_people"], 6)
        self.assertTrue(groups["occasional"]["too_few"])

    def test_decision_rule_needs_enough_people_and_a_clear_effect(self):
        good = {"ci95": [0.05, 0.3]}
        flat = {"ci95": [-0.05, 0.05]}
        effects = {"perf_strict": good, "comprehension": flat, "open": flat}
        self.assertEqual(decide(30, effects)[0], "default")
        self.assertEqual(decide(30, dict(effects, open={"ci95": [-0.6, -0.3]}))[0], "optional")
        self.assertEqual(decide(30, dict(effects, open={"ci95": [-0.2, -0.05]}))[0], "default")
        self.assertEqual(decide(30, dict(effects, perf_strict=flat))[0], "no clear difference")
        self.assertEqual(decide(12, effects)[0], "pilot")


if __name__ == "__main__":
    unittest.main()
