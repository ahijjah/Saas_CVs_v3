"""
P0-02a — Phase 3 diagnostic (READ-ONLY): D-01 MATCHED -> final CANNOT_DETERMINE.

Reads results.jsonl from an existing p002a_offline_compare run and, for every
assessment of the selected prompt version (default v10) where
llm_status == MATCHED and final_status == CANNOT_DETERMINE (the F-01 Phase 3
local relevance step), prints:

  - job_code, application_id, criterion_text, D-01 status / evidence / match_reason
  - whether each D-01 evidence item is verbatim in extracted_text and whether it
    is present in the local matcher's candidate texts
  - the local matcher result mapped to the criterion: status, partial_reason,
    role_relevance_score, supporting_evidence
  - job experience.relevant_roles and key_responsibilities (analysis_json)
  - the local matcher's candidate texts (CVFacts role titles + responsibilities)
  - every job-phrase x candidate-text pair: keyword-gate path, token_set_ratio,
    threshold, pass/fail

What it does NOT do:
  - no database writes: the connection is opened with
    default_transaction_read_only=on (checked) and only SELECTs run inside
    READ ONLY transactions;
  - no OpenAI calls, no D-01 re-run, no prompt reads or changes;
  - no rescoring and no change to any stored result.
It deterministically re-runs CVFactsExtractor and CriteriaMatchEngine on the
stored extracted_text (largest file per application, as the comparison did)
solely to show what the local matcher compared.

Output goes to stdout and contains CV-derived text: keep it on the server.

Usage (one-off worker container, see the module notes in the PR / chat):
    python scripts/p002a_phase3_cases.py --results /p002a_out/results.jsonl [--prompt 10]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CD = "CANNOT_DETERMINE"


# ── read-only database access (same guarantees as p002a_offline_compare) ─────

class ReadOnlyDB:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    async def connect(cls, dsn: str) -> "ReadOnlyDB":
        import asyncpg
        conn = await asyncpg.connect(
            dsn, server_settings={"default_transaction_read_only": "on",
                                  "search_path": "cv_analyzer, public"})
        return cls(conn)

    async def fetchrow(self, sql: str, *args):
        async with self.conn.transaction(readonly=True):
            rows = await self.conn.fetch(sql, *args)
        return rows[0] if rows else None

    async def close(self):
        await self.conn.close()


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


async def load_case(db: ReadOnlyDB, application_id: str) -> dict | None:
    app = await db.fetchrow(
        "SELECT job_id FROM applications WHERE application_id = $1", application_id)
    if app is None:
        return None
    text_row = await db.fetchrow(
        "SELECT extracted_text FROM application_files WHERE application_id = $1 "
        "ORDER BY length(coalesce(extracted_text, '')) DESC LIMIT 1", application_id)
    crit = await db.fetchrow(
        "SELECT analysis_json, to_jsonb(job_criteria) ->> 'requirements_schema_version' AS requirements_schema_version "
        "FROM job_criteria WHERE job_id = $1", app["job_id"])
    if not text_row or not crit:
        return None
    from services.requirements_guard import is_requirements_v2
    if is_requirements_v2(crit["requirements_schema_version"], _json(crit["analysis_json"])):
        print(f"SKIPPED requirements-v2 job {app['job_id']}: the legacy phase-3 cases do not support it",
              file=sys.stderr)
        return None
    return {"job_id": str(app["job_id"]), "text": text_row["extracted_text"] or "",
            "analysis_json": _json(crit["analysis_json"])}


# ── local matcher reconstruction ─────────────────────────────────────────────

def _stemmed_keywords() -> set[str]:
    """The keyword set _match_experience uses, read from its own source."""
    import services.criteria_matcher as cm
    src = inspect.getsource(cm._match_experience)
    block = src[src.index("domain_keywords = {"):]
    block = block[:block.index("}") + 1]
    out = set()
    for kw in set(re.findall(r'"([^"]+)"', block)):
        out.add(" ".join(cm._stem_word(w) for w in kw.split()) if " " in kw else cm._stem_word(kw))
    return out


def candidate_texts(facts) -> list[str]:
    """Exactly what _match_experience compares on the CV side."""
    import services.criteria_matcher as cm
    out: list[str] = []
    for e in facts.experience:
        if e.role_title:
            out.append(cm._normalize_text(e.role_title))
        out.extend(cm._normalize_text(r) for r in e.responsibilities)
    return out


def pair_diagnostics(job_reqs: list[str], cand: list[str]) -> list[dict]:
    """Mirror of the _match_experience relevance loop, without early exit."""
    import services.criteria_matcher as cm
    from rapidfuzz import fuzz
    kw = _stemmed_keywords()
    rows = []
    for jr in job_reqs:
        jn = cm._normalize_text(jr)
        jk = {cm._stem_word(t) for t in jn.split()} & kw
        for ct in cand:
            ck = {cm._stem_word(t) for t in ct.split()} & kw
            ratio = round(fuzz.token_set_ratio(jn, ct), 1)
            if jk and ck and (jk & ck):
                path, thr = "shared-keyword", 65
            elif not jk and not ck:
                path, thr = "no-keyword", 85
            else:
                path, thr = "SKIPPED(keyword one side/disjoint)", None
            rows.append({"job_req": jr, "cand": ct, "job_kw": sorted(jk), "cand_kw": sorted(ck),
                         "path": path, "ratio": ratio, "thr": thr,
                         "passes": thr is not None and ratio >= thr})
    return rows


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _norm_quote(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().strip("\"'")).lower()


# ── main ─────────────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    from config import get_settings
    from services.cv_evidence import CVFactsExtractor
    from services.criteria_matcher import CriteriaMatchEngine
    from services.deterministic_scoring import (
        _find_matching_local_criterion,
        _get_field,
        _is_relevance_qualified_experience_criterion,
    )

    backend = Path(__file__).resolve().parent.parent
    print("code identity (compare with the commit the comparison ran on):")
    for rel in ("services/criteria_matcher.py", "services/cv_evidence.py",
                "services/deterministic_scoring.py"):
        print(f"  md5 {rel}: {_md5(backend / rel)}")

    rows = [json.loads(line) for line in Path(args.results).read_text(encoding="utf-8").splitlines()
            if line.strip()]
    targets = [(r, a) for r in rows
               if r.get("ok") and str(r.get("prompt_version")) == str(args.prompt)
               for a in r.get("assessments", [])
               if a.get("llm_status") == "MATCHED" and a.get("final_status") == CD]
    print(f"\nresults.jsonl rows: {len(rows)}; "
          f"v{args.prompt} D-01 MATCHED -> final CANNOT_DETERMINE: {len(targets)}\n")
    if not targets:
        return 0

    db = await ReadOnlyDB.connect(get_settings().database_url.replace(
        "postgresql+asyncpg://", "postgresql://", 1))
    try:
        ro = await db.fetchrow("SHOW default_transaction_read_only")
        if ro[0] != "on":
            raise SystemExit("connection is not read-only; aborting")

        for n, (r, a) in enumerate(targets, 1):
            app_id = r["application_id"]
            print("=" * 110)
            print(f"[{n}/{len(targets)}] job_code={r.get('job_code')} application_id={app_id}")
            print(f"criterion_text: {a['criterion_text']!r} "
                  f"(required={a.get('required')}, relevance-qualified="
                  f"{_is_relevance_qualified_experience_criterion(a['criterion_text'])})")
            print(f"D-01: status={a['llm_status']} match_type={a.get('match_type')} "
                  f"confidence={a.get('confidence')} risk_flags={a.get('risk_flags')}")
            print(f"D-01 match_reason: {a.get('match_reason')!r}")
            print(f"final: status={a['final_status']} cd_reason={a.get('final_cd_reason')}")

            case = await load_case(db, app_id)
            if case is None:
                print("!! extracted_text or job_criteria not found — skipped\n")
                continue
            facts = CVFactsExtractor().extract(case["text"])
            local = CriteriaMatchEngine().match(facts, case["analysis_json"], app_id, case["job_id"])
            local_matches = list(local if isinstance(local, list) else (getattr(local, "matches", None) or []))
            stub = type("Assessment", (), {"required": a.get("required")})()
            lm = _find_matching_local_criterion(a["criterion_text"], local_matches, stub)
            cand = candidate_texts(facts)
            text_n = _norm_quote(case["text"])

            for ev in a.get("supporting_evidence") or []:
                q = _norm_quote(ev)
                print(f"D-01 evidence: {ev!r}\n"
                      f"    verbatim in extracted_text: {q in text_n} | "
                      f"in local candidate_texts: {any(q in c or c in q for c in cand if c)}")

            print(f"CVFacts total_experience_years: {facts.total_experience_years}")
            if lm is None:
                print("LOCAL: no local criterion maps to this criterion "
                      "(Phase 3 would not fire on this reconstruction)")
            else:
                print(f"LOCAL criterion_text: {_get_field(lm, 'criterion_text')!r}")
                print(f"LOCAL status: {_get_field(lm, 'status')}  confidence: {_get_field(lm, 'confidence')}  "
                      f"role_relevance_score: {_get_field(lm, 'role_relevance_score')}")
                print(f"LOCAL partial_reason: {_get_field(lm, 'partial_reason')!r}")
                print(f"LOCAL supporting_evidence: {_get_field(lm, 'supporting_evidence')}")

            exp = (case["analysis_json"] or {}).get("experience", {}) or {}
            roles = list(exp.get("relevant_roles", []) or [])
            resps = list(exp.get("key_responsibilities", []) or [])
            print(f"JOB minimum_years: {exp.get('minimum_years')}")
            print(f"JOB relevant_roles: {roles}")
            print(f"JOB key_responsibilities: {resps}")
            print(f"LOCAL candidate_texts ({len(cand)}):")
            for c in cand:
                print(f"    - {c!r}")

            diags = pair_diagnostics(roles + resps, cand)
            diags.sort(key=lambda d: (d["thr"] is None, -d["ratio"]))
            print(f"pairs: {len(diags)} total | "
                  f"shared-keyword {sum(d['path'] == 'shared-keyword' for d in diags)} | "
                  f"no-keyword {sum(d['path'] == 'no-keyword' for d in diags)} | "
                  f"skipped {sum(d['thr'] is None for d in diags)} | "
                  f"passing {sum(d['passes'] for d in diags)}")
            print(f"top {args.top} pairs (path / ratio / threshold):")
            for d in diags[:args.top]:
                print(f"    {d['path']:<36} {d['ratio']:>5} thr={d['thr']} "
                      f"job={d['job_req']!r} cand={d['cand'][:100]!r} "
                      f"job_kw={d['job_kw']} cand_kw={d['cand_kw']}")
            skipped = [d for d in diags if d["thr"] is None]
            if skipped:
                best_skipped = max(skipped, key=lambda d: d["ratio"])
                print(f"    best SKIPPED pair (never scored by the matcher): ratio={best_skipped['ratio']} "
                      f"job={best_skipped['job_req']!r} cand={best_skipped['cand'][:100]!r} "
                      f"job_kw={best_skipped['job_kw']} cand_kw={best_skipped['cand_kw']}")
            print()
    finally:
        await db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, help="results.jsonl from p002a_offline_compare")
    ap.add_argument("--prompt", default="10", help="prompt version to analyse (default 10)")
    ap.add_argument("--top", type=int, default=8, help="pairs to print per case (default 8)")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
