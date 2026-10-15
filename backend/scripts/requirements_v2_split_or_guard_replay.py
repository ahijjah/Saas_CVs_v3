"""
Offline replay of stored model responses through the FROZEN parser, the candidate injection guard and the candidate split-OR guard.
For each stored call it reports the frozen readiness, the readiness with the injection guard alone and the composed readiness (both guards),
under BOTH classification-policy settings, plus the split-OR groups found. This is a readiness report, separate from and never changing the
official benchmark gates (recomputed here by the frozen scorer from the same answers). No model, network or database.

  python scripts/requirements_v2_split_or_guard_replay.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (frozen scorer)
from parser_candidates.requirements_v2_injection_guard_1 import guarded_readiness, inspect_result as inspect_injection  # noqa: E402
from parser_candidates.requirements_v2_split_or_guard_1 import SPLIT_OR_VERSION, composed_readiness, inspect_result as inspect_split_or  # noqa: E402
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
        inj, sor = inspect_injection(case["jd"], res), inspect_split_or(res)
        row = {"case": rec["case"], "run": rec["run"],
               "split_or": [{"category": i["category"], "form": i["form"], "options": i["options"], "items": list(i["item_texts"].values()),
                             "evidence": i["shared_evidence"]} for i in sor["issues"]],
               "injection_issues": [i["kind"] for i in inj["issues"]]}
        for pol, name in ((True, "ack_required"), (False, "ack_not_required")):
            row[f"frozen_{name}"] = compute_readiness(res.requirements, require_classification_acknowledgment=pol).state
            row[f"injection_only_{name}"] = guarded_readiness(res.requirements, inj, require_classification_acknowledgment=pol).state
            row[f"composed_{name}"] = composed_readiness(res.requirements, inj, sor, require_classification_acknowledgment=pol).state
        row["changed_by_split_or"] = (row["composed_ack_required"] != row["injection_only_ack_required"]
                                      or row["composed_ack_not_required"] != row["injection_only_ack_not_required"])
        row["changed_vs_frozen"] = (row["composed_ack_required"] != row["frozen_ack_required"] or row["composed_ack_not_required"] != row["frozen_ack_not_required"])
        rows.append(row)
    scored = ev.score_runs(cases, answers)
    return {"rows": rows, "official_gates": ev.gates(scored)}


def render(report: dict) -> str:
    L = [f"# Split-OR guard replay ({SPLIT_OR_VERSION}, composed with the injection guard)", "",
         "Readiness per stored call: frozen parser, with the injection guard only, and composed with the split-OR guard. This is NOT a benchmark result: "
         "the official gates below are recomputed from the same answers by the frozen scorer and are unaffected by either guard.", ""]
    for name, rep in report.items():
        flagged = [r for r in rep["rows"] if r["split_or"]]
        L += [f"## {name}", "", f"Calls replayed: {len(rep['rows'])}; calls with a split-OR group: {len(flagged)}; readiness changed by the split-OR guard "
              f"(beyond the injection guard): {sum(1 for r in rep['rows'] if r['changed_by_split_or'])}", ""]
        if flagged:
            L += ["| case | run | form | options | frozen (ack req / not req) | injection guard only | composed (both policies) |", "|---|---|---|---|---|---|---|"]
            for r in flagged:
                g = r["split_or"][0]
                L.append(f"| {r['case']} | {r['run']} | {g['form']} | {' / '.join(g['options'])} | {r['frozen_ack_required']} / {r['frozen_ack_not_required']} | "
                         f"{r['injection_only_ack_required']} / {r['injection_only_ack_not_required']} | {r['composed_ack_required']} / {r['composed_ack_not_required']} |")
        L += ["", "Official gates (frozen scorer, unchanged by the guards): " + ", ".join(f"{k.split()[0]} {'PASS' if v else 'FAIL'}" for k, v in rep["official_gates"].items()), ""]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cases = ev.load_cases()
    report = {name: replay_run(cases, d) for name, d in RUNS.items()}
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
