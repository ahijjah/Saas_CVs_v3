#!/usr/bin/env python3
"""Bounded comparison of criteria_extraction_v2-3 (the active prompt text) and criteria_extraction_v2-4 (offline candidate)
on the stored JD of JOB-2026-0121, plus the offline regression of the 12-case benchmark.

  python scripts/requirements_v2_tc20_eval.py --dry-run [--out DIR]       plan and worst-case cost; NO network (the default)
  python scripts/requirements_v2_tc20_eval.py --execute --out DIR        the paid calls: 5 per arm, no retries, cap USD 0.10
  python scripts/requirements_v2_tc20_eval.py --offline-regression       the 12 stored benchmark answers, read-only, against the stored gates

Nothing here changes the active prompt, the frozen package, the official benchmark gates or any job. The labels are frozen
in tests/fixtures/requirements_v2_technical_coordinator_20/expected_labels.json (sha256 is written to the run manifest).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
FIX = BACKEND / "tests" / "fixtures" / "requirements_v2_technical_coordinator_20"
EVIDENCE = FIX / "evidence.json"
LABELS = FIX / "expected_labels.json"
BENCH_RUN = BACKEND / "benchmark_results" / "requirements_v2" / "v2-2_run1"

ARMS = {
    "v2-3": {"file": BACKEND / "prompt_candidates/criteria_extraction_v2-3/criteria_extraction_v2-3.txt",
             "sha256": "21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b"},
    "v2-4": {"file": BACKEND / "prompt_candidates/criteria_extraction_v2-4/criteria_extraction_v2-4.txt",
             "sha256": "dd2651bcda14ae816c89d33dd08ecb4aefa5d63e2e51dfbe7d2b39f8b1989a9e"},
}
MODEL = "gpt-4o-mini-2024-07-18"          # the approved snapshot (requested and returned must both match)
TEMPERATURE = 0.1
MAX_TOKENS = 6000
TIMEOUT_S = 90
PRICE_IN, PRICE_OUT = 0.15e-6, 0.60e-6    # USD per token, gpt-4o-mini list price (as in requirements_v2_extraction_run.py)
CAP_USD = 0.10
CALLS_PER_ARM = 5


class PlanError(RuntimeError):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_inputs() -> tuple[str, dict]:
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    jd = ev["description"]
    if sha256_bytes(jd.encode("utf-8")) != labels["jd_sha256"]:
        raise PlanError("the stored JD does not match the frozen labels")
    return jd, labels


# The job context of JOB-2026-0121 as it is on the VPS (empty fields are left out of the message, as in the live builder).
JOB_METADATA = {"title": "Technical Coordinator 20", "department": None, "experience_level": None,
                "location": None, "job_type": None, "work_mode": None}
_CONTEXT_FIELDS = (
    ("Job Title", "title"), ("Department", "department"), ("Seniority Level", "experience_level"),
    ("Location", "location"), ("Employment Type", "job_type"), ("Work Mode", "work_mode"),
)


def context_lines(metadata: dict | None) -> list[str]:
    """Mirrors services/requirements_v2/extraction/prompt.build_user_message: only non-empty fields, labelled as the live builder does."""
    context = [f"{label}: {metadata.get(key)}" for label, key in _CONTEXT_FIELDS if metadata and metadata.get(key)]
    return ["Job Context:", *context, ""] if context else []


def user_message(jd_text: str, metadata: dict | None = JOB_METADATA) -> str:
    """The user message of the live request. Mirrors services/requirements_v2/extraction/prompt.build_user_message (imported only
    by a test: this script never imports the extraction package, so it stays outside the production isolation rules)."""
    lines = [*context_lines(metadata), "Job Description (verbatim, between the markers):", "<<<JD", jd_text or "", "JD>>>"]
    return "\n".join(lines)


def context_sha256(metadata: dict | None = JOB_METADATA) -> str:
    return sha256_bytes(json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def messages_for(arm: str, jd: str) -> list[dict]:
    raw = ARMS[arm]["file"].read_bytes()
    if sha256_bytes(raw) != ARMS[arm]["sha256"]:
        raise PlanError(f"{arm}: prompt file sha256 does not match the pinned value")
    return [{"role": "system", "content": raw.decode("utf-8")}, {"role": "user", "content": user_message(jd)}]


def worst_case_cost(messages: list[dict]) -> float:
    """Upper bound: input tokens at most half the characters (Arabic and English both use fewer characters per token), output at max_tokens."""
    est_in = math.ceil(sum(len(m["content"]) for m in messages) / 2)
    return est_in * PRICE_IN + MAX_TOKENS * PRICE_OUT


def plan(jd: str) -> dict:
    calls = []
    for arm in ARMS:
        msgs = messages_for(arm, jd)
        for n in range(1, CALLS_PER_ARM + 1):
            calls.append({"arm": arm, "call": n, "worst_case_usd": round(worst_case_cost(msgs), 6)})
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > CAP_USD:
        raise PlanError(f"worst-case total {total} exceeds the cap {CAP_USD}")
    return {"calls": calls, "worst_case_total_usd": total, "cap_usd": CAP_USD, "model": MODEL, "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS, "timeout_s": TIMEOUT_S, "retries": 0}


SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.\-/]{1,80}$")


def _safe(value) -> str | None:
    """A provider field kept for diagnosis: a short identifier only. Free text is never kept, because it can echo request content."""
    return value if isinstance(value, str) and SAFE_TOKEN.match(value) else None


def sanitize_error(exc: BaseException) -> dict:
    """What is kept of a failed request: the exception class, the HTTP status and the provider's code, type and param.
    Never the message, headers, the API key or any request content."""
    body = getattr(exc, "body", None)
    err = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    status = getattr(exc, "status_code", None)
    return {"kind": type(exc).__name__,
            "http_status": status if isinstance(status, int) else None,
            "code": _safe(getattr(exc, "code", None) or err.get("code")),
            "type": _safe(getattr(exc, "type", None) or err.get("type")),
            "param": _safe(getattr(exc, "param", None) or err.get("param"))}


def prepare_output_dir(out: pathlib.Path) -> None:
    """A run never overwrites evidence: the directory must be new or empty."""
    if out.exists() and any(out.iterdir()):
        raise PlanError(f"{out} is not empty; previous evidence is never overwritten, choose a new directory")
    out.mkdir(parents=True, exist_ok=True)


def run(out: pathlib.Path, jd: str, labels: dict, api_call) -> dict:
    """Executes the planned calls in order. No retries. The run stops at the first failed call (its error is recorded), and
    before any call that could take the total (spent + worst case of every remaining call) above the cap."""
    p = plan(jd)
    msgs = {a: messages_for(a, jd) for a in ARMS}
    user_hashes = {a: sha256_bytes(msgs[a][1]["content"].encode("utf-8")) for a in ARMS}
    if len(set(user_hashes.values())) != 1:
        raise PlanError("the two arms do not send the same user message")
    prepare_output_dir(out)
    manifest = {"labels_sha256": sha256_bytes(LABELS.read_bytes()), "jd_sha256": labels["jd_sha256"],
                "prompts": {a: ARMS[a]["sha256"] for a in ARMS}, "job_metadata": JOB_METADATA,
                "context_sha256": context_sha256(JOB_METADATA), "user_message_sha256": next(iter(user_hashes.values())),
                "policy": "no retries; the run stops at the first failed call", "plan": p, "started": time.time()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    spent, attempted, stopped = 0.0, 0, None
    remaining = [c["worst_case_usd"] for c in p["calls"]]
    with (out / "calls.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(p["calls"]):
            if spent + sum(remaining[i:]) > CAP_USD:
                stopped = "cap_guard"
                break
            attempted += 1
            rec = {"arm": c["arm"], "call": c["call"]}
            try:
                r = api_call(msgs[c["arm"]])
                usage = r.get("usage") or {}
                cost = usage.get("prompt_tokens", 0) * PRICE_IN + usage.get("completion_tokens", 0) * PRICE_OUT
                spent += cost
                rec.update({"raw": r.get("raw", ""), "finish_reason": r.get("finish_reason"), "model": r.get("model"),
                            "usage": usage, "cost_usd": round(cost, 6), "error": None})
            except Exception as exc:                                  # recorded; never retried; the run stops here
                err = sanitize_error(exc)
                rec.update({"raw": "", "finish_reason": None, "model": None, "usage": {}, "cost_usd": 0.0, "error": err})
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                stopped = f"error:{err['kind']}"
                break
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
    records = [json.loads(x) for x in (out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    summary = {"spent_usd": round(spent, 6), "calls_attempted": attempted,
               "successful_calls": sum(1 for r in records if not r.get("error")), "stopped": stopped}
    (out / "run.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def diagnose(out: pathlib.Path, jd: str, arm: str, api_call) -> dict:
    """ONE request: the exact evaluation request of the given arm, under its worst-case reservation (which must fit the cap).
    No retries. Writes diagnostic.json with the request settings (no content), the result or the sanitized error."""
    msgs = messages_for(arm, jd)
    reservation = worst_case_cost(msgs)
    if reservation > CAP_USD:
        raise PlanError(f"the reservation {reservation} for one request exceeds the cap {CAP_USD}")
    prepare_output_dir(out)
    rec = {"arm": arm, "reservation_usd": round(reservation, 6), "reservation_cap_usd": CAP_USD,
           "request": {"model": MODEL, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "response_format": "json_object",
                       "timeout_s": TIMEOUT_S, "retries": 0,
                       "messages": [{"role": m["role"], "characters": len(m["content"]),
                                     "sha256": sha256_bytes(m["content"].encode("utf-8"))} for m in msgs]},
           "prompt_sha256": ARMS[arm]["sha256"], "job_metadata": JOB_METADATA}
    try:
        r = api_call(msgs)
        rec["result"] = {"finish_reason": r.get("finish_reason"), "model": r.get("model"), "usage": r.get("usage") or {},
                         "raw": r.get("raw", ""), "error": None}
    except Exception as exc:
        rec["result"] = {"error": sanitize_error(exc)}
    (out / "diagnostic.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def openai_call(messages: list[dict]) -> dict:
    """The only function that talks to the network. Used by --execute and --diagnose only. The client never retries; a failure
    is raised to the caller, which keeps only its sanitized description."""
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=TIMEOUT_S)
    r = client.chat.completions.create(model=MODEL, messages=messages, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                                       response_format={"type": "json_object"})
    ch = r.choices[0]
    return {"raw": ch.message.content or "", "finish_reason": ch.finish_reason, "model": r.model,
            "usage": {"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens}}


# ── scoring: the labels' checks, one function each (see expected_labels.json: check -> finding) ──────────────────────

def _items(obj: dict) -> list[tuple[str, dict]]:
    cats = obj.get("categories") or {}
    return [(c, it) for c, lst in cats.items() if isinstance(lst, list) for it in lst if isinstance(it, dict)]


def _covering(items, phrase):
    return [(c, it) for c, it in items if phrase in str(it.get("source_text", ""))]


def score_answer(raw: str, jd: str, labels: dict) -> dict:
    ev_vals = labels["expected_values"]
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {"valid_json": False, "checks": {k: False for k in labels["checks"]}, "invented": None, "warnings": None, "items": 0}
    if not isinstance(obj, dict):
        return {"valid_json": False, "checks": {k: False for k in labels["checks"]}, "invented": None, "warnings": None, "items": 0}
    items = _items(obj)
    dutyset = ev_vals["duty_lines"]
    resp = [it for _, it in items if it.get("origin") == "from_responsibilities"]
    checks = {
        "C_RESP": all(any(d in str(it.get("source_text", "")) for it in resp) for d in dutyset),
        "C_COMP": all(bool(_covering(items, line)) for line in ev_vals["competency_lines"]),
    }
    exp_or = [(c, it) for c, it in items if "ICT systems support" in str(it.get("source_text", ""))]
    checks["C_EXP_OR"] = len(exp_or) == 1 and exp_or[0][0] == "experience" and sorted(exp_or[0][1].get("alternatives") or []) == sorted(ev_vals["experience_or_alternatives"])
    fam = _covering(items, "Familiarity with business process documentation")
    checks["C_FAM_OR"] = len(fam) == 1 and ev_vals["familiarity_or_must_include"] in (fam[0][1].get("alternatives") or [])
    loc = _covering(items, "Knowledge of the local business and regulatory environment")
    checks["C_LOCAL"] = len(loc) == 1 and loc[0][0] == "domain_knowledge"
    checks["C_LOCATION"] = any(ev_vals["location_marker"] in str(c.get("source_text", "")) for c in (obj.get("non_scoreable_requirements") or []) if isinstance(c, dict))
    checks["C_REPORTING"] = any(ev_vals["reporting_marker"] in str(c.get("source_text", "")) and c.get("category") == "reporting_line"
                                for c in (obj.get("informational_items") or []) if isinstance(c, dict))
    soft = [it for c, it in items if c == "soft_skills"]
    texts = [str(it.get("text", "")).lower() for it in soft]
    checks["C_AND"] = (any("communication" in t for t in texts) and any("coordination" in t for t in texts) and any("teamwork" in t for t in texts)
                       and not any("communication" in t and "teamwork" in t for t in texts))
    invented_sources = sum(1 for _, it in items if str(it.get("source_text", "")) not in jd)
    invented_duties = sum(1 for it in resp if str(it.get("source_text", "")) not in dutyset)
    warnings = obj.get("warnings")
    return {"valid_json": True, "checks": checks, "items": len(items),
            "invented": {"source_not_verbatim": invented_sources, "duty_not_a_duty_line": invented_duties},
            "warnings": len(warnings) if isinstance(warnings, list) else None}


CATEGORY_CHECKS = {"omissions": ["C_RESP", "C_COMP"], "alternatives": ["C_EXP_OR", "C_FAM_OR"], "categories": ["C_LOCAL"],
                   "routing": ["C_LOCATION", "C_REPORTING"], "and_splitting": ["C_AND"]}


def report(records: list[dict], labels: dict) -> dict:
    out = {}
    for arm in ARMS:
        arm_recs = [r for r in records if r["arm"] == arm]
        rows = [r for r in arm_recs if r.get("scored")]
        errors = [r["error"] for r in arm_recs if r.get("error")]
        n = len(rows)
        if n == 0:
            out[arm] = {"status": "unavailable", "successful_calls": 0, "calls_attempted": len(arm_recs), "errors": errors,
                        "consistency": "unavailable", "note": "no successful call: nothing is reported as passing or consistent"}
            continue
        per_check = {k: sum(1 for r in rows if r["scored"]["checks"][k]) for k in labels["checks"]}
        out[arm] = {
            "status": "scored", "successful_calls": n, "calls_attempted": len(arm_recs), "errors": errors,
            "invalid_json": sum(1 for r in rows if not r["scored"]["valid_json"]),
            "pass_per_check": per_check,
            "by_category": {cat: {k: f"{per_check[k]}/{n}" for k in ks} for cat, ks in CATEGORY_CHECKS.items()},
            "invented_items_total": sum((r["scored"]["invented"] or {}).get("source_not_verbatim", 0) for r in rows),
            "invented_duties_total": sum((r["scored"]["invented"] or {}).get("duty_not_a_duty_line", 0) for r in rows),
            "warnings_per_call": [r["scored"]["warnings"] for r in rows],
            "items_per_call": [r["scored"]["items"] for r in rows],
            "consistency": {k: ("always" if per_check[k] == n else "never" if per_check[k] == 0 else "mixed") for k in labels["checks"]},
        }
    return out


# ── offline regression of the 12-case benchmark (read-only; the official gates are not changed) ─────────────────────────
def offline_regression() -> dict:
    import scripts.requirements_v2_extraction_eval as ev
    stored = json.loads((BENCH_RUN / "results.json").read_text(encoding="utf-8"))
    runs: dict[str, dict] = {"run1": {}, "run2": {}}
    for line in (BENCH_RUN / "calls.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("raw") and not r.get("quarantined"):
            runs[r["run"]][r["case"]] = {"raw": r["raw"], "finish_reason": r["finish_reason"]}
    cases = ev.load_cases()
    scored = ev.score_runs(cases, runs)
    gates = ev.gates(scored)
    same = gates == stored["gates"]
    return {"cases": len(cases), "gates_match_stored": same, "gates": gates,
            "mismatches": [k for k in gates if gates[k] != stored["gates"].get(k)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan and worst-case cost only (default)")
    mode.add_argument("--execute", action="store_true", help="perform the paid calls: 5 per arm, stops at the first failed call")
    mode.add_argument("--diagnose", action="store_true", help="ONE request of one arm, to diagnose a failure (no retries)")
    mode.add_argument("--offline-regression", action="store_true", help="12-case offline regression of the stored answers")
    ap.add_argument("--arm", choices=sorted(ARMS), default="v2-3", help="the arm for --diagnose")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory (never overwritten)")
    args = ap.parse_args(argv)
    if args.offline_regression:
        print(json.dumps(offline_regression(), ensure_ascii=False, indent=1))
        return 0
    jd, labels = load_inputs()
    p = plan(jd)
    if not (args.execute or args.diagnose):
        print(json.dumps({"mode": "dry-run", **p, "labels_sha256": sha256_bytes(LABELS.read_bytes()), "job_metadata": JOB_METADATA,
                          "context_sha256": context_sha256(JOB_METADATA),
                          "user_message_sha256": sha256_bytes(user_message(jd).encode("utf-8"))}, ensure_ascii=False, indent=1))
        return 0
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute and --diagnose need --out (a new directory) and OPENAI_API_KEY")
    if args.diagnose:
        rec = diagnose(args.out, jd, args.arm, openai_call)
        print(json.dumps(rec["result"], ensure_ascii=False, indent=1))
        return 0
    res = run(args.out, jd, labels, openai_call)
    records = [json.loads(line) for line in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    for r in records:
        r["scored"] = score_answer(r.get("raw", ""), jd, labels) if not r.get("error") else None
    rep = report(records, labels)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
