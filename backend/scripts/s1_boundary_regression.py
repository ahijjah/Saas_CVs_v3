"""
S1 s1-2 boundary regression — labelled SYNTHETIC cases against the S1 classifier.

Read-only evaluation harness. It changes no S1 behaviour, reads/writes no
database and uses only the synthetic fixture (no candidate, CV, application or
personal data). Each case is one job with one enumerated experience criterion,
classified through the real services.s1_requirements.classifier.classify_job
(prompt, validator, repair and assembly unchanged; cache disabled).

Modes
  dry-run  (default) build every request and print the token/cost budget; no client, no API call
  oracle   offline self-check: a fake client returns each case's labelled reference answer;
           every case must then score 100% (proves the labels match the deterministic pipeline)
  real     the real model (needs OPENAI_API_KEY / app config); --runs repetitions per case

Scoring separates three error classes per run:
  technical   failed_technical (API/timeout/truncation/budget)  -> never scored semantically
  validation  failed_validation after the single repair            -> never scored semantically
  semantic    an ok run whose labels differ from the expected ones (repaired runs are scored too)

Output: <out>/results.json (meta, summary, per-run records incl. raw model content) and <out>/report.md.

Usage (later, on the server):
  python scripts/s1_boundary_regression.py --out /tmp/s1_boundary                       # dry-run budget
  python scripts/s1_boundary_regression.py --out /tmp/s1_boundary --mode oracle         # offline check
  python scripts/s1_boundary_regression.py --out /tmp/s1_boundary --mode real --runs 3  # real model
  options: --model gpt-4o-mini --temperature 0 --cases A1,B1 --price-in 0.15 --price-out 0.60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.s0_experience import llm_call  # noqa: E402
from services.s1_requirements import classifier as clf  # noqa: E402
from services.s1_requirements import schema as sc  # noqa: E402
from services.s1_requirements.criteria import enumerate_experience_criteria  # noqa: E402
from services.s1_requirements.jd_text import JDText, normalize  # noqa: E402
from services.s1_requirements.validator import implied_policy  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "s1_eval_fixtures" / "s1_boundary_cases.json"
PRICE_IN, PRICE_OUT = 0.15, 0.60          # gpt-4o-mini USD per 1M tokens (ASSUMPTION; override with flags)
FIELDS = ("policy", "types", "provenance", "mapping", "setting", "duration", "ambiguity", "status", "spans",
          "anchor", "reasons")
STABILITY_FIELDS = ("policy", "types", "mapping_offered", "duration", "ambiguity")


# ── fixture ──────────────────────────────────────────────────────────────────

def load_cases(path: Path = FIXTURE, only: set[str] | None = None) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if not only or c["id"] in only]


def case_job_id(case: dict) -> str:
    return f"S1-BOUNDARY-{case['id']}"


def case_jd(case: dict) -> str:
    return "\n".join(case["jd_lines"])


def case_analysis(case: dict) -> dict:
    return {"experience": dict(case["analysis"])}


def case_criterion(case: dict):
    (c,) = enumerate_experience_criteria(case_job_id(case), case_analysis(case))
    return c


def duration_ids(case: dict) -> dict[str, str]:
    """candidate id -> candidate text for the case's JD."""
    return {did: m.text for did, _, m in JDText(case_jd(case)).durations()}


def oracle_response(case: dict) -> str:
    """The labelled reference answer as the model would return it."""
    o = dict(case["oracle"])
    if o.get("duration") is not None:
        by_text = {t: d for d, t in reversed(list(duration_ids(case).items()))}
        o["duration"] = by_text[o["duration"]]
    o["criterion_id"] = case_criterion(case).criterion_id
    return json.dumps({"criteria": [o]}, ensure_ascii=False)


# ── clients ──────────────────────────────────────────────────────────────────

class ScriptedClient:
    """Offline client: returns queued (content, finish_reason) items; an Exception item is raised."""

    def __init__(self, *items):
        self.items, self.requests = list(items), []

        async def create(**kw):
            self.requests.append(kw)
            item = self.items.pop(0)
            if isinstance(item, Exception):
                raise item
            content, finish = item if isinstance(item, tuple) else (item, "stop")
            usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),
                                                            finish_reason=finish)], usage=usage)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class Capture:
    """Pass-through wrapper: request/response untouched; copies the raw content of every call."""

    def __init__(self, inner, sink: list):
        async def create(**kw):
            r = await inner.chat.completions.create(**kw)
            ch = r.choices[0]
            sink.append({"call": "repair" if any(m.get("role") == "assistant" for m in kw["messages"]) else "main",
                         "finish_reason": getattr(ch, "finish_reason", None), "content": ch.message.content})
            return r
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


# ── observation + scoring ────────────────────────────────────────────────────

def _first_item(content) -> dict | None:
    try:
        it = json.loads(content)["criteria"][0]
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return it if isinstance(it, dict) else None


def _types(item: dict) -> list:
    return [t.get("type") for t in item.get("targets") or [] if isinstance(t, dict)]


def call_diagnostics(case: dict, raw: list, merge: dict | None = None) -> dict:
    """Diagnostics from the raw main/repair answers (before validation) and the repair merge.

    s1-4: the model's policy is never used; ``model_policy_mismatch_main`` only counts answers that still
    emit a policy disagreeing with their own target types. ``repair_attempted_type_change`` counts repairs
    whose raw answer changed target types (the scoped merge keeps error-free types);
    ``repair_discarded_changes`` counts criteria where the merge discarded out-of-scope repair edits."""
    main = next((_first_item(x["content"]) for x in raw if x["call"] == "main"), None)
    rep = next((_first_item(x["content"]) for x in raw if x["call"] == "repair"), None)
    d = {"model_policy_mismatch_main": False, "repair_attempted_type_change": False,
         "repair_discarded_changes": bool(merge and merge.get("discarded_changes")),
         "equivalent_rejected_by_alignment": False}
    if main is not None and isinstance(main.get("policy"), str):
        imp = implied_policy([t for t in _types(main) if t in ("role", "function")])
        d["model_policy_mismatch_main"] = imp is not None and main["policy"] != imp
    if main is not None and rep is not None:
        d["repair_attempted_type_change"] = _types(main) != _types(rep)
    return d


def observe(case: dict, out, raw: list = ()) -> dict:
    art = out.artifacts[0]
    outcome = {sc.STATUS_FAILED_TECHNICAL: "technical",
               sc.STATUS_FAILED_VALIDATION: "validation"}.get(art.spec_status, "ok")
    ai = art.audit.get("ai") or {}
    log = out.meta.get("call_log") or []
    obs = {
        "outcome": outcome, "status": art.spec_status, "reasons": [r.code for r in art.reasons],
        "repair_used": bool(out.meta.get("repair_used")), "validation": out.validation,
        "calls": [{k: c.get(k) for k in ("call", "finish_reason", "prompt_tokens", "completion_tokens")}
                  for c in log],
        "error": out.meta.get("error"),
        "diagnostics": {**call_diagnostics(case, list(raw), out.meta.get("repair_merge")),
                        # s1-5: the main answer's equivalent claim failed the alignment coverage contract
                        "equivalent_rejected_by_alignment": any(
                            " alignment" in e for e in (out.validation or {}).get("errors", []))},
        "repair_merge": out.meta.get("repair_merge"),
    }
    if outcome != "ok":
        return obs
    obs.update({
        "policy": art.policy,
        "targets": [{"id": t.target_id, "text": t.text, "type": t.type, "provenance": t.provenance}
                    for t in art.targets],
        "mapping_offered": sorted(m["target_id"] for m in art.audit.get("target_mappings", [])),
        "mappings": art.audit.get("target_mappings", []),
        "setting": art.setting.text if art.setting else None,
        "duration": duration_ids(case).get(ai.get("duration")) if ai.get("duration") else None,
        # effective ambiguity: AI-reported codes plus derived ones (relevance_basis unspecified)
        "ambiguity": sorted({r.code for r in art.reasons if r.kind == sc.KIND_AMBIGUITY}),
        "relevance_basis": ai.get("relevance_basis"),
        "restrictions": art.audit.get("restrictions", []),
        "policy_derivation": art.audit.get("policy_derivation"),
        "spans": [{"line": s.line, "text": s.text} for s in art.requirement_spans],
        "anchor": art.audit.get("requirement_anchor"),
        "note": ai.get("note"),
        "matches": art.audit.get("target_matches", {}),
    })
    return obs


def _eq(expected, actual) -> bool:
    if isinstance(expected, dict) and "one_of" in expected:
        return any(_eq(e, actual) for e in expected["one_of"])
    if isinstance(expected, list) and isinstance(actual, list):
        return sorted(expected) == sorted(actual)
    if isinstance(expected, str) and isinstance(actual, str):
        return normalize(expected) == normalize(actual)
    return expected == actual


def _same_target(exp_text: str, act_text: str) -> bool:
    a, b = normalize(exp_text), normalize(act_text)
    return a == b or a in b or b in a


def match_targets(expected: list[dict], actual: list[dict]) -> list[tuple[dict, dict | None]]:
    pairs, free = [], list(actual)
    for e in expected:
        hit = next((a for a in free if _same_target(e["text"], a["text"])), None)
        if hit is not None:
            free.remove(hit)
        pairs.append((e, hit))
    return pairs


def field_checks(exp: dict, obs: dict) -> dict[str, bool]:
    pairs = match_targets(exp["targets"], obs["targets"])
    same_count = len(exp["targets"]) == len(obs["targets"])
    joined = [normalize(s["text"]) for s in obs["spans"]]
    lines = {s["line"] for s in obs["spans"]}
    checks = {
        "policy": _eq(exp["policy"], obs["policy"]),
        "types": same_count and all(a is not None and a["type"] == e["type"] for e, a in pairs),
        "provenance": same_count and all(a is not None and a["provenance"] == e["provenance"] for e, a in pairs),
        "mapping": all(a is not None and (a["provenance"] == sc.PROV_JD_ASSERTED) == e["mapped"]
                       for e, a in pairs if "mapped" in e),
        "setting": _eq(exp["setting"], obs["setting"]),
        "duration": _eq(exp["duration"], obs["duration"]),
        "ambiguity": _eq(exp["ambiguity"], obs["ambiguity"]),
        "status": _eq(exp["status"], obs["status"]),
        "spans": all(any(normalize(t) in j for j in joined) for t in exp.get("span_includes", []))
        and not (lines & set(exp.get("span_forbidden_lines", []))),
        "anchor": _eq(exp["anchor"], obs["anchor"]) if "anchor" in exp else True,
        # business reasons that must be present (e.g. compound_requirement); other reasons are not scored here
        "reasons": all(code in obs["reasons"] for code in exp.get("reasons_include", [])),
    }
    return checks


def score(case: dict, obs: dict) -> dict:
    """Best-matching expectation (base or a listed alternative) for an ok run."""
    base = case["expected"]
    variants = [base] + [{**base, **alt} for alt in base.get("alternatives", [])]
    best = max((field_checks(v, obs) for v in variants), key=lambda ch: sum(ch.values()))
    return {"checks": best, "pass": all(best.values())}


# ── run ──────────────────────────────────────────────────────────────────────

def estimate(cases: list[dict]) -> dict:
    per_case = {}
    for case in cases:
        req = clf.build_request(JDText(case_jd(case)), [case_criterion(case)])
        msgs = [{"role": "system", "content": clf.S1_SYSTEM_PROMPT}, {"role": "user", "content": req.user_message}]
        per_case[case["id"]] = llm_call.request_token_upper_bound(msgs)
    return {"input_token_upper_bound_per_case": per_case,
            "input_token_upper_bound_total": sum(per_case.values()),
            "max_output_tokens_per_call": sc.S1_MAX_TOKENS}


async def run_case(case: dict, client, model: str) -> tuple[dict, list]:
    sink: list = []
    out = await clf.classify_job(case_job_id(case), case_jd(case), case_analysis(case),
                                 client=Capture(client, sink), model=model, cache=None)
    return observe(case, out, sink), sink


async def run_all(cases: list[dict], *, runs: int, client_for, model: str) -> list[dict]:
    records = []
    for run in range(1, runs + 1):
        for case in cases:
            obs, raw = await run_case(case, client_for(case), model)
            rec = {"case": case["id"], "family": case["family"], "run": run, **obs, "raw": raw}
            if obs["outcome"] == "ok":
                rec.update(score(case, obs))
            records.append(rec)
    return records


# ── summary ──────────────────────────────────────────────────────────────────

def _ratio(n, d):
    return None if not d else round(n / d, 4)


def summarize(records: list[dict], cases: list[dict], *, price_in: float = PRICE_IN,
              price_out: float = PRICE_OUT) -> dict:
    by_case = {c["id"]: c for c in cases}
    ok = [r for r in records if r["outcome"] == "ok"]
    outcomes = Counter(r["outcome"] for r in records)
    field_acc = {f: _ratio(sum(r["checks"][f] for r in ok), len(ok)) for f in FIELDS}

    tp = fp = fn = tn = 0
    sensitive: list[dict] = []
    false_pos, false_neg = [], []
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in ok:
        exp = by_case[r["case"]]["expected"]
        for e, a in match_targets(exp["targets"], r["targets"]):
            confusion[e["type"]][a["type"] if a else "missing"] += 1
            if "mapped" not in e or a is None:
                continue
            got = a["provenance"] == sc.PROV_JD_ASSERTED
            if e.get("mapping_policy_sensitive"):          # e.g. abbreviation punctuation: excluded from the gate
                sensitive.append({"case": r["case"], "run": r["run"], "target": e["text"], "jd_asserted": got})
                continue
            mapped = next((m["mapped_text"] for m in r["mappings"] if m["target_id"] == a["id"]), None)
            if got and e["mapped"]:
                tp += 1
            elif got:
                fp += 1
                false_pos.append({"case": r["case"], "run": r["run"], "target": e["text"], "mapped_text": mapped})
            elif e["mapped"]:
                fn += 1
                false_neg.append({"case": r["case"], "run": r["run"], "target": e["text"],
                                  "provenance": a["provenance"]})
            else:
                tn += 1

    stability, unstable = {}, []
    per_case_ok = defaultdict(list)
    for r in ok:
        per_case_ok[r["case"]].append(r)
    multi = {cid: rs for cid, rs in per_case_ok.items() if len(rs) >= 2}

    def sig(r, f):
        if f == "types":
            return tuple(sorted((normalize(t["text"]), t["type"]) for t in r["targets"]))
        v = r[f]
        return tuple(v) if isinstance(v, list) else v
    for f in STABILITY_FIELDS:
        stable = [cid for cid, rs in multi.items() if len({sig(r, f) for r in rs}) == 1]
        stability[f] = _ratio(len(stable), len(multi))
    for cid, rs in sorted(multi.items()):
        diff = [f for f in STABILITY_FIELDS if len({sig(r, f) for r in rs}) > 1]
        if diff:
            unstable.append({"case": cid, "fields": diff})
    stability["all_fields"] = _ratio(len(multi) - len(unstable), len(multi))

    pt = sum(c["prompt_tokens"] or 0 for r in records for c in r["calls"])
    ct = sum(c["completion_tokens"] or 0 for r in records for c in r["calls"])
    fam = defaultdict(lambda: [0, 0])
    for r in records:
        fam[r["family"]][1] += 1
        fam[r["family"]][0] += bool(r.get("pass"))
    return {
        "runs_total": len(records), "cases": len({r["case"] for r in records}),
        "outcomes": {"ok": outcomes["ok"], "failed_validation": outcomes["validation"],
                     "failed_technical": outcomes["technical"]},
        "repaired_ok_runs": sum(1 for r in ok if r["repair_used"]),
        "repair_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "repair"),
        "main_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "main"),
        "semantic_error_runs": sum(1 for r in ok if not r["pass"]),
        "pass_runs": sum(1 for r in ok if r["pass"]),
        "pass_rate_all_runs": _ratio(sum(1 for r in ok if r["pass"]), len(records)),
        "field_accuracy_ok_runs": field_acc,
        "policy_accuracy": field_acc["policy"], "status_accuracy": field_acc["status"],
        "ambiguity_accuracy": field_acc["ambiguity"],
        "jd_asserted": {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": _ratio(tp, tp + fp),
                        "recall": _ratio(tp, tp + fn), "false_positives": false_pos,
                        "false_negatives": false_neg, "policy_sensitive_excluded": sensitive},
        "type_confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        "stability": stability, "unstable_cases": unstable,
        "by_family": {f: {"pass": p, "runs": n} for f, (p, n) in sorted(fam.items())},
        "diagnostics": {k: sum(1 for r in records if r["diagnostics"][k])
                        for k in ("model_policy_mismatch_main", "repair_attempted_type_change",
                                  "repair_discarded_changes", "equivalent_rejected_by_alignment")},
        "match_counts": dict(Counter(m for r in ok for m in r["matches"].values())),
        "semantic_failures": [{"case": r["case"], "run": r["run"],
                               "failed_fields": [f for f, v in r["checks"].items() if not v]}
                              for r in ok if not r["pass"]],
        "validation_failures": [{"case": r["case"], "run": r["run"], "errors": r["validation"]}
                                for r in records if r["outcome"] == "validation"],
        "technical_failures": [{"case": r["case"], "run": r["run"], "status_reasons": r["reasons"],
                                "error": r["error"]} for r in records if r["outcome"] == "technical"],
        "tokens": {"prompt": pt, "completion": ct},
        "estimated_cost_usd": round((pt * price_in + ct * price_out) / 1e6, 5),
    }


def render_markdown(meta: dict, s: dict) -> str:
    j = s["jd_asserted"]
    lines = [
        "# S1 boundary regression", "",
        f"- prompt {meta['prompt_version']} ({meta['prompt_fingerprint']}), S1 {meta['s1_version']}, "
        f"model {meta['model']}, temperature {meta['temperature']}, mode {meta['mode']}, runs {meta['runs']}",
        f"- fixture {meta['fixture_version']}: {s['cases']} cases, {s['runs_total']} runs", "",
        "## Outcomes",
        f"- ok {s['outcomes']['ok']} (repaired {s['repaired_ok_runs']}), failed_validation "
        f"{s['outcomes']['failed_validation']}, failed_technical {s['outcomes']['failed_technical']}",
        f"- calls: main {s['main_calls']}, repair {s['repair_calls']}",
        f"- pass {s['pass_runs']} / {s['runs_total']}; semantic errors {s['semantic_error_runs']}", "",
        "## Accuracy (ok runs)",
        "| field | accuracy |", "|---|---|",
        *[f"| {f} | {v} |" for f, v in s["field_accuracy_ok_runs"].items()], "",
        "## jd_asserted",
        f"- TP {j['tp']}, FP {j['fp']}, FN {j['fn']}, TN {j['tn']}; precision {j['precision']}, recall {j['recall']}",
        *[f"- FALSE POSITIVE {x['case']} run {x['run']}: {x['target']!r} -> {x['mapped_text']!r}"
          for x in j["false_positives"]],
        f"- policy-sensitive mappings excluded from the gate: {len(j['policy_sensitive_excluded'])} "
        f"(jd_asserted {sum(x['jd_asserted'] for x in j['policy_sensitive_excluded'])})", "",
        "## Role/function confusion (expected -> actual)",
        *[f"- {k}: {v}" for k, v in s["type_confusion"].items()], "",
        "## Stability (cases with >= 2 ok runs)",
        *[f"- {k}: {v}" for k, v in s["stability"].items()],
        *[f"- unstable {u['case']}: {', '.join(u['fields'])}" for u in s["unstable_cases"]], "",
        "## Failures",
        *[f"- semantic {x['case']} run {x['run']}: {', '.join(x['failed_fields'])}" for x in s["semantic_failures"]],
        *[f"- validation {x['case']} run {x['run']}" for x in s["validation_failures"]],
        *[f"- technical {x['case']} run {x['run']}: {x['status_reasons']}" for x in s["technical_failures"]], "",
        "## Diagnostics",
        *[f"- {k}: {v}" for k, v in s["diagnostics"].items()],
        f"- match counts (ok runs): {s['match_counts']}", "",
        "## By family",
        *[f"- {f}: {v['pass']}/{v['runs']}" for f, v in s["by_family"].items()], "",
        f"Tokens: prompt {s['tokens']['prompt']}, completion {s['tokens']['completion']}; "
        f"estimated cost USD {s['estimated_cost_usd']}", "",
    ]
    return "\n".join(lines)


def build_meta(args, fixture_version: str) -> dict:
    return {"prompt_version": sc.S1_PROMPT_VERSION, "prompt_fingerprint": clf.prompt_fingerprint(),
            "s1_version": sc.S1_VERSION, "model": args.model, "temperature": args.temperature, "mode": args.mode,
            "runs": args.runs, "fixture_version": fixture_version,
            "created_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("dry-run", "oracle", "real"), default="dry-run")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default=sc.S1_MODEL)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--fixture", default=str(FIXTURE))
    ap.add_argument("--price-in", type=float, default=PRICE_IN)
    ap.add_argument("--price-out", type=float, default=PRICE_OUT)
    args = ap.parse_args(argv)

    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    cases = load_cases(Path(args.fixture), {c for c in args.cases.split(",") if c} or None)
    est = estimate(cases)
    calls_bound = len(cases) * args.runs * 2
    print(json.dumps({"cases": len(cases), "runs": args.runs, "max_calls_incl_repairs": calls_bound,
                      "input_token_upper_bound_one_run": est["input_token_upper_bound_total"]}, indent=1))
    if args.mode == "dry-run":
        print("DRY RUN: no OpenAI calls made.")
        return 0

    if args.mode == "oracle":
        def client_for(case):
            return ScriptedClient(oracle_response(case))
    else:
        real = llm_call.create_client()

        def client_for(case):
            return real
    previous = clf.S1_TEMPERATURE
    clf.S1_TEMPERATURE = args.temperature        # harness-side override, recorded in meta (default 0)
    loop = asyncio.new_event_loop()              # private loop: never clears the caller's event loop
    try:
        records = loop.run_until_complete(run_all(cases, runs=args.runs, client_for=client_for, model=args.model))
    finally:
        loop.close()
        clf.S1_TEMPERATURE = previous
    meta = build_meta(args, fixture.get("fixture_version", ""))
    meta["estimate"] = est
    summary = summarize(records, cases, price_in=args.price_in, price_out=args.price_out)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "records": records},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(meta, summary), encoding="utf-8")
    print(render_markdown(meta, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
