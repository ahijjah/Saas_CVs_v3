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
    sample.json          the selected sample
    results.jsonl        one line per (application, prompt): every assessment with
                         evidence, and a per-call log (main/repair, max_tokens,
                         finish_reason, token usage, validation errors, exceptions)
    cd_review.tsv        every CANNOT_DETERMINE row with evidence, for manual review
    report.md            full report incl. CD cases and criterion changes (CV-derived text)
    summary.txt          same report without CV-derived text (safe to paste; also printed)

Usage (inside a one-off container from the worker image; see PR/notes):
    python scripts/p002a_offline_compare.py --out-dir /p002a_out --dry-run
    python scripts/p002a_offline_compare.py --out-dir /p002a_out \
        --limit 40 --per-job 3 --include-job JOB-2026-0110
    (v9 keeps its production settings; v10 always runs with max_tokens=12000,
     so its repair call also uses 12000; the script aborts otherwise)
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import contextvars
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PROMPT_CODE = "recruitment.criteria_mapping"
V9_MD5_EXPECTED = "18de95b0f4559a9383aa2136db64ccba"   # md5(system_prompt || '\n'), exported 2026-09-30
CD = "CANNOT_DETERMINE"
# Agreed test value for v10 (jobs with 42-44 criteria were truncated at 7000/8000).
V10_MAX_TOKENS = 12000

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
    from services.requirements_guard import is_requirements_v2
    if is_requirements_v2(dict(crit).get("requirements_schema_version"), _json(crit["analysis_json"])):
        print(f"SKIPPED requirements-v2 job {job_id}: the legacy offline comparison does not support it",
              file=sys.stderr)
        return None
    weights = {k: crit[k] or 0 for k in (
        "weight_skills", "weight_experience", "weight_education", "weight_certifications",
        "weight_soft_skills", "weight_domain_knowledge", "weight_other")}
    return {"text": text_row["extracted_text"], "analysis_json": _json(crit["analysis_json"]),
            "weights": weights}


# ── prompt configurations (the only place max_tokens is decided) ─────────────

def build_prompt_configs(v9_row: dict, v10_text: str, v10_max_tokens: int) -> list[dict]:
    """v9 exactly as configured in production; v10 = v9 settings except the
    system prompt and max_tokens. Aborts unless v10 uses V10_MAX_TOKENS."""
    base = {"prompt_code": PROMPT_CODE, "model": v9_row["model"],
            "temperature": float(v9_row["temperature"]),
            "output_language": v9_row["output_language"]}
    v9 = dict(base, version=9, system_prompt=v9_row["system_prompt"],
              max_tokens=int(v9_row["max_tokens"]))
    v10 = dict(base, version=10, system_prompt=v10_text, max_tokens=int(v10_max_tokens))
    if v10["max_tokens"] != V10_MAX_TOKENS:
        raise SystemExit(f"v10 max_tokens is {v10['max_tokens']}, expected {V10_MAX_TOKENS}; refusing to run")
    return [v9, v10]


def expected_call_tokens(prompt_cfg: dict) -> tuple[int, int]:
    """(main call, repair call) max_tokens the mapper must send for this prompt."""
    from services.llm_criteria_mapper import _REPAIR_MIN_MAX_TOKENS
    configured = int(prompt_cfg["max_tokens"])
    return configured, max(configured, _REPAIR_MIN_MAX_TOKENS)


def check_call_tokens(prompt_cfg: dict, sent: list) -> None:
    """Abort if any D-01 call used a different max_tokens than intended."""
    main_tokens, repair_tokens = expected_call_tokens(prompt_cfg)
    expected = [main_tokens, repair_tokens][:len(sent)]
    if list(sent) != expected:
        raise SystemExit(
            f"prompt v{prompt_cfg['version']}: max_tokens sent {sent}, expected {expected}; aborting")


# ── one D-01 + F-01 run ──────────────────────────────────────────────────────

def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


class _CountingClient:
    """Wraps the real mapper client. Records, per D-01 call (capture only; the
    request and response are passed through unchanged): max_tokens sent,
    main/repair, duration, finish_reason, token usage, response length,
    validation errors of that response, or the exception raised."""

    def __init__(self, real, prompt_cfg: dict):
        self.calls = 0
        self.max_tokens: list[int] = []   # max_tokens actually sent, per call
        self.records: list[dict] = []
        outer = self

        class _Completions:
            async def create(self_inner, **kw):
                from services.llm_criteria_mapper import _response_validation_errors
                check_request(prompt_cfg, outer.calls, kw)
                rec: dict = {"call": "repair" if outer.calls else "main",
                             "max_tokens": kw.get("max_tokens"),
                             "prompt_version_sent": prompt_cfg["version"]}
                outer.calls += 1
                outer.max_tokens.append(kw.get("max_tokens"))
                t0 = time.monotonic()
                try:
                    resp = await real.chat.completions.create(**kw)
                except Exception as exc:
                    rec.update(seconds=round(time.monotonic() - t0, 1),
                               error=f"{type(exc).__name__}: {exc}"[:300])
                    outer.records.append(rec)
                    raise
                try:
                    choice = resp.choices[0]
                    content = choice.message.content or ""
                    finish = getattr(choice, "finish_reason", None)
                    usage = getattr(resp, "usage", None)
                    rec.update(
                        seconds=round(time.monotonic() - t0, 1),
                        finish_reason=finish if isinstance(finish, str) else None,
                        prompt_tokens=_int_or_none(getattr(usage, "prompt_tokens", None)),
                        completion_tokens=_int_or_none(getattr(usage, "completion_tokens", None)),
                        response_chars=len(content) if isinstance(content, str) else None,
                        validation_errors=_response_validation_errors(content)[:10]
                        if isinstance(content, str) else ["non-text content"],
                    )
                except Exception as exc:  # capture must never change the run
                    rec["capture_error"] = f"{type(exc).__name__}: {exc}"[:200]
                outer.records.append(rec)
                return resp

        self.chat = type("Chat", (), {"completions": _Completions()})()


def check_request(prompt_cfg: dict, calls_so_far: int, kw: dict) -> None:
    """Abort unless this OpenAI request belongs to this run: its own system
    prompt (the mapper may append the security suffix, never replace it) and
    at most main + one repair call."""
    if calls_so_far >= 2:
        raise SystemExit(f"prompt v{prompt_cfg['version']}: unexpected D-01 call #{calls_so_far + 1}; aborting")
    messages = kw.get("messages") or []
    sent = messages[0].get("content", "") if messages and isinstance(messages[0], dict) else ""
    if not isinstance(sent, str) or not sent.startswith(prompt_cfg["system_prompt"]):
        raise SystemExit(f"prompt v{prompt_cfg['version']}: request did not carry this run's system prompt; aborting")


# Per-run state for concurrent runs. The mapper looks up its OpenAI client and
# prompt through module-level functions; those are replaced ONCE (mapper_hooks)
# by dispatchers that read this ContextVar. asyncio gives every task its own
# copy of the context, so concurrent runs never see each other's prompt,
# client or call log.
_RUN: contextvars.ContextVar = contextvars.ContextVar("p002a_run")
_ORIGINAL_CLIENT_FACTORY: list = []


@contextlib.contextmanager
def mapper_hooks():
    import services.ai_service as ai
    import services.llm_criteria_mapper as m

    saved = (m._get_mapper_client, m._generate_qualitative_summary, ai.load_active_prompt)

    def _client():
        return _RUN.get()["client"]

    async def _prompt(*_a, **_k):
        return dict(_RUN.get()["prompt"])

    async def _no_summary(*_a, **_k):   # summary call skipped for every run
        return None

    _ORIGINAL_CLIENT_FACTORY.append(saved[0])
    m._get_mapper_client, m._generate_qualitative_summary, ai.load_active_prompt = _client, _no_summary, _prompt
    try:
        yield
    finally:
        m._get_mapper_client, m._generate_qualitative_summary, ai.load_active_prompt = saved
        _ORIGINAL_CLIENT_FACTORY.pop()


async def run_one(prompt_cfg: dict, case: dict, app_id: str, job_id: str, det_cfg, cv_facts, local) -> dict:
    """One D-01 + F-01 run. Must be called inside mapper_hooks()."""
    import services.llm_criteria_mapper as m
    from services.deterministic_scoring import DeterministicScoringEngine, deterministic_score_to_dict

    if not _ORIGINAL_CLIENT_FACTORY:
        raise RuntimeError("run_one must be called inside mapper_hooks()")
    client = _CountingClient(_ORIGINAL_CLIENT_FACTORY[-1](), prompt_cfg)
    t0 = time.monotonic()
    out: dict = {"application_id": app_id, "prompt_version": prompt_cfg["version"],
                 "configured_max_tokens": prompt_cfg["max_tokens"]}

    def _check_tokens() -> None:
        out["max_tokens_sent"] = list(client.max_tokens)
        out["call_log"] = list(client.records)
        check_call_tokens(prompt_cfg, client.max_tokens)

    token = _RUN.set({"client": client, "prompt": prompt_cfg})
    try:
        try:
            res = await m.LLMCriteriaMapper().assess(
                cv_facts=cv_facts, analysis_json=case["analysis_json"], raw_cv_text=case["text"],
                application_id=app_id, job_id=job_id, db=None)
        except m.CriteriaMappingResponseError as exc:
            _check_tokens()
            out.update(ok=False, error=f"validation: {exc}"[:500], calls=client.calls,
                       seconds=round(time.monotonic() - t0, 1))
            return out
        except Exception as exc:  # network/API errors are reported, not retried here
            _check_tokens()
            out.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:500], calls=client.calls,
                       seconds=round(time.monotonic() - t0, 1))
            return out
    finally:
        _RUN.reset(token)

    _check_tokens()
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
            "supporting_evidence": list(a.supporting_evidence),
            "final_status": (crit_by_text.get(a.criterion_text) or {}).get("status"),
            "final_cd_reason": (crit_by_text.get(a.criterion_text) or {}).get("cd_reason"),
            "verified_credit": (crit_by_text.get(a.criterion_text) or {}).get("verified_credit"),
            "upper_credit": (crit_by_text.get(a.criterion_text) or {}).get("upper_credit"),
            "pending_worth": (crit_by_text.get(a.criterion_text) or {}).get("pending_worth"),
        } for a in res.assessments],
    )
    return out


# ── report ───────────────────────────────────────────────────────────────────

# gpt-4o-mini list prices (USD per 1M tokens) used for the cost ESTIMATE only.
PRICE_IN_PER_M = 0.15
PRICE_OUT_PER_M = 0.60
STATUSES = ("MATCHED", "PARTIAL", "ABSENT", CD)
MATERIAL_SCORE_DELTA = 10


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "    -"


def _one_line(v: Any, n: int = 240) -> str:
    return str(v).replace("\n", " ").replace("\t", " ").replace("|", "/")[:n]


def _calls(rs: list[dict]) -> list[dict]:
    return [c for r in rs for c in r.get("call_log", [])]


def _section_runs(lines: list[str], rs: list[dict]) -> None:
    ok = [r for r in rs if r["ok"]]
    calls = _calls(rs)
    main = [c for c in calls if c["call"] == "main"]
    rep = [c for c in calls if c["call"] == "repair"]
    lines.append(f"- runs {len(rs)} | valid {len(ok)} | failed {len(rs) - len(ok)}")
    lines.append(f"- D-01 calls {len(calls)} (main {len(main)}, repair {len(rep)}); "
                 f"runs needing repair {sum(1 for r in rs if r.get('calls', 0) > 1)}; "
                 f"repair succeeded {sum(1 for r in ok if r.get('repair_used'))}")
    lines.append(f"- max_tokens configured {sorted({r['configured_max_tokens'] for r in rs})}; "
                 f"sent main {sorted({c['max_tokens'] for c in main})}, "
                 f"repair {sorted({c['max_tokens'] for c in rep})}")
    fin = collections.Counter(f"{c['call']}:{c.get('finish_reason') or c.get('error', '?')[:40]}" for c in calls)
    lines.append(f"- finish_reason per call: {dict(fin)}")
    lines.append(f"- truncated responses (finish_reason=length): "
                 f"{sum(1 for c in calls if c.get('finish_reason') == 'length')}")
    pt = sum(c.get("prompt_tokens") or 0 for c in calls)
    ct = sum(c.get("completion_tokens") or 0 for c in calls)
    missing = sum(1 for c in calls if c.get("prompt_tokens") is None and "error" not in c)
    cost = pt / 1e6 * PRICE_IN_PER_M + ct / 1e6 * PRICE_OUT_PER_M
    lines.append(f"- tokens: prompt {pt:,} | completion {ct:,} | estimated cost ${cost:.3f} "
                 f"(gpt-4o-mini list price){' | usage missing on %d calls' % missing if missing else ''}")
    maxc = max((c.get("completion_tokens") or 0 for c in calls), default=0)
    lines.append(f"- largest completion {maxc:,} tokens")
    secs = [r.get("seconds", 0) for r in rs]
    if secs:
        lines.append(f"- duration per run: mean {sum(secs) / len(secs):.1f}s, max {max(secs):.1f}s")


def _status_table(lines: list[str], ok: list[dict]) -> None:
    mix: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in ok:
        for a in r["assessments"]:
            grp = "required" if a["required"] else "preferred"
            mix[(grp, a["dimension"])][a["final_status"]] += 1
            mix[(grp, "ALL")][a["final_status"]] += 1
            mix[("all", "ALL")][a["final_status"]] += 1
    lines.append("| group | dimension | MATCHED | PARTIAL | ABSENT | CANNOT_DETERMINE | total |")
    lines.append("|---|---|---|---|---|---|---|")
    for key in sorted(mix, key=lambda k: (k[0] != "all", k[0], k[1] != "ALL", k[1])):
        c = mix[key]
        tot = sum(c.values())
        cells = " | ".join(f"{c[st]} ({_pct(c[st], tot).strip()})" for st in STATUSES)
        lines.append(f"| {key[0]} | {key[1]} | {cells} | {tot} |")


def build_report(results: list[dict], sample: list[dict], focus_jobs: list[str],
                 details: bool = True) -> str:
    """details=False → aggregate only (no CV-derived text): safe to paste."""
    by_v: dict[int, list[dict]] = collections.defaultdict(list)
    for r in results:
        by_v[int(r["prompt_version"])].append(r)
    L: list[str] = ["# P0-02a offline D-01 comparison — v9 vs v10", ""]
    L.append(f"Sample: {len(sample)} applications from {len({s['job_code'] for s in sample})} jobs; "
             f"focus jobs: {', '.join(focus_jobs) or '-'}")

    # 1-3, 9, 11 per prompt
    for v in sorted(by_v):
        rs = by_v[v]
        ok = [r for r in rs if r["ok"]]
        L += ["", f"## Prompt v{v}", ""]
        _section_runs(L, rs)
        L += ["", "### Status counts (final, after F-01)", ""]
        _status_table(L, ok)
        cdr = collections.Counter(a["final_cd_reason"] or "?" for r in ok for a in r["assessments"]
                                  if a["final_status"] == CD)
        L.append(f"\ncd_reason: {dict(cdr)}")
        L.append(f"recommendation: {dict(collections.Counter(r['recommendation'] for r in ok))}")
        if ok:
            L.append(f"verified mean {sum(r['verified'] for r in ok) / len(ok):.1f} | upper mean "
                     f"{sum(r['upper'] for r in ok) / len(ok):.1f} | runs with pending>0 "
                     f"{sum(1 for r in ok if r['pending'] > 0)}")
        # 9: errors
        bad = [r for r in rs if not r["ok"]]
        first_errs = [(r, c) for r in rs for c in r.get("call_log", []) if c.get("validation_errors")]
        L += ["", "### Errors (malformed JSON, truncation, validation, OpenAI)", ""]
        if not bad and not first_errs and not any("error" in c for c in _calls(rs)):
            L.append("- none")
        for r, c in first_errs:
            L.append(f"- {r['job_code']} {r['application_id']} {c['call']} call "
                     f"(finish_reason={c.get('finish_reason')}, completion_tokens={c.get('completion_tokens')}): "
                     f"{_one_line('; '.join(c['validation_errors'][:3]), 300)}")
        for r in rs:
            for c in r.get("call_log", []):
                if "error" in c:
                    L.append(f"- {r['job_code']} {r['application_id']} {c['call']} call raised: {_one_line(c['error'])}")
        for r in bad:
            L.append(f"- FAILED RUN {r['job_code']} {r['application_id']}: {_one_line(r['error'], 300)}")

    # 7: v10 per application
    if 10 in by_v:
        L += ["", "## v10 per application (verified / upper / pending / recommendation)", "",
              "| job | application | verified | upper | pending | recommendation | calls |",
              "|---|---|---|---|---|---|---|"]
        for r in sorted(by_v[10], key=lambda r: (r["job_code"], r["application_id"])):
            if r["ok"]:
                L.append(f"| {r['job_code']} | {r['application_id']} | {r['verified']} | {r['upper']} | "
                         f"{r['pending']} | {r['recommendation']} | {r['calls']} |")
            else:
                L.append(f"| {r['job_code']} | {r['application_id']} | FAILED | | | | {r.get('calls')} |")

    # 6, 8: paired comparison
    if 9 in by_v and 10 in by_v:
        v9 = {r["application_id"]: r for r in by_v[9] if r["ok"]}
        v10 = {r["application_id"]: r for r in by_v[10] if r["ok"]}
        both = sorted(set(v9) & set(v10))
        L += ["", f"## v9 → v10 (applications valid under both: {len(both)})", ""]
        trans = collections.Counter()
        unmatched = 0
        changes: list[tuple] = []
        for aid in both:
            a9 = {a["criterion_text"]: a for a in v9[aid]["assessments"]}
            for a in v10[aid]["assessments"]:
                b = a9.get(a["criterion_text"])
                if b is None:
                    unmatched += 1
                    continue
                trans[(b["final_status"], a["final_status"])] += 1
                if b["final_status"] != a["final_status"]:
                    changes.append((v10[aid]["job_code"], aid, a, b))
        tot = sum(trans.values())
        L.append(f"Criterion status transitions (same application + criterion text; {tot} pairs, "
                 f"{unmatched} v10 criteria without a v9 counterpart):")
        L.append("")
        L.append("| v9 \\ v10 | " + " | ".join(STATUSES) + " |")
        L.append("|---|" + "---|" * len(STATUSES))
        for s9 in STATUSES:
            L.append(f"| {s9} | " + " | ".join(str(trans[(s9, s10)]) for s10 in STATUSES) + " |")
        L.append(f"\nchanged: {len(changes)} of {tot} ({_pct(len(changes), tot).strip()})")
        rec_t = collections.Counter((v9[a]["recommendation"], v10[a]["recommendation"]) for a in both)
        L += ["", "Recommendation transitions:", ""]
        for (x, y), n in sorted(rec_t.items()):
            L.append(f"- {x} → {y}: {n}")
        if both:
            dv = [v10[a]["verified"] - v9[a]["verified"] for a in both]
            L.append(f"\nverified score v10−v9: mean {sum(dv) / len(dv):+.1f}, min {min(dv):+d}, max {max(dv):+d}")
        mat = [a for a in both if abs(v10[a]["verified"] - v9[a]["verified"]) >= MATERIAL_SCORE_DELTA
               or v9[a]["recommendation"] != v10[a]["recommendation"]]
        L += ["", f"Material differences (|verified Δ| ≥ {MATERIAL_SCORE_DELTA} or recommendation changed): "
              f"{len(mat)}", "",
              "| job | application | v9 verified | v9 rec | v10 verified | v10 upper | v10 pending | v10 rec |",
              "|---|---|---|---|---|---|---|---|"]
        for a in mat:
            x, y = v9[a], v10[a]
            L.append(f"| {y['job_code']} | {a} | {x['verified']} | {x['recommendation']} | {y['verified']} | "
                     f"{y['upper']} | {y['pending']} | {y['recommendation']} |")
        only = sorted((set(r["application_id"] for r in by_v[9]) | set(r["application_id"] for r in by_v[10]))
                      - set(both))
        L.append(f"\nnot comparable (failed under v9 and/or v10): {len(only)} {', '.join(only)}")

        if details:
            L += ["", "### Criterion status changes v9 → v10 (details)", "",
                  "| job | application | req | dimension | criterion | v9 | v10 | v10 cd_reason | v10 match_reason |",
                  "|---|---|---|---|---|---|---|---|---|"]
            for job, aid, a, b in sorted(changes, key=lambda t: (t[0], t[1])):
                L.append(f"| {job} | {aid} | {'R' if a['required'] else 'P'} | {a['dimension']} | "
                         f"{_one_line(a['criterion_text'], 120)} | {b['final_status']} | {a['final_status']} | "
                         f"{a['final_cd_reason'] or ''} | {_one_line(a['match_reason'], 200)} |")

    # 5: every CD case
    if details:
        L += ["", "## Every CANNOT_DETERMINE case", "",
              "| prompt | job | application | req | dimension | criterion | cd_reason | evidence | match_reason | pending_worth |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(results, key=lambda r: (r["prompt_version"], r["job_code"], r["application_id"])):
            for a in r.get("assessments", []):
                if a["final_status"] == CD:
                    L.append(f"| v{r['prompt_version']} | {r['job_code']} | {r['application_id']} | "
                             f"{'required' if a['required'] else 'preferred'} | {a['dimension']} | "
                             f"{_one_line(a['criterion_text'], 120)} | {a['final_cd_reason']} | "
                             f"{_one_line(' // '.join(a.get('supporting_evidence') or []), 240)} | "
                             f"{_one_line(a['match_reason'], 240)} | {a.get('pending_worth')} |")

    # 10: focus jobs
    for job in focus_jobs:
        L += ["", f"## Focus job {job}", ""]
        for v in sorted(by_v):
            rs = [r for r in by_v[v] if r["job_code"] == job]
            L += [f"### v{v}", ""]
            if not rs:
                L.append("- no runs")
                continue
            _section_runs(L, rs)
            for r in sorted(rs, key=lambda r: r["application_id"]):
                calls = "; ".join(
                    f"{c['call']} max_tokens={c['max_tokens']} finish={c.get('finish_reason')} "
                    f"completion={c.get('completion_tokens')} chars={c.get('response_chars')} "
                    f"errors={len(c.get('validation_errors') or [])}{' raised' if 'error' in c else ''}"
                    for c in r.get("call_log", []))
                res = (f"OK verified={r['verified']} upper={r['upper']} pending={r['pending']} "
                       f"rec={r['recommendation']} criteria={len(r['assessments'])}"
                       if r["ok"] else f"FAILED {_one_line(r['error'], 160)}")
                L.append(f"- {r['application_id']}: {res} | {calls}")
            L.append("")
    return "\n".join(L)


# ── main ─────────────────────────────────────────────────────────────────────

def write_outputs(out: Path, results: list[dict], sample: list[dict], focus_jobs: list[str]) -> str:
    with open(out / "results.jsonl", "w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda r: (r["application_id"], r["prompt_version"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "cd_review.tsv", "w", encoding="utf-8") as f:
        f.write("prompt\tjob_code\tapplication_id\trequired\tdimension\tcriterion\tcd_reason"
                "\tsupporting_evidence\tmatch_reason\tpending_worth\n")
        for r in results:
            for a in r.get("assessments", []):
                if a["final_status"] == CD:
                    f.write("\t".join(_one_line(x, 2000) for x in (
                        r["prompt_version"], r["job_code"], r["application_id"], a["required"],
                        a["dimension"], a["criterion_text"], a["final_cd_reason"],
                        " // ".join(a.get("supporting_evidence") or []), a["match_reason"],
                        a.get("pending_worth"))) + "\n")
    (out / "report.md").write_text(build_report(results, sample, focus_jobs, details=True) + "\n",
                                   encoding="utf-8")
    summary = build_report(results, sample, focus_jobs, details=False)
    (out / "summary.txt").write_text(summary + "\n", encoding="utf-8")
    return summary


def report_only(args) -> int:
    """Rebuild report.md / summary.txt / cd_review.tsv from an existing run (no DB, no OpenAI)."""
    out = Path(args.out_dir)
    results = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sample = json.loads((out / "sample.json").read_text(encoding="utf-8"))
    print(write_outputs(out, results, sample, args.include_job))
    return 0


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
        prompts = build_prompt_configs(dict(v9), built.text, args.v10_max_tokens)

        rows = await db.fetch(_SAMPLE_SQL, args.per_job, args.include_job, args.limit)
        sample = [{"application_id": str(r["application_id"]), "job_id": str(r["job_id"]),
                   "job_code": r["job_code"]} for r in rows]
        print(f"v9 md5 OK; v10 built ({len(built.applied)} edits). Sample: {len(sample)} applications, "
              f"{len({s['job_code'] for s in sample})} jobs")
        for p in prompts:
            main_t, repair_t = expected_call_tokens(p)
            print(f"  prompt v{p['version']}: model={p['model']} temperature={p['temperature']} "
                  f"max_tokens={main_t} (repair call {repair_t})")
        for s in sample:
            print(f"  {s['job_code']}  {s['application_id']}")
        (out / "sample.json").write_text(json.dumps(sample, indent=1), encoding="utf-8")
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

    with mapper_hooks():
        await asyncio.gather(*(one(s, p) for s in sample for p in prompts))

    summary = write_outputs(out, results, sample, args.include_job)
    print("\n" + summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--per-job", type=int, default=3)
    ap.add_argument("--include-job", action="append", default=[],
                    help="job_code to always include (repeatable)")
    ap.add_argument("--v10-max-tokens", type=int, default=V10_MAX_TOKENS,
                    help=f"must be {V10_MAX_TOKENS} (the agreed test value); anything else aborts")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--v9-md5", default=V9_MD5_EXPECTED)
    ap.add_argument("--dry-run", action="store_true", help="select the sample and build v10; no OpenAI calls")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from an existing --out-dir (no DB, no OpenAI)")
    args = ap.parse_args(argv)
    if args.report_only:
        return report_only(args)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
