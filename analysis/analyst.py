"""Validate survey comment citations and count distinct respondents."""
import re


def obj(**props):
    return dict(type="object", properties=props, required=list(props), additionalProperties=False)


def arr(item, limit):
    return dict(type="array", items=item, maxItems=limit)


def screen_comment(text):
    return re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|(?<!\w)\+?\d[\d ().-]{7,}\d', '[redacted]', text)


def check(out, comments, pairs):
    results = []
    for theme in out["themes"]:
        seen = set()
        item = {"finding": theme["finding"], "follow_up": theme["follow_up"]}
        for role in ("support","contrary"):
            refs = theme[role]
            if role == "support" and not refs:
                raise ValueError("Every retained theme needs support.")
            readers = set()
            for ref in refs:
                key, quote = ref["id"], ref["quote"]
                if key in seen or key not in comments or not quote.strip() or quote not in comments[key]["text"]:
                    raise ValueError(f"Invalid, repeated or conflicting citation {key}. Use an exact substring.")
                seen.add(key)
                readers.add(comments[key]["reader"])
            item[role] = refs
            item[role + "_readers"] = sorted(readers)
            item[role + "_count"] = len(readers)
        results.append(item)
    selected = []
    for item in out["paired"]:
        key = item["key"]
        if key not in pairs or any(x["key"] == key for x in selected):
            raise ValueError("Use distinct transition keys from the packet.")
        selected.append({**item, "support_count": len(set(pairs[key])), "readers": pairs[key]})
    return {"comment_findings": results, "paired_findings": selected}
