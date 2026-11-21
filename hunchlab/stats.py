"""Small-sample paired statistics using only the standard library.

Every respondent saw both versions, so effects are computed within person:
for each respondent, mean(outcome | B) - mean(outcome | A).
"""

from itertools import product as cartesian
from math import comb
import random
from statistics import mean


def person_differences(responses, outcome, role):
    by_person = {}
    for response in responses:
        if response.role != role:
            continue
        cell = by_person.setdefault(response.respondent, {"A": [], "B": []})
        cell[response.version].append(getattr(response, outcome))
    return {
        person: mean(cell["B"]) - mean(cell["A"])
        for person, cell in sorted(by_person.items())
        if cell["A"] and cell["B"]
    }


def bootstrap_ci(values, reps=10000, seed=7, level=0.95):
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean(rng.choices(values, k=n)) for _ in range(reps))
    tail = (1 - level) / 2
    return means[int(tail * reps)], means[int((1 - tail) * reps) - 1]


def sign_flip_p(values, mc_reps=20000, seed=11):
    """Two-sided permutation p-value for mean(values) == 0 by flipping signs within person."""
    values = [v for v in values if v != 0]
    if not values:
        return 1.0
    observed = abs(sum(values))
    if len(values) <= 16:
        flips = list(cartesian((1, -1), repeat=len(values)))
    else:
        rng = random.Random(seed)
        flips = [[rng.choice((1, -1)) for _ in values] for _ in range(mc_reps)]
    extreme = sum(abs(sum(s * v for s, v in zip(signs, values))) >= observed - 1e-12 for signs in flips)
    return extreme / len(flips)


def sign_test_p(values):
    """Exact two-sided sign test on the non-zero person differences."""
    positive = sum(v > 0 for v in values)
    negative = sum(v < 0 for v in values)
    n = positive + negative
    if n == 0:
        return 1.0
    k = min(positive, negative)
    p = 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p)


def paired_effect(responses, outcome, role):
    diffs = person_differences(responses, outcome, role)
    values = list(diffs.values())
    low, high = bootstrap_ci(values)
    return {
        "outcome": outcome,
        "role": role,
        "n_people": len(values),
        "mean_A": _mean_by(responses, outcome, role, "A"),
        "mean_B": _mean_by(responses, outcome, role, "B"),
        "mean_diff_B_minus_A": mean(values) if values else None,
        "ci95": [low, high],
        "better_with_B": sum(v > 0 for v in values),
        "worse_with_B": sum(v < 0 for v in values),
        "no_change": sum(v == 0 for v in values),
        "p_sign_flip": sign_flip_p(values),
        "p_sign_test": sign_test_p(values),
        "bayes": bayesian_bootstrap(values),
    }


def _mean_by(responses, outcome, role, version):
    values = [getattr(r, outcome) for r in responses if r.role == role and r.version == version]
    return mean(values) if values else None


def cell_counts(responses, outcome):
    """k/n per product and version, for reporting next to every summary."""
    table = {}
    for r in responses:
        cell = table.setdefault(r.product_name, {}).setdefault(r.version, {"k": 0, "n": 0, "position": r.position})
        cell["k"] += getattr(r, outcome)
        cell["n"] += 1
    return table


def projected_ci_halfwidth(values, sizes, reps=4000, seed=3):
    """If future respondents resemble the current ones, how wide would the 95% CI be at size n?

    A projection from the pilot, not data.
    """
    rng = random.Random(seed)
    out = {}
    for n in sizes:
        means = sorted(mean(rng.choices(values, k=n)) for _ in range(reps))
        out[n] = (means[int(0.975 * reps) - 1] - means[int(0.025 * reps)]) / 2
    return out


def simulated_power(p_a, effects, sizes, items=2, sims=2000, alpha=0.05, seed=5):
    """Power of the exact sign test to detect B - A = effect, simulated.

    Each simulated person answers `items` treated alerts in each version, correct with
    probability p_a (A) or p_a + effect (B), independently. A planning model, not data.
    """
    rng = random.Random(seed)
    table = {}
    for effect in effects:
        p_b = min(max(p_a + effect, 0.0), 1.0)
        row = {}
        for n in sizes:
            hits = 0
            for _ in range(sims):
                diffs = []
                for _ in range(n):
                    a = sum(rng.random() < p_a for _ in range(items))
                    b = sum(rng.random() < p_b for _ in range(items))
                    diffs.append(b - a)
                if sign_test_p(diffs) < alpha:
                    hits += 1
            row[n] = hits / sims
        table[round(effect, 3)] = row
    return table


def bayesian_bootstrap(values, draws=4000, seed=13):
    """Posterior probability that the mean person difference is above / below zero.

    Rubin's Bayesian bootstrap: Dirichlet(1, ..., 1) weights over respondents.
    """
    if not any(values):
        return {"p_above_zero": None, "p_below_zero": None}
    rng = random.Random(seed)
    above = below = 0
    for _ in range(draws):
        weights = [rng.gammavariate(1.0, 1.0) for _ in values]
        total = sum(weights)
        m = sum(w * v for w, v in zip(weights, values)) / total
        above += m > 1e-12
        below += m < -1e-12
    return {"p_above_zero": above / draws, "p_below_zero": below / draws}


def leave_one_out(responses, outcome, role):
    diffs = person_differences(responses, outcome, role)
    if len(diffs) < 3:
        return None
    full = mean(diffs.values())
    rows = []
    for person in diffs:
        rest = [v for p, v in diffs.items() if p != person]
        rows.append((person, mean(rest)))
    most = max(rows, key=lambda row: abs(row[1] - full))
    return {
        "full": full,
        "min": min(v for _, v in rows),
        "max": max(v for _, v in rows),
        "most_influential": most[0],
        "without_most_influential": most[1],
    }


def per_product(responses, outcome):
    """B - A per product, with which exposure B had (1 = seen first, 2 = seen second)."""
    out = {}
    for name in sorted({r.product_name for r in responses}):
        subset = [r for r in responses if r.product_name == name]
        role = subset[0].role
        diffs = list(person_differences(subset, outcome, role).values())
        b_exposures = sorted({r.exposure for r in subset if r.version == "B"})
        out[name] = {
            "role": role,
            "B_shown": "second" if b_exposures == [2] else "first" if b_exposures == [1] else "mixed",
            "n_people": len(diffs),
            "mean_A": _mean_by(subset, outcome, role, "A"),
            "mean_B": _mean_by(subset, outcome, role, "B"),
            "mean_diff_B_minus_A": mean(diffs) if diffs else None,
        }
    return out


def exposure_balance(responses, role):
    """How many B answers were first vs second exposures. Balanced means order cancels in B - A."""
    first = sum(r.version == "B" and r.exposure == 1 for r in responses if r.role == role)
    second = sum(r.version == "B" and r.exposure == 2 for r in responses if r.role == role)
    return {"B_first": first, "B_second": second, "balanced": first == second}


def exposure_effect(responses, outcome, role):
    """Within person: mean outcome on second exposure minus first. Learning / repetition check."""
    by_person = {}
    for r in responses:
        if r.role == role:
            by_person.setdefault(r.respondent, {1: [], 2: []})[r.exposure].append(getattr(r, outcome))
    values = [mean(c[2]) - mean(c[1]) for c in by_person.values() if c[1] and c[2]]
    low, high = bootstrap_ci(values)
    return {"mean_second_minus_first": mean(values) if values else None, "ci95": [low, high]}


def trust_by_answer(responses, role):
    """Mean trust for each 'performance known?' answer, by version. Calibrated readers should trust
    unverified claims less once they see them as unverified."""
    table = {}
    for r in responses:
        if r.role != role:
            continue
        cell = table.setdefault(r.performance_answer, {}).setdefault(r.version, [])
        cell.append(r.trust)
    return {
        answer: {version: {"n": len(v), "mean_trust": mean(v)} for version, v in sorted(by_version.items())}
        for answer, by_version in table.items()
    }


def subgroup_effects(responses, outcome, role, attribute, minimum=5):
    """B - A within person, split by a respondent attribute. Exploratory: groups are small."""
    out = {}
    for value in sorted({getattr(r, attribute) for r in responses}):
        subset = [r for r in responses if getattr(r, attribute) == value]
        diffs = list(person_differences(subset, outcome, role).values())
        if len(diffs) < minimum:
            out[value] = {"n_people": len(diffs), "too_few": True}
            continue
        low, high = bootstrap_ci(diffs)
        out[value] = {
            "n_people": len(diffs),
            "mean_diff_B_minus_A": mean(diffs),
            "ci95": [low, high],
            "p_B_better": bayesian_bootstrap(diffs)["p_above_zero"],
        }
    return out


def trust_calibration(responses, role):
    """Mean trust among readers who judged the claim correctly vs those who overclaimed, by version.

    A well-calibrated alert leaves readers who see a claim as unverified trusting it less than
    readers who think it was independently confirmed.
    """
    out = {}
    for version in ("A", "B"):
        rows = [r for r in responses if r.role == role and r.version == version]
        correct = [r.trust for r in rows if r.perf_strict]
        overclaim = [r.trust for r in rows if r.perf_overclaim]
        out[version] = {
            "mean_trust": mean(r.trust for r in rows) if rows else None,
            "correct_n": len(correct),
            "correct_mean_trust": mean(correct) if correct else None,
            "overclaim_n": len(overclaim),
            "overclaim_mean_trust": mean(overclaim) if overclaim else None,
        }
    return out
