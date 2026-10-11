#!/usr/bin/env python3
"""Example 5 removal experiment: criteria_extraction_v2-5 (FULL) versus the same prompt without Example 5 (VARIANT), on the TC20 JD.

  python3 backend/scripts/requirements_v2_ex5_experiment.py --dry-run                 verify every pin and print the plan; NO network (default)
  python3 backend/scripts/requirements_v2_ex5_experiment.py --execute --out DIR       the ten paid calls (NOT run by this commit)

The protocol is tests/fixtures/requirements_v2_ex5_experiment/PROTOCOL.md (recorded before any call; its hash is pinned below).
Reused unchanged: the candidate and control pins and the input verifier, the user message, the TC20 labels and scorer, the
sanitizer, the OpenAI call and pricing, and the audit facts of the repeat. Only the arm definitions and the run loop are new.
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
from scripts import requirements_v2_tc20_repeat as R  # noqa: E402
from scripts import requirements_v2_tc20_repeat_audit as RA  # noqa: E402

PROTOCOL = BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "PROTOCOL.md"
PROTOCOL_SHA256 = "5a7412438a42cdc50f4e41b847031584a1c1a0de34438eeea274650abad853be"
FULL = E.CANDIDATE                                              # criteria_extraction_v2-5
FULL_SHA256 = E.CANDIDATE_SHA256
VARIANT = BACKEND / "prompt_candidates" / "criteria_extraction_v2-5-noex5" / "criteria_extraction_v2-5-noex5.txt"
VARIANT_SHA256 = "7cf171c84d65ce74b2531c2ca1f3049c7e539774121c0faae347dc7bf0466e62"
REMOVED_SHA256 = "33bdd1dcfc0dd3fc761ea719e773ab1bd8db3909950e68cbded19fe8b99e31e6"
REMOVED_BYTES = 3223
EXAMPLE5_MARKER = b"\nExample 5 (EN"
USER_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
ARMS = {"FULL": {"file": FULL, "sha256": FULL_SHA256}, "VARIANT": {"file": VARIANT, "sha256": VARIANT_SHA256}}
ORDER_ARMS = ("FULL", "VARIANT")                                # alternating, FULL first
CALLS_PER_ARM = 5
CAP_USD = 1.00
MODEL = E.MODEL
OUTPUT_FILES = ["manifest.json", "calls.jsonl", "run.json", "scored.json"]
CHECKS = R.CHECKS


class ExperimentError(RuntimeError):
    pass


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def verify_identity() -> dict:
    """VARIANT must be the exact byte prefix of FULL before the Example 5 header, and the removed span must be pinned."""
    full = FULL.read_bytes()
    var = VARIANT.read_bytes()
    if sha(full) != FULL_SHA256:
        raise ExperimentError("FULL (criteria_extraction_v2-5) does not match its pinned sha256")
    if sha(var) != VARIANT_SHA256:
        raise ExperimentError("VARIANT does not match its pinned sha256")
    if full.count(EXAMPLE5_MARKER) != 1:
        raise ExperimentError("the Example 5 header must occur exactly once in FULL")
    cut = full.index(EXAMPLE5_MARKER)
    if full[:cut] != var or full[:cut] + full[cut:] != full:
        raise ExperimentError("VARIANT is not the exact prefix of FULL before Example 5")
    removed = full[cut:]
    if sha(removed) != REMOVED_SHA256 or len(removed) != REMOVED_BYTES:
        raise ExperimentError("the removed span does not match its pinned identity")
    return {"full_sha256": FULL_SHA256, "variant_sha256": VARIANT_SHA256, "removed_sha256": REMOVED_SHA256,
            "removed_bytes": REMOVED_BYTES, "variant_bytes": len(var), "full_bytes": len(full)}


def messages(arm: str) -> list[dict]:
    """The system prompt is the arm's file (checked against its pin); the user message is the stored TC20 message."""
    unit = R.tc20_unit()
    raw = ARMS[arm]["file"].read_bytes()
    if sha(raw) != ARMS[arm]["sha256"]:
        raise ExperimentError(f"{arm}: prompt sha256 does not match its pin")
    msgs = [{"role": "system", "content": raw.decode("utf-8")}, {"role": "user", "content": ev.user_message(unit["jd"], unit["metadata"])}]
    if sha(msgs[1]["content"].encode("utf-8")) != USER_SHA256:
        raise ExperimentError("the TC20 user message does not match the stored user-message sha256")
    return msgs


def call_order() -> list[dict]:
    out = []
    for n in range(1, CALLS_PER_ARM + 1):
        for arm in ORDER_ARMS:
            out.append({"order": len(out) + 1, "arm": arm, "call": n})
    return out


def pins() -> dict:
    if sha(PROTOCOL.read_bytes()) != PROTOCOL_SHA256:
        raise ExperimentError("the protocol does not match its pinned sha256 (the interpretation rules changed after they were fixed)")
    base = E.verify_inputs()
    return {**base, "identity": verify_identity(), "protocol_sha256": PROTOCOL_SHA256,
            "experiment_script_sha256": sha(pathlib.Path(__file__).read_bytes()),
            "repeat_script_sha256": sha((BACKEND / "scripts" / "requirements_v2_tc20_repeat.py").read_bytes()),
            "user_message_sha256": USER_SHA256}


def plan(cap: float = CAP_USD) -> dict:
    p = pins()
    worst = {arm: E.worst_case(messages(arm)) for arm in ORDER_ARMS}
    calls = [{**c, "worst_case_usd": worst[c["arm"]]} for c in call_order()]
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > cap:
        raise ExperimentError(f"worst-case total {total} exceeds the cap {cap}")
    return {"model": MODEL, "calls": calls, "call_count": len(calls), "per_arm_worst_case_usd": worst,
            "reservation_usd": total, "cap_usd": cap, "temperature": ev.TEMPERATURE, "max_tokens": ev.MAX_TOKENS,
            "response_format": "json_object", "timeout_s": ev.TIMEOUT_S, "retries": 0,
            "price_in_usd_per_token": C.PRICE_IN, "price_out_usd_per_token": C.PRICE_OUT, "pricing_status": C.PRICING_STATUS,
            "pins": p}


def run(out: pathlib.Path, api_call, cap: float = CAP_USD) -> dict:
    """Ten calls in order. No retries. Stops at the first failed call or unexpected returned model, and before any call that could
    take spent + the remaining worst cases above the cap. The manifest is written before the first call."""
    p = plan(cap)
    E.prepare_output_dir(out)
    manifest = {"protocol": "tests/fixtures/requirements_v2_ex5_experiment/PROTOCOL.md", "pins": p["pins"],
                "pricing_status": C.PRICING_STATUS, "plan": p, "expected_outputs": OUTPUT_FILES,
                "policy": "no retries; the run stops at the first failed call or unexpected returned model", "started": time.time()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    unit = R.tc20_unit()
    spent, attempted, stopped = 0.0, 0, None
    remaining = [c["worst_case_usd"] for c in p["calls"]]
    with (out / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(p["calls"]):
            if spent + sum(remaining[i:]) > cap:
                stopped = "cap_guard"
                break
            attempted += 1
            msgs = messages(c["arm"])
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


def reading(full_present: int, variant_present: int) -> str:
    """The protocol's rule 1, applied to the counts of calls with the labelled reporting sentence in the whole answer."""
    d = variant_present - full_present
    if d >= 3:
        return "supports the contribution of Example 5 (not proof of causation)"
    if d >= 1:
        return "inconclusive"
    return "weakens the hypothesis; does not rule out interactions with other instructions"


def _arm_facts(rows: list[dict]) -> dict:
    n = len(rows)
    return {"calls": n,
            "checks_passed": {k: sum(1 for r in rows if r["checks"][k]) for k in CHECKS},
            "reporting_statement_in_whole_answer": sum(1 for r in rows if r["reporting_statement_in_whole_answer"]),
            "informational_items_nonempty": sum(1 for r in rows if r["informational_items"] > 0),
            "duty_lines_found": [r["duty_lines_found"] for r in rows],
            "ict_alternatives_kept": sum(1 for r in rows if r["ict_alternatives_kept"]),
            "familiarity_alternatives_kept": sum(1 for r in rows if r["familiarity_alternatives_kept"]),
            "communication_split_calls": sum(1 for r in rows if r["communication_line_split_items"] >= 3)}


def report(records: list[dict], labels: dict, jd: str) -> dict:
    ok = [r for r in records if not r.get("error")]
    errors = [r.get("error") for r in records if r.get("error")]
    if not ok:
        return {"status": "unavailable", "errors": errors}
    facts = []
    for r in ok:
        f = RA._facts(r["raw"], labels, jd)
        facts.append({"order": r["order"], "arm": r["arm"], "call": r["call"], **f})
    per_call = [{"order": f["order"], "arm": f["arm"], "call": f["call"], "checks": f["checks"],
                 "failed": [k for k in CHECKS if not f["checks"][k]], "duty_lines_found": f["duty_lines_found"],
                 "reporting_statement_in_whole_answer": f["reporting_statement_in_whole_answer"],
                 "reporting_sentences_in_whole_answer": f["reporting_sentences_in_whole_answer"],
                 "informational_items": f["informational_items"], "ict_alternatives_kept": f["ict_alternatives_kept"],
                 "familiarity_alternatives_kept": f["familiarity_alternatives_kept"],
                 "communication_line_categories": f["communication_line_categories"],
                 "communication_line_split_items": f["communication_line_split_items"]} for f in facts]
    fresh = {arm: _arm_facts([f for f in facts if f["arm"] == arm]) for arm in ORDER_ARMS}
    full_p = fresh["FULL"]["reporting_statement_in_whole_answer"]
    var_p = fresh["VARIANT"]["reporting_statement_in_whole_answer"]
    direct = {k: {"VARIANT_minus_FULL": (fresh["VARIANT"]["checks_passed"][k] - fresh["FULL"]["checks_passed"][k])} for k in CHECKS}
    complete = all(fresh[a]["calls"] == CALLS_PER_ARM for a in ORDER_ARMS)
    return {"status": "scored" if complete else "partial: the comparison needs all ten calls", "successful_calls": len(ok), "errors": errors,
            "per_call": per_call, "fresh_arms": fresh,
            "direct_comparison": {"checks_passed_difference": direct,
                                  "reporting_present_difference": var_p - full_p,
                                  "reporting_reading": reading(full_p, var_p) if complete else None},
            "historical_context_not_pooled": _historical(labels, jd),
            "scorer": "frozen TC20 checks (requirements_v2_tc20_eval.score_answer; labels unchanged)"}


def _historical(labels: dict, jd: str) -> dict:
    """Context only: the earlier v2-5 calls, reported in their own runs and never pooled with the fresh arms."""
    paired = RA.run_facts([json.loads(x) for x in (A.EVIDENCE / "calls.jsonl").read_text(encoding="utf-8").splitlines()
                           if json.loads(x)["jd"] == "tc20"], labels, jd)
    repeat = RA.run_facts([json.loads(x) for x in (RA.RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()], labels, jd)
    return {"paired_run": {"v2-5_reporting_missing_whole_answer": paired["v2-5"]["reporting_missing_whole_answer"],
                           "v2-5_checks_passed": paired["v2-5"]["checks_passed"]},
            "repeat": {"v2-5_reporting_missing_whole_answer": repeat["v2-5"]["reporting_missing_whole_answer"],
                       "v2-5_checks_passed": repeat["v2-5"]["checks_passed"]}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="verify every pin and print the plan and reservation (default)")
    mode.add_argument("--execute", action="store_true", help="the ten paid calls")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory")
    args = ap.parse_args(argv)
    p = plan()
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "protocol": str(PROTOCOL.relative_to(BACKEND.parent)), "plan": p,
                          "reservation_usd": p["reservation_usd"], "cap_usd": CAP_USD, "pricing_verified": C.PRICING_VERIFIED,
                          "git_commit": p["pins"]["git_commit"], "expected_output_files": OUTPUT_FILES,
                          "previous_estimate_usd": 0.81101}, ensure_ascii=False, indent=1))
        return 0
    if not C.PRICING_VERIFIED:
        raise ExperimentError("pricing is not recorded as verified")
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute needs --out (a new or empty directory) and OPENAI_API_KEY")
    res = run(args.out, C.openai_call_model)
    records = [json.loads(x) for x in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    jd, labels = ev.load_inputs()
    rep = report(records, labels, jd)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
