"""
TEMPORARY — S2 s2-3 semantic-boundary regression against the REAL model.

Status: temporary evaluation script (untracked). Decide to keep or delete it after
review. It changes no production behaviour, reads no database and uses only the
synthetic cases below (no candidate data).

What it does
  * 11 synthetic one-entry CVs (cases A-K) against ONE fixed criterion:
      explicit_role, targets Construction Project Manager / Assistant Project
      Manager, setting construction, criterion text with the duration masked
      ("Minimum [N] years ..."). required_years is never sent to S2.
  * S0 is NOT called: each one-entry structure is built deterministically through
    the real S0 validator with a canned structurer response, so only S2 is measured.
  * Each case is classified INDEPENDENTLY (one S2 request per case) through the
    real services.s2_experience.classify_criterion with the current prompt and
    gpt-4o-mini, cache disabled (fresh calls), repeated --runs times (default 3).
  * Records per classification: case, run, expected, actual, pass, basis, reason,
    missing, quotes, S2 status/outcome, validation errors, finish_reason and token
    usage per call, plus the raw model content of every call.

Output (synthetic content only): <out>/results.jsonl, <out>/summary.json, stdout summary.

Usage (needs OPENAI_API_KEY / app config; makes 11 x runs S2 calls, + repairs):
  python scripts/s2_boundary_regression.py --out /tmp/s2_boundary --runs 3
  python scripts/s2_boundary_regression.py --out /tmp/s2_boundary --dry-run   # no API call
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.experience_accounting import RequirementSpec  # noqa: E402
from services.s0_experience import llm_call  # noqa: E402
from services.s0_experience import structurer as s0  # noqa: E402
from services.s2_experience import classifier as s2  # noqa: E402

PRICE_IN, PRICE_OUT = 0.15, 0.60      # gpt-4o-mini USD per 1M tokens (ASSUMPTION; override with flags)

SPEC = RequirementSpec(
    policy="explicit_role", required_years=5,                     # S4/S5 only: never sent to S2
    targets=("Construction Project Manager", "Assistant Project Manager"), setting="construction",
    spec_version="boundary-regression-1", criterion_id="BOUNDARY.experience.1",
    criterion_text=("Minimum 5 years of experience in a relevant role "
                    "(Construction Project Manager or Assistant Project Manager)"),
    source_spans=("Construction Project Manager", "Assistant Project Manager"))

# (case, title, employer, responsibility, expected label)
CASES = [
    ("A", None, None, "دعم المهام الإدارية والتشغيلية الأساسية تحت إشراف مباشر.", "not_relevant"),
    ("B", "Office Assistant", "BuildCo Construction", "Answered phones and filed documents.", "not_relevant"),
    ("C", None, None, "Coordinated meetings and travel for the sales team.", "not_relevant"),
    ("D", "Customer Service Agent", None, "Handled customer support tickets.", "not_relevant"),
    ("E", "Project Coordinator", "BuildCo Construction",
     "Supported the Construction Project Manager with schedules and RFIs.", "related"),
    ("F", "Site Coordinator", None, "Assisted the Project Manager with site logistics on commercial builds.",
     "related"),
    ("G", "Assistant Project Manager", "SoftwareCo",
     "Supported delivery of enterprise software implementation projects.", "related"),
    ("H", "Site Engineer", "BuildCo Construction",
     "Supervised construction works and coordinated site activities with the project management team.",
     "related"),
    ("I", "Project Assistant", "BuildCo Construction",
     "Supported the construction project management team with schedules, meeting records and project "
     "documentation.", "related"),
    ("J", "Officer", None, "Various duties.", "insufficient"),
    ("K", "Consultant", None, "Advised clients.", "not_relevant"),
]
J_MISSING_OK = {"function", "responsibilities"}


def case_cv(title, employer, resp) -> tuple[str, str]:
    """One-entry CV text + the canned S0 structurer response for it."""
    lines = ["EXPERIENCE"]
    hdr, ent = [], {"anchor_id": "A1", "kind": "employment", "ownership": "certain", "undated_reason": None}
    if title:
        lines.append(title)
        ent["title_line"], ent["title_text"] = len(lines), title
        hdr.append(len(lines))
    if employer:
        lines.append(employer)
        ent["employer_line"], ent["employer_text"] = len(lines), employer
        hdr.append(len(lines))
    lines.append("Jan 2019 - Dec 2021")
    hdr.append(len(lines))
    lines.append(f"- {resp}")
    ent["header_lines"], ent["body_lines"] = hdr, [[len(lines), len(lines)]]
    return "\n".join(lines), json.dumps({"entries": [ent], "ignored_anchors": []})


class _CannedS0:
    """Deterministic S0 'structurer' (no AI): returns the canned structure once."""

    def __init__(self, content):
        msg = SimpleNamespace(content=content)
        resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)

        async def create(**_):
            return resp
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


class _Capture:
    """Pass-through S2 client wrapper: request/response untouched; copies raw content."""

    def __init__(self, inner, sink):
        async def create(**kw):
            r = await inner.chat.completions.create(**kw)
            ch = r.choices[0]
            sink.append({"call": "repair" if any(m.get("role") == "assistant" for m in kw["messages"]) else "main",
                         "finish_reason": getattr(ch, "finish_reason", None), "content": ch.message.content})
            return r
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


async def build_doc(text, canned):
    doc = await s0.build_s0(text, client=_CannedS0(canned), today=date.today())
    if doc.structure_status != "validated" or len(doc.experience_entries()) != 1:
        raise RuntimeError(f"synthetic S0 structure invalid: {doc.structure_status} {doc.validation}")
    return doc


async def run_all(args) -> list[dict]:
    docs = {}
    for case, title, emp, resp, _ in CASES:
        text, canned = case_cv(title, emp, resp)
        docs[case] = (text, await build_doc(text, canned))
    if args.dry_run:
        for case, *_ in CASES:
            text, doc = docs[case]
            print(f"--- case {case}: S2 request (no API call) ---")
            print(json.dumps(s2.build_request(doc, SPEC, text).payload, ensure_ascii=False, indent=1))
        return []
    client = llm_call.create_client()
    rows = []
    for run in range(1, args.runs + 1):
        for case, title, emp, resp, expected in CASES:
            text, doc = docs[case]
            sink: list[dict] = []
            res = await s2.classify_criterion(doc, SPEC, extracted_text=text,
                                              client=_Capture(client, sink), cache=None)
            r = res.results[0] if res.ok and res.results else {}
            log = res.structurer.get("call_log") or []
            row = {"case": case, "run": run, "expected": expected, "actual": r.get("label"),
                   "pass": r.get("label") == expected, "basis": r.get("basis"), "reason": r.get("reason"),
                   "missing": r.get("missing"), "quotes": [q["original_text"] for q in r.get("quotes", [])],
                   "s2_status": res.status, "s2_status_reason": res.status_reason,
                   "outcome": res.structurer.get("outcome"), "validation": res.validation,
                   "calls": [{"call": c.get("call"), "finish_reason": c.get("finish_reason"),
                              "prompt_tokens": c.get("prompt_tokens"),
                              "completion_tokens": c.get("completion_tokens")} for c in log],
                   "raw": sink, "title": title, "employer": emp, "responsibility": resp}
            if case == "J" and row["pass"]:
                row["j_missing_ok"] = bool(J_MISSING_OK & set(row["missing"] or []))
            rows.append(row)
            print(f"run {run} case {case}: expected {expected:13s} actual {str(row['actual']):13s} "
                  f"{'PASS' if row['pass'] else 'FAIL'}  ({res.status}/{row['outcome'] or row['s2_status_reason']})")
    return rows


def summarize(rows, price_in, price_out) -> dict:
    n = len(rows)
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    pt = sum(c["prompt_tokens"] or 0 for r in rows for c in r["calls"])
    ct = sum(c["completion_tokens"] or 0 for r in rows for c in r["calls"])
    return {
        "prompt_version": s2.S2_PROMPT_VERSION, "prompt_fingerprint": s2.prompt_fingerprint(),
        "s2_version": s2.S2_VERSION, "model": s2.S2_MODEL,
        "accuracy": f"{sum(r['pass'] for r in rows)}/{n}",
        "by_case": {c: {"expected": rs[0]["expected"], "actual": [r["actual"] for r in rs],
                        "passes": sum(r["pass"] for r in rs), "runs": len(rs),
                        "stable": len({r["actual"] for r in rs}) == 1} for c, rs in sorted(by_case.items())},
        "confusion": dict(Counter(f"{r['expected']}->{r['actual']}" for r in rows)),
        "unstable_cases": sorted(c for c, rs in by_case.items() if len({r["actual"] for r in rs}) > 1),
        "repairs": sum(1 for r in rows if r["outcome"] == "repaired"),
        "validation_failures": sum(1 for r in rows if r["s2_status"] != "ok"),
        "j_missing_ok": [r.get("j_missing_ok") for r in by_case.get("J", [])],
        "calls": sum(len(r["calls"]) for r in rows),
        "tokens": {"prompt": pt, "completion": ct},
        "estimated_cost_usd": round((pt * price_in + ct * price_out) / 1e6, 5),
        "failures": [r for r in rows if not r["pass"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="print the 11 S2 requests; no API call")
    ap.add_argument("--price-in", type=float, default=PRICE_IN)
    ap.add_argument("--price-out", type=float, default=PRICE_OUT)
    args = ap.parse_args(argv)
    rows = asyncio.run(run_all(args))
    if args.dry_run:
        print("DRY RUN: no OpenAI calls made.")
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = summarize(rows, args.price_in, args.price_out)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, ensure_ascii=False, indent=2))
    for r in summary["failures"]:
        print("\nMISMATCH", json.dumps({k: r[k] for k in ("case", "run", "expected", "actual", "basis", "reason",
                                                         "missing", "quotes", "s2_status", "s2_status_reason",
                                                         "validation", "title", "employer", "responsibility")},
                                       ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
