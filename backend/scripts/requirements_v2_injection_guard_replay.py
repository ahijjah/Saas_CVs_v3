"""
Offline replay of stored model responses through the FROZEN parser and the candidate injection guard (requirements-v2-injection-guard-1).
Reports, per stored call, the readiness the frozen parser gives and the readiness with the guard, under BOTH classification-policy
settings, plus the guard's issues. This is a readiness report: it is separate from, and never changes, the official benchmark gates
(the script re-computes the official gates from the same answers and prints them unchanged). No model, network or database.

  python scripts/requirements_v2_injection_guard_replay.py [--run DIR ...] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (frozen scorer)
from parser_candidates.requirements_v2_injection_guard_1 import GUARD_VERSION, guarded_readiness, inspect_result  # noqa: E402
from services.requirements_v2.extraction import parse_response  # noqa: E402
from services.requirements_v2.readiness import compute_readiness  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
RUNS = {"v2-1 baseline": BACKEND / "benchmark_results" / "requirements_v2" / "baseline_v2-1",
        "v2-2 candidate": BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1"}


def replay_run(cases: list[dict], run_dir: Path) -> dict:
    by_id = {c["id"]: c for c in cases}
    rows, answers = [], {"run1": {}, "run2": {}}
    for line in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("raw") or rec.get("quarantined"):
            continue
        answers[rec["run"]][rec["case"]] = {"raw": rec["raw"], "finish_reason": rec["finish_reason"]}
        case = by_id[rec["case"]]
        res = parse_response(rec["raw"], case["jd"], rec["finish_reason"])
        if not res.ok:
            continue
        review = inspect_result(case["jd"], res)
        row = {"case": rec["case"], "run": rec["run"], "issues": [{"kind": i["kind"], "category": i["category"], "item": i.get("item_text"),
               "rules": i["rule_codes"], "directed_value": i["evidence"].get("directed_value")} for i in review["issues"]],
               "ai_directed_sentences": len(review["instruction_spans"])}
        for pol, name in ((True, "policy_ack_required"), (False, "policy_ack_not_required")):
            row[f"frozen_{name}"] = compute_readiness(res.requirements, require_classification_acknowledgment=pol).state
            row[f"guarded_{name}"] = guarded_readiness(res.requirements, review, require_classification_acknowledgment=pol).state
        row["changed"] = row["frozen_policy_ack_required"] != row["guarded_policy_ack_required"] or row["frozen_policy_ack_not_required"] != row["guarded_policy_ack_not_required"]
        rows.append(row)
    scored = ev.score_runs(cases, answers)
    return {"rows": rows, "official_gates": ev.gates(scored)}


def render(report: dict) -> str:
    L = [f"# Injection guard replay ({GUARD_VERSION})", "",
         "Readiness with the guard versus the frozen parser, per stored call. This is NOT a benchmark result: the official gates below are "
         "recomputed from the same answers by the frozen scorer and are unaffected by the guard.", ""]
    for name, rep in report.items():
        changed = [r for r in rep["rows"] if r["changed"]]
        L += [f"## {name}", "", f"Calls replayed: {len(rep['rows'])}; readiness changed by the guard: {len(changed)}; calls with AI-directed sentences in the JD: "
              f"{sum(1 for r in rep['rows'] if r['ai_directed_sentences'])}", ""]
        if changed:
            L += ["| case | run | frozen (ack required / not required) | with guard (both) | issues |", "|---|---|---|---|---|"]
            for r in changed:
                iss = "; ".join(f"{i['kind']}:{i['item'] or i['category']}" + (f" (directed {i['directed_value']})" if i["directed_value"] else "") for i in r["issues"])
                L.append(f"| {r['case']} | {r['run']} | {r['frozen_policy_ack_required']} / {r['frozen_policy_ack_not_required']} | "
                         f"{r['guarded_policy_ack_required']} / {r['guarded_policy_ack_not_required']} | {iss} |")
        unflagged = [r for r in rep["rows"] if r["ai_directed_sentences"] and not r["issues"]]
        if unflagged:
            L += ["", "AI-directed sentences found but nothing contaminated (not blocking): " + ", ".join(f"{r['case']} {r['run']}" for r in unflagged)]
        L += ["", "Official gates (frozen scorer, unchanged by the guard): " + ", ".join(f"{k.split()[0]} {'PASS' if v else 'FAIL'}" for k, v in rep["official_gates"].items()), ""]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="append", help="NAME=DIR (default: the stored v2-1 baseline and v2-2 runs)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    runs = dict(x.split("=", 1) for x in a.run) if a.run else {k: str(v) for k, v in RUNS.items()}
    cases = ev.load_cases()
    report = {name: replay_run(cases, Path(d)) for name, d in runs.items()}
    md = render(report)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "REPORT.md").write_text(md, encoding="utf-8")
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
