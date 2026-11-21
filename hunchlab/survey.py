"""Load, clean and score a Flashpoint export of the AI News Alerts Survey.

The output is one row per respondent per alert. Raw exports stay outside Git;
only pseudonymous IDs (R01, R02, ...) leave this module.
"""

import csv
from dataclasses import dataclass, asdict
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "fixtures" / "survey-key.json"
CONSENT_YES = "Yes, I agree"
SYNTHETIC_MARK = "SYNTHETIC"


@dataclass(frozen=True)
class Exclusion:
    row: str
    respondent: str
    reason: str


@dataclass(frozen=True)
class AlertResponse:
    respondent: str
    position: int
    product: str
    product_name: str
    role: str
    version: str
    exposure: int
    comprehension: int
    performance_answer: str
    perf_strict: int
    perf_lenient: int
    perf_overclaim: int
    trust: int
    open: int
    comment: str
    age_group: str = ""
    background: str = ""
    reader: str = ""

    def row(self):
        return asdict(self)


def load_key(path=KEY):
    key = json.loads(Path(path).read_text(encoding="utf-8"))
    seen = set()
    for alert in key["alerts"]:
        cell = (alert["product"], alert["version"])
        if alert["product"] not in key["products"] or alert["version"] not in ("A", "B") or cell in seen:
            raise ValueError(f"Invalid or duplicate alert in key: {alert}")
        seen.add(cell)
    if key.get("fixed_order"):
        positions = [alert["position"] for alert in key["alerts"]]
        if positions != list(range(1, len(positions) + 1)):
            raise ValueError("A fixed-order key needs positions 1..n in order")
    return key


def load_export(path):
    """Read a Flashpoint CSV. Flashpoint writes a grouped header row first, then the real one."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.reader(handle) if any(cell.strip() for cell in row)]
    if len(rows) > 1 and "Status" in rows[1] and any("/" in cell for cell in rows[1]):
        header, body = rows[1], rows[2:]
    else:
        header, body = rows[0], rows[1:]
    if "Status" not in header or "ID" not in header:
        raise ValueError("This does not look like a Flashpoint survey export")
    return [dict(zip(header, row)) for row in body]


def data_kind(records):
    """'real' or 'synthetic'. Synthetic rows carry SYNTHETIC in Sample Source; mixing is refused."""
    kinds = {
        "synthetic" if record.get("Sample Source", "").upper().startswith(SYNTHETIC_MARK) else "real"
        for record in records
    }
    if len(kinds) > 1:
        raise ValueError("This export mixes real and synthetic responses; analyze them separately")
    return kinds.pop() if kinds else "real"


def scale(value):
    value = value.strip()
    if not value or not value[0].isdigit():
        raise ValueError(f"Expected a 1-5 rating, got {value!r}")
    return int(value[0])


def clean(records, exclude_low_quality=None, keep_speeders=False):
    """Return (kept, exclusions). Rules apply in order; each row gets its first matching reason."""
    kept, exclusions, seen_complete = [], [], set()
    ordered = sorted(records, key=lambda record: record.get("Started At", ""))
    for record in ordered:
        reason = None
        quality = record.get("Quality Score (0-100)", "").strip()
        checks = " ".join(record.get(name, "") for name in ("Flashpoint Checks", "Quality Flags"))
        if record.get("Status") != "COMPLETE":
            reason = (record.get("Status") or "missing status").lower()
        elif record.get("CONSENT") != CONSENT_YES:
            reason = "no consent"
        elif record.get("Quality Excluded", "").strip().lower() in ("true", "yes", "1"):
            reason = f"excluded by Flashpoint: {record.get('Exclusion Reason') or 'no reason given'}"
        elif "SPEEDER" in checks and not keep_speeders:
            reason = f"speeder ({record.get('Duration (minutes)')} min, flagged by Flashpoint)"
        elif record.get("IP Hash") and record["IP Hash"] in seen_complete:
            reason = "duplicate complete from the same IP hash"
        elif exclude_low_quality is not None and quality and float(quality) < exclude_low_quality:
            reason = f"quality score {quality} below {exclude_low_quality}"
        if reason:
            exclusions.append(Exclusion(record.get("#", "?"), record.get("ID", "?"), reason))
        else:
            seen_complete.add(record.get("IP Hash"))
            kept.append(record)
    return kept, exclusions


def demographics(record):
    age = record.get("Q1", "").strip()
    role = record.get("Q3", "").strip()
    frequency = record.get("Q4", "").strip()
    return {
        "age_group": age if age in ("18–24", "25–34") else "35+ or not given",
        "background": "tech" if "tech-related" in role else "non-tech" if "non-tech" in role else "other",
        "reader": "frequent" if frequency in ("Daily", "A few times a week") else "occasional",
    }


def score(records, key):
    """Reshape kept records to one scored row per respondent per alert.

    Product and version come from the export's ALERTn_ARTICLE / ALERTn_VERSION columns,
    so counterbalanced surveys work. A fixed-order key is also checked against them.
    """
    labels = {text: name for name, text in key["performance_options"].items()}
    cells = {(a["product"], a["version"]): a for a in key["alerts"]}
    responses = []
    for number, record in enumerate(records, start=1):
        respondent = f"R{number:02d}"
        people = demographics(record)
        seen_products = set()
        for n in range(1, len(key["alerts"]) + 1):
            article = record.get(f"ALERT{n}_ARTICLE", "").strip()
            version = record.get(f"ALERT{n}_VERSION", "").strip()
            alert = cells.get((article, version))
            if alert is None:
                raise ValueError(f"{respondent} alert {n}: article {article!r} version {version!r} is not in the key")
            if key.get("fixed_order") and alert["position"] != n:
                raise ValueError(
                    f"{respondent} alert {n}: export says article {article!r} version {version!r}, "
                    f"but the fixed-order key puts that alert at position {alert['position']}"
                )
            product = key["products"][article]
            answer_text = record[f"Q{n}B"].strip()
            if answer_text not in labels:
                raise ValueError(f"{respondent} alert {n}: unknown performance answer {answer_text!r}")
            answer = labels[answer_text]
            lenient = key["lenient_scoring"][f"{product['role']}_{version}"]
            exposure = 2 if article in seen_products else 1
            seen_products.add(article)
            responses.append(
                AlertResponse(
                    respondent=respondent,
                    position=n,
                    product=product["id"],
                    product_name=product["name"],
                    role=product["role"],
                    version=version,
                    exposure=exposure,
                    comprehension=int(record[f"Q{n}A"].strip() == product["comprehension_correct"]),
                    performance_answer=answer,
                    perf_strict=int(answer == product["performance_correct"]),
                    perf_lenient=int(answer in lenient),
                    perf_overclaim=int(answer == "independent"),
                    trust=scale(record[f"Q{n}C"]),
                    open=scale(record[f"Q{n}D"]),
                    comment=record.get(f"Q{n}E", "").strip(),
                    **people,
                )
            )
    return responses


def funnel(records):
    started = len(records)
    consented = sum(record.get("CONSENT") == CONSENT_YES for record in records)
    complete = sum(record.get("Status") == "COMPLETE" for record in records)
    return {"started": started, "consented": consented, "complete": complete}
