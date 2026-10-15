"""
Offline replay of stored model responses through the FROZEN parser and the candidate warning adapter (composed with the injection and split-OR guards).
Per stored call: the model warnings as returned (strings and objects), what the frozen parser kept, how the adapter classified each, the item-specific
conflicts it linked, and the readiness (frozen / injection+split-OR / with the adapter) under BOTH classification-policy settings. A readiness report only:
the official benchmark gates are recomputed by the frozen scorer from the same answers and are not affected. No model, network or database.

  python scripts/requirements_v2_warning_adapter_replay.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (frozen scorer)
from parser_candidates.requirements_v2_injection_guard_1 import inspect_result as inspect_injection  # noqa: E402
from parser_candidates.requirements_v2_split_or_guard_1 import composed_readiness, inspect_result as inspect_split_or  # noqa: E402
from parser_candidates.requirements_v2_warning_adapter_1 import ADAPTER_VERSION, build_review, readiness, status  # noqa: E402
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
        wr = build_review(case["jd"], res.raw_ai_output, res.requirements)
        row = {"case": rec["case"], "run": rec["run"], "warnings_returned": len(wr["model_warnings"]), "warnings_kept_by_frozen_parser": len(res.ai_warnings),
               "warnings": [{"form": w["form"], "kind": w["kind"], "format_issues": w["format_issues"], "linked_items": len(w["linked_item_ids"])} for w in wr["model_warnings"]],
               "item_conflicts": [{"item": next(i["text"] for c in res.requirements["categories"].values() for i in c["items"] if i["id"] == w["item_id"]),
                                   "flagged_importance": w["flagged_importance"]} for w in wr["item_warnings"]],
               "unresolved_conflicts": len(status(wr, res.requirements).unresolved)}
        for pol, name in ((True, "ack_required"), (False, "ack_not_required")):
            row[f"frozen_{name}"] = compute_readiness(res.requirements, require_classification_acknowledgment=pol).state
            row[f"guards_{name}"] = composed_readiness(res.requirements, inj, sor, require_classification_acknowledgment=pol).state
            row[f"adapter_{name}"] = readiness(res.requirements, inj, sor, wr, require_classification_acknowledgment=pol).state
        row["changed_by_adapter"] = row["adapter_ack_required"] != row["guards_ack_required"] or row["adapter_ack_not_required"] != row["guards_ack_not_required"]
        rows.append(row)
    scored = ev.score_runs(cases, answers)
    return {"rows": rows, "official_gates": ev.gates(scored)}


def render(report: dict) -> str:
    L = [f"# Warning adapter replay ({ADAPTER_VERSION}, composed with the injection and split-OR guards)", "",
         "Model warnings per stored call, what the frozen parser kept, and the adapter's classification. This is NOT a benchmark result: the official gates below "
         "are recomputed from the same answers by the frozen scorer and are unaffected.", ""]
    for name, rep in report.items():
        rows = [r for r in rep["rows"] if r["warnings_returned"]]
        L += [f"## {name}", "", f"Calls replayed: {len(rep['rows'])}; calls with model warnings: {len(rows)}; warnings returned: {sum(r['warnings_returned'] for r in rows)}, "
              f"kept by the frozen parser: {sum(r['warnings_kept_by_frozen_parser'] for r in rows)}; item-specific conflicts linked: {sum(len(r['item_conflicts']) for r in rows)}; "
              f"calls whose readiness the adapter changes: {sum(1 for r in rep['rows'] if r['changed_by_adapter'])}", ""]
        if rows:
            L += ["| case | run | warnings (form: kind) | item conflicts | frozen (req / not req) | guards only | with adapter |", "|---|---|---|---|---|---|---|"]
            for r in rows:
                ws = "; ".join(f"{w['form']}: {w['kind']}" + (f" [{','.join(w['format_issues'])}]" if w["format_issues"] else "") for w in r["warnings"])
                ic = "; ".join(f"{c['item']} ({c['flagged_importance']})" for c in r["item_conflicts"]) or "-"
                L.append(f"| {r['case']} | {r['run']} | {ws} | {ic} | {r['frozen_ack_required']} / {r['frozen_ack_not_required']} | "
                         f"{r['guards_ack_required']} / {r['guards_ack_not_required']} | {r['adapter_ack_required']} / {r['adapter_ack_not_required']} |")
        L += ["", "Official gates (frozen scorer, unchanged): " + ", ".join(f"{k.split()[0]} {'PASS' if v else 'FAIL'}" for k, v in rep["official_gates"].items()), ""]
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
