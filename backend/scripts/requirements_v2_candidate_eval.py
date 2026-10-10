#!/usr/bin/env python3
"""Bounded comparison of full criteria_extraction_v2-3 (the control) with criteria_extraction_v2-5 (the offline candidate) on the
same model, over the TC20 JD (official labels and scorer) and two frozen English/Arabic evaluation JDs (expected outputs fixed
in tests/fixtures/requirements_v2_candidate_v25/eval_set before any call).

  python3 scripts/requirements_v2_candidate_eval.py --dry-run                 plan, budget, hashes; NO network (the default)
  python3 scripts/requirements_v2_candidate_eval.py --execute --out DIR       the paid calls (not run by this commit)

Groups are reported separately and never merged into one score: omissions, categories, OR/AND structure, importance, routing,
invented content (verbatim failures and invented duties) and extra items. Nothing here changes a prompt, a label, the scorer,
the frozen package or production configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402  (frozen labels, TC20 scorer, sanitizer, user message)
from scripts import requirements_v2_tc20_compare as cmp  # noqa: E402  (pricing constants and the recorded status)

CANDIDATE_DIR = BACKEND / "prompt_candidates" / "criteria_extraction_v2-5"
CANDIDATE = CANDIDATE_DIR / "criteria_extraction_v2-5.txt"
CANDIDATE_SHA256 = "f1569a8b400257db20b1c0b24fd7728605fe472eef7fff993a384f12f0cbc8db"
CONTROL = ev.ARMS["v2-3"]["file"]
CONTROL_SHA256 = ev.ARMS["v2-3"]["sha256"]
EVAL_DIR = BACKEND / "tests" / "fixtures" / "requirements_v2_candidate_v25" / "eval_set"
EVAL_MANIFEST = EVAL_DIR / "MANIFEST.json"
CAP_USD = 2.00
MODEL = cmp.MODEL
PRICE_IN, PRICE_OUT = cmp.PRICE_IN, cmp.PRICE_OUT
MODEL_MUST_MATCH = True
ARMS = {"v2-3": {"file": CONTROL, "sha256": CONTROL_SHA256}, "v2-5": {"file": CANDIDATE, "sha256": CANDIDATE_SHA256}}
CONDITION_LISTS = ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
GROUPS = ("omissions", "categories", "or_and", "importance", "routing", "invented", "extra_items")
DISCLOSED = [
    "model: the same pinned snapshot for both arms (gpt-4.1-2025-04-14); only the prompt differs",
    "pricing: the reservation uses the standard prices recorded in requirements_v2_tc20_compare.PRICING_STATUS (owner-supplied from the official page)",
    "temperature 0.1, max_tokens 6000, response_format json_object and timeout 90 for both arms",
    "the worst case estimates input tokens as characters/2, which overstates the real count",
    "the TC20 JD is scored with the frozen official checks; the two new JDs are scored with the expected-output scorer below (separate groups)",
]


class EvalError(RuntimeError):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND.parent, capture_output=True, text=True, timeout=10,
                              check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def verify_inputs() -> dict:
    """Refuses to plan if any pinned input differs from its recorded hash. Returns the hashes that go into every manifest."""
    if sha256_bytes(CONTROL.read_bytes()) != CONTROL_SHA256:
        raise EvalError("the control prompt (v2-3) does not match its pinned sha256")
    if sha256_bytes(CANDIDATE.read_bytes()) != CANDIDATE_SHA256:
        raise EvalError("the candidate prompt (v2-5) does not match its pinned sha256")
    eval_manifest = json.loads(EVAL_MANIFEST.read_text(encoding="utf-8"))
    for rel, digest in eval_manifest["files"].items():
        if sha256_bytes((EVAL_DIR / rel).read_bytes()) != digest:
            raise EvalError(f"eval-set file {rel} does not match its frozen sha256")
    return {"candidate_sha256": CANDIDATE_SHA256, "control_sha256": CONTROL_SHA256,
            "eval_set_manifest_sha256": sha256_bytes(EVAL_MANIFEST.read_bytes()),
            "labels_sha256": sha256_bytes(ev.LABELS.read_bytes()), "script_sha256": sha256_bytes(pathlib.Path(__file__).read_bytes()),
            "compare_script_sha256": sha256_bytes((BACKEND / "scripts" / "requirements_v2_tc20_compare.py").read_bytes()),
            "tc20_eval_script_sha256": sha256_bytes((BACKEND / "scripts" / "requirements_v2_tc20_eval.py").read_bytes()),
            "git_commit": git_commit()}


def jd_sets() -> list[dict]:
    """The evaluation units: TC20 (official labels) and the two frozen JDs (expected outputs), each with its call count per arm."""
    tc20_jd, _ = ev.load_inputs()
    out = [{"id": "tc20", "kind": "tc20", "jd": tc20_jd, "metadata": ev.JOB_METADATA, "calls_per_arm": 5}]
    for name in ("eval_en_field_service_01", "eval_ar_facilities_01"):
        spec = json.loads((EVAL_DIR / "expected" / f"{name}.json").read_text(encoding="utf-8"))
        jd = (EVAL_DIR / "jds" / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")
        out.append({"id": name, "kind": "expected", "jd": jd, "expected": spec, "metadata": {"title": spec["title"]},
                    "calls_per_arm": 3})
    return out


def messages(arm: str, unit: dict) -> list[dict]:
    raw = ARMS[arm]["file"].read_bytes()
    if sha256_bytes(raw) != ARMS[arm]["sha256"]:
        raise EvalError(f"{arm}: prompt sha256 does not match the pinned value")
    return [{"role": "system", "content": raw.decode("utf-8")},
            {"role": "user", "content": ev.user_message(unit["jd"], unit["metadata"])}]


def worst_case(msgs: list[dict]) -> float:
    est_in = math.ceil(sum(len(m["content"]) for m in msgs) / 2)
    return round(est_in * PRICE_IN + ev.MAX_TOKENS * PRICE_OUT, 6)


def plan(cap: float = CAP_USD) -> dict:
    verify_inputs()
    units = jd_sets()
    calls = []
    for unit in units:
        for arm in ARMS:
            msgs = messages(arm, unit)
            for n in range(1, unit["calls_per_arm"] + 1):
                calls.append({"jd": unit["id"], "arm": arm, "call": n, "worst_case_usd": worst_case(msgs)})
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > cap:
        raise EvalError(f"worst-case total {total} exceeds the cap {cap}")
    return {"model": MODEL, "calls": calls, "call_count": len(calls), "worst_case_total_usd": total, "cap_usd": cap,
            "temperature": ev.TEMPERATURE, "max_tokens": ev.MAX_TOKENS, "response_format": "json_object", "timeout_s": ev.TIMEOUT_S,
            "retries": 0, "price_in_usd_per_token": PRICE_IN, "price_out_usd_per_token": PRICE_OUT,
            "pricing_status": cmp.PRICING_STATUS}


# ── scoring of the two new JDs against their frozen expected outputs ───────────────────────────────────────────────

def _norm(s) -> str:
    return " ".join(str(s).split())


def score_expected(raw: str, jd: str, spec: dict) -> dict:
    """Groups for one answer. Each expected entry is matched to one answer item whose source_text contains the entry's source and
    whose text (or alternatives) contains its key; an item is used once. Every group is a count of failures, not a score."""
    zero = {g: 0 for g in GROUPS}
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        obj = None
    if not isinstance(obj, dict):
        return {"valid_json": False, "entries": len(spec["items"]), "conditions": len(spec["conditions"]),
                "groups": {**zero, "omissions": len(spec["items"]), "routing": len(spec["conditions"])}}
    items = [(c, it) for c, lst in (obj.get("categories") or {}).items() if isinstance(lst, list) for it in lst if isinstance(it, dict)]
    used: set[int] = set()
    g = dict(zero)
    matched_entries = []
    for e in spec["items"]:
        hit = None
        for idx, (_, it) in enumerate(items):
            if idx in used or e["source"] not in str(it.get("source_text", "")):
                continue
            blob = " ".join([str(it.get("text", "")), *[str(a) for a in (it.get("alternatives") or [])]]).casefold()
            if e["key"].casefold() in blob:
                hit = idx
                break
        if hit is None:
            g["omissions"] += 1
            continue
        used.add(hit)
        matched_entries.append(hit)
        c, it = items[hit]
        if c != e["category"]:
            g["categories"] += 1
        if it.get("importance") != e["importance"]:
            g["importance"] += 1
        alts = it.get("alternatives")
        if e["alternatives"]:
            if sorted(_norm(a) for a in (alts or [])) != sorted(_norm(a) for a in e["alternatives"]):
                g["or_and"] += 1
        elif alts is not None:
            g["or_and"] += 1
    for c_entry in spec["conditions"]:
        found = None
        for lst in CONDITION_LISTS:
            for row in obj.get(lst) or []:
                if isinstance(row, dict) and c_entry["source"] in str(row.get("source_text", "")) \
                        and c_entry["key"].casefold() in str(row.get("text", "")).casefold():
                    found = lst
        if found is None or found != c_entry["list"]:
            g["routing"] += 1
    expected_duties = {e["source"] for e in spec["items"] if e["origin"] == "from_responsibilities"}
    for idx, (c, it) in enumerate(items):
        src = str(it.get("source_text", ""))
        if src not in jd:
            g["invented"] += 1
        elif idx not in used:
            if it.get("origin") == "from_responsibilities" and src not in expected_duties:
                g["invented"] += 1
            else:
                g["extra_items"] += 1
    for lst in CONDITION_LISTS:
        for row in obj.get(lst) or []:
            if isinstance(row, dict) and str(row.get("source_text", "")) not in jd:
                g["invented"] += 1
    return {"valid_json": True, "entries": len(spec["items"]), "conditions": len(spec["conditions"]), "groups": g}


def score_tc20(raw: str, jd: str, labels: dict) -> dict:
    """The official TC20 checks, mapped to the same group names. Each group lists the failed checks, reported separately."""
    s = ev.score_answer(raw, jd, labels)
    if not s["valid_json"]:
        return {"valid_json": False, "checks": None}
    failed = [k for k, v in s["checks"].items() if not v]
    return {"valid_json": True, "checks": s["checks"], "failed_checks": failed,
            "invented": s["invented"], "warnings": s["warnings"], "items": s["items"],
            "groups": {"omissions": [k for k in failed if k in ("C_RESP", "C_COMP")],
                       "categories": [k for k in failed if k == "C_LOCAL"],
                       "or_and": [k for k in failed if k in ("C_EXP_OR", "C_FAM_OR", "C_AND")],
                       "routing": [k for k in failed if k in ("C_LOCATION", "C_REPORTING")]}}


# ── the protected run ─────────────────────────────────────────────────────────────────────────────────────────────────

def prepare_output_dir(out: pathlib.Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise EvalError(f"{out} is not empty; previous evidence is never overwritten")
    out.mkdir(parents=True, exist_ok=True)


def run(out: pathlib.Path, api_call, cap: float = CAP_USD) -> dict:
    """Calls in the planned order. No retries. Stops at the first failed call or returned-model mismatch, and before any call that
    could take spent + the remaining worst cases above the cap. Each record carries the input hashes."""
    p = plan(cap)
    pins = verify_inputs()
    units = {u["id"]: u for u in jd_sets()}
    prepare_output_dir(out)
    manifest = {"pins": pins, "pricing_status": cmp.PRICING_STATUS, "pricing_verified": cmp.PRICING_VERIFIED,
                "disclosed_differences": DISCLOSED, "policy": "no retries; the run stops at the first failed call",
                "plan": p, "started": time.time()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    spent, attempted, stopped = 0.0, 0, None
    remaining = [c["worst_case_usd"] for c in p["calls"]]
    with (out / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(p["calls"]):
            if spent + sum(remaining[i:]) > cap:
                stopped = "cap_guard"
                break
            attempted += 1
            unit = units[c["jd"]]
            msgs = messages(c["arm"], unit)
            withheld = [unit["jd"], msgs[0]["content"], msgs[1]["content"]]
            rec = {"jd": c["jd"], "arm": c["arm"], "call": c["call"], "model": MODEL,
                   "input": {"system_sha256": sha256_bytes(msgs[0]["content"].encode("utf-8")),
                             "user_sha256": sha256_bytes(msgs[1]["content"].encode("utf-8"))}}
            try:
                r = api_call(msgs)
                usage = r.get("usage") or {}
                cost = usage.get("prompt_tokens", 0) * PRICE_IN + usage.get("completion_tokens", 0) * PRICE_OUT
                spent += cost
                rec.update({"raw": r.get("raw", ""), "finish_reason": r.get("finish_reason"), "model_returned": r.get("model"),
                            "usage": usage, "cost_usd": round(cost, 6), "error": None})
                if MODEL_MUST_MATCH and r.get("model") != MODEL:
                    rec["error"] = {"kind": "ModelMismatch", "http_status": None, "code": None, "type": None, "param": None,
                                    "message": None}
            except Exception as exc:                                  # recorded; never retried; the run stops here
                rec.update({"raw": "", "finish_reason": None, "model_returned": None, "usage": {}, "cost_usd": 0.0,
                            "error": ev.sanitize_error(exc, withheld)})
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if rec["error"]:
                stopped = f"error:{rec['error']['kind']}"
                break
    summary = {"spent_usd": round(spent, 6), "calls_attempted": attempted,
               "successful_calls": sum(1 for x in (json.loads(l) for l in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines())
                                       if not x.get("error")), "stopped": stopped}
    (out / "run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def report(records: list[dict]) -> dict:
    """Groups per arm and per JD, separately. An arm with no successful call is reported as unavailable."""
    units = {u["id"]: u for u in jd_sets()}
    out: dict = {}
    for arm in ARMS:
        out[arm] = {}
        for jd_id, unit in units.items():
            rows = [r for r in records if r["arm"] == arm and r["jd"] == jd_id]
            ok = [r for r in rows if not r.get("error")]
            if not ok:
                out[arm][jd_id] = {"status": "unavailable", "errors": [r["error"] for r in rows if r.get("error")]}
                continue
            if unit["kind"] == "tc20":
                scored = [score_tc20(r["raw"], unit["jd"], ev.load_inputs()[1]) for r in ok]
                out[arm][jd_id] = {"status": "scored", "successful_calls": len(ok), "scorer": "official TC20 checks (frozen labels)",
                                   "failed_checks_per_call": [s.get("failed_checks") for s in scored]}
            else:
                scored = [score_expected(r["raw"], unit["jd"], unit["expected"]) for r in ok]
                totals = {g: sum(s["groups"][g] for s in scored) for g in GROUPS}
                out[arm][jd_id] = {"status": "scored", "successful_calls": len(ok), "scorer": "expected-output scorer (frozen before calls)",
                                   "invalid_json": sum(1 for s in scored if not s["valid_json"]),
                                   "expected_entries_per_call": len(unit["expected"]["items"]),
                                   "failures_total_by_group": totals,
                                   "failures_per_call": [s["groups"] for s in scored]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan, budget and hashes only (default)")
    mode.add_argument("--execute", action="store_true", help="the paid calls (needs verified pricing, --out and OPENAI_API_KEY)")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    p = plan()
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "plan": p, "pins": verify_inputs(), "pricing_verified": cmp.PRICING_VERIFIED,
                          "disclosed_differences": DISCLOSED,
                          "jds": [{"id": u["id"], "calls_per_arm": u["calls_per_arm"]} for u in jd_sets()]},
                         ensure_ascii=False, indent=1))
        return 0
    if not cmp.PRICING_VERIFIED:
        raise EvalError("pricing is not recorded as verified")
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute needs --out (a new directory) and OPENAI_API_KEY")
    res = run(args.out, cmp.openai_call_model)
    records = [json.loads(x) for x in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = report(records)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
