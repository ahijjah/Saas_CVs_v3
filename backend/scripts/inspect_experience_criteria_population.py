"""
READ-ONLY diagnostic: how are experience criteria structured across existing jobs,
and where could S2 RELATED vs NOT_RELEVANT change a criterion's status under S5?

Data source (the same one the scoring pipeline uses):
  jobs (job_code, title, status) LEFT JOIN job_criteria (analysis_json,
  criteria_extraction_status). D-01 builds its criteria with
  llm_criteria_mapper._flatten_criteria(analysis_json); its experience branch is
  mirrored here exactly (years+roles -> one unified criterion; years only -> one
  total-years criterion; roles only -> one PREFERRED criterion per role).
  Additionally inspected (reported separately, never merged):
    analysis_json.other_requirements  items that read like experience requirements
                                      (D-01 scores them in the "other" dimension today)
    analysis_json.domain_knowledge    reported as SETTING/DOMAIN EVIDENCE only (these are
                                      knowledge criteria, not experience criteria)

S1 does not exist: no criterion carries a stored policy. Every "inferred_policy" below is
a DIAGNOSTIC inference (explicit_role / functional / sector / pure_duration / UNKNOWN)
with a confidence and the rule that produced it. Original criterion text is preserved.

RELATED impact under current S5 (services/experience_accounting.py):
  semantic policy + NO threshold   -> N1/N3/N4: qualifying MATCHED, related PARTIAL,
                                      not_relevant ABSENT  => related_impact "scoring"
  semantic policy + threshold      -> only qualifying time counts; related-only ABSENT (T5)
                                      => related_impact "audit_only"
  pure_duration                    -> S2 is not run           => "not_applicable"
  UNKNOWN                          -> "unknown" (manual review)

Guarantees: one connection with default_transaction_read_only=on (verified with SHOW);
SELECT only, inside READ ONLY transactions; no writes; no OpenAI; no CV text; no
candidate data. Self-contained (asyncpg + DATABASE_URL / app config) so it can run in the
existing api image via stdin:

  cd /opt/cv-analyzer/backend
  docker compose exec -T api python - < /path/to/inspect_experience_criteria_population.py
  docker compose exec -T api python - --json < /path/to/... > /tmp/experience_population.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.requirements_guard import IS_V2_SQL  # noqa: E402  (requirements-v2 jobs are skipped explicitly)

POLICIES = ("explicit_role", "functional", "sector", "pure_duration", "UNKNOWN")
SEMANTIC = ("explicit_role", "functional", "sector")

JOBS_SQL = """
SELECT j.job_id::text AS job_id, j.job_code, j.title, j.status,
       jc.analysis_json, jc.criteria_extraction_status
FROM jobs j
LEFT JOIN job_criteria jc ON jc.job_id = j.job_id
WHERE NOT COALESCE(""" + IS_V2_SQL + """, FALSE)      -- requirements-v2 jobs are skipped explicitly (legacy-shape report)
ORDER BY j.job_code NULLS LAST, j.created_at
"""

# ── duration threshold detection (text) ─────────────────────────────────────

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20}
_NUM = r"(\d+(?:\.\d+)?|" + "|".join(_WORDS) + r")"
_THRESHOLD_RE = re.compile(
    _NUM + r"\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*" + _NUM + r"\s*)?(?:\+\s*)?"
    r"(years?|yrs?|months?|سنوات|سنة|سنين|أشهر|اشهر|شهر|شهور)", re.I)
_AR_DUAL = re.compile(r"(سنتين|سنتان|عامين|عامان)")
_EXPERIENCE_WORD = re.compile(r"experience|experienced|exposure|track record|worked|working as|خبرة|خبرات",
                              re.I)


def parse_threshold(text: str | None) -> dict | None:
    """First explicit duration threshold in the text -> {value, unit, raw} (lower bound of
    a range), else None. Deterministic; English, Arabic digits/units, '5+', '3-5'."""
    if not text:
        return None
    t = text.translate(_AR_DIGITS)
    m = _THRESHOLD_RE.search(t)
    if m:
        raw_num = m.group(1).lower()
        value = float(_WORDS.get(raw_num, raw_num))
        unit = m.group(3).lower()
        unit = "months" if unit.startswith(("month", "أشهر", "اشهر", "شهر", "شهور")) else "years"
        return {"value": value, "unit": unit, "raw": m.group(0).strip()}
    m = _AR_DUAL.search(t)
    if m:
        return {"value": 2.0, "unit": "years", "raw": m.group(0)}
    return None


# ── conservative policy inference for free-text experience requirements ─────

_SECTOR_RE = re.compile(r"\b(?:in|within|across)\s+(?:the\s+|a\s+|an\s+)?[\w&/ ,-]{2,50}?\s+"
                        r"(?:sector|industry|industries|field|environment|organi[sz]ations?|context)\b"
                        r"|قطاع|القطاع", re.I)
_ROLE_RE = re.compile(r"\b(?:experience|worked|working)\s+as\s+(?:an?\s+)?[A-Z]|\bas\s+an?\s+[A-Z][a-z]+", re.U)
_FUNCTION_RE = re.compile(r"\bexperience\s+(?:in|with|of|managing|leading|working on|delivering)\s+\w+"
                          r"|\bخبرة\s+في\s+\S+", re.I)
_DURATION_ONLY_RE = re.compile(r"^\W*(?:minimum|min\.?|at least|over|more than)?\s*" + _NUM +
                               r"\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:total\s+|overall\s+|professional\s+|"
                               r"relevant\s+|work\s+)?experience\W*$", re.I)


def infer_text_policy(text: str) -> tuple[str, str, str]:
    """(policy, confidence, reason) for a free-text requirement. Order matters; anything
    not matched by a deliberately narrow rule is UNKNOWN."""
    t = (text or "").strip()
    if _DURATION_ONLY_RE.match(t.translate(_AR_DIGITS)):
        return "pure_duration", "medium", "only a duration + generic 'experience' (no role/function/sector)"
    if _SECTOR_RE.search(t):
        return "sector", "medium", "text names a sector/industry/field/environment"
    if _ROLE_RE.search(t):
        return "explicit_role", "medium", "text requires experience AS a named role"
    if _FUNCTION_RE.search(t):
        return "functional", "low", "text requires experience IN/WITH an activity or subject"
    return "UNKNOWN", "none", "no narrow rule matched"


def _json(v: Any) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def related_impact(policy: str, has_threshold: bool) -> str:
    if policy == "pure_duration":
        return "not_applicable"
    if policy == "UNKNOWN":
        return "unknown"
    return "audit_only" if has_threshold else "scoring"


def pipeline_experience_rows(analysis: dict) -> list[dict]:
    """The experience criteria D-01 actually scores (mirror of _flatten_criteria's
    experience branch), each with a diagnostic policy inference."""
    exp = analysis.get("experience") if isinstance(analysis.get("experience"), dict) else {}
    min_years = exp.get("minimum_years", 0)
    roles = [str(r) for r in (exp.get("relevant_roles") or []) if r]
    has_years = bool(min_years) and isinstance(min_years, (int, float)) and min_years > 0
    rows: list[dict] = []
    if has_years:
        rt = exp.get("requirement_type")
        required = rt != "preferred" if rt else True
        if roles:
            roles_str = roles[0] if len(roles) == 1 else f"{', '.join(roles[:-1])} or {roles[-1]}"
            rows.append({"criterion_text": f"Minimum {min_years} years of experience in a relevant role ({roles_str})",
                         "required": required, "source": "analysis_json.experience (minimum_years + relevant_roles)",
                         "has_threshold": True, "threshold_value": float(min_years), "threshold_unit": "years",
                         "relevant_roles": roles, "inferred_policy": "explicit_role", "confidence": "high",
                         "reason": "structured relevant_roles + minimum_years (pipeline unified criterion)"})
        else:
            rows.append({"criterion_text": f"Minimum {min_years} years of relevant experience",
                         "required": required, "source": "analysis_json.experience (minimum_years only)",
                         "has_threshold": True, "threshold_value": float(min_years), "threshold_unit": "years",
                         "relevant_roles": [], "inferred_policy": "pure_duration", "confidence": "low",
                         "reason": "years without roles; the text says 'relevant experience', so it may also "
                                   "mean domain-relevant experience"})
    elif roles:
        for r in roles:
            rows.append({"criterion_text": r, "required": False,
                         "source": "analysis_json.experience.relevant_roles (no minimum_years)",
                         "has_threshold": False, "threshold_value": None, "threshold_unit": None,
                         "relevant_roles": [r], "inferred_policy": "explicit_role", "confidence": "high",
                         "reason": "structured relevant_roles without minimum_years (pipeline role-only criterion, "
                                   "always preferred)"})
    return rows


def other_requirement_rows(analysis: dict) -> list[dict]:
    rows = []
    for x in analysis.get("other_requirements") or []:
        t = str(x or "").strip()
        if not t:
            continue
        th = parse_threshold(t)
        if not (_EXPERIENCE_WORD.search(t) or (th and th["unit"] == "years")):
            continue
        pol, conf, why = infer_text_policy(t)
        rows.append({"criterion_text": t, "required": False,
                     "source": "analysis_json.other_requirements (experience-like text; D-01 scores it as 'other')",
                     "has_threshold": th is not None, "threshold_value": th["value"] if th else None,
                     "threshold_unit": th["unit"] if th else None, "relevant_roles": [],
                     "inferred_policy": pol, "confidence": conf, "reason": why})
    return rows


def build_rows(jobs: list[dict]) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    meta = {"jobs": len(jobs), "jobs_without_analysis": 0, "jobs_with_experience_criteria": 0,
            "domain_knowledge_items": 0}
    for j in jobs:
        a = _json(j.get("analysis_json"))
        if not a:
            meta["jobs_without_analysis"] += 1
            continue
        domain = [str(d) for d in (a.get("domain_knowledge") or []) if d]
        meta["domain_knowledge_items"] += len(domain)
        jrows = pipeline_experience_rows(a) + other_requirement_rows(a)
        if jrows:
            meta["jobs_with_experience_criteria"] += 1
        for r in jrows:
            r.update({"job_code": j.get("job_code"), "job_title": j.get("title"), "job_id": j.get("job_id"),
                      "setting_evidence": domain,
                      "setting_constrained": ("text" if r["inferred_policy"] == "sector"
                                              else "possible (domain_knowledge present)" if domain else "no evidence"),
                      "related_impact": related_impact(r["inferred_policy"], r["has_threshold"])})
            rows.append(r)
    return rows, meta


def aggregate(rows: list[dict], meta: dict) -> dict:
    thr = lambda r: "threshold" if r["has_threshold"] else "no_threshold"  # noqa: E731
    return {
        "A_jobs_inspected": meta["jobs"],
        "jobs_without_analysis_json": meta["jobs_without_analysis"],
        "B_jobs_with_experience_criteria": meta["jobs_with_experience_criteria"],
        "C_experience_criteria": len(rows),
        "C_by_source": dict(Counter(r["source"] for r in rows)),
        "D_threshold_vs_no_threshold": dict(Counter(thr(r) for r in rows)),
        "E_required_vs_preferred": dict(Counter("required" if r["required"] else "preferred" for r in rows)),
        "F_inferred_policy": {p: sum(r["inferred_policy"] == p for r in rows) for p in POLICIES},
        "G_threshold_x_policy": {f"{t}/{p}": n for (t, p), n in
                                 sorted(Counter((thr(r), r["inferred_policy"]) for r in rows).items())},
        "H_no_threshold_explicit_role": sum(not r["has_threshold"] and r["inferred_policy"] == "explicit_role"
                                            for r in rows),
        "I_no_threshold_functional": sum(not r["has_threshold"] and r["inferred_policy"] == "functional" for r in rows),
        "J_no_threshold_sector_or_setting": sum(not r["has_threshold"] and (
            r["inferred_policy"] == "sector" or r["setting_constrained"].startswith("possible")) for r in rows),
        "K_unknown": sum(r["inferred_policy"] == "UNKNOWN" for r in rows),
        "related_impact": dict(Counter(r["related_impact"] for r in rows)),
        "related_scoring_impact_required": sum(r["related_impact"] == "scoring" and r["required"] for r in rows),
        "domain_knowledge_items_context": meta["domain_knowledge_items"],
    }


def render(rows: list[dict], meta: dict) -> str:
    agg = aggregate(rows, meta)
    L = ["READ-ONLY experience-criteria population diagnostic (policy = DIAGNOSTIC INFERENCE; S1 does not exist)",
         ""]
    for k, v in agg.items():
        L.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")

    def line(r):
        return (f"  {r['job_code']} | {r['job_title']} | {'required' if r['required'] else 'preferred'} | "
                f"{r['inferred_policy']} ({r['confidence']}) | {r['criterion_text']}")
    impact = [r for r in rows if r["related_impact"] == "scoring"]
    L += ["", "=" * 100, f"RELATED scoring-impact population ({len(impact)} criteria): "
          "semantic, NO duration threshold -> under S5 qualifying=MATCHED, related=PARTIAL, "
          "not_relevant=ABSENT, so RELATED vs NOT_RELEVANT changes the criterion status", "=" * 100]
    L += [line(r) + f"\n      why: no threshold; {r['reason']}; setting: {r['setting_constrained']}"
          for r in impact] or ["  (none)"]
    unknown = [r for r in rows if r["related_impact"] == "unknown"]
    L += ["", f"UNKNOWN policy ({len(unknown)}) — impact cannot be inferred; review manually:"]
    L += [line(r) + f"   threshold={r['has_threshold']}" for r in unknown] or ["  (none)"]
    audit = [r for r in rows if r["related_impact"] == "audit_only"]
    L += ["", f"Threshold criteria where RELATED is audit/explainability only ({len(audit)}): "
          "only qualifying time counts; related-only -> ABSENT (T5_related_only)"]
    L += [line(r) + f"   threshold={r['threshold_value']:g} {r['threshold_unit']}" for r in audit] or ["  (none)"]
    pure = [r for r in rows if r["related_impact"] == "not_applicable"]
    L += ["", f"Pure-duration criteria ({len(pure)}): S2 not run; RELATED not applicable"]
    L += [line(r) for r in pure] or ["  (none)"]
    L += ["", "ALL experience criteria:"]
    for r in rows:
        L.append(f"  {r['job_code']} | {r['job_title']} | {r['source']}")
        L.append(f"      text: {r['criterion_text']}")
        L.append(f"      {'required' if r['required'] else 'preferred'}; threshold: "
                 f"{(str(r['threshold_value']) + ' ' + r['threshold_unit']) if r['has_threshold'] else 'none'}; "
                 f"roles: {r['relevant_roles']}; setting evidence: {r['setting_evidence']}")
        L.append(f"      inferred: {r['inferred_policy']} ({r['confidence']}) — {r['reason']}; "
                 f"RELATED impact: {r['related_impact']}")
    return "\n".join(L)


# ── read-only database access ───────────────────────────────────────────────

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


async def main_async(args) -> int:
    db = await ReadOnlyDB.connect(_dsn())
    try:
        jobs = [dict(r) for r in await db.fetch(JOBS_SQL)]
    finally:
        await db.close()
    rows, meta = build_rows(jobs)
    if args.json:
        print(json.dumps({"aggregate": aggregate(rows, meta), "criteria": rows}, ensure_ascii=False, indent=2,
                         default=str))
    else:
        print(render(rows, meta))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
