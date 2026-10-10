#!/usr/bin/env python3
"""Analysis of raw answers, and the controlled stronger-model comparison on the TC20 JD (criteria_extraction_v2-3, unchanged).

  python scripts/requirements_v2_tc20_compare.py --dry-run               plan, reservation under the cap, pricing status; NO network (default)
  python scripts/requirements_v2_tc20_compare.py --execute --out DIR     the paid calls: 5 calls, no retries, stop on the first failure (NOT run here)

The prompt is the full v2-3 text, the user message is the stored VPS one, the labels and the scorer are the frozen ones
(scripts/requirements_v2_tc20_eval.py), and they are imported, not copied. The only intended difference from the stored
gpt-4o-mini arm is the model. Results are reported in their own groups and never replace the existing scores.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from scripts import requirements_v2_tc20_eval as ev  # noqa: E402  (labels, scorer, sanitizer, user message: reused, not copied)
from scripts import requirements_v2_tc20_noexamples as nx  # noqa: E402  (education-alternative reader: reused)

ARM = "v2-3-full-stronger"
ARM_PROMPT = "v2-3"                                   # the unchanged full prompt (ev.ARMS["v2-3"])
MODEL = "gpt-4.1-2025-04-14"                          # pinned snapshot; see PRICING_STATUS before any run
PRICE_IN, PRICE_OUT = 2.00e-6, 8.00e-6                # USD per token: standard input $2 / output $8 per 1M tokens
PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-4.1"
PRICING_VERIFIED = True                             # set only after the official page is checked and recorded above
PRICING_STATUS = ("VERIFIED (owner-supplied from the official model page, " + PRICING_SOURCE + "): snapshot gpt-4.1-2025-04-14, "
                  "standard input $2.00 and output $8.00 per 1M tokens. The sandbox could not open the page (DNS), so the figures "
                  "were recorded from the owner's reading; the snapshot's availability to the key is checked by the first request "
                  "(a model that differs from the pinned one stops the run).")
CAP_USD = 0.40                                        # explicit cap for this comparison (five worst cases, see the dry-run)
CALLS = 5
DISCLOSED_DIFFERENCES = [
    "model: gpt-4.1-2025-04-14 instead of gpt-4o-mini-2024-07-18 (the purpose of the comparison)",
    "pricing: the reservation uses the unverified prices above; the stored run used the verified gpt-4o-mini list price of the earlier script",
    "temperature 0.1, max_tokens 6000, response_format json_object and timeout 90 are sent as in the stored arm; gpt-4.1 accepts all four",
    "the worst case estimates input tokens as characters/2 (the same convention as the stored plan), which overstates the real count",
]
STORED = nx.STORED
MODEL_MUST_MATCH = True                               # the returned model must equal the requested snapshot

ITEM_KEYS = ("text", "importance", "importance_cue", "source_text", "origin", "alternatives", "experience")
TOP_KEYS = ("scoreability", "categories", "category_weights", "non_scoreable_requirements", "post_hiring_conditions",
            "informational_items", "warnings")
CATEGORY_KEYS = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
CONDITION_KEYS = {"non_scoreable_requirements": ("text", "category", "reason", "source_text"),
                  "post_hiring_conditions": ("text", "category", "reason", "source_text"),
                  "informational_items": ("text", "category", "reason", "source_text")}
IMPORTANCES = ("required", "preferred")
ORIGINS = ("stated", "from_responsibilities")
SCOREABILITY = ("scoreable", "open_broad", "insufficient")


class ComparisonError(RuntimeError):
    pass


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── schema compliance (raw model output, against the v2-3 output contract) ───────────────────────────────────────────

def schema_problems(obj) -> list[str]:
    """Codes for every departure from the output contract. An empty list is full compliance."""
    if not isinstance(obj, dict):
        return ["not_an_object"]
    out = [f"top_missing:{k}" for k in TOP_KEYS if k not in obj]
    out += [f"top_extra:{k}" for k in obj if k not in TOP_KEYS]
    sc = obj.get("scoreability")
    if not isinstance(sc, dict) or sc.get("status") not in SCOREABILITY:
        out.append("scoreability_invalid")
    cats = obj.get("categories")
    if not isinstance(cats, dict):
        out.append("categories_not_object")
        cats = {}
    for c in CATEGORY_KEYS:
        if c not in cats:
            out.append(f"category_missing:{c}")
        elif not isinstance(cats[c], list):
            out.append(f"category_not_list:{c}")
    out += [f"category_unknown:{c}" for c in cats if c not in CATEGORY_KEYS]
    for c, lst in cats.items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            if not isinstance(it, dict):
                out.append("item_not_object")
                continue
            out += [f"item_missing:{k}" for k in ITEM_KEYS if k not in it]
            out += [f"item_extra:{k}" for k in it if k not in ITEM_KEYS]
            if it.get("importance") not in IMPORTANCES:
                out.append("item_importance_invalid")
            if it.get("origin") not in ORIGINS:
                out.append("item_origin_invalid")
            alt = it.get("alternatives", None)
            if alt is not None and not (isinstance(alt, list) and len(alt) >= 2 and all(isinstance(a, str) for a in alt)):
                out.append("item_alternatives_invalid")
            exp = it.get("experience", None)
            if exp is not None and not (isinstance(exp, dict) and "subject" in exp and "min_years" in exp):
                out.append("item_experience_invalid")
            if c != "experience" and exp is not None:
                out.append("item_experience_outside_experience")
    weights = obj.get("category_weights")
    if not isinstance(weights, dict):
        out.append("weights_not_object")
    else:
        out += [f"weight_missing:{c}" for c in CATEGORY_KEYS if c not in weights]
        for c in CATEGORY_KEYS:
            v = weights.get(c)
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100):
                out.append("weight_not_whole_0_100")
                break
    for name, keys in CONDITION_KEYS.items():
        lst = obj.get(name)
        if not isinstance(lst, list):
            continue
        for it in lst:
            if not isinstance(it, dict) or any(k not in it for k in keys):
                out.append(f"condition_invalid:{name}")
    if not isinstance(obj.get("warnings"), list) and "warnings" in obj:
        out.append("warnings_not_list")
    return out


# ── raw facts about one answer (what the stored defects are measured with) ───────────────────────────────────────────

def _items(obj: dict) -> list[tuple[str, dict]]:
    cats = obj.get("categories") or {}
    return [(c, it) for c, lst in cats.items() if isinstance(lst, list) for it in lst if isinstance(it, dict)]


def weight_validity(obj: dict) -> dict:
    """Raw proposal usability (the parser normalises a usable proposal; it does not accept whole-number-invalid input).
    usable: every value is a whole number 0..100 and at least one category WITH a required item has a positive weight.
    total_100 reports, separately, whether the raw total is exactly 100 (the prompt does not require it)."""
    weights = obj.get("category_weights") if isinstance(obj.get("category_weights"), dict) else {}
    vals = {c: weights.get(c) for c in CATEGORY_KEYS}
    whole = all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 for v in vals.values())
    required_cats = {c for c, it in _items(obj) if it.get("importance") == "required"}
    eligible_positive = sum(v for c, v in vals.items() if c in required_cats and isinstance(v, int) and not isinstance(v, bool))
    total = sum(v for v in vals.values() if isinstance(v, int) and not isinstance(v, bool))
    return {"whole_0_100": whole, "usable": bool(whole and eligible_positive > 0), "total": total, "total_is_100": total == 100,
            "positive_in_category_without_required": sorted(c for c, v in vals.items()
                                                            if isinstance(v, int) and v > 0 and c not in required_cats)}


def facts(raw: str, jd: str, labels: dict) -> dict:
    """Everything the audit and the comparison report about one answer. Parsing failure is recorded, never guessed."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {"valid_json": False, "schema_problems": ["invalid_json"], "checks": None}
    scored = ev.score_answer(raw, jd, labels)
    items = _items(obj) if isinstance(obj, dict) else []
    ev_vals = labels["expected_values"]
    comp = {line: sorted({c for c, it in items if line in str(it.get("source_text", ""))}) for line in ev_vals["competency_lines"]}
    soft = [it for c, it in items if c == "soft_skills"]
    return {
        "valid_json": True,
        "checks": scored["checks"],
        "schema_problems": schema_problems(obj),
        "weights": weight_validity(obj),
        "items_per_category": {c: sum(1 for cc, _ in items if cc == c) for c in CATEGORY_KEYS},
        "competency_lines_category": comp,
        "soft_skills_items": len(soft),
        "javascript_category": sorted({c for c, it in items if "JavaScript" in str(it.get("source_text", ""))}),
        # Measured on the ITEM text, not the source sentence: the baseline splits one source sentence into two items.
        "arabic_english_items": sum(1 for _, it in items if "Arabic" in str(it.get("text", "")) or "English" in str(it.get("text", ""))),
        "arabic_english_merged": any("Arabic" in str(it.get("text", "")) and "English" in str(it.get("text", "")) for _, it in items),
        "education_alternatives_retained": nx.education_retained(raw),
        "responsibility_items": sum(1 for _, it in items if it.get("origin") == "from_responsibilities"),
        "condition_items": {n: len(obj.get(n) or []) for n in CONDITION_KEYS} if isinstance(obj, dict) else None,
        "scoreability_status": (obj.get("scoreability") or {}).get("status") if isinstance(obj, dict) and isinstance(obj.get("scoreability"), dict) else None,
    }


def summarize(facts_list: list[dict]) -> dict:
    """Counts across answers, by group: official checks (category accuracy), weight validity, education, schema."""
    n = len(facts_list)
    ok = [f for f in facts_list if f.get("valid_json")]
    out = {"answers": n, "valid_json": len(ok)}
    if not ok:
        return out
    checks = {k: sum(1 for f in ok if f["checks"][k]) for k in ok[0]["checks"]}
    out["official_checks_pass"] = checks
    out["weights_usable"] = sum(1 for f in ok if f["weights"]["usable"])
    out["weights_total_is_100"] = sum(1 for f in ok if f["weights"]["total_is_100"])
    out["education_alternatives_retained"] = sum(1 for f in ok if f["education_alternatives_retained"])
    out["schema_compliant"] = sum(1 for f in ok if not f["schema_problems"])
    out["schema_problem_codes"] = dict(collections.Counter(c for f in ok for c in f["schema_problems"]))
    return out


def analysis_document(noex_rows: list[dict], stored_rows: list[dict], jd: str, labels: dict) -> dict:
    """The raw-answer comparison of the no-examples run with the stored v2-3 baseline (both read offline, nothing re-run)."""
    nx.verify_baseline()

    def per(rows):
        return [{"call": r["call"], **facts(r["raw"], jd, labels)} for r in rows if not r.get("error")]
    noex, stored = per(noex_rows), per(stored_rows)
    return {"noex": {"summary": summarize(noex), "answers": noex}, "stored_v2-3": {"summary": summarize(stored), "answers": stored}}


# ── the stronger-model comparison: plan, cost cap and protected execution ────────────────────────────────────────────

def messages(jd: str) -> list[dict]:
    return ev.messages_for(ARM_PROMPT, jd)


def worst_case_per_call(msgs: list[dict]) -> float:
    est_in = math.ceil(sum(len(m["content"]) for m in msgs) / 2)       # the stored plan's convention (overstates tokens)
    return round(est_in * PRICE_IN + ev.MAX_TOKENS * PRICE_OUT, 6)


def plan(jd: str, cap: float = CAP_USD) -> dict:
    per = worst_case_per_call(messages(jd))
    calls = [{"arm": ARM, "call": n, "worst_case_usd": per} for n in range(1, CALLS + 1)]
    total = round(sum(c["worst_case_usd"] for c in calls), 6)
    if total > cap:
        raise ComparisonError(f"worst-case total {total} exceeds the cap {cap}")
    return {"model": MODEL, "calls": calls, "worst_case_total_usd": total, "cap_usd": cap, "temperature": ev.TEMPERATURE,
            "max_tokens": ev.MAX_TOKENS, "response_format": "json_object", "timeout_s": ev.TIMEOUT_S, "retries": 0,
            "price_in_usd_per_token": PRICE_IN, "price_out_usd_per_token": PRICE_OUT, "pricing_status": PRICING_STATUS}


def confirm_pricing_or_refuse() -> None:
    """The paid path refuses to start until the owner records the verified snapshot and prices in this file's constants."""
    if not PRICING_VERIFIED:
        raise ComparisonError("pricing and availability are not verified; confirm them on the official pages and record them first")


def openai_call_model(messages_: list[dict]) -> dict:
    """The only network function here (--execute). The client never retries. The requested and the returned model must match."""
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=ev.TIMEOUT_S)
    r = client.chat.completions.create(model=MODEL, messages=messages_, temperature=ev.TEMPERATURE, max_tokens=ev.MAX_TOKENS,
                                       response_format={"type": "json_object"})
    ch = r.choices[0]
    return {"raw": ch.message.content or "", "finish_reason": ch.finish_reason, "model": r.model,
            "usage": {"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens}}


def run(out: pathlib.Path, jd: str, labels: dict, api_call, cap: float = CAP_USD) -> dict:
    p = plan(jd, cap)
    msgs = messages(jd)
    ev.prepare_output_dir(out)
    input_hashes = {"system_sha256": sha256_bytes(msgs[0]["content"].encode("utf-8")),
                    "user_sha256": sha256_bytes(msgs[1]["content"].encode("utf-8"))}
    manifest = {"arm": ARM, "model": MODEL, "pricing_status": PRICING_STATUS, "labels_sha256": sha256_bytes(ev.LABELS.read_bytes()),
                "jd_sha256": labels["jd_sha256"], "prompt": {"file": ev.ARMS[ARM_PROMPT]["file"].name,
                                                             "sha256": ev.ARMS[ARM_PROMPT]["sha256"]},
                "input": input_hashes, "job_metadata": ev.JOB_METADATA, "context_sha256": ev.context_sha256(ev.JOB_METADATA),
                "disclosed_differences": DISCLOSED_DIFFERENCES, "policy": "no retries; the run stops at the first failed call",
                "plan": p, "started": time.time()}
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
            rec = {"arm": ARM, "model": MODEL, "call": c["call"], "input": input_hashes}
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
            except Exception as exc:                                 # recorded; never retried; the run stops here
                rec.update({"raw": "", "finish_reason": None, "model_returned": None, "usage": {}, "cost_usd": 0.0,
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
    rows = [r for r in records if not r.get("error")]
    errors = [r["error"] for r in records if r.get("error")]
    if not rows:
        return {"status": "unavailable", "successful_calls": 0, "calls_attempted": len(records), "errors": errors,
                "note": "no successful call: nothing is reported as passing"}
    f = [facts(r["raw"], jd, labels) for r in rows]
    return {"status": "scored", "successful_calls": len(rows), "calls_attempted": len(records), "errors": errors,
            "groups_reported_separately": True, "official_scores_of_stored_runs_unchanged": True,
            "summary": summarize(f), "by_category": {cat: {k: f"{sum(1 for x in f if x['checks'][k])}/{len(f)}" for k in ks}
                                                       for cat, ks in ev.CATEGORY_CHECKS.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan, reservation and pricing status only (default)")
    mode.add_argument("--execute", action="store_true", help="the paid calls (refused until pricing is verified)")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="a new or empty directory (never overwritten)")
    args = ap.parse_args(argv)
    jd, labels = ev.load_inputs()
    p = plan(jd)
    if not args.execute:
        print(json.dumps({"mode": "dry-run", "arm": ARM, "plan": p, "pricing_verified": PRICING_VERIFIED,
                          "disclosed_differences": DISCLOSED_DIFFERENCES, "labels_sha256": sha256_bytes(ev.LABELS.read_bytes()),
                          "prompt_sha256": ev.ARMS[ARM_PROMPT]["sha256"],
                          "user_message_sha256": sha256_bytes(ev.user_message(jd).encode("utf-8"))}, ensure_ascii=False, indent=1))
        return 0
    confirm_pricing_or_refuse()
    if args.out is None or not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("--execute needs --out (a new directory) and OPENAI_API_KEY")
    res = run(args.out, jd, labels, openai_call_model)
    records = [json.loads(x) for x in (args.out / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = report(records, jd, labels)
    (args.out / "scored.json").write_text(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"run": res, "report": rep}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
