#!/usr/bin/env python3
"""TC20-only repeat of the v2-3 versus v2-5 comparison: five calls per arm, alternating, under the protocol in
tests/fixtures/requirements_v2_tc20_repeat/PROTOCOL.md (recorded before any call).

  python3 backend/scripts/requirements_v2_tc20_repeat.py --dry-run                 plan, hashes, reservation; NO network (default)
  python3 backend/scripts/requirements_v2_tc20_repeat.py --execute --out DIR       the paid calls (NOT run by this commit)

Reused unchanged: the prompts, the user message and the input verifier (requirements_v2_candidate_eval), the TC20 labels and
scorer and the sanitizer (requirements_v2_tc20_eval), the OpenAI call and pricing (requirements_v2_tc20_compare) and the
structure facts of the audit (requirements_v2_candidate_audit). The run loop is new because the existing executor plans all
22 calls; changing it would change its pinned hash and every recorded manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from scripts import requirements_v2_candidate_eval as E  # noqa: E402
from scripts import requirements_v2_candidate_audit as A  # noqa: E402
from scripts import requirements_v2_tc20_compare as C  # noqa: E402
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402

PROTOCOL = BACKEND / "tests" / "fixtures" / "requirements_v2_tc20_repeat" / "PROTOCOL.md"
PROTOCOL_SHA256 = "860eed9e7cbb3d9982f0d7999309f11874878961b62813de71ca56dd5393c0fe"
USER_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
ARMS = ("v2-3", "v2-5")
CALLS_PER_ARM = 5
CAP_USD = 1.00
MODEL = E.MODEL
OUTPUT_FILES = ["manifest.json", "calls.jsonl", "run.json", "scored.json"]
PREVIOUS_RUN = A.EVIDENCE                        # the paired run this repeat is compared with (never modified)
CHECKS = ("C_RESP", "C_COMP", "C_EXP_OR", "C_FAM_OR", "C_LOCAL", "C_LOCATION", "C_REPORTING", "C_AND")
OR_AND = ("C_EXP_OR", "C_FAM_OR", "C_AND")


class RepeatError(RuntimeError):
    pass


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tc20_unit() -> dict:
    return next(u for u in E.jd_sets() if u["id"] == "tc20")


def call_order() -> list[dict]:
    """v2-3 call 1, v2-5 call 1, v2-3 call 2, v2-5 call 2, ... (ten calls, order 1 to 10)."""
    out = []
    for n in range(1, CALLS_PER_ARM + 1):
        for arm in ARMS:
            out.append({"order": len(out) + 1, "arm": arm, "call": n})
    return out


def pins() -> dict:
    """Every hash a run manifest records: the existing pins, the protocol, this script and the commit."""
    if sha(PROTOCOL.read_bytes()) != PROTOCOL_SHA256:
        raise RepeatError("the protocol file does not match its pinned sha256 (decision rules changed after they were fixed)")
    return {**E.verify_inputs(), "protocol_sha256": PROTOCOL_SHA256, "repeat_script_sha256": sha(pathlib.Path(__file__).read_bytes())}


def plan(cap: float = CAP_USD) -> dict:
    p = pins()
    unit = tc20_unit()
    worst = {arm: E.worst_case(E.messages(arm, unit)) for arm in ARMS}
    msgs = E.messages("v2-3", unit)
    if sha(msgs[1]["content"].encode("utf-8")) != USER_SHA256:
        raise RepeatError("the TC20 user message does not match the stored user-message sha256")
    calls = [{**c, "worst_case_usd": worst[c["arm"]]} for c in call_order()]
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > cap:
        raise RepeatError(f"worst-case total {total} exceeds the cap {cap}")
    return {"model": MODEL, "calls": calls, "call_count": len(calls), "worst_case_total_usd": total, "cap_usd": cap,
            "temperature": ev.TEMPERATURE, "max_tokens": ev.MAX_TOKENS, "response_format": "json_object", "timeout_s": ev.TIMEOUT_S,
            "retries": 0, "price_in_usd_per_token": C.PRICE_IN, "price_out_usd_per_token": C.PRICE_OUT,
            "pricing_status": C.PRICING_STATUS, "pins": p}


def prepare_output_dir(out: pathlib.Path) -> None:
    E.prepare_output_dir(out)


def run(out: pathlib.Path, api_call, cap: float = CAP_USD) -> dict:
    """The ten calls in order. No retries. Stops at the first failed call or unexpected returned model, and before any call that
    could take spent + the remaining worst cases above the cap. The manifest is written before the first call."""
    p = plan(cap)
    unit = tc20_unit()
    prepare_output_dir(out)
    manifest = {"protocol": "tests/fixtures/requirements_v2_tc20_repeat/PROTOCOL.md", "protocol_sha256": PROTOCOL_SHA256,
                "pins": p["pins"], "pricing_status": C.PRICING_STATUS, "plan": p, "expected_outputs": OUTPUT_FILES,
                "policy": "no retries; the run stops at the first failed call or unexpected returned model", "started": time.time()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    spent, attempted, stopped = 0.0, 0, None
    remaining = [c["worst_case_usd"] for c in p["calls"]]
    with (out / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(p["calls"]):
            if spent + sum(remaining[i:]) > cap:
                stopped = "cap_guard"
                break
            attempted += 1
            msgs = E.messages(c["arm"], unit)
            withheld = [unit["jd"], msgs[0]["content"], msgs[1]["content"]]
            rec = {"order": c["order"], "arm": c["arm"], "call": c["call"], "model": MODEL,
                   "input": {"system_sha256": sha(msgs[0]["content"].encode("utf-8")), "user_sha256": sha(msgs[1]["content"].encode("utf-8"))}}
            try:
                r = api_call(msgs)
                usage = r.get("usage") or {}
                cost = usage.get("prompt_tokens", 0) * C.PRICE_IN + usage.get("completion_tokens", 0) * C.PRICE_OUT
                spent += cost
                rec.update({"raw": r.get("raw", ""), "finish_reason": r.get("finish_reason"), "model_returned": r.get("model"),
                            "usage": usage, "cost_usd": round(cost, 6), "error": None})
                if r.get("model") != MODEL:
                    rec["error"] = {"kind": "ModelMismatch", "http_status": None, "code": None, "type": None, "param": None, "message": None}
            except Exception as exc:                                  # recorded; never retried; the run stops here
                rec.update({"raw": "", "finish_reason": None, "model_returned": None, "usage": {}, "cost_usd": 0.0,
                            "error": ev.sanitize_error(exc, withheld)})
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if rec["error"]:
                stopped = f"error:{rec['error']['kind']}"
                break
    records = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = {"spent_usd": round(spent, 6), "calls_attempted": attempted,
               "successful_calls": sum(1 for x in records if not x.get("error")), "stopped": stopped}
    (out / "run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


# ── the report: the eight checks, duty completeness, reporting coverage, and the protocol's reading rules ─────────────

def _facts(raw: str, labels: dict, jd: str) -> dict:
    """Facts for one answer. An answer that is not valid JSON fails every check and has no duty, reporting or item content."""
    s = ev.score_answer(raw, jd, labels)
    checks = s["checks"] or {k: False for k in labels["checks"]}
    marker = labels["expected_values"]["reporting_marker"]
    out = {"checks": {k: bool(checks[k]) for k in CHECKS}, "valid_json": s["valid_json"],
           "reporting_present_in_answer": marker in raw, "duty_lines_found": 0, "informational_items": 0,
           "reporting_in_informational": False}
    if s["valid_json"]:
        st = A.tc20_structure(raw, jd, labels)
        obj = json.loads(raw)
        out.update({"duty_lines_found": st["duty_lines_found"], "informational_items": st["informational_items"],
                    "reporting_in_informational": any(marker in str(r.get("source_text", "")) for r in (obj.get("informational_items") or [])
                                                      if isinstance(r, dict))})
    return out


def _arm(rows: list[dict]) -> dict:
    return {"calls": len(rows),
            "checks_passed": {k: sum(1 for r in rows if r["checks"][k]) for k in CHECKS},
            "duty_lines_found": [r["duty_lines_found"] for r in rows],
            "reporting_present_in_answer": [r["reporting_present_in_answer"] for r in rows],
            "reporting_in_informational": [r["reporting_in_informational"] for r in rows]}


def previous_paired(labels: dict, jd: str) -> dict:
    """The paired run this repeat is compared with, read from its stored calls and scored by the same frozen functions."""
    rows = [json.loads(x) for x in (PREVIOUS_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    return {arm: _arm([_facts(r["raw"], labels, jd) for r in rows if r["jd"] == "tc20" and r["arm"] == arm]) for arm in ARMS}


def reading(now: dict, before: dict) -> dict:
    """The protocol's reading rules, applied mechanically. Anything not listed is a single-run observation."""
    v23, v25 = now["v2-3"], now["v2-5"]
    missing = lambda arm: sum(1 for x in arm["reporting_present_in_answer"] if not x)
    return {
        "duty_completeness_reproduced": v25["duty_lines_found"] == [16] * CALLS_PER_ARM and 0 in v23["duty_lines_found"],
        "reporting_regression_reproduced": missing(v25) >= 3 and missing(v23) <= 1,
        "reporting_missing_now": {"v2-3": missing(v23), "v2-5": missing(v25)},
        "reporting_missing_previous": {"v2-3": missing(before["v2-3"]), "v2-5": missing(before["v2-5"])},
        "or_and_failed_calls_now": {arm: {k: now[arm]["calls"] - now[arm]["checks_passed"][k] for k in OR_AND} for arm in ARMS},
        "status": "exploratory evidence of consistency; not a significance test; not deployment readiness; v2-5 stays inactive",
    }


def report(records: list[dict], labels: dict, jd: str) -> dict:
    ok = [r for r in records if not r.get("error")]
    errors = [r.get("error") for r in records if r.get("error")]
    if not ok:
        return {"status": "unavailable", "errors": errors}
    facts = [{"order": r["order"], "arm": r["arm"], "call": r["call"], **_facts(r["raw"], labels, jd)} for r in ok]
    per_call = [{"order": f["order"], "arm": f["arm"], "call": f["call"], "checks": f["checks"],
                 "failed": [k for k in CHECKS if not f["checks"][k]], "duty_lines_found": f["duty_lines_found"],
                 "reporting_present_in_answer": f["reporting_present_in_answer"], "reporting_in_informational": f["reporting_in_informational"],
                 "informational_items": f["informational_items"]} for f in facts]
    now = {arm: _arm([f for f in facts if f["arm"] == arm]) for arm in ARMS}
    before = previous_paired(labels, jd)
    complete = all(now[arm]["calls"] == CALLS_PER_ARM for arm in ARMS)
    return {"status": "scored" if complete else "partial: the reading rules need all ten calls",
            "successful_calls": len(ok), "errors": errors, "per_call": per_call, "per_arm_now": now,
            "per_arm_previous_paired_run": before,
            "reading_rules": reading(now, before) if complete else None,
            "scorer": "frozen TC20 checks (requirements_v2_tc20_eval.score_answer; labels unchanged)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="verify every pin and print the plan and reservation (default)")
    mode.add_argument("--execute", action="store_true", help="the paid calls: 10, alternating, stop at the first failure")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory")
    args = ap.parse_args(argv)
    p = plan()
    jd, labels = ev.load_inputs()
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "protocol": str(PROTOCOL.relative_to(BACKEND.parent)), "plan": p,
                          "reservation_usd": p["worst_case_total_usd"], "cap_usd": CAP_USD,
                          "pricing_verified": C.PRICING_VERIFIED, "user_message_sha256": USER_SHA256,
                          "labels_sha256": E.sha256_bytes(ev.LABELS.read_bytes()), "git_commit": p["pins"]["git_commit"],
                          "expected_output_files": OUTPUT_FILES,
                          "previous_paired_run": str(PREVIOUS_RUN.relative_to(BACKEND.parent))}, ensure_ascii=False, indent=1))
        return 0
    if not C.PRICING_VERIFIED:
        raise RepeatError("pricing is not recorded as verified")
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute needs --out (a new or empty directory) and OPENAI_API_KEY")
    res = run(args.out, C.openai_call_model)
    records = [json.loads(x) for x in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = report(records, labels, jd)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
