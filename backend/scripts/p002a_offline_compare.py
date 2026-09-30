"""
P0-02a — offline D-01 prompt comparison: v9 vs v10 (READ-ONLY).

Runs the P0-02a code path (strict validation + one repair call + F-01
verified/upper scoring + recommendation) on the same real CVs twice: once with
the production v9 prompt and once with v10 built from it. Nothing is written
to the database:
  - the connection is opened with default_transaction_read_only=on and every
    query runs inside a READ ONLY transaction;
  - only SELECTs are issued; no prompt is inserted or activated;
  - the D-01 mapper runs with db=None and the qualitative-summary call is
    skipped (it does not affect statuses).
OpenAI IS called (gpt-4o-mini, about 2-4 calls per CV).

Inputs mirror workers/cv_score.py: extracted_text from application_files,
job_criteria.analysis_json and weights, CVFactsExtractor, CriteriaMatchEngine
(for the Phase 3 local relevance check), the det_* settings from system_config.

Outputs (in --out-dir; contain CV-derived text, keep them on the server):
    d01_prompt_v10.txt   v10 built from the DB v9 (for review / later insert)
    v10_build_report.md  builder edit report
    results.jsonl        one line per (application, prompt) with every assessment
    cd_review.tsv        every CANNOT_DETERMINE row, for manual review
    summary.txt          aggregate comparison (no personal data; also printed)

Usage (inside a one-off container from the worker image; see PR/notes):
    python scripts/p002a_offline_compare.py --out-dir /p002a_out --dry-run
    python scripts/p002a_offline_compare.py --out-dir /p002a_out \
        --limit 40 --per-job 3 --include-job JOB-2026-0110 --v10-max-tokens 12000
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PROMPT_CODE = "recruitment.criteria_mapping"
V9_MD5_EXPECTED = "18de95b0f4559a9383aa2136db64ccba"   # md5(system_prompt || '\n'), exported 2026-09-30
CD = "CANNOT_DETERMINE"

_SAMPLE_SQL = """
WITH v9 AS (
    SELECT a.application_id, a.job_id, j.job_code, a.scored_at,
           row_number() OVER (PARTITION BY a.job_id ORDER BY a.scored_at DESC) AS rn
    FROM application_scores s
    JOIN applications a ON a.application_id = s.application_id
    JOIN jobs j         ON j.job_id = a.job_id
    WHERE s.llm_match_results_json ->> 'prompt_version' = '9'
      AND a.processing_status = 'ai_scored'
      AND EXISTS (SELECT 1 FROM application_files f
                  WHERE f.application_id = a.application_id
                    AND length(coalesce(f.extracted_text, '')) >= 500)
)
SELECT application_id, job_id, job_code
FROM v9
WHERE rn <= $1 OR job_code = ANY($2::text[])
ORDER BY (job_code = ANY($2::text[])) DESC, scored_at DESC
LIMIT $3
"""


# ── read-only database access ────────────────────────────────────────────────

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

    async def fetch(self, sql: str, *args) -> list:
        async with self.conn.transaction(readonly=True):
            return await self.conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def close(self):
        await self.conn.close()


def _plain_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


# ── inputs ───────────────────────────────────────────────────────────────────

async def load_prompt_v9(db: ReadOnlyDB) -> dict:
    row = await db.fetchrow(
        "SELECT version, system_prompt, model, temperature, max_tokens, output_language "
        "FROM ai_prompts WHERE prompt_code = $1 AND version = 9", PROMPT_CODE)
    if row is None:
        raise SystemExit("prompt v9 not found")
    return dict(row)


async def load_det_config(db: ReadOnlyDB):
    from services.deterministic_scoring import DeterministicScoringConfig
    rows = await db.fetch("SELECT key, value FROM system_config WHERE key LIKE 'scoring_v2.det_%'")
    m = {r["key"]: r["value"] for r in rows}

    def f(k, d):
        return float(m[k]) if k in m else d

    return DeterministicScoringConfig(
        partial_credit=f("scoring_v2.det_partial_credit", 0.50),
        required_weight=f("scoring_v2.det_required_weight", 0.70),
        preferred_weight=f("scoring_v2.det_preferred_weight", 0.30),
        enable_required_absent_floor=m.get("scoring_v2.det_enable_required_absent_floor", "false").lower() == "true",
        required_absent_floor_threshold=f("scoring_v2.det_required_absent_floor_threshold", 0.50),
        required_absent_floor_cap=f("scoring_v2.det_required_absent_floor_cap", 0.40),
    )


async def load_case(db: ReadOnlyDB, application_id, job_id) -> dict | None:
    text_row = await db.fetchrow(
        "SELECT extracted_text FROM application_files WHERE application_id = $1 "
        "ORDER BY length(coalesce(extracted_text, '')) DESC LIMIT 1", application_id)
    crit = await db.fetchrow("SELECT * FROM job_criteria WHERE job_id = $1", job_id)
    if not text_row or not crit:
        return None
    weights = {k: crit[k] or 0 for k in (
        "weight_skills", "weight_experience", "weight_education", "weight_certifications",
        "weight_soft_skills", "weight_domain_knowledge", "weight_other")}
    return {"text": text_row["extracted_text"], "analysis_json": _json(crit["analysis_json"]),
            "weights": weights}


# ── one D-01 + F-01 run ──────────────────────────────────────────────────────

class _CountingClient:
    """Wraps the real mapper client and counts chat.completions.create calls."""

    def __init__(self, real):
        self.calls = 0
        outer = self

        class _Completions:
            async def create(self_inner, **kw):
                outer.calls += 1
                return await real.chat.completions.create(**kw)

        self.chat = type("Chat", (), {"completions": _Completions()})()


async def run_one(prompt_cfg: dict, case: dict, app_id: str, job_id: str, det_cfg, cv_facts, local) -> dict:
    import services.llm_criteria_mapper as m
    from services.deterministic_scoring import DeterministicScoringEngine, deterministic_score_to_dict

    client = _CountingClient(m._get_mapper_client())
    t0 = time.monotonic()
    out: dict = {"application_id": app_id, "prompt_version": prompt_cfg["version"]}

    async def _no_summary(*a, **k):
        return None

    async def _prompt(*a, **k):
        return dict(prompt_cfg)

    with patch.object(m, "_get_mapper_client", return_value=client), \
         patch.object(m, "_generate_qualitative_summary", _no_summary), \
         patch("services.ai_service.load_active_prompt", _prompt):
        try:
            res = await m.LLMCriteriaMapper().assess(
                cv_facts=cv_facts, analysis_json=case["analysis_json"], raw_cv_text=case["text"],
                application_id=app_id, job_id=job_id, db=None)
        except m.CriteriaMappingResponseError as exc:
            out.update(ok=False, error=f"validation: {exc}"[:500], calls=client.calls,
                       seconds=round(time.monotonic() - t0, 1))
            return out
        except Exception as exc:  # network/API errors are reported, not retried here
            out.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:500], calls=client.calls,
                       seconds=round(time.monotonic() - t0, 1))
            return out

    det = deterministic_score_to_dict(DeterministicScoringEngine(det_cfg).score(res, case["weights"], local))
    crit_by_text = {c["criterion_text"]: c for d in det["dimensions"].values() for c in d["criteria"]}
    out.update(
        ok=True, calls=client.calls, repair_used=client.calls > 1,
        seconds=round(time.monotonic() - t0, 1),
        verified=det["verified_score"], upper=det["upper_score"], pending=det["pending_points"],
        decision_verified_basis=det["decision_verified_basis"],
        decision_if_verified=det["decision_if_verified"], recommendation=det["recommendation"],
        assessments=[{
            "criterion_text": a.criterion_text, "dimension": a.dimension, "required": a.required,
            "llm_status": a.status, "cd_reason": a.cd_reason, "confidence": a.confidence,
            "match_type": a.match_type, "risk_flags": a.risk_flags, "match_reason": a.match_reason,
            "final_status": (crit_by_text.get(a.criterion_text) or {}).get("status"),
        } for a in res.assessments],
    )
    return out


# ── summary ──────────────────────────────────────────────────────────────────

def summarise(results: list[dict], sample: list[dict]) -> str:
    lines: list[str] = []
    by_v: dict[str, list[dict]] = collections.defaultdict(list)
    for r in results:
        by_v[str(r["prompt_version"])].append(r)

    lines.append(f"Sample: {len(sample)} applications from "
                 f"{len({s['job_code'] for s in sample})} jobs")
    for v in sorted(by_v, key=int):
        rs = by_v[v]
        ok = [r for r in rs if r["ok"]]
        lines.append(f"\n=== prompt v{v} ===")
        lines.append(f"runs {len(rs)} | valid {len(ok)} | failed {len(rs) - len(ok)} | "
                     f"repair used {sum(1 for r in ok if r.get('repair_used'))} | "
                     f"LLM calls {sum(r.get('calls', 0) for r in rs)}")
        for r in rs:
            if not r["ok"]:
                lines.append(f"  FAILED {r['application_id']}: {r['error'][:160]}")
        mix: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
        cd_reasons = collections.Counter()
        for r in ok:
            for a in r["assessments"]:
                grp = "required" if a["required"] else "preferred"
                mix[(grp, a["dimension"])][a["final_status"]] += 1
                mix[(grp, "ALL")][a["final_status"]] += 1
                if a["final_status"] == CD:
                    cd_reasons[a["cd_reason"] or "?"] += 1
        lines.append("status mix (final, after F-01) — MATCHED / PARTIAL / ABSENT / CD / total, CD%:")
        for key in sorted(mix, key=lambda k: (k[0], k[1] != "ALL", k[1])):
            c = mix[key]
            tot = sum(c.values())
            lines.append(f"  {key[0]:9} {key[1]:16} {c['MATCHED']:5} {c['PARTIAL']:5} {c['ABSENT']:5} "
                         f"{c[CD]:5} {tot:6}  {100 * c[CD] / tot if tot else 0:5.1f}%")
        lines.append(f"cd_reason: {dict(cd_reasons)}")
        lines.append(f"recommendation: {dict(collections.Counter(r['recommendation'] for r in ok))}")
        if ok:
            lines.append(f"mean verified {sum(r['verified'] for r in ok) / len(ok):.1f} | "
                         f"mean upper {sum(r['upper'] for r in ok) / len(ok):.1f} | "
                         f"apps with pending>0 {sum(1 for r in ok if r['pending'] > 0)}")

    if "9" in by_v and "10" in by_v:
        v9 = {r["application_id"]: r for r in by_v["9"] if r["ok"]}
        v10 = {r["application_id"]: r for r in by_v["10"] if r["ok"]}
        both = sorted(set(v9) & set(v10))
        trans = collections.Counter((v9[a]["recommendation"], v10[a]["recommendation"]) for a in both)
        changed = [a for a in both if v9[a]["recommendation"] != v10[a]["recommendation"]]
        lines.append(f"\n=== v9 → v10 (both valid: {len(both)}) ===")
        lines.append(f"recommendation changed: {len(changed)}")
        for (a, b), n in sorted(trans.items()):
            if a != b:
                lines.append(f"  {a:>18} → {b:<18} {n}")
        if both:
            dv = [v10[a]["verified"] - v9[a]["verified"] for a in both]
            lines.append(f"verified score change v10−v9: mean {sum(dv) / len(dv):+.1f}, "
                         f"min {min(dv):+d}, max {max(dv):+d}")
        lines.append("changed applications: " + ", ".join(changed))
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────

async def main_async(args) -> int:
    from config import get_settings
    from build_d01_prompt_v10 import build_v10

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db = await ReadOnlyDB.connect(_plain_dsn(get_settings().database_url))
    try:
        ro = await db.fetchrow("SHOW default_transaction_read_only")
        if ro[0] != "on":
            raise SystemExit("connection is not read-only; aborting")

        v9 = await load_prompt_v9(db)
        md5 = hashlib.md5((v9["system_prompt"] + "\n").encode("utf-8")).hexdigest()
        if md5 != args.v9_md5:
            raise SystemExit(f"v9 md5 {md5} != expected {args.v9_md5}; v9 changed — stop and review")
        built = build_v10(v9["system_prompt"])
        (out / "v10_build_report.md").write_text(
            "applied:\n" + "\n".join(built.applied) + "\n\nnot found:\n" + "\n".join(built.not_found)
            + "\n\nreview:\n" + "\n".join(built.review) + "\n\nerrors:\n" + "\n".join(built.errors) + "\n",
            encoding="utf-8")
        if built.errors:
            raise SystemExit(f"v10 build failed: {built.errors}")
        (out / "d01_prompt_v10.txt").write_text(built.text, encoding="utf-8")
        prompts = [
            {"prompt_code": PROMPT_CODE, "version": 9, "system_prompt": v9["system_prompt"],
             "model": v9["model"], "temperature": float(v9["temperature"]),
             "max_tokens": int(v9["max_tokens"]), "output_language": v9["output_language"]},
            {"prompt_code": PROMPT_CODE, "version": 10, "system_prompt": built.text,
             "model": v9["model"], "temperature": float(v9["temperature"]),
             "max_tokens": int(args.v10_max_tokens or v9["max_tokens"]),
             "output_language": v9["output_language"]},
        ]

        rows = await db.fetch(_SAMPLE_SQL, args.per_job, args.include_job, args.limit)
        sample = [{"application_id": str(r["application_id"]), "job_id": str(r["job_id"]),
                   "job_code": r["job_code"]} for r in rows]
        print(f"v9 md5 OK; v10 built ({len(built.applied)} edits). Sample: {len(sample)} applications, "
              f"{len({s['job_code'] for s in sample})} jobs; v10 max_tokens={prompts[1]['max_tokens']}")
        for s in sample:
            print(f"  {s['job_code']}  {s['application_id']}")
        if args.dry_run:
            print("dry run: no OpenAI calls made")
            return 0

        det_cfg = await load_det_config(db)
        cases = {}
        for s in sample:
            case = await load_case(db, s["application_id"], s["job_id"])
            if case:
                cases[s["application_id"]] = case
    finally:
        await db.close()

    from services.cv_evidence import CVFactsExtractor
    from services.criteria_matcher import CriteriaMatchEngine

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def one(s, prompt):
        case = cases.get(s["application_id"])
        if case is None:
            return
        async with sem:
            facts = CVFactsExtractor().extract(case["text"])
            local = CriteriaMatchEngine().match(facts, case["analysis_json"], s["application_id"], s["job_id"])
            r = await run_one(prompt, case, s["application_id"], s["job_id"], det_cfg, facts, local)
            r["job_code"] = s["job_code"]
            results.append(r)
            print(f"  v{prompt['version']:<2} {s['job_code']} {s['application_id']} "
                  f"{'OK ' + r['recommendation'] if r['ok'] else 'FAILED'} calls={r['calls']} {r['seconds']}s",
                  flush=True)

    await asyncio.gather(*(one(s, p) for s in sample for p in prompts))

    with open(out / "results.jsonl", "w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda r: (r["application_id"], r["prompt_version"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "cd_review.tsv", "w", encoding="utf-8") as f:
        f.write("prompt\tjob_code\tapplication_id\trequired\tdimension\tcriterion\tcd_reason\tmatch_reason\n")
        for r in results:
            for a in r.get("assessments", []):
                if a["final_status"] == CD:
                    f.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in (
                        r["prompt_version"], r["job_code"], r["application_id"], a["required"],
                        a["dimension"], a["criterion_text"], a["cd_reason"], a["match_reason"][:300])) + "\n")
    summary = summarise(results, sample)
    (out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    print("\n" + summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--per-job", type=int, default=3)
    ap.add_argument("--include-job", action="append", default=[],
                    help="job_code to always include (repeatable)")
    ap.add_argument("--v10-max-tokens", type=int, default=0, help="0 = same as v9")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--v9-md5", default=V9_MD5_EXPECTED)
    ap.add_argument("--dry-run", action="store_true", help="select the sample and build v10; no OpenAI calls")
    args = ap.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
