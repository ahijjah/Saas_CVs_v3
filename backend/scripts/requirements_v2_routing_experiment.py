#!/usr/bin/env python3
"""Location-routing experiment: the scope-clarification prompt (CONTROL) versus the same prompt plus one rule 9 sentence that binds the
location label to non_scoreable_requirements (ROUTED), on the TC20 JD. Exploratory. Both arms stay inactive.

  /opt/cv-analyzer-req-v2-benchmark-venv/bin/python backend/scripts/requirements_v2_routing_experiment.py --dry-run
      verify every pin and print the plan and the exact reservation; NO network (the default)
  /opt/cv-analyzer-req-v2-benchmark-venv/bin/python backend/scripts/requirements_v2_routing_experiment.py --execute --out DIR
      the twenty paid calls into a new or empty DIR (NOT run by this commit)

The protocol is tests/fixtures/requirements_v2_routing_experiment/PROTOCOL.md (recorded before any call; its hash is pinned below).
Reused unchanged: the scope experiment's structure checks and per-call facts, the candidate pin and input verifier, the user message,
the TC20 labels and frozen scorer, the sanitizer, the OpenAI call and pricing. Only the arms, the location measure, the guard and the
run loop are new.
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
from scripts import requirements_v2_scope_experiment as S  # noqa: E402
from scripts import requirements_v2_tc20_compare as C  # noqa: E402
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402
from scripts import requirements_v2_tc20_repeat as R  # noqa: E402
from scripts import requirements_v2_tc20_repeat_audit as RA  # noqa: E402

PROTOCOL = BACKEND / "tests" / "fixtures" / "requirements_v2_routing_experiment" / "PROTOCOL.md"
PROTOCOL_SHA256 = "73fa404fbeff5edcac00a7da8dbce7283070482c17d222033306abc66625fff8"
CONTROL = S.CLARIFIED                                           # criteria_extraction_v2-5-coverage, unchanged
CONTROL_SHA256 = S.CLARIFIED_SHA256
ROUTED = BACKEND / "prompt_candidates" / "criteria_extraction_v2-5-routing" / "criteria_extraction_v2-5-routing.txt"
ROUTED_SHA256 = "f5042e34777a45c4e55f686c1d0e1bccb5fd8cea0e8adf69a85204ecd5fe56d5"
ANCHOR = b"closest label above."
INSERTED = b"\n   A statement whose category is location goes in non_scoreable_requirements regardless of the job description heading it appears under, and never in informational_items or post_hiring_conditions."
INSERTED_SHA256 = "d01b159fe0c476616296c981f27b82571aefe97ea8d598e3a5f230af1f7681d9"
LOCATION_MARKER = "MoNE office in Ramallah"
USER_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
ARMS = {"CONTROL": {"file": CONTROL, "sha256": CONTROL_SHA256}, "ROUTED": {"file": ROUTED, "sha256": ROUTED_SHA256}}
ORDER_ARMS = ("CONTROL", "ROUTED")                              # alternating, CONTROL first
CALLS_PER_ARM = 10
CAP_USD = 2.00
MODEL = E.MODEL
OUTPUT_FILES = ["manifest.json", "calls.jsonl", "run.json", "scored.json"]
CHECKS = R.CHECKS
SUPPORT_AT = 4
WEAKEN_AT = -4
CEILING = 9


class ExperimentError(RuntimeError):
    pass


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def verify_identity() -> dict:
    """ROUTED must equal CONTROL with exactly the inserted rule 9 span added after the anchor, and nothing else changed."""
    control = CONTROL.read_bytes()
    routed = ROUTED.read_bytes()
    if sha(control) != CONTROL_SHA256 or sha(routed) != ROUTED_SHA256:
        raise ExperimentError("an arm does not match its pinned sha256")
    if sha(INSERTED) != INSERTED_SHA256:
        raise ExperimentError("the inserted span does not match its pinned sha256")
    if control.count(ANCHOR) != 1:
        raise ExperimentError("the rule 9 anchor must occur exactly once in CONTROL")
    cut = control.index(ANCHOR) + len(ANCHOR)
    if control[:cut] + INSERTED + control[cut:] != routed:
        raise ExperimentError("ROUTED is not CONTROL with only the inserted rule 9 sentence")
    if control[:cut] != routed[:cut] or control[cut:] != routed[cut + len(INSERTED):]:
        raise ExperimentError("bytes outside the inserted sentence differ")
    return {"control_sha256": CONTROL_SHA256, "routed_sha256": ROUTED_SHA256, "inserted_sha256": INSERTED_SHA256,
            "inserted_bytes": len(INSERTED), "control_bytes": len(control), "routed_bytes": len(routed)}


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
        raise ExperimentError("the protocol does not match its pinned sha256 (the rules changed after they were fixed)")
    base = E.verify_inputs()
    return {**base, "identity": verify_identity(), "protocol_sha256": PROTOCOL_SHA256,
            "routing_script_sha256": sha(pathlib.Path(__file__).read_bytes()),
            "scope_script_sha256": sha((BACKEND / "scripts" / "requirements_v2_scope_experiment.py").read_bytes()),
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
    """Twenty calls in order. No retries. Stops at the first failed call or unexpected returned model, and before any call that could
    take spent + the remaining worst cases above the cap. The manifest is written before the first call."""
    p = plan(cap)
    E.prepare_output_dir(out)
    manifest = {"protocol": "tests/fixtures/requirements_v2_routing_experiment/PROTOCOL.md", "pins": p["pins"],
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


def location_facts(raw: str) -> dict:
    o = json.loads(raw)
    nonscore = [c for c in o.get("non_scoreable_requirements") or [] if isinstance(c, dict) and c.get("category") == "location"]
    elsewhere = [c for c in (o.get("informational_items") or []) + (o.get("post_hiring_conditions") or [])
                 if isinstance(c, dict) and c.get("category") == "location"]
    retained = LOCATION_MARKER in raw
    correct = any(LOCATION_MARKER in str(c.get("source_text", "")) for c in nonscore) and not elsewhere
    return {"location_retained": retained, "location_correctly_routed": correct,
            "location_in_non_scoreable": bool(nonscore), "location_elsewhere": len(elsewhere)}


def facts(raw: str, labels: dict, jd: str) -> dict:
    f = S.facts(raw, labels, jd)
    return {**f, **location_facts(raw)}


def reading(control_p: int, routed_p: int, guards: dict) -> str:
    """The protocol's interpretation rules, applied in their fixed order: guard, ceiling, then the difference."""
    failed = [name for name, (c, r) in guards.items() if r < c]
    if failed:
        return f"regression in a guarded group ({', '.join(failed)}): reported; the location result does not offset it"
    if control_p >= CEILING:
        return "inconclusive: the control is near ceiling"
    d = routed_p - control_p
    if d >= SUPPORT_AT:
        return "supports the routing sentence helping location on this JD (exploratory; not proof of causation)"
    if d <= WEAKEN_AT:
        return "the routing sentence did not help location on this JD in this run (exploratory)"
    return "inconclusive (small or absent difference)"


def _arm(rows: list[dict]) -> dict:
    return {"calls": len(rows),
            "location_correctly_routed": sum(1 for r in rows if r["location_correctly_routed"]),
            "location_retained": sum(1 for r in rows if r["location_retained"]),
            "location_in_non_scoreable": sum(1 for r in rows if r["location_in_non_scoreable"]),
            "location_elsewhere": sum(r["location_elsewhere"] for r in rows),
            "reporting_complete": sum(1 for r in rows if r["primary_retained_and_routed"]),
            "duty_complete": sum(1 for r in rows if r["duty_lines_found"] == 16),
            "schema_conforming": sum(1 for r in rows if not r["schema_violations"]),
            "invented_source_free": sum(1 for r in rows if r["invented_sources"] == 0),
            "checks_passed": {k: sum(1 for r in rows if r["checks"][k]) for k in CHECKS}}


def report(records: list[dict], labels: dict, jd: str) -> dict:
    ok = [r for r in records if not r.get("error")]
    errors = [r.get("error") for r in records if r.get("error")]
    if not ok:
        return {"status": "unavailable", "errors": errors}
    rows = [{"order": r["order"], "arm": r["arm"], "call": r["call"], **facts(r["raw"], labels, jd)} for r in ok]
    per_call = [{k: f[k] for k in ("order", "arm", "call", "location_retained", "location_correctly_routed", "location_in_non_scoreable",
                                   "location_elsewhere", "primary_retained_and_routed", "reporting_statement_in_whole_answer",
                                   "duty_lines_found", "checks", "schema_violations", "invented_sources")} for f in rows]
    fresh = {arm: _arm([f for f in rows if f["arm"] == arm]) for arm in ORDER_ARMS}
    complete = all(fresh[a]["calls"] == CALLS_PER_ARM for a in ORDER_ARMS)
    c, r_ = fresh["CONTROL"], fresh["ROUTED"]
    guards = {"reporting_complete": (c["reporting_complete"], r_["reporting_complete"]),
              "duty_complete": (c["duty_complete"], r_["duty_complete"]),
              "schema_conforming": (c["schema_conforming"], r_["schema_conforming"]),
              "invented_source_free": (c["invented_source_free"], r_["invented_source_free"])}
    direct = {"location_correctly_routed_difference_ROUTED_minus_CONTROL": r_["location_correctly_routed"] - c["location_correctly_routed"],
              "guards": {k: {"CONTROL": a, "ROUTED": b, "regressed": b < a} for k, (a, b) in guards.items()},
              "checks_passed_difference_ROUTED_minus_CONTROL": {k: r_["checks_passed"][k] - c["checks_passed"][k] for k in CHECKS},
              "primary_reading": reading(c["location_correctly_routed"], r_["location_correctly_routed"], guards) if complete else None}
    return {"status": "scored" if complete else "partial: the comparison needs all twenty calls", "successful_calls": len(ok),
            "errors": errors, "per_call": per_call, "fresh_arms": fresh, "direct_comparison": direct,
            "historical_context_not_pooled": _historical(labels, jd),
            "scorer": "frozen TC20 checks (requirements_v2_tc20_eval.score_answer; labels unchanged)"}


def _historical(labels: dict, jd: str) -> dict:
    """Context only: the scope experiment's CLARIFIED and FULL calls, kept as their own run."""
    scope_run = BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "run"
    scope = [json.loads(x) for x in (scope_run / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    out = {}
    for arm in ("CLARIFIED", "FULL"):
        calls = [facts(r["raw"], labels, jd) for r in scope if r["arm"] == arm]
        out[f"scope_experiment_{arm}"] = {"calls": len(calls),
                                          "location_correctly_routed": sum(1 for c in calls if c["location_correctly_routed"]),
                                          "reporting_complete": sum(1 for c in calls if c["primary_retained_and_routed"])}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="verify every pin and print the plan and reservation (default)")
    mode.add_argument("--execute", action="store_true", help="the twenty paid calls")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory")
    args = ap.parse_args(argv)
    p = plan()
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "protocol": str(PROTOCOL.relative_to(BACKEND.parent)), "plan": p,
                          "reservation_usd": p["reservation_usd"], "cap_usd": CAP_USD, "pricing_verified": C.PRICING_VERIFIED,
                          "git_commit": p["pins"]["git_commit"], "expected_output_files": OUTPUT_FILES},
                         ensure_ascii=False, indent=1))
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
