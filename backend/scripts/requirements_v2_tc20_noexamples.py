#!/usr/bin/env python3
"""Control experiment on the TC20 JD: criteria_extraction_v2-3 with ONLY the EXAMPLES block removed (criteria_extraction_v2-3-noex).

  python scripts/requirements_v2_tc20_noexamples.py --dry-run                plan, reservation under the cap, stored-answer comparison; NO network (the default)
  python scripts/requirements_v2_tc20_noexamples.py --execute --out DIR      the paid calls: 5 calls, no retries, cap USD 0.03 (not run without approval)

Everything else is the existing bounded evaluation: the same JD, the same VPS job context (through the same user message),
the same model and settings, the frozen labels and the frozen scorer (scripts/requirements_v2_tc20_eval.py), which are imported
and not changed. Education-alternative retention is reported separately and is not part of the official checks.
Nothing here changes the active prompt, the frozen package, the official benchmark gates or any job.
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
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402  (labels, scorer, sanitizer, user message: reused, not copied)

BASE = ev.ARMS["v2-3"]["file"]
BASE_SHA256 = ev.ARMS["v2-3"]["sha256"]
VARIANT = BACKEND / "prompt_candidates" / "criteria_extraction_v2-3-noex" / "criteria_extraction_v2-3-noex.txt"
VARIANT_SHA256 = "764d2ee4ad8a523e67ef27b143b41d01538853d8f7d3c8921f3adf4430b87d90"
REMOVED_SHA256 = "71e72b12bf873ef6d7cc0982235e41dc6201875a2a55166aaf4c568b2d20c322"
EXAMPLES_MARKER = "EXAMPLES (illustrative only; never copy them into your answer)\n"
ARM = "v2-3-noex"
CAP_USD = 0.03
CALLS = 5
STORED = ev.FIX / "audit" / "run_tc20_v2-3_v2-4"
# The JD sentence that carries the education alternatives (rule 4). Retention is read from the answer's education items.
EDUCATION_SOURCE = "Bachelor’s degree in Computer Science, Information Technology"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def check_variant() -> bytes:
    """The variant must be an exact byte prefix of v2-3, cut at the EXAMPLES heading, with the pinned hashes."""
    base = BASE.read_bytes()
    raw = VARIANT.read_bytes()
    marker = EXAMPLES_MARKER.encode("utf-8")
    if sha256_bytes(base) != BASE_SHA256:
        raise ev.PlanError("v2-3 prompt file sha256 does not match the pinned value")
    if sha256_bytes(raw) != VARIANT_SHA256:
        raise ev.PlanError("variant prompt file sha256 does not match the pinned value")
    if base.count(marker) != 1 or base.index(marker) != len(raw) or base[:len(raw)] != raw:
        raise ev.PlanError("the variant is not the exact prefix of v2-3 before the EXAMPLES block")
    if sha256_bytes(base[len(raw):]) != REMOVED_SHA256:
        raise ev.PlanError("the removed span does not match the pinned value")
    return raw


def messages(jd: str) -> list[dict]:
    raw = check_variant()
    return [{"role": "system", "content": raw.decode("utf-8")}, {"role": "user", "content": ev.user_message(jd)}]


def plan(jd: str, cap: float = CAP_USD) -> dict:
    msgs = messages(jd)
    per_call = round(ev.worst_case_cost(msgs), 6)
    calls = [{"arm": ARM, "call": n, "worst_case_usd": per_call} for n in range(1, CALLS + 1)]
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > cap:
        raise ev.PlanError(f"worst-case total {total} exceeds the cap {cap}")
    return {"calls": calls, "worst_case_total_usd": total, "cap_usd": cap, "model": ev.MODEL, "temperature": ev.TEMPERATURE,
            "max_tokens": ev.MAX_TOKENS, "timeout_s": ev.TIMEOUT_S, "retries": 0}


def education_retained(raw: str) -> bool | None:
    """True when the education item of the JD's degree sentence keeps its alternatives (a non-empty list). None when the answer
    is not a JSON object. Reported separately; it is not one of the labelled checks and it never changes an official score."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    items = (obj.get("categories") or {}).get("education") or []
    return any(isinstance(it, dict) and EDUCATION_SOURCE in str(it.get("source_text", "")) and bool(it.get("alternatives"))
               for it in items)


def run(out: pathlib.Path, jd: str, labels: dict, api_call, cap: float = CAP_USD) -> dict:
    """The planned calls in order. No retries. Stops at the first failed call (its sanitized error is recorded) and before any
    call that would take spent + the worst case of the remaining calls above the cap. The output directory must be new or empty."""
    p = plan(jd, cap)
    msgs = messages(jd)
    ev.prepare_output_dir(out)
    input_hashes = {"system_sha256": sha256_bytes(msgs[0]["content"].encode("utf-8")),
                    "user_sha256": sha256_bytes(msgs[1]["content"].encode("utf-8"))}
    manifest = {"arm": ARM, "labels_sha256": sha256_bytes(ev.LABELS.read_bytes()), "jd_sha256": labels["jd_sha256"],
                "prompt": {"file": VARIANT.name, "sha256": VARIANT_SHA256, "base_sha256": BASE_SHA256, "removed_span_sha256": REMOVED_SHA256},
                "input": input_hashes, "job_metadata": ev.JOB_METADATA, "context_sha256": ev.context_sha256(ev.JOB_METADATA),
                "policy": "no retries; the run stops at the first failed call", "plan": p, "started": time.time()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    withheld = [jd, msgs[0]["content"], msgs[1]["content"]]
    spent, attempted, stopped = 0.0, 0, None
    remaining = [c["worst_case_usd"] for c in p["calls"]]
    with (out / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(p["calls"]):
            if spent + sum(remaining[i:]) > cap:
                stopped = "cap_guard"
                break
            attempted += 1
            rec = {"arm": ARM, "call": c["call"], "input": input_hashes}
            try:
                r = api_call(msgs)
                usage = r.get("usage") or {}
                cost = usage.get("prompt_tokens", 0) * ev.PRICE_IN + usage.get("completion_tokens", 0) * ev.PRICE_OUT
                spent += cost
                rec.update({"raw": r.get("raw", ""), "finish_reason": r.get("finish_reason"), "model": r.get("model"),
                            "usage": usage, "cost_usd": round(cost, 6), "error": None})
            except Exception as exc:                                  # recorded; never retried; the run stops here
                rec.update({"raw": "", "finish_reason": None, "model": None, "usage": {}, "cost_usd": 0.0,
                            "error": ev.sanitize_error(exc, withheld)})
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if rec["error"]:
                stopped = f"error:{rec['error']['kind']}"
                break
    records = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = {"spent_usd": round(spent, 6), "calls_attempted": attempted,
               "successful_calls": sum(1 for r in records if not r.get("error")), "stopped": stopped}
    (out / "run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def report(records: list[dict], jd: str, labels: dict) -> dict:
    """The official checks, scored by the frozen scorer, and the education-alternative retention reported separately."""
    errors = [r["error"] for r in records if r.get("error")]
    rows = [r for r in records if not r.get("error")]
    n = len(rows)
    if n == 0:
        return {"status": "unavailable", "successful_calls": 0, "calls_attempted": len(records), "errors": errors,
                "note": "no successful call: nothing is reported as passing or consistent"}
    scored = [ev.score_answer(r["raw"], jd, labels) for r in rows]
    per_check = {k: sum(1 for s in scored if s["checks"][k]) for k in labels["checks"]}
    retained = [education_retained(r["raw"]) for r in rows]
    return {
        "status": "scored", "successful_calls": n, "calls_attempted": len(records), "errors": errors,
        "invalid_json": sum(1 for s in scored if not s["valid_json"]),
        "pass_per_check": per_check,
        "by_category": {cat: {k: f"{per_check[k]}/{n}" for k in ks} for cat, ks in ev.CATEGORY_CHECKS.items()},
        "consistency": {k: ("always" if per_check[k] == n else "never" if per_check[k] == 0 else "mixed") for k in labels["checks"]},
        "items_per_call": [s["items"] for s in scored],
        "education_alternative_retention": {"reported_separately": True, "official_score": False,
                                            "retained_per_call": retained, "retained": sum(1 for x in retained if x)},
    }


def stored_comparison(jd: str) -> dict:
    """The five stored v2-3 answers of the TC20 run, read offline: their prompt and input hashes against this variant's, and the
    official pass counts and education retention those answers had. Nothing is re-scored or re-run here."""
    manifest = json.loads((STORED / "manifest.json").read_text(encoding="utf-8"))
    stored_report = json.loads((STORED / "scored.json").read_text(encoding="utf-8"))["report"]["v2-3"]
    rows = [json.loads(x) for x in (STORED / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r["arm"] == "v2-3"]
    current_user = sha256_bytes(ev.user_message(jd).encode("utf-8"))
    retained = [education_retained(r["raw"]) for r in rows]
    return {
        "stored_calls": len(rows), "stored_errors": [r.get("error") for r in rows if r.get("error")],
        "stored_prompt_sha256": manifest["prompts"]["v2-3"], "variant_prompt_sha256": VARIANT_SHA256,
        "stored_user_message_sha256": manifest["user_message_sha256"], "current_user_message_sha256": current_user,
        "user_message_identical": manifest["user_message_sha256"] == current_user,
        "stored_context_sha256": manifest["context_sha256"], "current_context_sha256": ev.context_sha256(ev.JOB_METADATA),
        "stored_raw_sha256": [sha256_bytes(r["raw"].encode("utf-8")) for r in rows],
        "stored_pass_per_check": stored_report["pass_per_check"],
        "stored_education_alternative_retention": {"reported_separately": True, "official_score": False,
                                                   "retained_per_call": retained, "retained": sum(1 for x in retained if x)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan, reservation and stored comparison only (default)")
    mode.add_argument("--execute", action="store_true", help="the paid calls: 5, no retries, stops at the first failed call")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory (never overwritten)")
    args = ap.parse_args(argv)
    jd, labels = ev.load_inputs()
    p = plan(jd)
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "arm": ARM, "plan": p, "labels_sha256": sha256_bytes(ev.LABELS.read_bytes()),
                          "prompt": {"file": VARIANT.name, "sha256": VARIANT_SHA256, "characters": len(check_variant().decode("utf-8"))},
                          "job_metadata": ev.JOB_METADATA, "context_sha256": ev.context_sha256(ev.JOB_METADATA),
                          "user_message_sha256": sha256_bytes(ev.user_message(jd).encode("utf-8")),
                          "stored_comparison": stored_comparison(jd)}, ensure_ascii=False, indent=1))
        return 0
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute needs --out (a new directory) and OPENAI_API_KEY")
    res = run(args.out, jd, labels, ev.openai_call)
    records = [json.loads(x) for x in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = report(records, jd, labels)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
