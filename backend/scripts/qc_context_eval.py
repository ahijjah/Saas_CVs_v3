"""
Job-analysis qualifying-context evaluation harness (evaluation infrastructure only).

Evaluates answers for the frozen (not yet implemented) analysis field

    experience.qualifying_context = {"state": "identified" | "none" | "uncertain",
                                     "contexts": [verbatim JD phrases],   # AND-combined
                                     "source": "analysis"}

against labelled SYNTHETIC job descriptions. Nothing here is wired into production: it does not import
services.ai_service, the production job-analysis prompt, D-01, the S1 classifier, the database or OpenAI.
It reuses two pure S1 utilities READ-ONLY: jd_text (canonical words, JDText.find with the Arabic proclitic
boundary) and criteria.enumerate_experience_criteria (the D-01 enumeration mirror, for the family-N gap flag).

Definitions (labels follow them):
  identified  the JD restricts WHICH otherwise relevant experience counts (sector, project domain, organisation)
  none        no qualifying restriction (generic environment wording and company "about us" text are none)
  uncertain   the analysis cannot decide safely, OR the shared experience block cannot represent it
              (role-specific contexts); candidate phrases may be returned
Contexts are verbatim JD phrases (Arabic proclitic chain allowed on the first word, nothing else); an OR stays
inside one phrase; several contexts are ANDed. domain_knowledge is never a qualifying context.

Modes
  oracle   every case answered with its labelled reference answer (must score 100%)
  replay   score recorded raw answers: --replay FILE with {"responses": {case_id: [raw, ...]}} (one per run)
  real     DISABLED in this version (no candidate prompt exists yet; no OpenAI call is possible)

Output (with --out): <out>/results.json (meta, summary, per-run records) and <out>/report.md.

Usage:
  python scripts/qc_context_eval.py --mode oracle --set main --runs 3
  python scripts/qc_context_eval.py --mode oracle --set heldout --out /tmp/qc_ho
  python scripts/qc_context_eval.py --mode replay --replay answers.json --set all
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.s1_requirements.criteria import enumerate_experience_criteria  # noqa: E402
from services.s1_requirements.jd_text import JDText, locate_words, words  # noqa: E402

EVAL_VERSION = "qc-eval-1"
FIXTURE_DIR = Path(__file__).resolve().parent / "qc_eval_fixtures"
MAIN_FIXTURE = FIXTURE_DIR / "qc_main_cases.json"
HELDOUT_FIXTURE = FIXTURE_DIR / "qc_heldout_cases.json"
PROMPT_DIR = FIXTURE_DIR / "prompts"            # future eval-only candidate templates (none in this version)

STATES = ("identified", "none", "uncertain")
QC_KEYS = {"state", "contexts", "source"}
FAMILIES = tuple("ABCDEFGHIJKLMN")
FLAG_NO_CRITERION = "no_experience_criterion"
# leading words a one_of variant may add to its shortest form (articles / prepositions only); the Arabic
# attached proclitic chain is handled by the canonical-word comparison, not listed here
ONE_OF_LEADING = frozenset({"in", "on", "at", "within", "the", "a", "an", "في", "ضمن", "لدى"})


# ── fixture ──────────────────────────────────────────────────────────────────

def load_fixture(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_cases(path: Path = MAIN_FIXTURE, only: set[str] | None = None) -> list[dict]:
    fx = load_fixture(path)
    out = []
    for c in fx["cases"]:
        if only and c["id"] not in only:
            continue
        out.append({**c, "split": fx["split"]})
    return out


def case_jd(case: dict) -> str:
    return "\n".join(case["jd_lines"])


def variants(slot) -> list[str]:
    """A slot is a phrase or {"one_of": [phrases]}."""
    return list(slot["one_of"]) if isinstance(slot, dict) else [slot]


def strip_leading(ws: list[str]) -> list[str]:
    i = 0
    while i < len(ws) - 1 and ws[i] in ONE_OF_LEADING:
        i += 1
    return ws[i:]


def one_of_narrow(slot) -> bool:
    """Every variant equals the shortest one once leading articles/prepositions are removed."""
    vs = [words(v) for v in variants(slot)]
    core = strip_leading(min(vs, key=len))
    return all(strip_leading(v) == core for v in vs)


def enumeration_gap(case: dict) -> bool:
    """True when today's experience enumeration (D-01 mirror) yields NO criterion for the analysis stub."""
    return not enumerate_experience_criteria(case["id"], {"experience": case["analysis_stub"]})


# ── answers ──────────────────────────────────────────────────────────────────

def oracle_response(case: dict) -> str:
    return json.dumps(case["oracle"], ensure_ascii=False)


class OracleResponder:
    def respond(self, case: dict, run: int) -> str:
        return oracle_response(case)


class ReplayResponder:
    """Recorded raw answers: {case_id: [raw_run1, raw_run2, ...]}; a non-string entry is JSON-encoded."""

    def __init__(self, responses: dict):
        self.responses = responses

    def respond(self, case: dict, run: int) -> str | None:
        seq = self.responses.get(case["id"]) or []
        if run >= len(seq):
            return None
        raw = seq[run]
        return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


class ScriptedResponder(ReplayResponder):
    """Tests: per-case scripted answers, falling back to the oracle answer."""

    def respond(self, case: dict, run: int) -> str:
        seq = self.responses.get(case["id"])
        if not seq:
            return oracle_response(case)
        raw = seq[min(run, len(seq) - 1)]
        return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


def make_real_responder(*_args, **_kwargs):
    """Real mode is not enabled in this version. When it is, it must render an EVAL-ONLY candidate template
    from PROMPT_DIR (never the production job-analysis prompt) and import the model client lazily inside this
    function, so oracle/replay never import or need OpenAI."""
    raise NotImplementedError("qc real mode is disabled in this version; use oracle or replay")


def parse_qc(raw) -> tuple[dict | None, str | None]:
    """Strict, harness-local parsing (no production normalisation). Accepts the qualifying_context object
    itself or a full analysis object carrying experience.qualifying_context. -> (qc, None) or (None, error)."""
    if raw is None:
        return None, "no_response"
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None, "invalid_json"
    if isinstance(obj, dict) and "experience" in obj:
        exp = obj.get("experience")
        if not isinstance(exp, dict) or "qualifying_context" not in exp:
            return None, "missing_qualifying_context"
        obj = exp["qualifying_context"]
    if not isinstance(obj, dict):
        return None, "not_an_object"
    if set(obj) != QC_KEYS:
        return None, "bad_keys"
    if obj["state"] not in STATES:
        return None, "bad_state"
    if obj["source"] != "analysis":
        return None, "bad_source"
    ctx = obj["contexts"]
    if not isinstance(ctx, list) or not all(isinstance(c, str) and words(c) for c in ctx):
        return None, "bad_contexts"
    keys = [tuple(words(c)) for c in ctx]
    if len(set(keys)) != len(keys):
        return None, "duplicate_contexts"
    return {"state": obj["state"], "contexts": list(ctx), "source": "analysis"}, None


# ── deterministic checks ─────────────────────────────────────────────────────

def grounded(jd: JDText, phrase: str) -> bool:
    """Verbatim (comparison-form, word-bounded, Arabic proclitic allowed on the left) occurrence in the JD."""
    return bool(words(phrase)) and bool(jd.find(phrase))


def phrase_matches(actual: str, slot) -> bool:
    """Accepted span: same canonical words as one variant; only an Arabic proclitic chain may be attached to
    the first word of the answer. No paraphrase, no partial phrase, no fuzzy matching."""
    aw = words(actual)
    for v in variants(slot):
        vw = words(v)
        if len(aw) == len(vw) and any(i == 0 for i, _ in locate_words(vw, aw)):
            return True
    return False


def slots_match(actual: list[str], slots: list) -> bool:
    """One-to-one: every expected slot matched by exactly one answer context, nothing left over (order-free)."""
    if len(actual) != len(slots):
        return False

    def bt(i: int, used: frozenset) -> bool:
        if i == len(actual):
            return True
        return any(j not in used and phrase_matches(actual[i], s) and bt(i + 1, used | {j})
                   for j, s in enumerate(slots))
    return bt(0, frozenset())


def _contains_run(whole: list[str], part: list[str]) -> bool:
    return bool(part) and any(whole[i:i + len(part)] == part for i in range(len(whole) - len(part) + 1))


def decoy_hit(contexts: list[str], decoys: list[str]) -> bool:
    for c in contexts:
        cw = strip_leading(words(c))
        for d in decoys:
            dw = strip_leading(words(d))
            if _contains_run(dw, cw) or _contains_run(cw, dw):
                return True
    return False


def check(case: dict, qc: dict | None) -> dict:
    exp = case["expected"]
    out = {"valid": qc is not None, "state": qc["state"] if qc else None,
           "contexts": qc["contexts"] if qc else [], "state_ok": False, "consistency_ok": False,
           "grounding_ok": False, "context_ok": None, "candidates_ok": None, "decoy_hit": False}
    if qc is None:
        return out
    st, ctx = qc["state"], qc["contexts"]
    jd = JDText(case_jd(case))
    out["state_ok"] = st == exp["state"]
    out["consistency_ok"] = (len(ctx) >= 1) if st == "identified" else (not ctx) if st == "none" else True
    out["grounding_ok"] = all(grounded(jd, c) for c in ctx)
    out["ungrounded"] = [c for c in ctx if not grounded(jd, c)]
    if st == "identified" and exp["state"] == "identified":
        out["context_ok"] = slots_match(ctx, exp["contexts"])
    if st == "uncertain" and exp["state"] == "uncertain" and "allowed_candidates" in exp:
        out["candidates_ok"] = all(any(phrase_matches(c, s) for s in exp["allowed_candidates"]) for c in ctx)
    out["decoy_hit"] = decoy_hit(ctx, case.get("decoys") or [])
    return out


def passed(chk: dict) -> bool:
    # decoy_hit is diagnostic: decoys only label expected-none cases, where any context already fails
    return (chk["valid"] and chk["state_ok"] and chk["consistency_ok"] and chk["grounding_ok"]
            and chk["context_ok"] is not False and chk["candidates_ok"] is not False)


# ── run ──────────────────────────────────────────────────────────────────────

def run_all(cases: list[dict], responder, runs: int = 1) -> list[dict]:
    records = []
    for case in cases:
        for r in range(runs):
            raw = responder.respond(case, r)
            qc, err = parse_qc(raw)
            chk = check(case, qc)
            records.append({"case_id": case["id"], "family": case["family"], "lang": case["lang"],
                            "split": case["split"], "run": r, "raw": raw, "error": err,
                            "expected_state": case["expected"]["state"], **chk, "pass": passed(chk)})
    return records


# ── metrics ──────────────────────────────────────────────────────────────────

def _ratio(n, d):
    return None if not d else round(n / d, 4)


def answer_key(rec: dict) -> tuple:
    if not rec["valid"]:
        return ("invalid", rec["error"])
    return (rec["state"], tuple(sorted(" ".join(words(c)) for c in rec["contexts"])))


def stability(records: list[dict]) -> dict:
    by_case = defaultdict(list)
    for r in records:
        by_case[r["case_id"]].append(answer_key(r))
    if not by_case:
        return {"cases": 0, "stable_cases": 0, "stable_rate": None, "modal_agreement": None, "unstable": []}
    agree = [Counter(keys).most_common(1)[0][1] / len(keys) for keys in by_case.values()]
    unstable = sorted(cid for cid, keys in by_case.items() if len(set(keys)) > 1)
    return {"cases": len(by_case), "stable_cases": len(by_case) - len(unstable),
            "stable_rate": _ratio(len(by_case) - len(unstable), len(by_case)),
            "modal_agreement": round(sum(agree) / len(agree), 4), "unstable": unstable}


def metrics(records: list[dict]) -> dict:
    n = len(records)
    pred = lambda r, s: r["valid"] and r["state"] == s          # noqa: E731
    exp_id = [r for r in records if r["expected_state"] == "identified"]
    exp_none = [r for r in records if r["expected_state"] == "none"]
    exp_unc = [r for r in records if r["expected_state"] == "uncertain"]
    pred_id = [r for r in records if pred(r, "identified")]
    tp = sum(1 for r in exp_id if pred(r, "identified"))
    false_qc = [r for r in exp_none if pred(r, "identified")]
    missed = [r for r in exp_id if pred(r, "none")]
    with_ctx = [r for r in records if r["valid"] and r["contexts"]]
    both_id = [r for r in records if r["context_ok"] is not None]
    return {
        "runs": n, "pass": sum(r["pass"] for r in records), "pass_rate": _ratio(sum(r["pass"] for r in records), n),
        "invalid_outputs": sum(not r["valid"] for r in records),
        "state_accuracy": _ratio(sum(r["state_ok"] for r in records), n),
        "identified_precision": _ratio(tp, len(pred_id)), "identified_recall": _ratio(tp, len(exp_id)),
        "false_qualifying_context": len(false_qc), "false_qualifying_context_rate": _ratio(len(false_qc), len(exp_none)),
        "false_qualifying_context_cases": sorted({r["case_id"] for r in false_qc}),
        "identified_on_uncertain": sum(1 for r in exp_unc if pred(r, "identified")),
        "missed_qualifying_context": len(missed), "missed_qualifying_context_rate": _ratio(len(missed), len(exp_id)),
        "safe_miss_uncertain": sum(1 for r in exp_id if pred(r, "uncertain")),
        "uncertain_accuracy": _ratio(sum(1 for r in exp_unc if pred(r, "uncertain")), len(exp_unc)),
        "uncertain_overuse": sum(1 for r in records if pred(r, "uncertain") and r["expected_state"] != "uncertain"),
        "consistency_violations": sum(1 for r in records if r["valid"] and not r["consistency_ok"]),
        "grounding_accuracy": _ratio(sum(r["grounding_ok"] for r in with_ctx), len(with_ctx)),
        "grounding_failures": sum(not r["grounding_ok"] for r in with_ctx),
        "context_accuracy": _ratio(sum(bool(r["context_ok"]) for r in both_id), len(both_id)),
        "candidates_violations": sum(r["candidates_ok"] is False for r in records),
        "decoy_hits": sum(r["decoy_hit"] for r in records),
        "stability": stability(records),
    }


def summarize(records: list[dict], cases: list[dict]) -> dict:
    def grouped(key):
        g = defaultdict(list)
        for r in records:
            g[r[key]].append(r)
        return {k: metrics(v) for k, v in sorted(g.items())}
    gaps = [c["id"] for c in cases if FLAG_NO_CRITERION in (c.get("flags") or [])]
    return {"overall": metrics(records), "by_split": grouped("split"), "by_lang": grouped("lang"),
            "by_family": grouped("family"),
            "enumeration_gaps": {"flagged_cases": gaps,
                                 "confirmed": [cid for cid in gaps
                                               if enumeration_gap(next(c for c in cases if c["id"] == cid))],
                                 "note": "context-only requirement: today's experience enumeration produces no "
                                         "criterion; recorded, not fixed"},
            "failed": sorted({r["case_id"] for r in records if not r["pass"]})}


HEADLINE = ("pass_rate", "state_accuracy", "false_qualifying_context", "missed_qualifying_context",
            "identified_precision", "identified_recall", "uncertain_accuracy", "grounding_accuracy",
            "grounding_failures", "context_accuracy", "invalid_outputs", "decoy_hits")


def render_markdown(meta: dict, s: dict) -> str:
    o = s["overall"]

    def row(name, m):
        return (f"| {name} | {m['runs']} | {m['pass_rate']} | {m['state_accuracy']} | {m['false_qualifying_context']} "
                f"| {m['missed_qualifying_context']} | {m['grounding_accuracy']} | {m['context_accuracy']} "
                f"| {m['stability']['stable_rate']} |")
    head = ["| group | runs | pass | state acc | FALSE QC | missed | grounding | context | stable |",
            "|---|---|---|---|---|---|---|---|---|"]
    lines = [f"# Qualifying-context evaluation ({meta['mode']}, {meta['eval_version']})", "",
             f"Fixtures: {', '.join(meta['fixture_versions'])}; runs per case: {meta['runs']}", "",
             f"**False qualifying contexts: {o['false_qualifying_context']}** "
             f"(cases: {', '.join(o['false_qualifying_context_cases']) or '-'})", "",
             *[f"- {k}: {o[k]}" for k in HEADLINE],
             f"- stability: {o['stability']['stable_cases']}/{o['stability']['cases']} cases "
             f"(modal agreement {o['stability']['modal_agreement']})", "",
             "## By split", *head, *[row(k, v) for k, v in s["by_split"].items()], "",
             "## By language", *head, *[row(k, v) for k, v in s["by_lang"].items()], "",
             "## By family", *head, *[row(k, v) for k, v in s["by_family"].items()], "",
             "## Known gap (family N)",
             f"- flagged: {s['enumeration_gaps']['flagged_cases']}; enumeration confirms no criterion for: "
             f"{s['enumeration_gaps']['confirmed']} ({s['enumeration_gaps']['note']})", "",
             f"Failed cases: {', '.join(s['failed']) or 'none'}", ""]
    return "\n".join(lines)


# ── prompt guard (for future eval-only candidate templates) ──────────────────

def fixture_phrases(cases: list[dict]) -> set[str]:
    """Every labelled phrase of a fixture set: roles, context/candidate variants, oracle contexts, decoys."""
    out = set()
    for c in cases:
        out |= {r for r in c["analysis_stub"].get("relevant_roles") or []}
        exp = c["expected"]
        for slot in (exp.get("contexts") or []) + (exp.get("allowed_candidates") or []):
            out |= set(variants(slot))
        out |= set(c["oracle"]["contexts"])
        out |= set(c.get("decoys") or [])
    return out


def prompt_leaks(prompt: str, cases: list[dict]) -> list[str]:
    """Fixture phrases (core form, leading articles/prepositions removed) occurring in a prompt template."""
    pw = words(prompt)
    return sorted(p for p in fixture_phrases(cases) if _contains_run(pw, strip_leading(words(p))))


# ── CLI ──────────────────────────────────────────────────────────────────────

def fixture_paths(which: str) -> list[Path]:
    return {"main": [MAIN_FIXTURE], "heldout": [HELDOUT_FIXTURE], "all": [MAIN_FIXTURE, HELDOUT_FIXTURE]}[which]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("oracle", "replay", "real"), default="oracle")
    ap.add_argument("--set", dest="which", choices=("main", "heldout", "all"), default="all")
    ap.add_argument("--fixture", action="append", default=[], help="fixture path(s); overrides --set")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--replay", default="", help="replay file {\"responses\": {case_id: [raw, ...]}}")
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.mode == "real":
        print("qc real mode is DISABLED in this version: no candidate prompt, no OpenAI call. "
              "Use --mode oracle or --mode replay.", file=sys.stderr)
        return 2
    paths = [Path(p) for p in args.fixture] or fixture_paths(args.which)
    only = {c for c in args.cases.split(",") if c} or None
    cases = [c for p in paths for c in load_cases(p, only)]
    if args.mode == "oracle":
        responder, runs = OracleResponder(), args.runs
    else:
        if not args.replay:
            ap.error("--mode replay needs --replay FILE")
        responses = load_fixture(Path(args.replay))["responses"]
        responder = ReplayResponder(responses)
        runs = max((len(responses.get(c["id"]) or []) for c in cases), default=0) or 1
    records = run_all(cases, responder, runs=runs)
    summary = summarize(records, cases)
    meta = {"eval_version": EVAL_VERSION, "mode": args.mode, "runs": runs,
            "fixture_versions": [load_fixture(p)["fixture_version"] for p in paths],
            "created_at": datetime.now(timezone.utc).isoformat()}
    report = render_markdown(meta, summary)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "records": records},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0 if not summary["failed"] or args.mode != "oracle" else 1


if __name__ == "__main__":
    sys.exit(main())
