#!/usr/bin/env python3
"""Audit facts for the TC20 repeat, kept SEPARATE by run: the paired run (22-call audit folder) and the repeat (10 calls).

  python3 backend/scripts/requirements_v2_tc20_repeat_audit.py --write      writes analysis.json next to the repeat's run files

Only facts read from the stored raw answers (whole answer, not only informational_items) and the frozen checks. No causal claim is
made here: the module records what each answer contains, and any cause is a hypothesis for a controlled experiment.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from scripts import requirements_v2_candidate_audit as A  # noqa: E402
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402
from scripts import requirements_v2_tc20_repeat as R  # noqa: E402

RUN = R.BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "run"
# The four sentences of the JD's "Reporting and Supervision" paragraph that are reporting statements (the labelled check uses the first).
REPORTING_SENTENCES = {
    "submit_deliverables": "submit deliverables",
    "it_focal_point": "in coordination with the MoNE IT focal point",
    "regular_communication": "maintain regular communication",
}
ICT_PHRASE = "ICT systems support"
FAMILIARITY_PHRASE = "Familiarity with business process"
COMM_PHRASE = "Strong communication, coordination"


def _facts(raw: str, labels: dict, jd: str) -> dict:
    checks = ev.score_answer(raw, jd, labels)["checks"]
    o = json.loads(raw)
    items = [(c, it) for c, lst in (o.get("categories") or {}).items() for it in lst]
    st = A.tc20_structure(raw, jd, labels)
    comm = [c for c, it in items if COMM_PHRASE in str(it.get("source_text", ""))]
    ict = [(c, it.get("alternatives")) for c, it in items if ICT_PHRASE in str(it.get("source_text", ""))]
    fam = [(c, it.get("alternatives")) for c, it in items if FAMILIARITY_PHRASE in str(it.get("source_text", ""))]
    return {
        "checks": {k: bool(checks[k]) for k in R.CHECKS},
        "duty_lines_found": st["duty_lines_found"],
        "reporting_sentences_in_whole_answer": {k: v in str(raw) for k, v in REPORTING_SENTENCES.items()},
        "reporting_statement_in_whole_answer": labels["expected_values"]["reporting_marker"] in raw,
        "informational_items": len(o.get("informational_items") or []),
        "ict_alternatives_kept": any(a is not None for _, a in ict),
        "familiarity_alternatives_kept": any(a is not None for _, a in fam),
        "communication_line_categories": comm,
        "communication_line_split_items": sum(1 for c, it in items if COMM_PHRASE in str(it.get("source_text", "")) and
                                              str(it.get("text", "")).lower() in ("communication", "coordination", "teamwork")),
    }


def run_facts(rows: list[dict], labels: dict, jd: str) -> dict:
    out = {}
    for arm in R.ARMS:
        calls = [{"call": r["call"], **_facts(r["raw"], labels, jd)} for r in sorted(rows, key=lambda x: x["call"]) if r["arm"] == arm]
        out[arm] = {"calls": calls,
                    "duty_zero_calls": sum(1 for c in calls if c["duty_lines_found"] == 0),
                    "reporting_missing_whole_answer": sum(1 for c in calls if not c["reporting_statement_in_whole_answer"]),
                    "informational_empty_calls": sum(1 for c in calls if c["informational_items"] == 0),
                    "ict_alternatives_kept": sum(1 for c in calls if c["ict_alternatives_kept"]),
                    "familiarity_alternatives_kept": sum(1 for c in calls if c["familiarity_alternatives_kept"]),
                    "communication_split_calls": sum(1 for c in calls if c["communication_line_split_items"] >= 3),
                    "checks_passed": {k: sum(1 for c in calls if c["checks"][k]) for k in R.CHECKS}}
    return out


def analysis(repeat_rows: list[dict], labels: dict, jd: str) -> dict:
    prev_rows = [json.loads(x) for x in (A.EVIDENCE / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    prev_rows = [r for r in prev_rows if r["jd"] == "tc20"]
    prev = run_facts(prev_rows, labels, jd)
    now = run_facts(repeat_rows, labels, jd)
    pooled = {}
    for arm in R.ARMS:
        pooled[arm] = {k: prev[arm][k] + now[arm][k] for k in ("duty_zero_calls", "reporting_missing_whole_answer", "informational_empty_calls",
                                                                 "ict_alternatives_kept", "familiarity_alternatives_kept", "communication_split_calls")}
        pooled[arm]["calls"] = len(prev[arm]["calls"]) + len(now[arm]["calls"])
    return {"runs_kept_separate": {"paired_run": {"source": "tests/fixtures/requirements_v2_candidate_v25/audit/run_v2-3_v2-5_20261010",
                                                  "arms": prev},
                                   "repeat": {"source": "tests/fixtures/requirements_v2_tc20_repeat/run", "arms": now}},
            "pooled_for_description_only": pooled,
            "note": "pooled counts are descriptive; they are not a significance test; no cause is identified"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    jd, labels = ev.load_inputs()
    rows = [json.loads(x) for x in (RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    out = analysis(rows, labels, jd)
    if args.write:
        (RUN / "analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["pooled_for_description_only"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
