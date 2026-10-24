"""Persistence of item-level scoring: runs, item evaluations, human resolutions, and the view the API returns.

A run is one scoring of one application against one exact requirements revision. The model's item results are validated
(services/item_scoring/evaluator.py), the numbers are computed by services/item_scoring/calc.py, and everything is written in ONE
transaction guarded by a per-application advisory lock, so a retry or a concurrent write can never create a second current run or a
second result for an item (a partial unique index and UNIQUE (run_id, item_id) are the backstop). Earlier runs are never deleted.

What is stored for the displayed outcome of the CURRENT run is mirrored onto applications (scoring_status, item_final_score, decision), so
lists, exports and analytics need no join:
    scored           every Required item has a result      final score (whole number), stored decision from the thresholds
    review_required  at least one Required item is unclear  no final score, no decision; the provisional range is on the run
    not_scored       the job has no Required item           no score (NULL, not 0), no decision

Staleness is derived, never stored: a run is stale when its requirements_revision differs from job_criteria.requirements_revision.
The stored decision is never changed by a mandatory gate: eligibility is a separate status (services/eligibility_service.py).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.item_scoring import calc
from services.item_scoring.evaluator import EvaluatedItem, EvaluationOutput, ItemSpec, EVALUATION_SOURCE_PREFIX

logger = logging.getLogger(__name__)

RESOLVABLE_RESULTS = (calc.MET, calc.PARTIALLY_MET, calc.NOT_MET)
REASON_LIMIT = 1000


class ItemScoringError(Exception):
    code = "item_scoring_error"
    status = 400

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if status:
            self.status = status


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str
    final_score: int | None
    decision: str | None
    created: bool                         # False when an existing current run was returned (an idempotent retry)


def _frac(value: Any) -> Fraction | None:
    return None if value is None else Fraction(str(value))


def _breakdown(category_weights: dict[str, int], rows: list[dict]) -> calc.Breakdown:
    inputs = [calc.ItemInput(r["item_id"], r["category"], int(r["item_weight"]), r["result"]) for r in rows if r["importance"] == "required"]
    return calc.compute(category_weights, inputs)


def _numeric(value: Fraction | None) -> str | None:
    return None if value is None else format(calc.exact_decimal(value), "f")


# ── writing a run ───────────────────────────────────────────────────────────────────────────────────────────────────

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_CURRENT_RUN = text("SELECT run_id::text, status, final_score, decision FROM application_scoring_runs"
                    " WHERE application_id = CAST(:aid AS uuid) AND is_current")


async def record_run(db: AsyncSession, *, application_id: str, tenant_id: str, job_id: str, revision: int,
                     category_weights: dict[str, int], specs: list[ItemSpec], output: EvaluationOutput, model: str,
                     thresholds: tuple[int, int], gatekeeper_hint: dict | None = None, requested_by: str | None = None,
                     rescore: bool = False) -> RunSummary:
    """Write one run with its evaluations and events, and mirror the outcome onto the application. Idempotent unless `rescore`.
    The caller owns the transaction and has already checked the revision (services.requirements_v2.scoring.lock_revision)."""
    await db.execute(_LOCK, {"key": f"item-scoring:{application_id}"})
    existing = (await db.execute(_CURRENT_RUN, {"aid": application_id})).first()
    if existing is not None and not rescore:
        return RunSummary(existing[0], existing[1], existing[2], existing[3], created=False)
    if existing is not None:
        await db.execute(text("UPDATE application_scoring_runs SET is_current = false, superseded_at = now() WHERE run_id = CAST(:rid AS uuid)"),
                         {"rid": existing[0]})

    by_id = {e.item_id: e for e in output.items}
    rows = [{"item_id": s.item_id, "category": s.category, "importance": s.importance, "item_weight": s.weight,
             "result": by_id[s.item_id].result} for s in specs]
    breakdown = _breakdown(category_weights, rows)
    final_score = breakdown.final_score
    qualified, partial = thresholds
    decision = None
    if final_score is not None:
        from services.ai_service import determine_decision
        decision = determine_decision(final_score, qualified, partial)

    run_id = (await db.execute(text("""
        INSERT INTO application_scoring_runs
            (application_id, tenant_id, job_id, requirements_revision, status, category_weights, final_score_raw, final_score_min,
             final_score_max, final_score, decision, qualified_threshold_used, partial_threshold_used, model, narrative, gatekeeper_hint,
             requested_by)
        VALUES (CAST(:aid AS uuid), CAST(:tid AS uuid), CAST(:jid AS uuid), :rev, :status, CAST(:weights AS jsonb), :raw, :min, :max, :score,
                :decision, :qt, :pt, :model, CAST(:narrative AS jsonb), CAST(:hint AS jsonb), CAST(:by AS uuid))
        RETURNING run_id::text"""), {
        "aid": application_id, "tid": tenant_id, "jid": job_id, "rev": revision, "status": breakdown.status,
        "weights": json.dumps(category_weights), "raw": _numeric(breakdown.final_raw), "min": _numeric(breakdown.final_min),
        "max": _numeric(breakdown.final_max), "score": final_score, "decision": decision,
        "qt": qualified if final_score is not None else None, "pt": partial if final_score is not None else None, "model": model,
        "narrative": json.dumps(output.narrative, ensure_ascii=False), "hint": json.dumps(gatekeeper_hint or {}), "by": requested_by})).scalar_one()

    source = f"{EVALUATION_SOURCE_PREFIX}:{model}"
    for position, spec in enumerate(specs):
        evaluated: EvaluatedItem = by_id[spec.item_id]
        evaluation_id = (await db.execute(text("""
            INSERT INTO application_item_evaluations
                (run_id, application_id, tenant_id, requirements_revision, position, item_id, category, importance, item_text, item_weight,
                 ai_result, result, evidence, explanation, confidence, evaluation_source, review_reason)
            VALUES (CAST(:rid AS uuid), CAST(:aid AS uuid), CAST(:tid AS uuid), :rev, :pos, :item, :cat, :imp, :txt, :w, :res, :res,
                    CAST(:evidence AS jsonb), :expl, :conf, :src, :why)
            ON CONFLICT (run_id, item_id) DO NOTHING
            RETURNING evaluation_id::text"""), {
            "rid": run_id, "aid": application_id, "tid": tenant_id, "rev": revision, "pos": position, "item": spec.item_id, "cat": spec.category,
            "imp": spec.importance, "txt": spec.text[:2000], "w": spec.weight, "res": evaluated.result,
            "evidence": json.dumps(list(evaluated.evidence), ensure_ascii=False), "expl": evaluated.explanation or None,
            "conf": None if evaluated.confidence is None else format(evaluated.confidence, "f"), "src": source[:100],
            "why": evaluated.review_reason})).scalar_one_or_none()
        if evaluation_id is not None:
            await db.execute(text("""
                INSERT INTO application_item_evaluation_events (evaluation_id, run_id, application_id, tenant_id, event_type, new_result)
                VALUES (CAST(:eid AS uuid), CAST(:rid AS uuid), CAST(:aid AS uuid), CAST(:tid AS uuid), 'evaluated', :res)"""),
                {"eid": evaluation_id, "rid": run_id, "aid": application_id, "tid": tenant_id, "res": evaluated.result})

    await _mirror(db, application_id, breakdown.status, final_score, decision, qualified, partial)
    return RunSummary(run_id, breakdown.status, final_score, decision, created=True)


async def _mirror(db: AsyncSession, application_id: str, status: str, final_score: int | None, decision: str | None,
                  qualified: int, partial: int) -> None:
    await db.execute(text("""
        UPDATE applications SET scoring_status = :status, item_final_score = :score, decision = :decision,
               qualified_threshold_used = :qt, partial_threshold_used = :pt,
               processing_status = 'ai_scored', evaluation_stage = 3, scored_at = now()
        WHERE application_id = CAST(:aid AS uuid)"""), {
        "status": status, "score": final_score, "decision": decision, "aid": application_id,
        "qt": qualified if final_score is not None else None, "pt": partial if final_score is not None else None})


# ── a human resolves an unclear Required item ───────────────────────────────────────────────────────────────────────

async def resolve_item(db: AsyncSession, *, application_id: str, item_id: str, result: str, reason: str, user_id: str) -> dict:
    """Resolve one unclear Required item of the CURRENT run as met / partially_met / not_met. The reason is required. The category and final
    scores are recalculated from the run's own weight snapshot; when the last unclear item is resolved the score is finalised and the stored
    decision is set. The caller (the route) has checked the role and the tenant and owns the transaction and the audit record."""
    reason = (reason or "").strip()[:REASON_LIMIT]
    if result not in RESOLVABLE_RESULTS:
        raise ItemScoringError(f"result must be one of {', '.join(RESOLVABLE_RESULTS)}.", code="invalid_result", status=422)
    if not reason:
        raise ItemScoringError("A resolution needs a reason.", code="resolution_reason_required", status=422)
    await db.execute(_LOCK, {"key": f"item-scoring:{application_id}"})
    run = (await db.execute(text("""
        SELECT run_id::text, tenant_id::text, job_id::text, category_weights FROM application_scoring_runs
        WHERE application_id = CAST(:aid AS uuid) AND is_current FOR UPDATE"""), {"aid": application_id})).mappings().first()
    if run is None:
        raise ItemScoringError("This application has no item-level scoring.", code="no_item_scoring", status=404)
    row = (await db.execute(text("""
        SELECT evaluation_id::text, importance, result FROM application_item_evaluations
        WHERE run_id = CAST(:rid AS uuid) AND item_id = :item FOR UPDATE"""), {"rid": run["run_id"], "item": item_id})).mappings().first()
    if row is None:
        raise ItemScoringError("That requirement is not part of this scoring run.", code="item_not_found", status=404)
    if row["importance"] != "required":
        raise ItemScoringError("Only a Required item can be resolved.", code="not_required", status=409)
    if row["result"] != calc.UNCLEAR:
        raise ItemScoringError("Only an unclear item can be resolved.", code="not_unclear", status=409)

    await db.execute(text("""
        UPDATE application_item_evaluations SET result = :res, resolved_by = CAST(:uid AS uuid), resolved_at = now(), resolution_reason = :reason
        WHERE evaluation_id = CAST(:eid AS uuid)"""), {"res": result, "uid": user_id, "reason": reason, "eid": row["evaluation_id"]})
    await db.execute(text("""
        INSERT INTO application_item_evaluation_events (evaluation_id, run_id, application_id, tenant_id, event_type, previous_result,
                                                         new_result, user_id, reason)
        VALUES (CAST(:eid AS uuid), CAST(:rid AS uuid), CAST(:aid AS uuid), CAST(:tid AS uuid), 'resolved', :prev, :new, CAST(:uid AS uuid), :reason)"""),
        {"eid": row["evaluation_id"], "rid": run["run_id"], "aid": application_id, "tid": run["tenant_id"], "prev": row["result"],
         "new": result, "uid": user_id, "reason": reason})

    rows = [dict(r) for r in (await db.execute(text("""
        SELECT item_id, category, importance, item_weight, result FROM application_item_evaluations WHERE run_id = CAST(:rid AS uuid)"""),
        {"rid": run["run_id"]})).mappings()]
    weights = {k: int(v) for k, v in dict(run["category_weights"]).items()}
    breakdown = _breakdown(weights, rows)
    final_score = breakdown.final_score
    decision = None
    qualified = partial = 0
    if final_score is not None:
        from services.ai_service import determine_decision
        from services.threshold_service import get_thresholds
        qualified, partial = await get_thresholds(db, run["tenant_id"], run["job_id"])
        decision = determine_decision(final_score, qualified, partial)
    await db.execute(text("""
        UPDATE application_scoring_runs SET status = :status, final_score_raw = :raw, final_score_min = :min, final_score_max = :max,
               final_score = :score, decision = :decision, qualified_threshold_used = :qt, partial_threshold_used = :pt
        WHERE run_id = CAST(:rid AS uuid)"""), {
        "status": breakdown.status, "raw": _numeric(breakdown.final_raw), "min": _numeric(breakdown.final_min),
        "max": _numeric(breakdown.final_max), "score": final_score, "decision": decision, "rid": run["run_id"],
        "qt": qualified if final_score is not None else None, "pt": partial if final_score is not None else None})
    await _mirror(db, application_id, breakdown.status, final_score, decision, qualified, partial)
    return {"status": breakdown.status, "final_score": final_score, "decision": decision, "previous_result": row["result"], "new_result": result}


# ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────────────

async def load_view(db: AsyncSession, application_id: str) -> dict | None:
    """The item-level scoring of an application for the API, or None when it has none (a legacy score or no score). Every number is
    recomputed from the stored item results with calc.py, so the view can never disagree with the evaluations."""
    app = (await db.execute(text("""
        SELECT a.application_id::text, a.job_id::text, a.decision, a.eligibility_status, a.scoring_status,
               (SELECT requirements_revision FROM job_criteria jc WHERE jc.job_id = a.job_id) AS current_revision
        FROM applications a WHERE a.application_id = CAST(:aid AS uuid)"""), {"aid": application_id})).mappings().first()
    if app is None:
        return None
    run = (await db.execute(text("""
        SELECT run_id::text, status, requirements_revision, category_weights, model, narrative, gatekeeper_hint, created_at,
               qualified_threshold_used, partial_threshold_used, decision
        FROM application_scoring_runs WHERE application_id = CAST(:aid AS uuid) AND is_current"""), {"aid": application_id})).mappings().first()
    if run is None:
        return None
    evaluations = [dict(r) for r in (await db.execute(text("""
        SELECT e.evaluation_id::text, e.item_id, e.category, e.importance, e.item_text, e.item_weight, e.ai_result, e.result, e.evidence,
               e.explanation, e.confidence, e.evaluation_source, e.evaluated_at, e.resolved_at, e.resolution_reason, u.full_name AS resolved_by_name
        FROM application_item_evaluations e LEFT JOIN users u ON u.user_id = e.resolved_by
        WHERE e.run_id = CAST(:rid AS uuid) ORDER BY e.position"""), {"rid": run["run_id"]})).mappings()]
    history: dict[str, list[dict]] = {}
    for r in (await db.execute(text("""
        SELECT ev.evaluation_id::text, ev.event_type, ev.previous_result, ev.new_result, ev.reason, ev.created_at, u.full_name
        FROM application_item_evaluation_events ev LEFT JOIN users u ON u.user_id = ev.user_id
        WHERE ev.run_id = CAST(:rid AS uuid) ORDER BY ev.created_at, ev.event_id"""), {"rid": run["run_id"]})).mappings():
        history.setdefault(r["evaluation_id"], []).append({
            "event_type": r["event_type"], "previous_result": r["previous_result"], "new_result": r["new_result"], "reason": r["reason"],
            "at": r["created_at"].isoformat() if r["created_at"] else None, "by": r["full_name"]})

    weights = {k: int(v) for k, v in dict(run["category_weights"]).items()}
    required = [e for e in evaluations if e["importance"] == "required"]
    preferred = [e for e in evaluations if e["importance"] == "preferred"]
    breakdown = _breakdown(weights, evaluations)
    earned = {i.item_id: i.earned for c in breakdown.categories for i in c.items}

    def item_view(e: dict) -> dict:
        resolved = e["resolved_at"] is not None
        return {
            "item_id": e["item_id"], "text": e["item_text"], "weight": e["item_weight"], "result": e["result"], "ai_result": e["ai_result"],
            "earned": calc.format_number(earned.get(e["item_id"])) if e["importance"] == "required" else None,
            "evidence": list(e["evidence"] or []), "explanation": e["explanation"],
            "confidence": None if e["confidence"] is None else format(e["confidence"], "f"), "source": e["evaluation_source"],
            "evaluated_at": e["evaluated_at"].isoformat() if e["evaluated_at"] else None,
            "resolution": {"by": e["resolved_by_name"], "at": e["resolved_at"].isoformat(), "reason": e["resolution_reason"],
                           "previous_result": e["ai_result"]} if resolved else None,
            "history": history.get(e["evaluation_id"], [])}

    categories = []
    for c in breakdown.categories:
        categories.append({
            "category": c.category, "weight": c.weight, "score": calc.format_number(c.score), "score_min": calc.format_number(c.score_min),
            "score_max": calc.format_number(c.score_max), "contribution": calc.format_number(c.contribution),
            "contribution_min": calc.format_number(c.contribution_min), "contribution_max": calc.format_number(c.contribution_max),
            "items": [item_view(e) for e in required if e["category"] == c.category]})
    counts = calc.preferred_summary(e["result"] for e in preferred)
    current = app["current_revision"]
    stale = current is not None and run["requirements_revision"] != current
    return {
        "application_id": app["application_id"], "scoring_method": "item_level_v2", "status": breakdown.status,
        "requirements_revision_used": run["requirements_revision"], "current_revision": current, "stale": stale,
        "final_score": breakdown.final_score, "final_score_raw": calc.format_number(breakdown.final_raw),
        "range": None if breakdown.status != calc.REVIEW_REQUIRED else {
            "min": calc.format_number(breakdown.final_min), "max": calc.format_number(breakdown.final_max)},
        "unclear_count": len(breakdown.unclear_item_ids), "decision": app["decision"], "eligibility_status": app["eligibility_status"],
        "qualified_threshold": run["qualified_threshold_used"], "partial_threshold": run["partial_threshold_used"],
        "categories": categories, "preferred": {"summary": counts, "items": [item_view(e) for e in preferred]},
        "narrative": run["narrative"] or {}, "model": run["model"], "scored_at": run["created_at"].isoformat() if run["created_at"] else None,
    }
