"""Matrix rows: one tenant-isolated, access-scoped query for the page, plus a fixed number of follow-up queries for the visible cells.

The query count does not grow with the page size or the number of columns: base rows with aggregates, tags, the Required/Preferred
cells, the flagged evaluations, and the gate cells (five statements for a page, plus the caller's one registry read).

The access scope (tenant, job, agency client assignments) comes from the caller as a WHERE clause built by the shared
routers.applications._build_candidate_filter_clause, so the matrix shows exactly the applications the workspace shows. Every value
that came from the client is a bind parameter; the only text spliced into SQL is chosen from the constants in this module.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.matrix import registry as R
from services.matrix import summaries as S
from services.matrix.config import Filters, Sort

EXPORT_ROW_LIMIT = 5000

_BASE = """
    SELECT a.application_id, a.candidate_name, a.applied_at, a.submission_source AS source, a.workflow_status, a.assigned_user_id,
           COALESCE(au.full_name, au.email) AS assigned_user_name, a.processing_status, a.stopped_reason, a.eligibility_status,
           a.scoring_status, COALESCE(a.item_final_score, s.final_score) AS score, (s.application_id IS NOT NULL) AS has_legacy,
           scr.run_id, COALESCE(scr.requirements_revision, s.requirements_revision_used) AS revision_used,
           jcr.requirements_schema_version AS schema_version, jcr.requirements_revision AS current_revision
    FROM applications a
    JOIN jobs j ON j.job_id = a.job_id
    LEFT JOIN application_scores s ON s.application_id = a.application_id
    LEFT JOIN application_scoring_runs scr ON scr.application_id = a.application_id AND scr.is_current
    LEFT JOIN job_criteria jcr ON jcr.job_id = a.job_id
    LEFT JOIN users au ON au.user_id = a.assigned_user_id
"""

# An evaluation is "open" while a person has not resolved it: its effective result is still the model's.
_OPEN = "e.result = e.ai_result"
_EV = f"""
    SELECT e.run_id,
           count(*) FILTER (WHERE e.importance = 'required')                                        AS m_total,
           count(*) FILTER (WHERE e.importance = 'required' AND e.result = 'met')                   AS m_met,
           count(*) FILTER (WHERE e.importance = 'required' AND e.result = 'partially_met')         AS m_partial,
           count(*) FILTER (WHERE e.importance = 'required' AND e.result = 'not_met')               AS m_not_met,
           count(*) FILTER (WHERE e.importance = 'required' AND e.result = 'unclear')               AS m_unclear,
           count(*) FILTER (WHERE e.importance = 'preferred')                                       AS p_total,
           count(*) FILTER (WHERE e.importance = 'preferred' AND e.result = 'yes')                  AS p_yes,
           count(*) FILTER (WHERE e.importance = 'preferred' AND e.result = 'no')                   AS p_no,
           count(*) FILTER (WHERE e.importance = 'preferred' AND e.result NOT IN ('yes', 'no'))     AS p_unclear,
           count(*) FILTER (WHERE e.result IN ('unclear', 'not_clear'))                             AS fl_unclear,
           count(*) FILTER (WHERE {_OPEN} AND e.review_reason = 'no_verified_evidence')             AS fl_noev,
           count(*) FILTER (WHERE {_OPEN} AND e.review_reason = 'low_confidence')                   AS fl_low,
           count(*) FILTER (WHERE {_OPEN} AND e.review_reason = 'conflicting_evidence')             AS fl_conflict,
           count(*) FILTER (WHERE e.result IN ('unclear', 'not_clear') OR ({_OPEN} AND e.review_reason IS NOT NULL)) AS fl_items
    FROM application_item_evaluations e
    WHERE e.run_id IN (SELECT run_id FROM base WHERE run_id IS NOT NULL)
    GROUP BY e.run_id
"""
_GT = """
    SELECT g.application_id,
           count(*)                                                              AS g_total,
           count(*) FILTER (WHERE g.result IN ('passed', 'overridden'))          AS g_passed,
           count(*) FILTER (WHERE g.result = 'failed')                           AS g_failed,
           count(*) FILTER (WHERE g.result = 'needs_verification')               AS g_review,
           count(*) FILTER (WHERE g.result = 'not_assessed')                     AS g_na
    FROM application_gate_results g
    WHERE g.application_id IN (SELECT application_id FROM base)
    GROUP BY g.application_id
"""
# Effective eligibility. A confirmed failed gate is Not Eligible whatever the score is; missing, unclear or unverifiable evidence (a gate
# needing verification, or an unresolved Required item) is Review Required; no enforced gate and nothing unresolved is Not Assessed.
_ELIGIBILITY = """CASE WHEN b.eligibility_status = 'not_eligible' THEN 'not_eligible'
                        WHEN b.eligibility_status = 'review_required' OR b.scoring_status = 'review_required' THEN 'review_required'
                        WHEN b.eligibility_status = 'eligible' THEN 'eligible'
                        ELSE 'not_assessed' END"""
_SCORING = "CASE WHEN b.scoring_status IS NOT NULL THEN b.scoring_status WHEN b.has_legacy THEN 'legacy' ELSE 'none' END"
_STALE = ("(b.schema_version = 2 AND b.revision_used IS NOT NULL AND b.current_revision IS NOT NULL "
          "AND b.revision_used <> b.current_revision)")

_MATRIX = f"""
    WITH base AS ({{base}}),
    ev AS ({_EV}),
    gt AS ({_GT}),
    m AS (
        SELECT b.*, {_ELIGIBILITY} AS eligibility, {_SCORING} AS scoring_state, {_STALE} AS stale,
               ev.m_total, ev.m_met, ev.m_partial, ev.m_not_met, ev.m_unclear, ev.p_total, ev.p_yes, ev.p_no, ev.p_unclear,
               ev.fl_unclear, ev.fl_noev, ev.fl_low, ev.fl_conflict, ev.fl_items,
               COALESCE(gt.g_total, 0) AS g_total, COALESCE(gt.g_passed, 0) AS g_passed, COALESCE(gt.g_failed, 0) AS g_failed,
               COALESCE(gt.g_review, 0) AS g_review, COALESCE(gt.g_na, 0) AS g_na,
               (b.processing_status = 'failed' AND b.stopped_reason IN ('processing_error', 'extraction_failed'))::int AS fl_proc,
               COALESCE(ev.fl_items, 0) + COALESCE(gt.g_review, 0)
                 + (b.processing_status = 'failed' AND b.stopped_reason IN ('processing_error', 'extraction_failed'))::int AS flag_total
        FROM base b
        LEFT JOIN ev ON ev.run_id = b.run_id
        LEFT JOIN gt ON gt.application_id = b.application_id
    )
"""

_RANK_ELIGIBILITY = "CASE m.eligibility WHEN 'eligible' THEN 1 WHEN 'review_required' THEN 2 WHEN 'not_assessed' THEN 3 ELSE 4 END"
_RANK_SCORING = "CASE m.scoring_state WHEN 'scored' THEN 1 WHEN 'review_required' THEN 2 WHEN 'not_scored' THEN 3 WHEN 'legacy' THEN 4 ELSE 5 END"
_STATIC_SORT = {
    "candidate.name": "lower(m.candidate_name)", "candidate.applied_at": "m.applied_at", "decision.eligibility": _RANK_ELIGIBILITY,
    "decision.score": "m.score", "decision.scoring": _RANK_SCORING, "decision.flags": "m.flag_total", "mandatory.summary": "m.m_met",
    "preferred.summary": "m.p_yes", "gates.summary": "m.g_failed", "workflow.status": "m.workflow_status",
}


def _item_result(kind: str, key: str) -> str:
    """SQL for the matrix status of one criteria cell of the application row `m`; `:{key}` is the bound item id / gate key."""
    if kind == "mandatory":
        return (f"COALESCE((SELECT e.result FROM application_item_evaluations e WHERE e.run_id = m.run_id AND e.item_id = :{key} "
                f"AND e.importance = 'required'), 'not_assessed')")
    if kind == "preferred":
        return (f"COALESCE((SELECT CASE WHEN e.result IN ('yes', 'no') THEN e.result ELSE 'unclear' END FROM application_item_evaluations e "
                f"WHERE e.run_id = m.run_id AND e.item_id = :{key} AND e.importance = 'preferred'), 'not_assessed')")
    return (f"COALESCE((SELECT CASE g.result WHEN 'passed' THEN 'passed' WHEN 'overridden' THEN 'passed' WHEN 'failed' THEN 'failed' "
            f"WHEN 'needs_verification' THEN 'review_required' ELSE 'not_assessed' END FROM application_gate_results g "
            f"WHERE g.application_id = m.application_id AND g.gate_key = :{key}), 'not_assessed')")


_CELL_RANK = {
    "mandatory": "CASE {x} WHEN 'met' THEN 1 WHEN 'partially_met' THEN 2 WHEN 'not_met' THEN 3 WHEN 'unclear' THEN 4 ELSE 5 END",
    "preferred": "CASE {x} WHEN 'yes' THEN 1 WHEN 'unclear' THEN 2 WHEN 'no' THEN 3 ELSE 4 END",
    "gate": "CASE {x} WHEN 'passed' THEN 1 WHEN 'review_required' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END",
}
_FLAG_PREDICATE = {
    "unclear_criteria": "COALESCE(m.fl_unclear, 0) > 0", "conflicting_evidence": "COALESCE(m.fl_conflict, 0) > 0",
    "evidence_not_found": "COALESCE(m.fl_noev, 0) > 0", "low_confidence": "COALESCE(m.fl_low, 0) > 0", "processing_error": "m.fl_proc > 0",
    "unresolved_review": "(COALESCE(m.m_unclear, 0) + m.g_review) > 0",
}


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _filters(f: Filters, user_id: str) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    p: dict[str, Any] = {}
    if f.search:
        where.append("m.candidate_name ILIKE :f_search ESCAPE '\\'")
        p["f_search"] = _like(f.search)
    if f.eligibility:
        where.append("m.eligibility = ANY(:f_elig)")
        p["f_elig"] = list(f.eligibility)
    if f.scoring_status:
        where.append("m.scoring_state = ANY(:f_scoring)")
        p["f_scoring"] = list(f.scoring_status)
    if f.score_min is not None:
        where.append("m.score >= :f_smin")
        p["f_smin"] = f.score_min
    if f.score_max is not None:
        where.append("m.score <= :f_smax")
        p["f_smax"] = f.score_max
    if f.workflow_status:
        where.append("m.workflow_status = ANY(:f_wf)")
        p["f_wf"] = list(f.workflow_status)
    if f.source:
        where.append("m.source = ANY(:f_source)")
        p["f_source"] = list(f.source)
    if f.flags:
        where.append("(" + " OR ".join(_FLAG_PREDICATE[k] for k in f.flags) + ")")
    if f.applied_after:
        where.append("m.applied_at >= CAST(:f_after AS date)")
        p["f_after"] = f.applied_after
    if f.applied_before:
        where.append("m.applied_at < CAST(:f_before AS date) + 1")
        p["f_before"] = f.applied_before
    if f.assigned_to == "me":
        where.append("m.assigned_user_id = CAST(:f_me AS uuid)")
        p["f_me"] = user_id
    elif f.assigned_to == "unassigned":
        where.append("m.assigned_user_id IS NULL")
    elif f.assigned_to:
        where.append("m.assigned_user_id = CAST(:f_assignee AS uuid)")
        p["f_assignee"] = f.assigned_to
    for kind, items in (("mandatory", f.mandatory), ("preferred", f.preferred), ("gate", f.gates)):
        for i, flt in enumerate(items):
            key = f"f_{kind}_{i}"
            p[key] = flt.gate_key if kind == "gate" else flt.item_id
            p[f"{key}_r"] = list(flt.results)
            where.append(f"{_item_result(kind, key)} = ANY(:{key}_r)")
    return where, p


def _order(sort: Sort | None, registry: Mapping[str, R.Column]) -> tuple[str, dict[str, Any]]:
    """ORDER BY text chosen from this module's constants, plus the bind for a criteria column. The tie-break (newest application, then the
    application id) makes the order total, so paging is stable."""
    expr, params = _STATIC_SORT["candidate.applied_at"], {}
    direction = "DESC"
    if sort is not None:
        column = registry.get(sort.column)
        direction = "ASC" if sort.direction == "asc" else "DESC"
        if column is not None and column.kind == "static" and sort.column in _STATIC_SORT:
            expr = _STATIC_SORT[sort.column]
        elif column is not None and column.kind in _CELL_RANK:
            expr = _CELL_RANK[column.kind].format(x=_item_result(column.kind, "s_ref"))
            params["s_ref"] = column.ref
    return f"{expr} {direction} NULLS LAST, m.applied_at DESC, m.application_id", params


async def fetch_rows(db: AsyncSession, *, access_where: str, access_params: Mapping[str, Any], user_id: str, filters: Filters,
                     sort: Sort | None, registry: Mapping[str, R.Column], visible: Sequence[str], page: int | None,
                     page_size: int | None) -> tuple[list[dict[str, Any]], int]:
    """(rows, total matching). page None = every matching row, capped at EXPORT_ROW_LIMIT + 1 so the caller can refuse an oversized export."""
    where, fparams = _filters(filters, user_id)
    order, oparams = _order(sort, registry)
    cte = _MATRIX.replace("{base}", _BASE + " WHERE " + access_where)
    params = {**access_params, **fparams, **oparams}
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    if page is None:
        limit_sql, params["lim"] = " LIMIT :lim", EXPORT_ROW_LIMIT + 1
    else:
        limit_sql, params["lim"], params["off"] = " LIMIT :lim OFFSET :off", page_size, (page - 1) * (page_size or 0)
    result = await db.execute(text(cte + "SELECT m.*, count(*) OVER () AS total_rows FROM m" + where_sql + " ORDER BY " + order + limit_sql), params)
    raw = [dict(r) for r in result.mappings()]
    total = int(raw[0]["total_rows"]) if raw else 0
    if not raw and page and page > 1:                       # a page past the end: the total still has to be right
        total = int((await db.execute(text(cte + "SELECT count(*) FROM m" + where_sql), {k: v for k, v in params.items() if k not in ("lim", "off")})).scalar() or 0)
    return await _decorate(db, raw, registry, visible), total


async def _decorate(db: AsyncSession, raw: list[dict[str, Any]], registry: Mapping[str, R.Column], visible: Sequence[str]) -> list[dict[str, Any]]:
    if not raw:
        return []
    app_ids = [str(r["application_id"]) for r in raw]
    run_ids = [str(r["run_id"]) for r in raw if r["run_id"]]
    tags: dict[str, list[dict[str, str]]] = {}
    if "candidate.tags" in visible:
        for r in (await db.execute(text("""
            SELECT act.application_id::text AS aid, t.tag_id::text AS tag_id, t.tag_name FROM application_candidate_tags act
            JOIN candidate_tags t ON t.tag_id = act.tag_id WHERE act.application_id = ANY(CAST(:ids AS uuid[])) ORDER BY t.tag_name"""),
                {"ids": app_ids})).mappings():
            tags.setdefault(r["aid"], []).append({"id": r["tag_id"], "name": r["tag_name"]})
    item_ids = sorted({registry[c].ref for c in visible if c in registry and registry[c].kind in ("mandatory", "preferred")})
    gate_keys = sorted({registry[c].ref for c in visible if c in registry and registry[c].kind == "gate"})
    cells: dict[tuple[str, str], Mapping[str, Any]] = {}
    if item_ids and run_ids:
        for r in (await db.execute(text("""
            SELECT run_id::text AS run_id, item_id, importance, result, ai_result, confidence, review_reason
            FROM application_item_evaluations WHERE run_id = ANY(CAST(:runs AS uuid[])) AND item_id = ANY(:ids)"""),
                {"runs": run_ids, "ids": item_ids})).mappings():
            cells[(r["run_id"], r["item_id"])] = r
    gate_cells: dict[tuple[str, str], Mapping[str, Any]] = {}
    review_gates: dict[str, list[dict[str, str]]] = {}
    for r in (await db.execute(text("""
        SELECT application_id::text AS aid, gate_key, result, computed_result FROM application_gate_results
        WHERE application_id = ANY(CAST(:ids AS uuid[]))"""), {"ids": app_ids})).mappings():
        gate_cells[(r["aid"], r["gate_key"])] = r
        if r["result"] == "needs_verification":
            review_gates.setdefault(r["aid"], []).append({"type": "gate", "id": r["gate_key"]})
    flagged: dict[str, list[dict[str, Any]]] = {}
    if run_ids:
        for r in (await db.execute(text("""
            SELECT e.run_id::text AS run_id, e.item_id, e.importance, e.result, e.review_reason,
                   EXISTS (SELECT 1 FROM application_gate_results g WHERE g.application_id = e.application_id
                           AND g.requirement_item_id = e.item_id) AS gate_linked
            FROM application_item_evaluations e WHERE e.run_id = ANY(CAST(:runs AS uuid[]))
              AND (e.result IN ('unclear', 'not_clear') OR (e.result = e.ai_result AND e.review_reason IS NOT NULL))
            ORDER BY e.position"""), {"runs": run_ids})).mappings():
            flagged.setdefault(r["run_id"], []).append(dict(r))

    rows: list[dict[str, Any]] = []
    for r in raw:
        aid = str(r["application_id"])
        rid = str(r["run_id"]) if r["run_id"] else None
        r["review_gate_targets"] = review_gates.get(aid, [])
        row_cells: dict[str, dict[str, Any]] = {}
        for cid in visible:
            column = registry.get(cid)
            if column is None or column.kind == "static":
                continue
            if column.kind == "gate":
                g = gate_cells.get((aid, column.ref))
                row_cells[cid] = {"status": S.gate_status(g["result"] if g else None), "overridden": bool(g and g["result"] == "overridden")}
                continue
            e = cells.get((rid, column.ref)) if rid else None
            if column.kind == "mandatory":
                status = S.mandatory_status(e["result"] if e and e["importance"] == "required" else None)
            else:
                status = S.preferred_status(e["result"] if e and e["importance"] == "preferred" else None)
            row_cells[cid] = {"status": status, "confidence": None if not e or e["confidence"] is None else float(e["confidence"]),
                              "resolved": bool(e and e["result"] != e["ai_result"]), "review_reason": e["review_reason"] if e else None,
                              "flagged": status == "unclear" or bool(e and e["result"] == e["ai_result"] and e["review_reason"])}
        rows.append({
            "application_id": aid, "candidate_name": r["candidate_name"],
            "applied_at": r["applied_at"].isoformat() if r["applied_at"] else None, "source": r["source"], "workflow_status": r["workflow_status"],
            "assigned_to": {"id": str(r["assigned_user_id"]), "name": r["assigned_user_name"]} if r["assigned_user_id"] else None,
            "tags": tags.get(aid, []),
            "eligibility": S.eligibility_label_key(r["eligibility"]),
            "score": None if r["score"] is None else int(r["score"]),
            "scoring": {"state": r["scoring_state"], "method": "item_level_v2" if r["scoring_status"] else ("legacy_category" if r["has_legacy"] else "none"),
                        "revision_used": r["revision_used"], "stale": bool(r["stale"])},
            "mandatory": S.mandatory_summary(r), "preferred": S.preferred_summary(r), "gates": S.gate_summary(r),
            "flags": S.flags(r, flagged.get(rid, []) if rid else []),
            "cells": row_cells})
    return rows
