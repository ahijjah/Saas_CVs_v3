"""
S1 s1-6 CONTEXT evaluation (Architecture C P4b) — labelled SYNTHETIC cases against the real S1 classifier.

Read-only harness: no database, no production wiring, no candidate data. Every case is ONE job; all of its
enumerated experience criteria are classified by services.s1_requirements.classifier.classify_job (prompt,
validator, scoped repair and assembly unchanged; cache disabled) and scored per criterion.

Modes
  dry-run (default)  build every request, print the token budget and run the offline independence probe; no
                     client is ever created
  oracle             a scripted client returns each case's labelled reference answer: every case must score
                     100% and every offline hard gate must pass (proves the labels match the deterministic code)
  real               the real model. Refused unless --confirm-real; the held-out fixture additionally needs
                     --allow-heldout. Exact pins: S1 prompt version + fingerprint, S1 version, model, temperature,
                     max tokens, fixture SHA256; client max_retries=0 (one HTTP call per intended call), no
                     fallback model. Only S1's own single scoped repair call may follow a main call. Technical
                     failures are recorded as such, never as an answer. NOT run in P4a.

Scoring (per criterion): settings as a SET (order irrelevant: several contexts are AND), compared after
canon_context (comparison form, leading in/on/within/at/the/a/an/في/ضمن/لدى/داخل dropped; nothing else), with
one_of alternatives for Arabic attached letters; plus status and required reason codes.

Gates (evaluate_gates)
  hard (must be 0)    setting outside the requirement spans; ungrounded setting; failed S1 artefact observed as
                      "no context" (failed runs record settings None, never []); an S2 view produced for an
                      unresolved context; independence probe failure; false agreed / false agreed_none of the
                      joint QC x S1 replay (reported "not_available" until the P4c agreement exists)
  accuracy            settings-set accuracy (main >= .85, held-out >= .80); false-none on positive context
                      criteria <= .05; false context on negative criteria <= .10; >= 90% of cases with identical
                      canonical settings in every run (stability, needs >= 2 runs); technical + validation
                      failures <= 5%
  informational       status / reason accuracy, joint agreement (soft target .70, P4c)

TUNING RULES: tune on the MAIN fixture only. The HELD-OUT fixture is SHA-pinned, run at most once per prompt
version, and is permanently exposed after its first real run (it can never again be unseen validation). No
fixture is edited after real output has been seen. Old s1-5.2 results are not evidence for s1-6.0.

Usage (later, after explicit approval):
  python scripts/s1_context_eval.py --out /tmp/s1ctx                                   # dry-run
  python scripts/s1_context_eval.py --out /tmp/s1ctx --mode oracle --runs 1             # offline self-check
  python scripts/s1_context_eval.py --out /tmp/s1ctx --mode real --runs 5 --confirm-real
  python scripts/s1_context_eval.py --out /tmp/s1ho --fixture heldout --mode real --runs 5 --confirm-real \\
      --allow-heldout
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.s0_experience import llm_call  # noqa: E402
from services.s1_requirements import assemble as asm  # noqa: E402
from services.s1_requirements import classifier as clf  # noqa: E402
from services.s1_requirements import schema as sc  # noqa: E402
from services.s1_requirements.criteria import enumerate_experience_criteria  # noqa: E402
from services.s1_requirements.jd_text import JDText, normalize  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "s1_eval_fixtures"
FIXTURES = {"main": FIXTURE_DIR / "s1_ctx_main_cases.json", "heldout": FIXTURE_DIR / "s1_ctx_heldout_cases.json"}
# SHA256 pins: finalised before ANY real call; a changed file is refused in real mode
FIXTURE_SHA256 = {
    "main": "c72803fbdddb45bd1c6ea45a4e8c949fdd9ea5742e20d0c511bdfd919954fc4b",
    "heldout": "bb949d061ddcb4a5d0d6a0d10b6af6794d4279a4e3f28d0d44fc561d55b94165",
}
PINNED = {"prompt_version": "s1-6.0", "prompt_fingerprint": "af9f496563a4", "s1_version": "1.5.0",
          "model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 4000}
CLIENT_MAX_RETRIES = 0
CLIENT_TIMEOUT_S = 120.0
DEFAULT_RUNS = 5
GATES = {
    "main": {"settings_accuracy": 0.85, "false_none_rate": 0.05, "false_context_rate": 0.10},
    "heldout": {"settings_accuracy": 0.80, "false_none_rate": 0.05, "false_context_rate": 0.10},
}
STABILITY_MIN = 0.90
FAILURE_RATE_MAX = 0.05
JOINT_AGREEMENT_SOFT = 0.70
LEADING_WORDS = frozenset({"in", "on", "within", "at", "the", "a", "an", "في", "ضمن", "لدى", "داخل"})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture(name_or_path: str) -> tuple[dict, Path]:
    path = FIXTURES.get(name_or_path) or Path(name_or_path)
    return json.loads(path.read_text(encoding="utf-8")), path


def case_job_id(case: dict) -> str:
    return f"S1-CTX-{case['id']}"


def case_jd(case: dict) -> str:
    return "\n".join(case["jd_lines"])


def case_analysis(case: dict) -> dict:
    return {"experience": dict(case["analysis"])}


def case_criteria(case: dict):
    return enumerate_experience_criteria(case_job_id(case), case_analysis(case))


def canon_context(text: str) -> str:
    """Comparison form with leading function words dropped (scoring leniency only; never used by S1)."""
    toks = normalize(text).strip(" .,;:").split()
    while toks and toks[0] in LEADING_WORDS:
        toks = toks[1:]
    return " ".join(toks)


def _elem_ok(exp, act: str) -> bool:
    if isinstance(exp, dict):
        return any(_elem_ok(e, act) for e in exp["one_of"])
    return canon_context(exp) == canon_context(act)


def settings_match(expected: list, actual: list | None) -> bool:
    """SET comparison: same size and a one-to-one assignment of expected labels to observed contexts."""
    if actual is None or len(expected) != len(actual):
        return False

    def assign(i, free):
        return i == len(expected) or any(_elem_ok(expected[i], a) and assign(i + 1, free[:k] + free[k + 1:])
                                         for k, a in enumerate(free))
    return assign(0, list(actual))


def oracle_response(case: dict) -> str:
    durs = {m.text: did for did, _, m in reversed(JDText(case_jd(case)).durations())}
    items = []
    for c, spec in zip(case_criteria(case), case["criteria"]):
        o = copy.deepcopy(spec["oracle"])
        if o.get("duration") is not None:
            o["duration"] = durs[o["duration"]]
        o["criterion_id"] = c.criterion_id
        items.append(o)
    return json.dumps({"criteria": items}, ensure_ascii=False)


# ── clients ──────────────────────────────────────────────────────────────────

class ScriptedClient:
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
    """Pass-through: records every main / repair call (model, temperature, raw content) untouched."""

    def __init__(self, inner, sink: list):
        async def create(**kw):
            r = await inner.chat.completions.create(**kw)
            ch = r.choices[0]
            sink.append({"call": "repair" if any(m.get("role") == "assistant" for m in kw["messages"]) else "main",
                         "model": kw.get("model"), "temperature": kw.get("temperature"),
                         "max_tokens": kw.get("max_tokens"), "finish_reason": getattr(ch, "finish_reason", None),
                         "content": ch.message.content})
            return r
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def make_real_client():
    """The ONE real client: zero SDK retries, pinned timeout; lazy import (never in offline modes)."""
    from openai import AsyncOpenAI
    from config import get_settings
    return AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=CLIENT_MAX_RETRIES,
                       timeout=CLIENT_TIMEOUT_S)


# ── observation ──────────────────────────────────────────────────────────────

def _view_code(art) -> str:
    try:
        asm.s2_views(art, require_resolved=False)
    except asm.S1ViewError as e:
        return e.code or "blocked"
    return "VIEW"


def observe_criterion(art, jd: JDText) -> dict:
    failed = art.spec_status in (sc.STATUS_FAILED_TECHNICAL, sc.STATUS_FAILED_VALIDATION, sc.STATUS_PENDING)
    obs = {"criterion_id": art.criterion_id, "status": art.spec_status,
           "outcome": {sc.STATUS_FAILED_TECHNICAL: "technical",
                       sc.STATUS_FAILED_VALIDATION: "validation"}.get(art.spec_status, "ok"),
           "reasons": [r.code for r in art.reasons], "policy": art.policy,
           # a failed artefact has NO reading: None, never [] ("no context")
           "settings": None if failed else [s.text for s in art.settings],
           "setting_spans": [s.jd_span.to_dict() for s in art.settings],
           "requirement_spans": [s.to_dict() for s in art.requirement_spans],
           "restrictions": art.audit.get("restrictions", []), "view": _view_code(art)}
    obs["outside_requirement"] = sum(not any(s.jd_span.within(r) for r in art.requirement_spans) for s in art.settings)
    obs["ungrounded"] = sum(jd.span_on_line(s.jd_span.line, s.text, boundaries=True) is None for s in art.settings)
    obs["failed_as_none"] = bool(failed and obs["settings"] is not None)
    return obs


def observe(case: dict, out, raw: list) -> dict:
    jd = JDText(case_jd(case))
    return {"job_outcome": out.status, "status_reason": out.status_reason,
            "criteria": [observe_criterion(a, jd) for a in out.artifacts],
            "calls": [{k: c.get(k) for k in ("call", "model", "temperature", "max_tokens", "finish_reason")}
                      for c in raw],
            "raw": raw, "validation": out.validation, "repair_used": bool(out.meta.get("repair_used")),
            "repair_merge": out.meta.get("repair_merge"), "error": out.meta.get("error")}


def score(case: dict, obs: dict) -> dict:
    per = []
    for spec, o in zip(case["criteria"], obs["criteria"]):
        e = spec["expected"]
        ok_run = o["outcome"] == "ok"
        per.append({"settings": ok_run and settings_match(e["settings"], o["settings"]),
                    "status": ok_run and o["status"] == e["status"],
                    "reasons": ok_run and all(r in o["reasons"] for r in e.get("reasons_include", []))})
    return {"checks": per, "pass": len(per) == len(case["criteria"]) and all(all(p.values()) for p in per)}


# ── independence probe (offline; the model input never depends on the qualifying context) ─────────────

QC_PROBES = [
    {"state": "identified", "contexts": ["probe context"], "source": "analysis"},
    {"state": "none", "contexts": [], "source": "recruiter"},
    {"state": "uncertain", "contexts": ["x"], "source": "analysis"},
]


def independence_probe(cases: list[dict]) -> list[dict]:
    failures = []
    for case in cases:
        jd = JDText(case_jd(case))
        base_c = enumerate_experience_criteria(case_job_id(case), case_analysis(case))
        base = clf.build_request(jd, base_c)
        for qc in QC_PROBES:
            a = case_analysis(case)
            a["experience"]["qualifying_context"] = copy.deepcopy(qc)
            a["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": {"source": qc["source"]},
                                             "latest_run": {"status": "ok", "result": qc}}
            crits = enumerate_experience_criteria(case_job_id(case), a)
            req = clf.build_request(jd, crits)
            if (crits != base_c or req.user_message != base.user_message
                    or clf.s1_cache_key(req) != clf.s1_cache_key(base)):
                failures.append({"case": case["id"], "probe": qc})
    return failures


# ── run ──────────────────────────────────────────────────────────────────────

async def run_case(case: dict, client) -> dict:
    sink: list = []
    out = await clf.classify_job(case_job_id(case), case_jd(case), case_analysis(case),
                                 client=Capture(client, sink), model=PINNED["model"], cache=None)
    return observe(case, out, sink)


async def run_all(cases: list[dict], *, runs: int, client_for) -> list[dict]:
    records = []
    for run in range(1, runs + 1):
        for case in cases:
            obs = await run_case(case, client_for(case))
            records.append({"case": case["id"], "family": case["family"], "lang": case["lang"], "run": run,
                            **obs, **score(case, obs)})
    return records


def _ratio(n, d):
    return None if not d else round(n / d, 4)


def summarize(records: list[dict], cases: list[dict], *, independence_failures: int = 0) -> dict:
    by_id = {c["id"]: c for c in cases}
    crit_runs = [(r, spec, o, ch) for r in records
                 for spec, o, ch in zip(by_id[r["case"]]["criteria"], r["criteria"], r["checks"])]
    ok = [(r, spec, o, ch) for r, spec, o, ch in crit_runs if o["outcome"] == "ok"]
    outcomes = Counter(o["outcome"] for _, _, o, _ in crit_runs)
    positive = [(o, spec) for _, spec, o, _ in ok if spec["expected"]["polarity"] == "positive"]
    negative = [(o, spec) for _, spec, o, _ in ok if spec["expected"]["polarity"] == "negative"]
    false_none = [o for o, _ in positive if not o["settings"]]
    false_ctx = [o for o, _ in negative if o["settings"]]

    sig = defaultdict(list)
    for r in records:
        sig[r["case"]].append(tuple(
            (o["outcome"], None if o["settings"] is None else tuple(sorted(canon_context(x) for x in o["settings"])))
            for o in r["criteria"]))
    multi = {k: v for k, v in sig.items() if len(v) >= 2}
    stable = [k for k, v in multi.items() if len(set(v)) == 1]
    fam = defaultdict(lambda: [0, 0])
    for r in records:
        fam[r["family"]][1] += 1
        fam[r["family"]][0] += bool(r["pass"])
    hard = {
        "setting_outside_requirement_spans": sum(o["outside_requirement"] for _, _, o, _ in crit_runs),
        "ungrounded_setting": sum(o["ungrounded"] for _, _, o, _ in crit_runs),
        "failed_artifact_treated_as_none": sum(o["failed_as_none"] for _, _, o, _ in crit_runs),
        "unconfirmed_context_yielding_s2_view": sum(o["view"] == "VIEW" for _, _, o, _ in crit_runs),
        "independence_failures": independence_failures,
        "false_agreed_none": "not_available",          # joint QC x S1 replay needs the P4c agreement
        "false_agreed": "not_available",
    }
    return {
        "runs_total": len(records), "cases": len({r["case"] for r in records}),
        "criterion_runs": len(crit_runs),
        "outcomes": {"ok": outcomes["ok"], "failed_validation": outcomes["validation"],
                     "failed_technical": outcomes["technical"]},
        "failure_rate": _ratio(outcomes["validation"] + outcomes["technical"], len(crit_runs)),
        "settings_accuracy": _ratio(sum(ch["settings"] for *_, ch in ok), len(ok)),
        "status_accuracy": _ratio(sum(ch["status"] for *_, ch in ok), len(ok)),
        "reason_accuracy": _ratio(sum(ch["reasons"] for *_, ch in ok), len(ok)),
        "false_none_rate": _ratio(len(false_none), len(positive)),
        "false_context_rate": _ratio(len(false_ctx), len(negative)),
        "stability": _ratio(len(stable), len(multi)),
        "unstable_cases": sorted(set(multi) - set(stable)),
        "pass_runs": sum(1 for r in records if r["pass"]),
        "hard": hard,
        "joint_agreement_rate": "not_available",
        "repair_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "repair"),
        "main_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "main"),
        "by_family": {f: {"pass": p, "runs": n} for f, (p, n) in sorted(fam.items())},
        "semantic_failures": [{"case": r["case"], "run": r["run"],
                               "criteria": [{"expected": spec["expected"]["settings"], "observed": o["settings"],
                                             "status": o["status"], "checks": ch}
                                            for spec, o, ch in zip(by_id[r["case"]]["criteria"], r["criteria"],
                                                                   r["checks"]) if not all(ch.values())]}
                              for r in records if not r["pass"]],
    }


def evaluate_gates(s: dict, split: str) -> dict:
    g = GATES[split]
    res = {f"hard:{k}": (v == 0 if v != "not_available" else None) for k, v in s["hard"].items()}
    res.update({
        "settings_accuracy": s["settings_accuracy"] is not None and s["settings_accuracy"] >= g["settings_accuracy"],
        "false_none_rate": s["false_none_rate"] is not None and s["false_none_rate"] <= g["false_none_rate"],
        "false_context_rate": s["false_context_rate"] is not None and s["false_context_rate"] <= g["false_context_rate"],
        "stability": None if s["stability"] is None else s["stability"] >= STABILITY_MIN,
        "failure_rate": s["failure_rate"] is not None and s["failure_rate"] <= FAILURE_RATE_MAX,
    })
    decided = [v for v in res.values() if v is not None]
    return {"split": split, "gates": res, "all_decided_pass": all(decided),
            "undecided": sorted(k for k, v in res.items() if v is None)}


def render_markdown(meta: dict, s: dict, gates: dict) -> str:
    lines = ["# S1 s1-6 context evaluation", "",
             f"- prompt {meta['prompt_version']} ({meta['prompt_fingerprint']}), S1 {meta['s1_version']}, model "
             f"{meta['model']}, temperature {meta['temperature']}, mode {meta['mode']}, runs {meta['runs']}",
             f"- fixture {meta['fixture']} {meta['fixture_version']} sha256 {meta['fixture_sha256']}", "",
             "## Gates", *[f"- {k}: {v}" for k, v in gates["gates"].items()],
             f"- all decided gates pass: {gates['all_decided_pass']} (undecided: {gates['undecided']})", "",
             "## Metrics",
             *[f"- {k}: {s[k]}" for k in ("settings_accuracy", "false_none_rate", "false_context_rate", "stability",
                                          "failure_rate", "status_accuracy", "reason_accuracy",
                                          "joint_agreement_rate")],
             f"- outcomes {s['outcomes']}; calls main {s['main_calls']}, repair {s['repair_calls']}", "",
             "## By family", *[f"- {f}: {v['pass']}/{v['runs']}" for f, v in s["by_family"].items()], "",
             "## Semantic failures",
             *[f"- {x['case']} run {x['run']}: {x['criteria']}" for x in s["semantic_failures"]], ""]
    return "\n".join(lines)


def check_pins(fixture_name: str, path: Path) -> list[str]:
    problems = []
    actual = {"prompt_version": sc.S1_PROMPT_VERSION, "prompt_fingerprint": clf.prompt_fingerprint(),
              "s1_version": sc.S1_VERSION, "model": sc.S1_MODEL, "temperature": clf.S1_TEMPERATURE,
              "max_tokens": sc.S1_MAX_TOKENS}
    problems += [f"{k}: pinned {PINNED[k]!r}, actual {actual[k]!r}" for k in PINNED if PINNED[k] != actual[k]]
    if fixture_name not in FIXTURE_SHA256:
        problems.append(f"fixture {fixture_name!r} is not a pinned fixture")
    elif sha256_file(path) != FIXTURE_SHA256[fixture_name]:
        problems.append(f"fixture {fixture_name} sha256 {sha256_file(path)} != pinned {FIXTURE_SHA256[fixture_name]}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", choices=tuple(FIXTURES), default="main")
    ap.add_argument("--mode", choices=("dry-run", "oracle", "real"), default="dry-run")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--confirm-real", action="store_true", help="required for --mode real (paid model calls)")
    ap.add_argument("--allow-heldout", action="store_true", help="required to run the held-out fixture for real")
    args = ap.parse_args(argv)

    fixture, path = load_fixture(args.fixture)
    only = {c for c in args.cases.split(",") if c}
    cases = [c for c in fixture["cases"] if not only or c["id"] in only]
    if args.mode == "real":
        if not args.confirm_real:
            print("REFUSED: --mode real makes real model calls; pass --confirm-real explicitly.")
            return 2
        if args.fixture == "heldout" and not args.allow_heldout:
            print("REFUSED: the held-out fixture is single-use; pass --allow-heldout explicitly.")
            return 2
        problems = check_pins(args.fixture, path)
        if problems:
            print("REFUSED: pins do not match:\n- " + "\n- ".join(problems))
            return 2
    budget = {c["id"]: llm_call.request_token_upper_bound(
        [{"role": "system", "content": clf.S1_SYSTEM_PROMPT},
         {"role": "user", "content": clf.build_request(JDText(case_jd(c)), case_criteria(c)).user_message}])
        for c in cases}
    ind = independence_probe(cases)
    print(json.dumps({"fixture": args.fixture, "cases": len(cases), "runs": args.runs,
                      "max_calls_incl_repairs": len(cases) * args.runs * 2,
                      "input_token_upper_bound_one_run": sum(budget.values()),
                      "independence_failures": len(ind)}, indent=1))
    if args.mode == "dry-run":
        print("DRY RUN: no model calls made.")
        return 0 if not ind else 1

    if args.mode == "oracle":
        def client_for(case):
            return ScriptedClient(oracle_response(case))
    else:
        real = make_real_client()

        def client_for(case):
            return real
    loop = asyncio.new_event_loop()
    try:
        records = loop.run_until_complete(run_all(cases, runs=args.runs, client_for=client_for))
    finally:
        loop.close()
    summary = summarize(records, cases, independence_failures=len(ind))
    gates = evaluate_gates(summary, args.fixture)
    meta = {"prompt_version": sc.S1_PROMPT_VERSION, "prompt_fingerprint": clf.prompt_fingerprint(),
            "s1_version": sc.S1_VERSION, "model": PINNED["model"], "temperature": clf.S1_TEMPERATURE,
            "max_tokens": sc.S1_MAX_TOKENS, "client_max_retries": CLIENT_MAX_RETRIES if args.mode == "real" else None,
            "mode": args.mode, "runs": args.runs, "fixture": args.fixture,
            "fixture_version": fixture.get("fixture_version"), "fixture_sha256": sha256_file(path),
            "created_at": datetime.now(timezone.utc).isoformat()}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "gates": gates,
                                                  "records": records}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    report = render_markdown(meta, summary, gates)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
