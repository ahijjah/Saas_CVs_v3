"""
Offline replay of ALL stored model responses (v2-1 baseline 24 + v2-2 run1 24) through the candidate pipeline's single entry point.
Reports, per call and for BOTH classification-policy settings: pipeline readiness, the open gates and the unresolved issues, whether the frozen readiness alone
would have been "ready", contract validity and raw/original integrity. The official benchmark gates are recomputed by the frozen scorer from the same answers and
compared with the saved results.json: they are a separate section and are not affected by anything the pipeline reports. No model, network or database.

  python scripts/requirements_v2_pipeline_replay.py [--out DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import requirements_v2_extraction_eval as ev  # noqa: E402  (frozen scorer)
from parser_candidates.requirements_v2_pipeline_1 import PIPELINE_VERSION, extract, validate_state  # noqa: E402
from services.requirements_v2.extraction import parse_response  # noqa: E402
from services.requirements_v2.readiness import compute_readiness  # noqa: E402
from services.requirements_v2.comparison import original_digest, snapshot_original  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
BASE = BACKEND / "benchmark_results" / "requirements_v2"
RUNS = {"v2-1 baseline": BASE / "baseline_v2-1", "v2-2 candidate": BASE / "v2-2_run1"}


_ID = re.compile(r"req_[0-9a-f]{12}")


def _norm_ids(obj, mapping=None):
    """The frozen parser mints random item ids on every call, so two parses of the same response differ only by id. Replace each id by its order of first appearance."""
    mapping = {} if mapping is None else mapping
    if isinstance(obj, dict):
        return {_norm_ids(k, mapping): _norm_ids(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_ids(v, mapping) for v in obj]
    if isinstance(obj, str):
        return _ID.sub(lambda m: mapping.setdefault(m.group(0), f"item{len(mapping) + 1}"), obj)
    return obj


def replay_run(cases: list[dict], run_dir: Path) -> dict:
    by_id = {c["id"]: c for c in cases}
    manifest = json.loads((run_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    prompt = {"version": manifest["prompt_version"], "sha256": manifest["prompt_sha256"]}
    rows, answers, skipped = [], {"run1": {}, "run2": {}}, 0
    for line in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if not rec.get("raw") or rec.get("quarantined"):
            skipped += 1
            continue
        answers[rec["run"]][rec["case"]] = {"raw": rec["raw"], "finish_reason": rec["finish_reason"]}
        case = by_id[rec["case"]]
        frozen = parse_response(rec["raw"], case["jd"], rec["finish_reason"])
        row = {"case": rec["case"], "run": rec["run"], "parsed": frozen.ok}
        for pol, name in ((True, "ack_required"), (False, "ack_not_required")):
            st = extract(case["jd"], rec["raw"], finish_reason=rec["finish_reason"], extraction_prompt=prompt, require_classification_acknowledgment=pol)
            row[f"contract_problems_{name}"] = validate_state(st)
            row[f"pipeline_{name}"] = st["readiness"]["state"]
            row[f"frozen_{name}"] = compute_readiness(frozen.requirements, require_classification_acknowledgment=pol).state if frozen.ok else "parse_failed"
            row[f"issues_{name}"] = [(i["gate"], i["kind"]) for i in st["unresolved_issues"] if (i["blocks_when_ack_required"] if pol else i["blocks_when_ack_not_required"])]
            row[f"visible_only_{name}"] = [(i["gate"], i["kind"]) for i in st["unresolved_issues"] if not (i["blocks_when_ack_required"] if pol else i["blocks_when_ack_not_required"])]
            if pol:
                row["gates"] = sorted(st["gates"])
                row["raw_intact"] = st["raw_response"]["text"] == rec["raw"]
                same = (not frozen.ok) or (_norm_ids(st["requirements"]) == _norm_ids(frozen.requirements) and _norm_ids(st["original"]) == _norm_ids(frozen.original)
                                           and st["raw_ai_output"] == frozen.raw_ai_output)
                # the snapshot is the digest-checked original of the very draft the parser produced (same ids), and the draft starts out identical to it
                row["original_intact"] = same and (not frozen.ok or (original_digest(st["original"]) == st["original_digest"] and snapshot_original(st["requirements"]) == st["original"]))
                row["warnings"] = len(st["normalized_warnings"])
                row["similarity_warnings"] = len(st["informational"]["similarity_warnings"])
                row["generic_notes"] = len(st["informational"]["generic_model_notes"])
        for name in ("ack_required", "ack_not_required"):
            row[f"newly_blocked_{name}"] = row[f"frozen_{name}"] == "ready" and row[f"pipeline_{name}"] != "ready"
        rows.append(row)
    scored = ev.score_runs(cases, answers)
    saved = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))["gates"]
    recomputed = ev.gates(scored)
    return {"rows": rows, "skipped": skipped, "official_gates": recomputed, "official_gates_saved": saved, "official_gates_match_saved": recomputed == saved}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for name in ("ack_required", "ack_not_required"):
        out[name] = {"pipeline_states": dict(collections.Counter(r[f"pipeline_{name}"] for r in rows)), "frozen_states": dict(collections.Counter(r[f"frozen_{name}"] for r in rows)),
                     "frozen_ready_but_pipeline_blocked": sum(r[f"newly_blocked_{name}"] for r in rows),
                     "unresolved_by_kind": dict(collections.Counter(f"{g}:{k}" for r in rows for g, k in r[f"issues_{name}"]))}
    out["contract_problems"] = sum(bool(r["contract_problems_ack_required"] or r["contract_problems_ack_not_required"]) for r in rows)
    out["raw_or_original_not_intact"] = sum(not (r["raw_intact"] and r["original_intact"]) for r in rows)
    return out


def render(report: dict) -> str:
    L = [f"# Pipeline replay ({PIPELINE_VERSION})", "",
         "Candidate readiness and unresolved issues for every stored response (48), reported under BOTH classification-policy settings. This is a readiness report, "
         "NOT a benchmark result: the official gates (last line of each section) are recomputed from the same answers by the frozen scorer, compared with the saved "
         "`results.json`, and are unchanged. 'Newly blocked' = the frozen readiness alone says `ready`, the pipeline does not.", ""]
    for name, rep in report.items():
        rows, s = rep["rows"], rep["summary"]
        L += [f"## {name}", "", f"Calls replayed: {len(rows)} (unusable skipped: {rep['skipped']}); parsed: {sum(r['parsed'] for r in rows)}; contract problems: {s['contract_problems']}; "
              f"raw/original/draft not intact: {s['raw_or_original_not_intact']}", ""]
        for pol in ("ack_required", "ack_not_required"):
            p = s[pol]
            L += [f"**Policy {'Yes (acknowledgment required)' if pol == 'ack_required' else 'No (acknowledgment not required)'}** — pipeline states: {p['pipeline_states']}; "
                  f"frozen-only states: {p['frozen_states']}; frozen ready but pipeline blocked: {p['frozen_ready_but_pipeline_blocked']}; blocking issues by kind: {p['unresolved_by_kind'] or 'none'}", ""]
        L += ["| case | run | pipeline (Yes / No) | frozen only (Yes / No) | blocking issues (Yes) | blocking issues (No) | visible only (No) | warnings | similarity |", "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            fmt = lambda xs: "; ".join(f"{g}:{k}" for g, k in xs) or "-"
            L.append(f"| {r['case']} | {r['run']} | {r['pipeline_ack_required']} / {r['pipeline_ack_not_required']} | {r['frozen_ack_required']} / {r['frozen_ack_not_required']} | "
                     f"{fmt(r['issues_ack_required'])} | {fmt(r['issues_ack_not_required'])} | {fmt(r['visible_only_ack_not_required'])} | {r['warnings']} | {r['similarity_warnings']} |")
        L += ["", "Official gates (frozen scorer, recomputed): " + ", ".join(f"{k.split()[0]} {'PASS' if v else 'FAIL'}" for k, v in rep["official_gates"].items()),
              f"Identical to the saved results.json: {'yes' if rep['official_gates_match_saved'] else 'NO'}", ""]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cases = ev.load_cases()
    report = {}
    for name, d in RUNS.items():
        rep = replay_run(cases, d)
        rep["summary"] = summarize(rep["rows"])
        report[name] = rep
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
