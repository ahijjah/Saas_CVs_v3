"""Offline replay of STORED TC20 answers through the coverage prototype. No model is called; stored files are read, never changed.

Reports two things separately:
  * coverage: which requirement units of the JD the answer accounts for (this prototype);
  * OR/AND and routing correctness: the frozen checks in scripts/requirements_v2_tc20_eval.py, reused read-only.
Coverage is not extraction accuracy. A replay can show that a stored answer omits a line; it cannot show that a new prompt does better.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
sys.path[:0] = [str(HERE), str(BACKEND / "scripts")]
import coverage as C  # noqa: E402
import inventory as I  # noqa: E402
import requirements_v2_tc20_eval as frozen  # noqa: E402  (read-only reuse: score_answer and _items)

FIX = BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20"
SOURCES = {
    "repeat_gpt-4.1": BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run" / "calls.jsonl",
    "audit_gpt-4o-mini": FIX / "audit" / "run_tc20_v2-3_v2-4" / "calls.jsonl",
}
ANNOTATION = HERE / "annotations" / "tc20.json"
RESULTS = HERE / "results"


def load_jd():
    jd = json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))["description"]
    labels = json.loads((FIX / "expected_labels.json").read_text(encoding="utf-8"))
    if I.sha256_text(jd) != labels["jd_sha256"]:
        raise SystemExit("TC20 JD does not match the frozen label hash")
    return jd, labels


def answer_quotes(raw: str) -> list:
    """Every quote the answer makes: categories items, non-scoreable conditions and informational items (all of them account for lines)."""
    obj = json.loads(raw)
    quotes = [it.get("source_text") for _, it in frozen._items(obj)]
    for key in ("non_scoreable_requirements", "informational_items"):
        quotes += [c.get("source_text") for c in (obj.get(key) or []) if isinstance(c, dict)]
    return quotes


def replay(jd, labels, inv, path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        row = {"arm": rec["arm"], "call": rec["call"]}
        try:
            quotes = answer_quotes(rec["raw"])
        except (TypeError, ValueError):
            row["invalid_json"] = True
            rows.append(row)
            continue
        row["invalid_json"] = False
        cov = C.cover(inv, jd, quotes)
        row["coverage"] = cov
        row["frozen_checks"] = frozen.score_answer(rec["raw"], jd, labels)["checks"]
        rows.append(row)
    return rows


def duty_omissions(row, duty_lines):
    """Duty lines whose unit is omitted (not merely partial or ambiguous)."""
    out = []
    for u in row["coverage"]["units"]:
        if u["status"] == "omitted" and u["text"].strip() in duty_lines:
            out.append(u["text"].strip())
    return out


def main() -> int:
    jd, labels = load_jd()
    inv = I.build_inventory(jd, json.loads(ANNOTATION.read_text(encoding="utf-8")))
    duties = set(labels["expected_values"]["duty_lines"])
    report = {"jd_sha256": inv.jd_sha256, "inventory": inv.diagnostics, "cover_threshold": C.COVER_THRESHOLD, "sources": {}}
    for name, path in SOURCES.items():
        rows = replay(jd, labels, inv, path)
        per = []
        for r in rows:
            if r["invalid_json"]:
                per.append({"arm": r["arm"], "call": r["call"], "invalid_json": True})
                continue
            cov = r["coverage"]
            per.append({"arm": r["arm"], "call": r["call"], "invalid_json": False,
                        "coverage": cov["summary"], "quote_counts": cov["quote_counts"], "repeated_quotes_in_answer": cov["repeated_quotes_in_answer"],
                        "duty_omissions": duty_omissions(r, duties),
                        "omitted_units": [u["text"] for u in cov["units"] if u["status"] == "omitted"],
                        "partial_units": [u["text"] for u in cov["units"] if u["status"] == "partial"],
                        "ambiguous_units": [u["text"] for u in cov["units"] if u["status"] == "ambiguous"],
                        "frozen_checks": r["frozen_checks"]})
        report["sources"][name] = per
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "replay.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print_summary(report)
    return 0


def print_summary(report):
    for name, per in report["sources"].items():
        print(f"== {name}")
        for p in per:
            if p["invalid_json"]:
                print(f"  {p['arm']} call {p['call']}: invalid JSON")
                continue
            s = p["coverage"]
            print(f"  {p['arm']} call {p['call']}: covered {s['covered']}/{s['requirement_units']}, partial {s['partial']}, "
                  f"ambiguous {s['ambiguous']}, omitted {s['omitted']}, duty omissions {len(p['duty_omissions'])}, "
                  f"| frozen checks failing: {[k for k, v in p['frozen_checks'].items() if not v] or 'none'}")


if __name__ == "__main__":
    raise SystemExit(main())
