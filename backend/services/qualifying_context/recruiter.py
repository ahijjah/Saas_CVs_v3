"""
Recruiter review, confirmation and editing of qualifying context (Architecture C phase P3).

  review_status(analysis_json) -> the single source of truth for what the UI shows and which actions exist
  build_confirm / build_edit   -> pure transitions on a copy of analysis_json (no I/O)
  confirm_qualifying_context / edit_qualifying_context
                               -> one locked, tenant-scoped transaction: SELECT ... FOR UPDATE, optimistic check
                                  against expected_qualifying_context, UPDATE analysis_json, strict audit_logs
                                  row, commit. original_analysis_json is never touched.

Stored object stays exactly {"state": "identified" | "none", "contexts": [...], "source": "recruiter"};
recruiter provenance lives only in analysis_json.qualifying_context_audit.current. latest_run (the AI reading)
is never modified here. A recruiter can never store "uncertain", and a missing object is never "none".
"""
from __future__ import annotations

import copy
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.qualifying_context.persistence import AUDIT_KEY, AUDIT_SCHEMA, QC_KEY
from services.qualifying_context.schema import (
    RUN_FAILED_TECHNICAL, RUN_FAILED_VALIDATION, SOURCE_ANALYSIS, SOURCE_RECRUITER, STATE_IDENTIFIED, STATE_NONE,
    STATE_UNCERTAIN,
)

EDIT_ROLES = ("admin", "hr_manager")              # same rule as PUT /jobs/{id}/criteria/content
MAX_CONTEXTS = 10
MAX_CONTEXT_CHARS = 200

PROVENANCE_CONFIRMED = "recruiter_confirmed"
PROVENANCE_EDITED = "recruiter_edited"
ACTION_CONFIRMED = "qualifying_context_confirmed"
ACTION_EDITED = "qualifying_context_edited"

STATUS_NOT_ASSESSED = "not_assessed"
STATUS_ASSESSMENT_FAILED = "assessment_failed"
STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_CONFIRMED = "confirmed"
STATUS_EDITED = "edited"

CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_INVALID = "invalid_qualifying_context"
CODE_CHANGED = "qualifying_context_changed"
CODE_NOTHING_TO_CONFIRM = "nothing_to_confirm"


class QCRecruiterError(Exception):
    def __init__(self, http_status: int, code: str, message: str, analysis: dict | None = None):
        super().__init__(message)
        self.http_status, self.code, self.message = http_status, code, message
        self.analysis = analysis

    def detail(self) -> dict:
        """API error body; for conflicts it carries the current state so the client can reload."""
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.analysis is not None or self.code in (CODE_CHANGED, CODE_NOTHING_TO_CONFIRM):
            out["qualifying_context"] = stored_qc(self.analysis)
            out["qualifying_context_review"] = review_status(self.analysis)
        return out


# ── reading ──────────────────────────────────────────────────────────────────

def _experience(analysis: Any) -> dict:
    exp = analysis.get("experience") if isinstance(analysis, dict) else None
    return exp if isinstance(exp, dict) else {}


def stored_qc(analysis: Any) -> Any:
    """The stored qualifying_context exactly as stored (None when absent)."""
    return _experience(analysis).get(QC_KEY)


def _audit(analysis: Any) -> dict:
    a = analysis.get(AUDIT_KEY) if isinstance(analysis, dict) else None
    return a if isinstance(a, dict) else {}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def review_status(analysis: Any) -> dict:
    """What the recruiter sees. A missing object is never reported as "none"."""
    qc, audit = stored_qc(analysis), _audit(analysis)
    current = audit.get("current") if isinstance(audit.get("current"), dict) else {}
    latest = audit.get("latest_run") if isinstance(audit.get("latest_run"), dict) else None
    out = {"status": STATUS_NOT_ASSESSED, "can_confirm": False, "can_edit": True, "state": None, "contexts": [],
           "changed_by_name": None, "changed_at": None}
    if isinstance(qc, dict):
        state = qc.get("state")
        contexts = list(qc.get("contexts") or []) if isinstance(qc.get("contexts"), list) else []
        out.update(state=state, contexts=contexts)
        if qc.get("source") == SOURCE_RECRUITER:
            confirmed = current.get("provenance") == PROVENANCE_CONFIRMED
            out.update(status=STATUS_CONFIRMED if confirmed else STATUS_EDITED,
                       changed_by_name=current.get("user_name"), changed_at=current.get("changed_at"))
        elif qc.get("source") == SOURCE_ANALYSIS and state in (STATE_IDENTIFIED, STATE_NONE):
            out.update(status=STATUS_AWAITING_CONFIRMATION, can_confirm=True)
        else:                                       # AI "uncertain" (or an unrecognised object): needs a decision
            out.update(status=STATUS_NEEDS_CONFIRMATION)
        return out
    if latest is not None and (latest.get("status") in (RUN_FAILED_TECHNICAL, RUN_FAILED_VALIDATION)
                               or latest.get("applied") is False):
        out["status"] = STATUS_ASSESSMENT_FAILED
    return out


# ── validation and pure transitions ─────────────────────────────────────────

def ensure_can_edit(role: str | None) -> None:
    if (role or "").lower() not in EDIT_ROLES:
        raise QCRecruiterError(403, CODE_FORBIDDEN, "Only tenant admins and HR managers can review the "
                                                     "required experience context")


def _has_control_chars(s: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in s)


def validate_edit(state: Any, contexts: Any) -> dict:
    """-> the recruiter object to store. Only leading/trailing whitespace is trimmed; nothing else changes."""
    if state == STATE_UNCERTAIN:
        raise QCRecruiterError(422, CODE_INVALID, "Choose whether this job limits which experience counts")
    if state not in (STATE_IDENTIFIED, STATE_NONE):
        raise QCRecruiterError(422, CODE_INVALID, "state must be 'identified' or 'none'")
    if not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts):
        raise QCRecruiterError(422, CODE_INVALID, "contexts must be a list of text values")
    if state == STATE_NONE:
        if contexts:
            raise QCRecruiterError(422, CODE_INVALID, "No contexts can be given when there is no restriction")
        return {"state": STATE_NONE, "contexts": [], "source": SOURCE_RECRUITER}
    if any(_has_control_chars(c) for c in contexts):
        raise QCRecruiterError(422, CODE_INVALID, "Contexts must not contain control characters")
    cleaned = [c.strip() for c in contexts]
    if not cleaned:
        raise QCRecruiterError(422, CODE_INVALID, "Add at least one context, or choose 'No'")
    if any(not c for c in cleaned):
        raise QCRecruiterError(422, CODE_INVALID, "Contexts must not be blank")
    if len(cleaned) > MAX_CONTEXTS:
        raise QCRecruiterError(422, CODE_INVALID, f"At most {MAX_CONTEXTS} contexts are allowed")
    if any(len(c) > MAX_CONTEXT_CHARS for c in cleaned):
        raise QCRecruiterError(422, CODE_INVALID, f"Each context must be at most {MAX_CONTEXT_CHARS} characters")
    if len(set(cleaned)) != len(cleaned):
        raise QCRecruiterError(422, CODE_INVALID, "Contexts must not repeat")
    return {"state": STATE_IDENTIFIED, "contexts": cleaned, "source": SOURCE_RECRUITER}


def _check_expected(analysis: dict, expected: Any) -> None:
    if canonical(stored_qc(analysis)) != canonical(expected):
        raise QCRecruiterError(409, CODE_CHANGED, "The required experience context was changed by someone else "
                                                  "or by an automatic update", analysis)


@dataclass(frozen=True)
class Transition:
    analysis: dict
    previous_qc: Any
    new_qc: dict
    previous_current: Any
    action: str


def _apply(analysis: dict, new_qc: dict, provenance: str, user, now: str, action: str) -> Transition:
    out = copy.deepcopy(analysis) if isinstance(analysis, dict) else {}
    exp = out.get("experience")
    if not isinstance(exp, dict):
        exp = {}
        out["experience"] = exp
    previous_qc = copy.deepcopy(exp.get(QC_KEY))
    audit = out.get(AUDIT_KEY)
    if not isinstance(audit, dict):
        audit = {"schema": AUDIT_SCHEMA}
        out[AUDIT_KEY] = audit
    previous_current = copy.deepcopy(audit.get("current"))
    one_level = ({k: v for k, v in previous_current.items() if k != "previous"}
                 if isinstance(previous_current, dict) else previous_current)
    exp[QC_KEY] = new_qc
    audit["current"] = {"source": SOURCE_RECRUITER, "provenance": provenance,
                        "user_id": str(user.user_id), "user_name": getattr(user, "full_name", None),
                        "changed_at": now, "previous": one_level, "previous_qualifying_context": previous_qc}
    # latest_run (the AI reading) and every other key are left exactly as they were
    return Transition(out, previous_qc, copy.deepcopy(new_qc), previous_current, action)


def build_confirm(analysis: dict, expected: Any, user, now: str) -> Transition:
    _check_expected(analysis, expected)
    qc = stored_qc(analysis)
    if not (isinstance(qc, dict) and qc.get("source") == SOURCE_ANALYSIS
            and qc.get("state") in (STATE_IDENTIFIED, STATE_NONE)):
        raise QCRecruiterError(409, CODE_NOTHING_TO_CONFIRM,
                               "There is no automatic suggestion that can be confirmed", analysis)
    new_qc = {"state": qc["state"], "contexts": copy.deepcopy(qc.get("contexts") or []), "source": SOURCE_RECRUITER}
    return _apply(analysis, new_qc, PROVENANCE_CONFIRMED, user, now, ACTION_CONFIRMED)


def build_edit(analysis: dict, state: Any, contexts: Any, expected: Any, user, now: str) -> Transition:
    new_qc = validate_edit(state, contexts)
    _check_expected(analysis, expected)
    return _apply(analysis, new_qc, PROVENANCE_EDITED, user, now, ACTION_EDITED)


# ── DB operations (one locked, tenant-scoped transaction) ───────────────────

LOCK_SQL = """
    SELECT jc.analysis_json
    FROM job_criteria jc
    JOIN jobs j ON j.job_id = jc.job_id
    WHERE jc.job_id = :jid AND j.tenant_id = :tid
    FOR UPDATE OF jc
"""
UPDATE_SQL = """
    UPDATE job_criteria SET
        analysis_json  = CAST(:aj AS jsonb),
        last_edited_by = :uid,
        last_edited_at = now()
    WHERE job_id = :jid
"""


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run(db, user, job_id: str, transition) -> dict:
    from sqlalchemy import text
    from database import set_rls_context
    from services.audit_service import log_action

    ensure_can_edit(getattr(user, "role", None))
    await set_rls_context(db, user.tenant_id, user.role)
    try:
        row = (await db.execute(text(LOCK_SQL), {"jid": job_id, "tid": user.tenant_id})).mappings().first()
        if not row:
            raise QCRecruiterError(404, CODE_NOT_FOUND, "Job not found")
        current = _load(row["analysis_json"])
        t: Transition = transition(current if isinstance(current, dict) else {})
        await db.execute(text(UPDATE_SQL), {"aj": json.dumps(t.analysis, ensure_ascii=False),
                                            "uid": str(user.user_id), "jid": job_id})
        await log_action(db, user.tenant_id, str(user.user_id), getattr(user, "email", None), t.action,
                         resource_type="job", resource_id=job_id,
                         details={"job_id": job_id, "previous_qualifying_context": t.previous_qc,
                                  "new_qualifying_context": t.new_qc, "previous_current": t.previous_current},
                         strict=True)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return {"success": True, "qualifying_context": t.new_qc,
            "qualifying_context_review": review_status(t.analysis)}


async def confirm_qualifying_context(db, user, job_id: str, expected: Any) -> dict:
    now = _now()
    return await _run(db, user, job_id, lambda a: build_confirm(a, expected, user, now))


async def edit_qualifying_context(db, user, job_id: str, state: Any, contexts: Any, expected: Any) -> dict:
    now = _now()
    ensure_can_edit(getattr(user, "role", None))
    validate_edit(state, contexts)                  # 422 before taking the row lock
    return await _run(db, user, job_id, lambda a: build_edit(a, state, contexts, expected, user, now))
