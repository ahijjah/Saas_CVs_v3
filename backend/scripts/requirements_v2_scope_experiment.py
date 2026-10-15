#!/usr/bin/env python3
"""Scope-clarification experiment: criteria_extraction_v2-5 (FULL) versus the same prompt with one clarified COVERAGE sentence
(CLARIFIED), on the TC20 JD. Both arms stay inactive; nothing here changes a production prompt.

  /opt/cv-analyzer-req-v2-benchmark-venv/bin/python backend/scripts/requirements_v2_scope_experiment.py --dry-run
      verify every pin and print the plan and the exact reservation; NO network (the default)
  /opt/cv-analyzer-req-v2-benchmark-venv/bin/python backend/scripts/requirements_v2_scope_experiment.py --execute --out DIR
      the ten paid calls into a new or empty DIR (NOT run by this commit)

The protocol is tests/fixtures/requirements_v2_scope_experiment/PROTOCOL.md (recorded before any call; its hash is pinned below).
Reused unchanged: the candidate pin and input verifier, the user message, the TC20 labels and frozen scorer, the sanitizer, the OpenAI
call and pricing, and the audit's per-call facts. Only the arm definitions, the structure checks and the run loop are new.
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
from scripts import requirements_v2_tc20_compare as C  # noqa: E402
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402
from scripts import requirements_v2_tc20_repeat as R  # noqa: E402
from scripts import requirements_v2_tc20_repeat_audit as RA  # noqa: E402

PROTOCOL = BACKEND / "tests" / "fixtures" / "requirements_v2_scope_experiment" / "PROTOCOL.md"
PROTOCOL_SHA256 = "5210b3edecffa2d7844ae09f52352375f02ad043cdb53aba3114519365091482"
FULL = E.CANDIDATE                                              # criteria_extraction_v2-5, unchanged
FULL_SHA256 = E.CANDIDATE_SHA256
CLARIFIED = BACKEND / "prompt_candidates" / "criteria_extraction_v2-5-coverage" / "criteria_extraction_v2-5-coverage.txt"
CLARIFIED_SHA256 = "266329fc57c34a00362ea6f77984641e42d6900cd5181adf083941ca8d61103e"
OLD_SENTENCE = (b"Every requirement, duty and competency line found in a section becomes an item (rules 4 to 8) or a condition (rule 9).")
OLD_SHA256 = "64f3f06ce8e9dd5e54c8c4383efa6b90cb00e457d9cb7435f28366ad48033aef"
NEW_SENTENCE = (b"Every requirement, duty and competency line found in a section becomes an item (rules 4 to 8) or, when rule 9 applies, "
                b"a condition in one of its three lists (non-scoreable requirements, post-hiring conditions or informational statements).")
NEW_SHA256 = "1ef27d146c06e923bc59d9db544122934eed7d2c9adb580f86d9bc2285448ae8"
USER_SHA256 = "ff6b334ad32300a05933cf96284c6c253d86ed9442ffcac37a8b543a8c240473"
ARMS = {"FULL": {"file": FULL, "sha256": FULL_SHA256}, "CLARIFIED": {"file": CLARIFIED, "sha256": CLARIFIED_SHA256}}
ORDER_ARMS = ("FULL", "CLARIFIED")                              # alternating, FULL first
CALLS_PER_ARM = 5
CAP_USD = 1.00
MODEL = E.MODEL
OUTPUT_FILES = ["manifest.json", "calls.jsonl", "run.json", "scored.json"]
CHECKS = R.CHECKS
REPORTING = tuple(RA.REPORTING_SENTENCES.values())             # "submit deliverables", "in coordination with ...", "maintain ..."
OUTPUT_TOP = {"scoreability", "categories", "category_weights", "non_scoreable_requirements", "post_hiring_conditions",
              "informational_items", "warnings"}
CATEGORY_KEYS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
ITEM_KEYS = {"text", "importance", "importance_cue", "source_text", "origin", "alternatives", "experience"}
CONDITION_KEYS = {"text", "category", "reason", "source_text"}
IMPORTANCE = {"required", "preferred"}
CONDITION_CATEGORIES = {"work_authorization", "location", "salary", "availability", "travel", "schedule", "background_check",
                        "reference_check", "medical_check", "document_submission", "company_description", "benefits",
                        "reporting_line", "hr_statement", "other"}
SUPPORT_AT = 3


class ExperimentError(RuntimeError):
    pass


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def squash(s: str) -> str:
    return " ".join(str(s).split())


def verify_identity() -> dict:
    """CLARIFIED must equal FULL with exactly one sentence replaced; every other byte of the two files is identical."""
    full = FULL.read_bytes()
    var = CLARIFIED.read_bytes()
    if sha(full) != FULL_SHA256:
        raise ExperimentError("FULL (criteria_extraction_v2-5) does not match its pinned sha256")
    if sha(var) != CLARIFIED_SHA256:
        raise ExperimentError("CLARIFIED does not match its pinned sha256")
    if sha(OLD_SENTENCE) != OLD_SHA256 or sha(NEW_SENTENCE) != NEW_SHA256:
        raise ExperimentError("the replaced or the replacing sentence does not match its pinned sha256")
    if full.count(OLD_SENTENCE) != 1:
        raise ExperimentError("the obligation sentence must occur exactly once in FULL")
    cut = full.index(OLD_SENTENCE)
    if full[:cut] + NEW_SENTENCE + full[cut + len(OLD_SENTENCE):] != var:
        raise ExperimentError("CLARIFIED is not FULL with only the obligation sentence replaced")
    if full[:cut] != var[:cut] or full[cut + len(OLD_SENTENCE):] != var[cut + len(NEW_SENTENCE):]:
        raise ExperimentError("bytes outside the obligation sentence differ")
    return {"full_sha256": FULL_SHA256, "clarified_sha256": CLARIFIED_SHA256, "old_sentence_sha256": OLD_SHA256,
            "new_sentence_sha256": NEW_SHA256, "full_bytes": len(full), "clarified_bytes": len(var)}


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
            "scope_script_sha256": sha(pathlib.Path(__file__).read_bytes()),
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
    manifest = {"protocol": "tests/fixtures/requirements_v2_scope_experiment/PROTOCOL.md", "pins": p["pins"],
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


def schema_violations(raw: str) -> list[str]:
    """Output-contract checks that the frozen TC20 scorer does not make. Returns a list of short labels; empty means conforming."""
    try:
        o = json.loads(raw)
    except (TypeError, ValueError):
        return ["not_json"]
    if not isinstance(o, dict):
        return ["not_object"]
    bad = []
    if set(o) != OUTPUT_TOP:
        bad.append("top_level_keys")
    cats = o.get("categories")
    if not isinstance(cats, dict) or set(cats) != set(CATEGORY_KEYS):
        bad.append("category_keys")
        cats = {}
    for name, lst in cats.items():
        for it in lst if isinstance(lst, list) else [None]:
            if not isinstance(it, dict) or set(it) != ITEM_KEYS:
                bad.append("item_keys")
            elif it["importance"] not in IMPORTANCE or not isinstance(it["source_text"], str):
                bad.append("item_values")
    for name in ("non_scoreable_requirements", "post_hiring_conditions", "informational_items"):
        for cond in o.get(name) if isinstance(o.get(name), list) else [None]:
            if not isinstance(cond, dict) or set(cond) != CONDITION_KEYS:
                bad.append("condition_keys")
            elif cond["category"] not in CONDITION_CATEGORIES:
                bad.append("condition_category")
    return bad


def invented_sources(raw: str, jd: str) -> int:
    """Items and conditions whose source_text does not occur in the job description (whitespace collapsed; rule 2)."""
    o = json.loads(raw)
    src_jd = squash(jd)
    entries = [it for lst in (o.get("categories") or {}).values() if isinstance(lst, list) for it in lst]
    entries += [c for name in ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
                for c in (o.get(name) or []) if isinstance(c, dict)]
    return sum(1 for e in entries if not isinstance(e, dict) or squash(e.get("source_text", "")) not in src_jd or not e.get("source_text"))


def routed_reporting(raw: str) -> dict:
    """Each reporting sentence: is there an informational item with category reporting_line whose source_text contains it?"""
    o = json.loads(raw)
    info = [c for c in (o.get("informational_items") or []) if isinstance(c, dict) and c.get("category") == "reporting_line"]
    return {s: any(s.lower() in str(c.get("source_text", "")).lower() for c in info) for s in REPORTING}


def facts(raw: str, labels: dict, jd: str) -> dict:
    base = RA._facts(raw, labels, jd)
    whole = base["reporting_sentences_in_whole_answer"]
    whole_all = all(whole.values())
    routed = routed_reporting(raw)
    return {**base, "primary_retained_and_routed": whole_all and all(routed.values()),
            "reporting_sentences_routed": routed,
            "reporting_line_items": sum(1 for c in json.loads(raw).get("informational_items") or [] if isinstance(c, dict) and c.get("category") == "reporting_line"),
            "schema_violations": schema_violations(raw), "invented_sources": invented_sources(raw, jd)}


def reading(full_p: int, clarified_p: int) -> str:
    """Interpretation rule 1 of the protocol, applied to the primary outcome counts."""
    d = clarified_p - full_p
    if d >= SUPPORT_AT:
        return "supports the clarification helping on this JD (not proof of causation; not general reliability)"
    if d <= -(SUPPORT_AT):
        return "the clarification did not help on this JD in this run (not evidence of harm in general)"
    return "inconclusive (small or absent difference)"


def _arm(rows: list[dict]) -> dict:
    n = len(rows)
    return {"calls": n,
            "primary_retained_and_routed": sum(1 for r in rows if r["primary_retained_and_routed"]),
            "reporting_statement_in_whole_answer": sum(1 for r in rows if r["reporting_statement_in_whole_answer"]),
            "reporting_line_items_nonempty": sum(1 for r in rows if r["reporting_line_items"] > 0),
            "checks_passed": {k: sum(1 for r in rows if r["checks"][k]) for k in CHECKS},
            "duty_lines_found": [r["duty_lines_found"] for r in rows],
            "ict_alternatives_kept": sum(1 for r in rows if r["ict_alternatives_kept"]),
            "familiarity_alternatives_kept": sum(1 for r in rows if r["familiarity_alternatives_kept"]),
            "communication_split_calls": sum(1 for r in rows if r["communication_line_split_items"] >= 3),
            "schema_conforming_calls": sum(1 for r in rows if not r["schema_violations"]),
            "invented_source_items": [r["invented_sources"] for r in rows]}


def report(records: list[dict], labels: dict, jd: str) -> dict:
    ok = [r for r in records if not r.get("error")]
    errors = [r.get("error") for r in records if r.get("error")]
    if not ok:
        return {"status": "unavailable", "errors": errors}
    facts_rows = [{"order": r["order"], "arm": r["arm"], "call": r["call"], **facts(r["raw"], labels, jd)} for r in ok]
    per_call = [{k: f[k] for k in ("order", "arm", "call", "checks", "duty_lines_found", "primary_retained_and_routed",
                                   "reporting_statement_in_whole_answer", "reporting_sentences_in_whole_answer",
                                   "reporting_sentences_routed", "reporting_line_items", "ict_alternatives_kept",
                                   "familiarity_alternatives_kept", "communication_line_categories",
                                   "communication_line_split_items", "schema_violations", "invented_sources")}
                for f in facts_rows]
    fresh = {arm: _arm([f for f in facts_rows if f["arm"] == arm]) for arm in ORDER_ARMS}
    complete = all(fresh[a]["calls"] == CALLS_PER_ARM for a in ORDER_ARMS)
    full_p, cl_p = fresh["FULL"]["primary_retained_and_routed"], fresh["CLARIFIED"]["primary_retained_and_routed"]
    direct = {"primary_difference_CLARIFIED_minus_FULL": cl_p - full_p,
              "checks_passed_difference_CLARIFIED_minus_FULL": {k: fresh["CLARIFIED"]["checks_passed"][k] - fresh["FULL"]["checks_passed"][k] for k in CHECKS},
              "descriptive_only": {g: fresh["CLARIFIED"][g] for g in ("duty_lines_found", "ict_alternatives_kept", "familiarity_alternatives_kept",
                                                                       "communication_split_calls", "schema_conforming_calls", "invented_source_items")},
              "primary_reading": reading(full_p, cl_p) if complete else None}
    return {"status": "scored" if complete else "partial: the comparison needs all ten calls", "successful_calls": len(ok), "errors": errors,
            "per_call": per_call, "fresh_arms": fresh, "direct_comparison": direct,
            "historical_context_not_pooled": _historical(labels, jd),
            "scorer": "frozen TC20 checks (requirements_v2_tc20_eval.score_answer; labels unchanged)"}


def _historical(labels: dict, jd: str) -> dict:
    """Context only: earlier v2-5 (FULL) calls, reported per run and never pooled with the fresh arms."""
    ex5_rows = [json.loads(x) for x in (BACKEND / "tests" / "fixtures" / "requirements_v2_ex5_experiment" / "run" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    ex5_full = [r for r in ex5_rows if r["arm"] == "FULL"]
    paired = [json.loads(x) for x in (RA.A.EVIDENCE / "calls.jsonl").read_text(encoding="utf-8").splitlines() if json.loads(x)["jd"] == "tc20"]
    repeat = [json.loads(x) for x in (RA.RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    out = {}
    for name, rows, arm in (("paired_run_v2-5", paired, "v2-5"), ("repeat_v2-5", repeat, "v2-5"), ("ex5_experiment_FULL", ex5_full, "FULL")):
        calls = [facts(r["raw"], labels, jd) for r in rows if r["arm"] == arm]
        out[name] = {"calls": len(calls), "primary_retained_and_routed": sum(1 for c in calls if c["primary_retained_and_routed"]),
                     "reporting_statement_in_whole_answer": sum(1 for c in calls if c["reporting_statement_in_whole_answer"])}
    return out


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
