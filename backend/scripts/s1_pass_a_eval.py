"""
S1 two-pass PASS A evaluation (Step 2): the target pass ONLY (current prompt s1a-1.4, a CANDIDATE pending real
evaluation; s1a-1.1 and s1a-1.3 are the baselines it is compared with, s1a-1.2 was withdrawn, see
scripts/s1_eval_results/pass_a/PASS_A_EVAL_LOG.md), against the existing P4 MAIN context fixture
(scripts/s1_eval_fixtures/s1_ctx_main_cases.json, unchanged) and its labelled oracle answers.

Read-only harness: no database, no production wiring, no candidate data, no Pass B. Every case is ONE job; all of
its enumerated experience criteria go through services.s1_two_pass.pass_a_runner.run_pass_a_job (prompt,
validator, one-repair orchestration, F6 and assembly unchanged; cache disabled) and are scored per criterion.

Gold (derived from each criterion's labelled s1-6 oracle answer; no fixture field is added or changed):
  targets       hinted: (hint text, oracle type) for every hint; hint-less: the oracle role/function restrictions
  target_basis  hinted -> targets; role/function restriction -> targets; else vague -> unspecified;
                else a context restriction / setting -> setting_only; else total_experience
  policy        role/function types -> explicit_role / functional / mixed; setting_only -> sector; otherwise
                pure_duration
The Pass A ORACLE answer is the same labelled answer with every context element removed (settings, context
restrictions, ambiguous_context_scope, requirement spans that only carry a context sentence) and the gold
target_basis added; --mode oracle proves gold, oracle and deterministic code agree (every metric 1.0).

Metrics (criterion-runs; accuracies over runs that produced a reading, failures counted separately):
  target_accuracy       observed role/function targets == gold (one-to-one, same type, same canonical word run)
  policy_accuracy       observed policy == gold policy
  target_basis_accuracy observed target_basis == gold basis
  unsafe_target_policy_loss (HARD GATE, must be 0): a gold role/function target carried by no observed target,
                        or a downgrade (role/function policy -> sector / pure_duration; sector -> pure_duration).
                        A failed run is never unsafe (fail closed) but counts as a failure.
  stability             share of cases whose per-criterion signature (outcome, basis, policy, typed targets) is
                        identical in every run (needs >= 2 runs, else undecided)
  failure_rate          (failed_validation + failed_technical) criterion-runs / all criterion-runs
  repair_rate           jobs that needed the repair call (reported only, never a gate)
Fixtures: --fixture main (default) or --fixture target_basis (scripts/s1_eval_fixtures/s1a_target_basis_cases_v2.json,
s1a-basis-2: the 44 s1a-basis-1 cases with the same gold, the s1a-1.3 oracle answers adding where_evidence to the 10
setting_only cases; SHA-pinned; its names_role_or_work gold is a fixture field only and is not scored). The
s1a-basis-1 file stays byte for byte (ARCHIVED_FIXTURES) for the reproducibility of the s1a-1.1 runs; it is not
selectable. --fixture target_basis_heldout (s1a_target_basis_heldout_cases.json, s1a-basis-heldout-1, 52 cases,
SHA-pinned) is the S1-A-1.3 HELD-OUT set: written after prompt s1a-1.3 was fixed, expected classifications frozen,
never used for tuning; a real run is refused without --allow-heldout and is meant to run once, only after the
s1a-1.3 MAIN regression passes. The v3 held-out fixtures are not selectable.
Reporting only (never a gate; S1-A-1.3):
  basis_confusion        gold target_basis x observed target_basis (failed runs as "failed_<kind>")
  by_gold_basis / by_language / by_group   the accuracies, unsafe and failure counts per slice (en / ar)
  where_evidence         setting_only gold runs with / without evidence, evidence on any other gold basis, runs
                         whose main or repair errors concern where_evidence
  sector_to_function     setting_only gold read as a role / function target (main answer or after a repair)
  unsafe_breakdown       policy downgrades and target losses (the two causes of the hard gate)
  repairs                repaired jobs, their outcome and the failure outcome details (f6 / where guard / other)
  stability_by_case      per case: runs, distinct signatures, stable
Diagnostic (reporting only, never a gate):
  target_expansion      an observed target that carries a gold target but quotes materially more words than it
                        (e.g. the gold function followed by where / for whom words). Extra words that are only a
                        closed list of leading prepositions / articles, an Arabic proclitic or "experience" / "خبرة"
                        do not count. Such a target hides a context inside the target text.
Gates: hard unsafe_target_policy_loss == 0 and independence_failures == 0; target, policy and basis accuracy
>= 0.95; stability >= 0.95; failure_rate <= 0.05.

Modes: dry-run (default; no client), oracle (scripted labelled answers), real (refused unless --confirm-real and
all pins match: Pass A prompt version + SHA + fingerprint, S1 v4 version, model, temperature, max tokens, fixture
SHA; client max_retries=0). The v3 held-out fixtures are NOT available to this harness. Recorded runs of an earlier
runnable prompt version (s1a-1.1) replay offline through run_case(..., prompt_version=...) under their own contract.

Usage (later, after explicit approval):
  python scripts/s1_pass_a_eval.py --out /tmp/s1a                                      # dry-run
  python scripts/s1_pass_a_eval.py --out /tmp/s1a --mode oracle --runs 2               # offline self-check
  python scripts/s1_pass_a_eval.py --out /tmp/s1a_main --mode real --runs 5 --confirm-real
(also reachable as: python scripts/s1_context_eval.py --stage pass_a ...)
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import s1_context_eval as ctx  # noqa: E402
from services.s0_experience import llm_call  # noqa: E402
from services.s1_requirements.criteria import enumerate_experience_criteria  # noqa: E402
from services.s1_requirements.jd_text import JDText  # noqa: E402
from services.s1_requirements.validator import implied_policy  # noqa: E402
from services.s1_two_pass import pass_a as pa  # noqa: E402
from services.s1_two_pass import pass_a_runner as pr  # noqa: E402
from services.s1_two_pass import prompt_a  # noqa: E402
from services.s1_two_pass import schema as v4  # noqa: E402

FIXTURE = "main"                                   # default fixture
# the fixtures this harness accepts (never a held-out file): MAIN context cases and the Option D target-basis set
FIXTURES = {"main": ctx.FIXTURES["main"],
            "target_basis": ctx.FIXTURE_DIR / "s1a_target_basis_cases_v2.json",
            "target_basis_heldout": ctx.FIXTURE_DIR / "s1a_target_basis_heldout_cases.json"}
FIXTURE_SHA256 = {"main": ctx.FIXTURE_SHA256["main"],
                  "target_basis": "2daadcb18350dc908fb48ab6081c8ba2fda010f78c2fbb9c55233f780d2ea3e0",
                  "target_basis_heldout": "81e607d183a186bf1d153a534bdd92898a7cb247f5ad3378cf4b109fc95f7018"}
HELDOUT_FIXTURES = ("target_basis_heldout",)          # a real run needs --allow-heldout
# earlier fixture versions, byte for byte, for the reproducibility of recorded runs (never selectable)
ARCHIVED_FIXTURES = {"target_basis@s1a-basis-1": (ctx.FIXTURE_DIR / "s1a_target_basis_cases.json",
                                                  "02a452bba2de4374f394db5a9d4d11065ab80f70749f3337be5550007d304293")}
PINNED = {"prompt_version": "s1a-1.4", "prompt_fingerprint": "1cc53afc9e79",
          "prompt_sha256": "1cc53afc9e79e2137ed85c5569f9a07a348350367153ed0396190dbe58613de3",
          "s1_version": "2.0.0", "model": "gpt-4o-mini", "temperature": 0.0, "max_tokens": 4000}
THRESHOLDS = {"target_accuracy": 0.95, "policy_accuracy": 0.95, "target_basis_accuracy": 0.95,
              "stability": 0.95, "failure_rate": 0.05}
DEFAULT_RUNS = 5
TARGET_POLICIES = ("explicit_role", "functional", "mixed")
CONTEXT_AMBIGUITY = "ambiguous_context_scope"


# ── gold and oracle (derived; the fixture is read-only) ─────────────────────

def case_specs(case: dict) -> list[dict]:
    """Per-criterion specs: MAIN cases carry "criteria"; target-basis cases are one criterion with its own gold."""
    return case["criteria"] if "criteria" in case else [{"oracle": case["oracle"], "gold": case["gold"]}]


def pass_a_gold(spec: dict, hints: list[str]) -> dict:
    if "gold" in spec:                                           # explicit gold (target-basis fixture)
        g = spec["gold"]
        return {"targets": [(t["text"], t["kind"]) for t in g["targets"]], "basis": g["target_basis"],
                "policy": g["policy"]}
    o = spec["oracle"]
    if "restrictions" not in o:                                  # hinted criterion
        types = [t["type"] for t in o.get("targets") or []]
        targets = [(h, t) for h, t in zip(hints, types)]
        return {"targets": targets, "basis": "targets", "policy": implied_policy(types) or "pure_duration"}
    rs = o["restrictions"]
    rf = [(r["text"], r["kind"]) for r in rs if r["kind"] in ("role", "function")]
    if rf:
        basis, policy = "targets", implied_policy([k for _, k in rf])
    elif any(r["kind"] == "vague" for r in rs):
        basis, policy = "unspecified", "pure_duration"
    elif any(r["kind"] == "context" for r in rs) or o.get("settings"):
        basis, policy = "setting_only", "sector"
    else:
        basis, policy = "total_experience", "pure_duration"
    return {"targets": rf, "basis": basis, "policy": policy}


def case_golds(case: dict) -> list[dict]:
    crits = ctx.case_criteria(case)
    return [pass_a_gold(spec, list(c.target_hints)) for c, spec in zip(crits, case_specs(case))]


def _context_lines(o: dict) -> set[int]:
    lines = {x["line"] for x in o.get("settings") or [] if isinstance(x, dict)}
    lines |= {r["line"] for r in o.get("restrictions") or [] if r.get("kind") == "context"}
    return lines


def pass_a_oracle_item(spec: dict, gold: dict) -> dict:
    """The labelled answer in the Pass A wire: no context element, the gold target_basis added."""
    o = copy.deepcopy(spec["oracle"])
    o.pop("settings", None)
    o.pop("setting", None)
    if "restrictions" in o:
        o["restrictions"] = [r for r in o["restrictions"] if r["kind"] != "context"]
    o["ambiguity"] = [a for a in o.get("ambiguity") or [] if a != CONTEXT_AMBIGUITY]
    ctx_only = _context_lines(spec["oracle"])
    anchors = ({r["line"] for r in o.get("restrictions") or []}
               | {t["jd_span"]["line"] for t in o.get("targets") or [] if t.get("jd_span")})
    first = o["requirement_spans"][0]["line"] if o.get("requirement_spans") else None
    o["requirement_spans"] = [s for s in o.get("requirement_spans") or []
                              if s["line"] == first or s["line"] in anchors or s["line"] not in ctx_only]
    o["target_basis"] = gold["basis"]
    return o


def oracle_response(case: dict) -> str:
    durs = {m.text: did for did, _, m in reversed(JDText(ctx.case_jd(case)).durations())}
    items = []
    for c, spec, gold in zip(ctx.case_criteria(case), case_specs(case), case_golds(case)):
        o = pass_a_oracle_item(spec, gold)
        if o.get("duration") is not None:
            o["duration"] = durs[o["duration"]]
        o["criterion_id"] = c.criterion_id
        items.append(o)
    return json.dumps({"criteria": items}, ensure_ascii=False)


# ── observation and scoring ─────────────────────────────────────────────────

def observe(case: dict, res, sink: list) -> dict:
    crit = []
    if res.status == "ok":
        for f in res.frozen:
            crit.append({"criterion_id": f.criterion.criterion_id, "outcome": "ok", "basis": f.frame.target_basis,
                         "policy": f.artefact.policy, "status": f.artefact.spec_status,
                         "reasons": [r.code for r in f.artefact.reasons],
                         "targets": [{"text": t.text, "type": t.type} for t in f.frame.targets],
                         "where_evidence": [s.text for s in f.frame.where_evidence]})
    else:
        for a in res.failed:
            crit.append({"criterion_id": a.criterion_id,
                         "outcome": "technical" if a.spec_status == "failed_technical" else "validation",
                         "basis": None, "policy": None, "status": a.spec_status,
                         "reasons": [r.code for r in a.reasons], "targets": None, "where_evidence": None})
    meta = res.outcome.meta if res.outcome else {}
    return {"job_outcome": res.status, "reason": res.outcome.reason if res.outcome else None, "criteria": crit,
            "calls": [{k: c.get(k) for k in ("call", "model", "temperature", "max_tokens", "finish_reason")}
                      for c in sink],
            "raw": sink, "repair_used": bool(meta.get("repair_used")), "outcome_detail": meta.get("outcome"),
            "errors": res.outcome.errors if res.outcome else None}


def _targets_equal(gold: list[tuple[str, str]], obs: list[dict]) -> bool:
    if len(gold) != len(obs):
        return False

    def assign(i, free):
        return i == len(gold) or any(t["type"] == gold[i][1] and ctx._carries(gold[i][0], t["text"])
                                     and assign(i + 1, free[:k] + free[k + 1:]) for k, t in enumerate(free))
    return assign(0, list(obs))


IGNORABLE_EXTRA = frozenset(ctx.LEADING_WORDS) | {"of", "experience", "خبرة", "خبرات"}


def expansion(gold_text: str, observed_text: str) -> list[str]:
    """Material words an observed target adds around a gold target it carries ([] if none / not carried)."""
    g, o = ctx.words(gold_text), ctx.words(observed_text)
    hits = ctx.locate_words(g, o)
    if not g or not hits:
        return []
    i, _residue = hits[0]
    extra = o[:i] + o[i + len(g):]
    return [w for w in extra if w not in IGNORABLE_EXTRA]


def target_expansions(gold: dict, o: dict) -> list[dict]:
    out = []
    for g, _ in gold["targets"]:
        for t in o.get("targets") or []:
            extra = expansion(g, t["text"])
            if extra:
                out.append({"gold": g, "observed": t["text"], "extra_words": extra})
    return out


def check(gold: dict, o: dict) -> dict:
    """Per criterion-run checks; failed runs score nothing and are never unsafe (counted as failures)."""
    if o["outcome"] != "ok":
        return {"ok": False, "targets": False, "policy": False, "basis": False, "unsafe": False,
                "targets_lost": [], "policy_downgraded": False, "expanded": [], "sector_to_function": False}
    lost = [g for g, _ in gold["targets"] if not any(ctx._carries(g, t["text"]) for t in o["targets"])]
    gp, op = gold["policy"], o["policy"]
    down = (gp in TARGET_POLICIES and op in ("sector", "pure_duration")) or (gp == "sector" and op == "pure_duration")
    return {"ok": True, "targets": _targets_equal(gold["targets"], o["targets"]), "policy": op == gp,
            "basis": o["basis"] == gold["basis"], "unsafe": bool(lost or down), "targets_lost": lost,
            "policy_downgraded": bool(down), "expanded": target_expansions(gold, o),
            # reporting only (never unsafe by the gate definition): a sector-only reading turned into a target
            "sector_to_function": gp == "sector" and bool(o["targets"])}


def score(case: dict, obs: dict) -> dict:
    golds = case_golds(case)
    checks = [check(g, o) for g, o in zip(golds, obs["criteria"])]
    return {"checks": checks, "gold": [{"targets": g["targets"], "basis": g["basis"], "policy": g["policy"]}
                                       for g in golds],
            "pass": len(checks) == len(golds) and all(c["ok"] and c["targets"] and c["policy"] and c["basis"]
                                                      and not c["unsafe"] for c in checks)}


def _signature(rec: dict) -> tuple:
    return tuple((o["outcome"], o["basis"], o["policy"],
                  None if o["targets"] is None else tuple(sorted((" ".join(ctx.words(t["text"])), t["type"])
                                                                 for t in o["targets"])))
                 for o in rec["criteria"])


def _slice(pairs: list[tuple[dict, dict]]) -> dict:
    ok = [c for _, c in pairs if c["ok"]]
    return {"criterion_runs": len(pairs), "ok_runs": len(ok), "failures": len(pairs) - len(ok),
            "target_accuracy": ctx._ratio(sum(c["targets"] for c in ok), len(ok)),
            "policy_accuracy": ctx._ratio(sum(c["policy"] for c in ok), len(ok)),
            "target_basis_accuracy": ctx._ratio(sum(c["basis"] for c in ok), len(ok)),
            "unsafe": sum(1 for _, c in pairs if c["unsafe"])}


def _mentions_where(errors: list) -> bool:
    return any(isinstance(e, str) and "where_evidence" in e for e in errors or [])


def s1a13_report(records: list[dict]) -> dict:
    """S1-A-1.3 reporting (never a gate): confusion, slices, where_evidence, conversions, unsafe causes, repairs and
    per-case stability."""
    rows = [(r, g, o, c) for r in records for g, o, c in zip(r["gold"], r["criteria"], r["checks"])]
    conf: dict = defaultdict(Counter)
    by_basis, by_lang, by_group = defaultdict(list), defaultdict(list), defaultdict(list)
    for r, g, o, c in rows:
        conf[g["basis"]][o["basis"] if o["outcome"] == "ok" else f"failed_{o['outcome']}"] += 1
        by_basis[g["basis"]].append((r, c))
        by_lang[r.get("lang")].append((r, c))
        by_group[r.get("family")].append((r, c))
    setting_ok = [(r, o) for r, g, o, c in rows if g["basis"] == "setting_only" and o["outcome"] == "ok"]
    sig = defaultdict(list)
    for r in records:
        sig[r["case"]].append(_signature(r))
    repaired = [r for r in records if r.get("repair_used")]
    return {
        "basis_confusion": {k: dict(sorted(v.items())) for k, v in sorted(conf.items())},
        "by_gold_basis": {k: _slice(v) for k, v in sorted(by_basis.items())},
        "by_language": {str(k): _slice(v) for k, v in sorted(by_lang.items(), key=lambda x: str(x[0]))},
        "by_group": {str(k): _slice(v) for k, v in sorted(by_group.items(), key=lambda x: str(x[0]))},
        "where_evidence": {
            "setting_only_gold_ok_runs": len(setting_ok),
            "with_evidence": sum(1 for _, o in setting_ok if o.get("where_evidence")),
            "missing": [{"case": r["case"], "run": r["run"], "observed_basis": o["basis"]}
                        for r, o in setting_ok if not o.get("where_evidence")],
            "unexpected": [{"case": r["case"], "run": r["run"], "gold_basis": g["basis"],
                            "where_evidence": o["where_evidence"]}
                           for r, g, o, c in rows if g["basis"] != "setting_only" and o.get("where_evidence")],
            "invalid_runs": [{"case": r["case"], "run": r["run"],
                              "main": _mentions_where((r.get("errors") or {}).get("errors")),
                              "after_repair": _mentions_where((r.get("errors") or {}).get("repair_errors")),
                              "job_outcome": r["job_outcome"]}
                             for r in records if _mentions_where((r.get("errors") or {}).get("errors"))
                             or _mentions_where((r.get("errors") or {}).get("repair_errors"))],
        },
        "sector_to_function": {
            "count": sum(1 for *_, c in rows if c.get("sector_to_function")),
            "in_main_answer": sum(1 for r, *_, c in rows if c.get("sector_to_function") and not r.get("repair_used")),
            "after_repair": sum(1 for r, *_, c in rows if c.get("sector_to_function") and r.get("repair_used")),
            "details": [{"case": r["case"], "run": r["run"], "repair_used": r.get("repair_used"),
                         "targets": o["targets"]} for r, g, o, c in rows if c.get("sector_to_function")],
        },
        "unsafe_breakdown": {"policy_downgrades": sum(1 for *_, c in rows if c["policy_downgraded"]),
                             "target_losses": sum(1 for *_, c in rows if c["targets_lost"])},
        "repairs": {"jobs_repaired": len(repaired),
                    "repaired_ok": sum(1 for r in repaired if r["job_outcome"] == "ok"),
                    "repaired_failed": sum(1 for r in repaired if r["job_outcome"] == "failed"),
                    "failure_details": dict(sorted(Counter(str(r.get("outcome_detail")) for r in records
                                                           if r["job_outcome"] == "failed").items()))},
        "stability_by_case": {k: {"runs": len(v), "distinct_signatures": len(set(v)), "stable": len(set(v)) == 1}
                              for k, v in sorted(sig.items())},
    }


def summarize(records: list[dict], *, independence_failures: int = 0) -> dict:
    runs = [(r, c) for r in records for c in r["checks"]]
    ok = [c for _, c in runs if c["ok"]]
    outcomes = Counter(o["outcome"] for r in records for o in r["criteria"])
    unsafe = [(r, c) for r, c in runs if c["unsafe"]]
    sig = defaultdict(list)
    for r in records:
        sig[r["case"]].append(_signature(r))
    multi = {k: v for k, v in sig.items() if len(v) >= 2}
    stable = [k for k, v in multi.items() if len(set(v)) == 1]
    gold_bases = Counter(g["basis"] for r in records for g in r["gold"])
    return {
        "runs_total": len(records), "cases": len({r["case"] for r in records}), "criterion_runs": len(runs),
        "outcomes": {"ok": outcomes["ok"], "failed_validation": outcomes["validation"],
                     "failed_technical": outcomes["technical"]},
        "failure_rate": ctx._ratio(outcomes["validation"] + outcomes["technical"], len(runs)),
        "target_accuracy": ctx._ratio(sum(c["targets"] for c in ok), len(ok)),
        "policy_accuracy": ctx._ratio(sum(c["policy"] for c in ok), len(ok)),
        "target_basis_accuracy": ctx._ratio(sum(c["basis"] for c in ok), len(ok)),
        "repair_rate": ctx._ratio(sum(1 for r in records if r.get("repair_used")), len(records)),
        "stability": ctx._ratio(len(stable), len(multi)),
        "unstable_cases": sorted(set(multi) - set(stable)),
        "gold_basis_distribution": dict(sorted(gold_bases.items())),
        "hard": {"unsafe_target_policy_loss": len(unsafe), "independence_failures": independence_failures},
        "target_expansion": {                                  # DIAGNOSTIC ONLY: never part of a gate
            "criterion_runs": sum(1 for _, c in runs if c.get("expanded")),
            "rate": ctx._ratio(sum(1 for c in ok if c.get("expanded")), len(ok)),
            "cases": sorted({r["case"] for r, c in runs if c.get("expanded")}),
            "details": [{"case": r["case"], "run": r["run"], **x} for r, c in runs for x in c.get("expanded", [])],
        },
        "unsafe": [{"case": r["case"], "run": r["run"], "repair_used": r.get("repair_used"),
                    "targets_lost": c["targets_lost"], "policy_downgraded": c["policy_downgraded"]}
                   for r, c in unsafe],
        "repair_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "repair"),
        "main_calls": sum(1 for r in records for c in r["calls"] if c["call"] == "main"),
        "failures": [{"case": r["case"], "run": r["run"], "reason": r.get("reason")}
                     for r in records if r["job_outcome"] == "failed"],
        "misses": [{"case": r["case"], "run": r["run"], "gold": g, "observed": {k: o[k] for k in
                                                                               ("basis", "policy", "targets")},
                    "checks": {k: c[k] for k in ("targets", "policy", "basis", "unsafe")}}
                   for r in records for g, o, c in zip(r["gold"], r["criteria"], r["checks"])
                   if c["ok"] and not (c["targets"] and c["policy"] and c["basis"] and not c["unsafe"])],
        "s1a13": s1a13_report(records),                       # REPORTING ONLY: never part of a gate
    }


def evaluate_gates(s: dict) -> dict:
    res = {f"hard:{k}": v == 0 for k, v in s["hard"].items()}
    for k in ("target_accuracy", "policy_accuracy", "target_basis_accuracy"):
        res[k] = s[k] is not None and s[k] >= THRESHOLDS[k]
    res["stability"] = None if s["stability"] is None else s["stability"] >= THRESHOLDS["stability"]
    res["failure_rate"] = s["failure_rate"] is not None and s["failure_rate"] <= THRESHOLDS["failure_rate"]
    decided = [v for v in res.values() if v is not None]
    return {"gates": res, "all_decided_pass": all(decided), "undecided": sorted(k for k, v in res.items() if v is None)}


def render_markdown(meta: dict, s: dict, gates: dict) -> str:
    return "\n".join([
        "# S1 Pass A (target pass) evaluation", "",
        f"- prompt {meta['prompt_version']} ({meta['prompt_fingerprint']}), S1 {meta['s1_version']}, model "
        f"{meta['model']}, temperature {meta['temperature']}, mode {meta['mode']}, runs {meta['runs']}",
        f"- fixture {meta['fixture']} {meta['fixture_version']} sha256 {meta['fixture_sha256']}", "",
        "## Gates", *[f"- {k}: {v}" for k, v in gates["gates"].items()],
        f"- all decided gates pass: {gates['all_decided_pass']} (undecided: {gates['undecided']})", "",
        "## Metrics",
        *[f"- {k}: {s[k]}" for k in ("target_accuracy", "policy_accuracy", "target_basis_accuracy", "stability",
                                     "failure_rate", "repair_rate", "unstable_cases", "gold_basis_distribution")],
        f"- outcomes {s['outcomes']}; calls main {s['main_calls']}, repair {s['repair_calls']}",
        f"- hard {s['hard']}", "",
        "## Unsafe target / policy loss", *[f"- {x}" for x in s["unsafe"]], "",
        "## S1-A-1.3 reporting (not a gate)",
        f"- basis confusion (gold -> observed): {s['s1a13']['basis_confusion']}",
        *[f"- by gold basis {k}: {v}" for k, v in s["s1a13"]["by_gold_basis"].items()],
        *[f"- by language {k}: {v}" for k, v in s["s1a13"]["by_language"].items()],
        *[f"- by group {k}: {v}" for k, v in s["s1a13"]["by_group"].items()],
        f"- where_evidence: {s['s1a13']['where_evidence']}",
        f"- sector_to_function: {s['s1a13']['sector_to_function']}",
        f"- unsafe breakdown: {s['s1a13']['unsafe_breakdown']}",
        f"- repairs: {s['s1a13']['repairs']}",
        f"- unstable cases: {[k for k, v in s['s1a13']['stability_by_case'].items() if not v['stable']]}", "",
        "## Target expansion (diagnostic only, not a gate)",
        f"- criterion-runs {s['target_expansion']['criterion_runs']}, rate {s['target_expansion']['rate']}, "
        f"cases {s['target_expansion']['cases']}",
        *[f"- {x}" for x in s["target_expansion"]["details"]], "",
        "## Failures", *[f"- {x}" for x in s["failures"]], "",
        "## Misses", *[f"- {x}" for x in s["misses"]], ""])


# ── independence (offline: the Pass A input never depends on the qualifying context) ──────────────────

def independence_probe(cases: list[dict]) -> list[dict]:
    failures = []
    for case in cases:
        jd = JDText(ctx.case_jd(case))
        base_c = ctx.case_criteria(case)
        base = pa.build_pass_a_request(jd, base_c)
        for qc in ctx.QC_PROBES:
            a = ctx.case_analysis(case)
            a["experience"]["qualifying_context"] = copy.deepcopy(qc)
            a["qualifying_context_audit"] = {"schema": "qc_audit_v1", "current": {"source": qc["source"]},
                                             "latest_run": {"status": "ok", "result": qc}}
            crits = enumerate_experience_criteria(ctx.case_job_id(case), a)
            req = pa.build_pass_a_request(jd, crits)
            if (crits != base_c or req.user_message != base.user_message
                    or pa.pass_a_cache_key(req) != pa.pass_a_cache_key(base)):
                failures.append({"case": case["id"], "probe": qc})
    return failures


# ── run ──────────────────────────────────────────────────────────────────────

async def run_case(case: dict, client, prompt_version: str | None = None) -> dict:
    """prompt_version None = the current prompt; "s1a-1.1" replays under its own prompt and contract."""
    sink: list = []
    res = await pr.run_pass_a_job(ctx.case_job_id(case), ctx.case_jd(case), ctx.case_analysis(case),
                                  client=ctx.Capture(client, sink), model=PINNED["model"], cache=None,
                                  prompt_version=prompt_version)
    return observe(case, res, sink)


async def run_all(cases: list[dict], *, runs: int, client_for, prompt_version: str | None = None) -> list[dict]:
    records = []
    for run in range(1, runs + 1):
        for case in cases:
            obs = await run_case(case, client_for(case), prompt_version)
            records.append({"case": case["id"], "family": case.get("family", case.get("group")),
                            "lang": case["lang"], "run": run,
                            **obs, **score(case, obs)})
    return records


def check_pins(path: Path, fixture: str = FIXTURE) -> list[str]:
    actual = {"prompt_version": v4.S1A_PROMPT_VERSION, "prompt_fingerprint": prompt_a.pass_a_prompt_fingerprint(),
              "prompt_sha256": prompt_a.S1A_PROMPT_SHA256, "s1_version": v4.S1V4_VERSION, "model": v4.S1A_MODEL,
              "temperature": v4.S1A_TEMPERATURE, "max_tokens": v4.S1A_MAX_TOKENS}
    problems = [f"{k}: pinned {PINNED[k]!r}, actual {actual[k]!r}" for k in PINNED if PINNED[k] != actual[k]]
    try:
        prompt_a.load_pass_a_prompt()
    except prompt_a.PromptIntegrityError as exc:
        problems.append(str(exc))
    if ctx.sha256_file(path) != FIXTURE_SHA256[fixture]:
        problems.append(f"fixture {fixture} sha256 {ctx.sha256_file(path)} != pinned {FIXTURE_SHA256[fixture]}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", choices=tuple(FIXTURES), default=FIXTURE,
                    help="MAIN context cases, the target-basis development set or the S1-A-1.3 target-basis "
                         "held-out set (real run only with --allow-heldout)")
    ap.add_argument("--mode", choices=("dry-run", "oracle", "real"), default="dry-run")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--confirm-real", action="store_true", help="required for --mode real (paid model calls)")
    ap.add_argument("--allow-heldout", action="store_true",
                    help="required to run the held-out fixture for real (single use, after MAIN passes)")
    args = ap.parse_args(argv)

    path = FIXTURES[args.fixture]
    fixture = json.loads(path.read_text(encoding="utf-8"))
    only = {c for c in args.cases.split(",") if c}
    cases = [c for c in fixture["cases"] if not only or c["id"] in only]
    if args.mode == "real":
        if not args.confirm_real:
            print("REFUSED: --mode real makes real model calls; pass --confirm-real explicitly.")
            return 2
        if args.fixture in HELDOUT_FIXTURES and not args.allow_heldout:
            print("REFUSED: the held-out fixture is single-use (only after the MAIN regression passes); pass "
                  "--allow-heldout explicitly.")
            return 2
        problems = check_pins(path, args.fixture)
        if problems:
            print("REFUSED: pins do not match:\n- " + "\n- ".join(problems))
            return 2
    system = prompt_a.load_pass_a_prompt()
    budget = {c["id"]: llm_call.request_token_upper_bound(
        [{"role": "system", "content": system},
         {"role": "user", "content": pa.build_pass_a_request(JDText(ctx.case_jd(c)), ctx.case_criteria(c)).user_message}])
        for c in cases}
    ind = independence_probe(cases)
    print(json.dumps({"stage": "pass_a", "fixture": args.fixture, "cases": len(cases), "runs": args.runs,
                      "max_calls_incl_repairs": len(cases) * args.runs * 2,
                      "input_token_upper_bound_one_run": sum(budget.values()),
                      "independence_failures": len(ind)}, indent=1))
    if args.mode == "dry-run":
        print("DRY RUN: no model calls made.")
        return 0 if not ind else 1
    if args.mode == "oracle":
        def client_for(case):
            return ctx.ScriptedClient(oracle_response(case))
    else:
        real = ctx.make_real_client()

        def client_for(case):
            return real
    loop = asyncio.new_event_loop()
    try:
        records = loop.run_until_complete(run_all(cases, runs=args.runs, client_for=client_for))
    finally:
        loop.close()
    summary = summarize(records, independence_failures=len(ind))
    gates = evaluate_gates(summary)
    meta = {"stage": "pass_a", "prompt_version": v4.S1A_PROMPT_VERSION,
            "prompt_fingerprint": prompt_a.pass_a_prompt_fingerprint(), "prompt_sha256": prompt_a.S1A_PROMPT_SHA256,
            "s1_version": v4.S1V4_VERSION, "model": PINNED["model"], "temperature": v4.S1A_TEMPERATURE,
            "max_tokens": v4.S1A_MAX_TOKENS,
            "client_max_retries": ctx.CLIENT_MAX_RETRIES if args.mode == "real" else None,
            "mode": args.mode, "runs": args.runs, "fixture": args.fixture,
            "fixture_version": fixture.get("fixture_version"), "fixture_sha256": ctx.sha256_file(path),
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
