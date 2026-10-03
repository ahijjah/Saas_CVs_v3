"""
READ-ONLY inspection: real jobs and their stored experience-related criteria.

Purpose: pick real job -> real experience criterion pairs for the offline S2
evaluation. It only EXPOSES what is stored; it does not infer or generate S1
RequirementSpecs and makes no OpenAI calls.

Sources (all existing, used by the scoring pipeline):
  jobs                             job_code, title, status, created_at
  job_criteria.analysis_json       experience{minimum_years, requirement_type,
                                   relevant_roles, key_responsibilities, ...},
                                   domain_knowledge[], other_requirements[],
                                   scoring_weights
  job_criteria (columns)           experience[] / domain_knowledge[] /
                                   other_requirements[] arrays, weight_* columns
  application_scores.llm_match_results_json
                                   the criterion texts D-01 actually assessed
                                   (dimension, required, criterion_class), with
                                   the number of applications per text
  applications / application_files counts only (no names, emails or CV text)

The "pipeline criterion" lines mirror llm_criteria_mapper._flatten_criteria's
experience branch exactly (years+roles -> one unified criterion; years only ->
one total-years criterion; roles only -> one preferred criterion per role).
key_responsibilities are shown but are NOT scored criteria (Issue #12).

Weights: the schema stores weights per DIMENSION (weight_experience, ...), not
per criterion; inside a dimension the deterministic scorer splits required /
preferred by system_config (defaults 0.70 / 0.30).

Guarantees: one connection with default_transaction_read_only=on (verified via
SHOW before any query); SELECTs only, each inside a READ ONLY transaction;
nothing is written anywhere. Self-contained (needs only asyncpg + the app's
DATABASE_URL), so it can run inside the existing api/worker image via stdin:

  cd /opt/cv-analyzer/backend
  docker compose exec -T api python - < /path/to/inspect_job_experience_criteria.py
  docker compose exec -T api python - --min-apps 5 < /path/to/inspect_job_experience_criteria.py
  docker compose exec -T api python - --json < ... > /tmp/jobs_experience.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import textwrap
from typing import Any

MIN_TEXT_CHARS = 800          # same default as the S2 evaluation sample
EXPERIENCE_HINT = re.compile(
    r"experience|years?|yrs|track record|background in|worked|exposure|سنوات|سنة|خبرة|خبرات", re.I)

JOBS_SQL = """
SELECT j.job_id::text AS job_id, j.job_code, j.title, j.status, j.created_at,
       jc.analysis_json, jc.experience AS col_experience, jc.domain_knowledge AS col_domain,
       jc.other_requirements AS col_other,
       jc.weight_experience, jc.weight_domain_knowledge, jc.weight_other,
       jc.criteria_extraction_status, jc.last_edited_at,
       (SELECT count(*) FROM applications a WHERE a.job_id = j.job_id) AS apps,
       (SELECT count(*) FROM applications a WHERE a.job_id = j.job_id AND EXISTS (
            SELECT 1 FROM application_files f
            WHERE f.application_id = a.application_id AND f.extraction_status = 'done'
              AND length(coalesce(f.extracted_text, '')) >= $1)) AS apps_with_text,
       (SELECT count(*) FROM applications a JOIN application_scores s ON s.application_id = a.application_id
         WHERE a.job_id = j.job_id) AS apps_scored
FROM jobs j
LEFT JOIN job_criteria jc ON jc.job_id = j.job_id
ORDER BY j.job_code NULLS LAST, j.created_at
"""

SCORED_SQL = """
SELECT a.job_id::text AS job_id, e->>'criterion_text' AS criterion_text, e->>'dimension' AS dimension,
       e->>'required' AS required, e->>'criterion_class' AS criterion_class,
       count(*) AS applications
FROM applications a
JOIN application_scores s ON s.application_id = a.application_id
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(s.llm_match_results_json -> 'assessments') = 'array'
         THEN s.llm_match_results_json -> 'assessments' ELSE '[]'::jsonb END) e
WHERE e->>'dimension' IN ('experience', 'domain_knowledge')
   OR e->>'criterion_class' IN ('experience', 'domain_knowledge')
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 3, 6 DESC, 2
"""


class ReadOnlyDB:
    def __init__(self, conn):
        self.conn = conn

    @classmethod
    async def connect(cls, dsn: str) -> "ReadOnlyDB":
        import asyncpg
        conn = await asyncpg.connect(dsn, server_settings={"default_transaction_read_only": "on",
                                                           "search_path": "cv_analyzer, public"})
        ro = await conn.fetchval("SHOW default_transaction_read_only")
        if ro != "on":
            await conn.close()
            raise RuntimeError(f"connection is not read-only (default_transaction_read_only={ro!r})")
        return cls(conn)

    async def fetch(self, sql: str, *args):
        async with self.conn.transaction(readonly=True):
            return await self.conn.fetch(sql, *args)

    async def close(self):
        await self.conn.close()


def _dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        try:
            sys.path.insert(0, os.getcwd())
            from config import get_settings
            url = get_settings().database_url
        except Exception as exc:                       # pragma: no cover - environment specific
            raise SystemExit(f"DATABASE_URL not set and config unavailable: {exc}")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _json(v: Any) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def pipeline_experience_criteria(analysis: dict) -> list[dict]:
    """Mirror of llm_criteria_mapper._flatten_criteria (experience branch only)."""
    exp = analysis.get("experience") or {}
    min_years = exp.get("minimum_years", 0)
    roles = [str(r) for r in (exp.get("relevant_roles") or []) if r]
    has_years = bool(min_years) and isinstance(min_years, (int, float)) and min_years > 0
    out: list[dict] = []
    if has_years:
        rt = exp.get("requirement_type")
        required = rt != "preferred" if rt else True
        if roles:
            roles_str = roles[0] if len(roles) == 1 else f"{', '.join(roles[:-1])} or {roles[-1]}"
            out.append({"text": f"Minimum {min_years} years of experience in a relevant role ({roles_str})",
                        "required": required, "shape": "years+roles", "min_years": min_years})
        else:
            out.append({"text": f"Minimum {min_years} years of relevant experience",
                        "required": required, "shape": "years_only (total years)", "min_years": min_years})
    elif roles:
        for r in roles:
            out.append({"text": r, "required": False, "shape": "role_only (no years)", "min_years": None})
    return out


def build_report(jobs: list[dict], scored: list[dict], min_apps: int) -> list[dict]:
    by_job: dict[str, list[dict]] = {}
    for r in scored:
        by_job.setdefault(r["job_id"], []).append(r)
    out = []
    for j in jobs:
        if j["apps"] < min_apps:
            continue
        a = _json(j["analysis_json"])
        exp = a.get("experience") if isinstance(a.get("experience"), dict) else {}
        other = [str(x) for x in (a.get("other_requirements") or []) if x]
        col_other = [str(x) for x in (j["col_other"] or [])]
        out.append({
            "job_id": j["job_id"], "job_code": j["job_code"], "title": j["title"], "status": j["status"],
            "created_at": j["created_at"].isoformat() if j["created_at"] else None,
            "applications": j["apps"], "applications_with_text": j["apps_with_text"],
            "applications_scored": j["apps_scored"],
            "criteria_extraction_status": j["criteria_extraction_status"],
            "criteria_last_edited_at": j["last_edited_at"].isoformat() if j["last_edited_at"] else None,
            "weights": {"experience": j["weight_experience"], "domain_knowledge": j["weight_domain_knowledge"],
                        "other": j["weight_other"], "analysis_json.scoring_weights": a.get("scoring_weights")},
            "analysis_experience": {
                "minimum_years": exp.get("minimum_years"),
                "requirement_type": exp.get("requirement_type"),
                "relevant_roles": exp.get("relevant_roles") or [],
                "key_responsibilities": exp.get("key_responsibilities") or [],
                "other_keys": {k: v for k, v in exp.items() if k not in (
                    "minimum_years", "requirement_type", "relevant_roles", "key_responsibilities")},
            },
            "pipeline_experience_criteria": pipeline_experience_criteria(a),
            "analysis_domain_knowledge": [str(x) for x in (a.get("domain_knowledge") or []) if x],
            "analysis_other_requirements_experience_like": [x for x in other if EXPERIENCE_HINT.search(x)],
            "column_experience": [str(x) for x in (j["col_experience"] or [])],
            "column_domain_knowledge": [str(x) for x in (j["col_domain"] or [])],
            "column_other_requirements_experience_like": [x for x in col_other if EXPERIENCE_HINT.search(x)],
            "scored_criteria_d01": [
                {"criterion_text": r["criterion_text"], "dimension": r["dimension"], "required": r["required"],
                 "criterion_class": r["criterion_class"], "applications": r["applications"]}
                for r in by_job.get(j["job_id"], [])],
        })
    return out


def _wrap(s: str, indent: str) -> str:
    return textwrap.fill(s, width=110, initial_indent=indent, subsequent_indent=indent + "  ")


def render_text(report: list[dict], scored_available: bool) -> str:
    L = [f"READ-ONLY job / experience-criteria inspection — {len(report)} job(s)", ""]
    L.append(f"{'job_code':<16} {'apps':>5} {'text':>5} {'scored':>6}  {'min_yrs':>7}  title")
    for r in report:
        L.append(f"{str(r['job_code']):<16} {r['applications']:>5} {r['applications_with_text']:>5} "
                 f"{r['applications_scored']:>6}  {str(r['analysis_experience']['minimum_years']):>7}  {r['title']}")
    L.append("")
    for r in report:
        ae = r["analysis_experience"]
        L += ["=" * 110,
              f"{r['job_code']}  {r['title']}  [{r['status']}]  job_id={r['job_id']}  created={r['created_at']}",
              f"applications={r['applications']}  with_text(>={MIN_TEXT_CHARS} chars)={r['applications_with_text']}"
              f"  scored={r['applications_scored']}  criteria_extraction={r['criteria_extraction_status']}"
              f"  last_edited={r['criteria_last_edited_at']}",
              f"weights: experience={r['weights']['experience']}  domain_knowledge={r['weights']['domain_knowledge']}"
              f"  other={r['weights']['other']}  (per dimension; no per-criterion weights stored)",
              "",
              "analysis_json.experience:",
              f"  minimum_years={ae['minimum_years']!r}  requirement_type={ae['requirement_type']!r}"]
        L.append("  relevant_roles:" + ("" if ae["relevant_roles"] else " (none)"))
        L += [_wrap(f"- {x}", "    ") for x in ae["relevant_roles"]]
        L.append("  key_responsibilities (context only, not scored):" + ("" if ae["key_responsibilities"] else " (none)"))
        L += [_wrap(f"- {x}", "    ") for x in ae["key_responsibilities"]]
        if ae["other_keys"]:
            L.append(_wrap(f"other keys: {json.dumps(ae['other_keys'], ensure_ascii=False)}", "  "))
        L.append("pipeline experience criteria (as _flatten_criteria builds them):" +
                 ("" if r["pipeline_experience_criteria"] else " (none)"))
        for c in r["pipeline_experience_criteria"]:
            L.append(_wrap(f"- [{'required' if c['required'] else 'preferred'}] [{c['shape']}] {c['text']}", "    "))
        L.append("analysis_json.domain_knowledge (scored as domain_knowledge, preferred):" +
                 ("" if r["analysis_domain_knowledge"] else " (none)"))
        L += [_wrap(f"- {x}", "    ") for x in r["analysis_domain_knowledge"]]
        if r["analysis_other_requirements_experience_like"]:
            L.append("analysis_json.other_requirements mentioning experience/years (scored as other, preferred):")
            L += [_wrap(f"- {x}", "    ") for x in r["analysis_other_requirements_experience_like"]]
        if r["column_experience"] or r["column_domain_knowledge"] or r["column_other_requirements_experience_like"]:
            L.append("job_criteria array columns (editable copies):")
            L += [_wrap(f"- experience: {x}", "    ") for x in r["column_experience"]]
            L += [_wrap(f"- domain_knowledge: {x}", "    ") for x in r["column_domain_knowledge"]]
            L += [_wrap(f"- other (experience-like): {x}", "    ")
                  for x in r["column_other_requirements_experience_like"]]
        if not scored_available:
            L.append("criteria actually assessed by D-01: (llm_match_results_json not available)")
        else:
            L.append("criteria actually assessed by D-01 (experience / domain_knowledge, stored results):" +
                     ("" if r["scored_criteria_d01"] else " (none stored)"))
            for c in r["scored_criteria_d01"]:
                req = {"true": "required", "false": "preferred"}.get(str(c["required"]).lower(), c["required"])
                L.append(_wrap(f"- [{c['dimension']}/{c['criterion_class']}] [{req}] x{c['applications']} apps: "
                               f"{c['criterion_text']}", "    "))
        L.append("")
    return "\n".join(L)


async def main_async(args) -> int:
    db = await ReadOnlyDB.connect(_dsn())
    try:
        jobs = [dict(r) for r in await db.fetch(JOBS_SQL, MIN_TEXT_CHARS)]
        try:
            scored = [dict(r) for r in await db.fetch(SCORED_SQL)]
            scored_available = True
        except Exception as exc:                       # e.g. column absent on an old schema
            print(f"note: stored D-01 criteria unavailable ({type(exc).__name__}: {exc})", file=sys.stderr)
            scored, scored_available = [], False
    finally:
        await db.close()
    if args.job_code:
        wanted = set(args.job_code)
        jobs = [j for j in jobs if j["job_code"] in wanted]
    report = build_report(jobs, scored, args.min_apps)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report, scored_available))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-apps", type=int, default=0, help="only jobs with at least this many applications")
    ap.add_argument("--job-code", action="append", help="only these job codes (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
