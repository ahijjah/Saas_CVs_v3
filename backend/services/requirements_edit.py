"""
Requirements-v2 editing service: the only write path for a v2 document after extraction.

A save is a list of typed operations sent by an editor. The client never sends a document. The document is rebuilt on the
server from the TRUSTED stored state, inside one transaction that holds the job_criteria row lock:

    check the editor role (before any SQL)
    parse the operations strictly
    set the tenant and role context (transaction-local)
    load jobs.description for the job, tenant-scoped
    lock job_criteria FOR UPDATE; refuse unless requirements_schema_version = 2 and a document exists
    refuse a stale client revision
    validate the stored document structure
    apply the operations through requirements_v2/editing.py (pure, on a copy)
    carry_server_owned(stored, edited): the stored server-owned blocks replace anything else
    record_structure_edits: structured-field changes become server-owned records
    validate_final: every returned error refuses the save
    structural_status(document, description): valid open findings are allowed, a structural_review_invalid refuses the save
    compute the append-only retired ids, then write once with the revision check and increment the revision

Nothing here is exposed by a route. Audit is deferred until an endpoint is exposed. The caller owns the transaction: this
module never commits, so a refusal, or any failure after the call, leaves nothing behind when the caller rolls back.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass
from typing import Any, Union

from services.requirements_v2.contract import (
    CATEGORIES, IMPORTANCES, ITEM_ID_RE, ORIGIN_RECRUITER_ADDED, Issue, collect_item_ids, is_int,
)
from services.requirements_v2.editing import (
    add_item, remove_item, set_category_weight, set_importance, set_item_weight, set_structure, set_text,
)
from services.requirements_v2.readiness import carry_server_owned
from services.requirements_v2.structure import record_structure_edits
from services.requirements_v2.structural_review import READINESS_INVALID, structural_status
from services.requirements_v2.validation import validate_final, validate_structure

EDITOR_ROLES = frozenset({"admin", "recruiter"})
REQUIREMENTS_SCHEMA_VERSION = 2
REQUIREMENTS_KEY = "requirements"
MAX_OPERATIONS = 100
REUSED_RETIRED_ID = "retired_item_id_reused"
UNKNOWN_ITEM_ID = "unknown_item_id"

WEIGHT_COLUMN = {
    "skills": "weight_skills",
    "experience": "weight_experience",
    "education": "weight_education",
    "certifications": "weight_certifications",
    "soft_skills": "weight_soft_skills",
    "domain_knowledge": "weight_domain_knowledge",
    "other_requirements": "weight_other",
}


# ---- errors ----

class RequirementsEditError(Exception):
    """Base error. `code` is stable and machine-readable; `status` is the HTTP status a future router would use."""
    code = "requirements_edit_error"
    status = 400

    def __init__(self, message: str, *, code: str | None = None, issues: tuple[Issue, ...] = ()):
        super().__init__(message)
        self.code = code or type(self).code
        self.issues = issues


class PermissionDenied(RequirementsEditError):
    code, status = "permission_denied", 403


class JobNotFound(RequirementsEditError):
    code, status = "job_not_found", 404


class NotRequirementsV2(RequirementsEditError):
    code, status = "not_requirements_v2", 409


class NoRequirementsDocument(RequirementsEditError):
    code, status = "no_requirements_document", 409


class StoredDocumentInvalid(RequirementsEditError):
    code, status = "stored_document_invalid", 500


class RevisionConflict(RequirementsEditError):
    code, status = "revision_conflict", 409


class InvalidRequirements(RequirementsEditError):
    code, status = "invalid_requirements", 422


class InvalidOperation(RequirementsEditError):
    code, status = "invalid_operation", 422


# ---- the caller and the result ----

@dataclass(frozen=True)
class EditingUser:
    """The authenticated caller. The role comes from the users row, never from the request."""
    user_id: str
    tenant_id: str
    role: str


@dataclass(frozen=True)
class SaveResult:
    revision: int
    open_structural_finding_ids: tuple[str, ...]
    recorded_structure_edits: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PlannedSave:
    document: dict
    weights: dict[str, int]                          # weight column -> category weight
    retired_item_ids: list[str]
    open_structural_finding_ids: tuple[str, ...]
    recorded_structure_edits: tuple[tuple[str, str], ...]


# ---- operations ----

@dataclass(frozen=True)
class SetText:
    item_id: str
    text: str


@dataclass(frozen=True)
class SetImportance:
    item_id: str
    importance: str


@dataclass(frozen=True)
class SetItemWeight:
    item_id: str
    weight: int | None


@dataclass(frozen=True)
class SetAlternatives:
    item_id: str
    alternatives: tuple[str, ...] | None


@dataclass(frozen=True)
class SetExperience:
    item_id: str
    experience: dict | None


@dataclass(frozen=True)
class SetCategoryWeight:
    category: str
    weight: int


@dataclass(frozen=True)
class AddItem:
    category: str
    text: str
    importance: str
    weight: int | None
    alternatives: tuple[str, ...] | None
    experience: dict | None


@dataclass(frozen=True)
class RemoveItem:
    item_id: str


Operation = Union[SetText, SetImportance, SetItemWeight, SetAlternatives, SetExperience, SetCategoryWeight,
                  AddItem, RemoveItem]

# The exact field set of each operation. Anything else is refused, so a protected field can never be sent.
OPERATION_FIELDS: dict[str, frozenset[str]] = {
    "set_text": frozenset({"item_id", "text"}),
    "set_importance": frozenset({"item_id", "importance"}),
    "set_item_weight": frozenset({"item_id", "weight"}),
    "set_alternatives": frozenset({"item_id", "alternatives"}),
    "set_experience": frozenset({"item_id", "experience"}),
    "set_category_weight": frozenset({"category", "weight"}),
    "add_item": frozenset({"category", "text", "importance", "weight", "alternatives", "experience"}),
    "remove_item": frozenset({"item_id"}),
}
EXPERIENCE_FIELDS = frozenset({"subject", "min_years"})


# ---- who may edit ----

def check_editor(role: Any) -> None:
    """Only admin and recruiter may edit. Every other role, including a missing one, is refused."""
    if role not in EDITOR_ROLES:
        raise PermissionDenied("Only an admin or a recruiter can edit requirements.")


# ---- parsing ----

def parse_operations(body: object) -> tuple[Operation, ...]:
    """Strict parse of the request body. Nothing is interpreted beyond types, enumerations and field sets: the
    operations are then checked against the document by plan_operations."""
    if not isinstance(body, list):
        raise InvalidOperation("Operations must be a list.")
    if not body:
        raise InvalidOperation("At least one operation is required.")
    if len(body) > MAX_OPERATIONS:
        raise InvalidOperation(f"At most {MAX_OPERATIONS} operations are accepted per save.")
    return tuple(_parse_one(raw, pos) for pos, raw in enumerate(body))


def _parse_one(raw: object, pos: int) -> Operation:
    if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
        raise InvalidOperation(f"Operation {pos} must be an object with an 'op' name.")
    kind = raw["op"]
    if kind not in OPERATION_FIELDS:
        raise InvalidOperation(f"Operation {pos} has an unknown name {kind!r}.")
    expected = OPERATION_FIELDS[kind] | {"op"}
    if set(raw) != expected:
        raise InvalidOperation(f"Operation {pos} ({kind}) must have exactly the fields {sorted(expected)}.")
    builder = _BUILDERS[kind]
    return builder(raw, pos)


def _str(value: object, field: str, pos: int) -> str:
    if not isinstance(value, str):
        raise InvalidOperation(f"Operation {pos}: {field} must be text.")
    return value


def _item_id(value: object, pos: int) -> str:
    if not isinstance(value, str) or not ITEM_ID_RE.match(value):
        raise InvalidOperation(f"Operation {pos}: item_id is not a valid requirement id.")
    return value


def _category(value: object, pos: int) -> str:
    if value not in CATEGORIES:
        raise InvalidOperation(f"Operation {pos}: unknown category.")
    return value


def _importance(value: object, pos: int) -> str:
    if value not in IMPORTANCES:
        raise InvalidOperation(f"Operation {pos}: importance must be 'required' or 'preferred'.")
    return value


def _whole_or_none(value: object, field: str, pos: int) -> int | None:
    if value is not None and not is_int(value):
        raise InvalidOperation(f"Operation {pos}: {field} must be a whole number or null.")
    return value


def _whole(value: object, field: str, pos: int) -> int:
    if not is_int(value):
        raise InvalidOperation(f"Operation {pos}: {field} must be a whole number.")
    return value


def _alternatives(value: object, pos: int) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        raise InvalidOperation(f"Operation {pos}: alternatives must be a list of text or null.")
    return tuple(value)


def _experience(value: object, pos: int) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != EXPERIENCE_FIELDS:
        raise InvalidOperation(f"Operation {pos}: experience must be {{subject, min_years}} or null.")
    if value["subject"] is not None and not isinstance(value["subject"], str):
        raise InvalidOperation(f"Operation {pos}: experience.subject must be text or null.")
    if value["min_years"] is not None and not is_int(value["min_years"]):
        raise InvalidOperation(f"Operation {pos}: experience.min_years must be a whole number or null.")
    return {"subject": value["subject"], "min_years": value["min_years"]}


_BUILDERS = {
    "set_text": lambda r, p: SetText(_item_id(r["item_id"], p), _str(r["text"], "text", p)),
    "set_importance": lambda r, p: SetImportance(_item_id(r["item_id"], p), _importance(r["importance"], p)),
    "set_item_weight": lambda r, p: SetItemWeight(_item_id(r["item_id"], p), _whole_or_none(r["weight"], "weight", p)),
    "set_alternatives": lambda r, p: SetAlternatives(_item_id(r["item_id"], p), _alternatives(r["alternatives"], p)),
    "set_experience": lambda r, p: SetExperience(_item_id(r["item_id"], p), _experience(r["experience"], p)),
    "set_category_weight": lambda r, p: SetCategoryWeight(_category(r["category"], p), _whole(r["weight"], "weight", p)),
    "add_item": lambda r, p: AddItem(_category(r["category"], p), _str(r["text"], "text", p),
                                     _importance(r["importance"], p), _whole_or_none(r["weight"], "weight", p),
                                     _alternatives(r["alternatives"], p), _experience(r["experience"], p)),
    "remove_item": lambda r, p: RemoveItem(_item_id(r["item_id"], p)),
}


# ---- planning (pure) ----

def plan_retired_ids(stored: dict, after: dict, retired_before: object) -> list[str]:
    """Append-only: every id ever retired stays retired, and every item the save removed is added. Sorted for a stable store."""
    removed = collect_item_ids(stored) - collect_item_ids(after)
    return sorted(set(retired_before) | removed)


def plan_operations(stored: dict, original: dict | None, retired_before: object, ops: tuple[Operation, ...], *,
                    user_id: str, now: str, jd_text: str | None) -> PlannedSave:
    """Rebuild the document from the trusted stored state with the operations applied, and check it. Pure: no database,
    and no input is mutated. Raises StoredDocumentInvalid, InvalidOperation or InvalidRequirements."""
    issues = validate_structure(stored)
    if issues:
        raise StoredDocumentInvalid("The stored requirements document is malformed.", issues=issues)
    retired = sorted(set(retired_before))

    edited = copy.deepcopy(stored)
    for pos, op in enumerate(ops):
        edited = _apply(edited, op, retired, pos)

    carried = carry_server_owned(stored, edited)
    document, recorded = record_structure_edits(stored, carried, original, user_id=user_id, recorded_at=now)

    final = validate_final(document)
    if not final.ok:
        raise InvalidRequirements("The edited requirements break a final-save rule.", issues=final.errors)

    structural = structural_status(document, jd_text)
    invalid = tuple(i for i in structural.issues if i.code == READINESS_INVALID)
    if invalid:
        raise InvalidRequirements("The structural review does not match the job description.", issues=invalid)

    _refuse_retired_reuse(stored, document, retired)
    return PlannedSave(
        document=document,
        weights=_weights(document),
        retired_item_ids=plan_retired_ids(stored, document, retired),
        open_structural_finding_ids=structural.open_ids,
        recorded_structure_edits=recorded,
    )


def _apply(doc: dict, op: Operation, retired: list[str], pos: int) -> dict:
    try:
        if isinstance(op, SetText):
            return set_text(doc, op.item_id, op.text)
        if isinstance(op, SetImportance):
            return set_importance(doc, op.item_id, op.importance)
        if isinstance(op, SetItemWeight):
            return set_item_weight(doc, op.item_id, op.weight)
        if isinstance(op, SetAlternatives):
            return set_structure(doc, op.item_id, alternatives=op.alternatives)
        if isinstance(op, SetExperience):
            return set_structure(doc, op.item_id, experience=op.experience)
        if isinstance(op, SetCategoryWeight):
            return set_category_weight(doc, op.category, op.weight)
        if isinstance(op, RemoveItem):
            return remove_item(doc, op.item_id)
        out, _ = add_item(doc, op.category, op.text, op.importance, origin=ORIGIN_RECRUITER_ADDED, source_text=None,
                          alternatives=list(op.alternatives) if op.alternatives is not None else None,
                          experience=op.experience, weight=op.weight, reserved_ids=retired)
        return out
    except KeyError as exc:
        raise InvalidOperation(f"Operation {pos}: no requirement has that id.", code=UNKNOWN_ITEM_ID) from exc
    except ValueError as exc:
        raise InvalidOperation(f"Operation {pos}: {exc}") from exc


def _refuse_retired_reuse(stored: dict, after: dict, retired: list[str]) -> None:
    """Defensive: a new item id must never be one that was retired. Generated ids and reserved_ids make this unreachable
    through the operations, so a hit here means a bug and the save is refused."""
    reused = sorted((collect_item_ids(after) - collect_item_ids(stored)) & set(retired))
    if reused:
        raise InvalidRequirements("A new item reuses a retired id.",
                                  issues=tuple(Issue(REUSED_RETIRED_ID, f"Retired id {i!r} cannot be reused.")
                                               for i in reused))


def _weights(doc: dict) -> dict[str, int]:
    return {WEIGHT_COLUMN[c]: doc["categories"][c]["weight"] for c in CATEGORIES}


# ---- the write (the only one) ----
# SQL uses psycopg2 named parameters. The caller passes a DB-API connection inside a transaction it commits or rolls back.

_SET_CONTEXT = ("SELECT set_config('app.current_tenant_id', %(tid)s, true),"
                " set_config('app.current_role', %(role)s, true)")
_LOAD_JOB = "SELECT j.description FROM jobs j WHERE j.job_id = %(jid)s AND j.tenant_id = %(tid)s"
_LOCK_CRITERIA = (
    "SELECT analysis_json, original_analysis_json, requirements_schema_version, requirements_revision,"
    " requirements_retired_item_ids FROM job_criteria WHERE job_id = %(jid)s FOR UPDATE")
_UPDATE_CRITERIA = (
    "UPDATE job_criteria SET"
    " analysis_json = jsonb_set(COALESCE(analysis_json, '{}'::jsonb), '{requirements}', CAST(%(doc)s AS jsonb)),"
    " weight_skills = %(weight_skills)s, weight_experience = %(weight_experience)s,"
    " weight_education = %(weight_education)s, weight_certifications = %(weight_certifications)s,"
    " weight_soft_skills = %(weight_soft_skills)s, weight_domain_knowledge = %(weight_domain_knowledge)s,"
    " weight_other = %(weight_other)s,"
    " requirements_retired_item_ids = CAST(%(retired)s AS jsonb),"
    " requirements_revision = requirements_revision + 1,"
    " last_edited_by = CAST(%(uid)s AS uuid), last_edited_at = now(), updated_at = now()"
    " WHERE job_id = %(jid)s AND requirements_revision = %(client_revision)s"
    " AND requirements_schema_version = %(schema_version)s"
    " RETURNING requirements_revision")


def save_operations(conn: Any, user: EditingUser, job_id: str, *, client_revision: int,
                    ops: tuple[Operation, ...], now: str) -> SaveResult:
    """Apply a save on a DB-API connection (psycopg2). The caller owns the transaction: commit on success, roll back on any
    exception. Nothing is written unless every check passes, and the revision is incremented only by the final UPDATE."""
    check_editor(user.role)
    if not is_int(client_revision) or client_revision < 0:
        raise InvalidOperation("client_revision must be a whole number from 0.", code="invalid_revision")
    if not ops:
        raise InvalidOperation("At least one operation is required.")
    try:
        uuid.UUID(str(job_id))
        uuid.UUID(str(user.tenant_id))
    except (TypeError, ValueError) as exc:
        raise JobNotFound("Job not found.") from exc

    cur = conn.cursor()
    cur.execute(_SET_CONTEXT, {"tid": user.tenant_id, "role": user.role})
    cur.execute(_LOAD_JOB, {"jid": job_id, "tid": user.tenant_id})
    job = cur.fetchone()
    if job is None:
        raise JobNotFound("Job not found.")

    cur.execute(_LOCK_CRITERIA, {"jid": job_id})
    criteria = cur.fetchone()
    if criteria is None:
        raise NoRequirementsDocument("This job has no criteria record.")
    analysis_json, original_json, schema_version, revision, retired = criteria
    if schema_version != REQUIREMENTS_SCHEMA_VERSION:
        raise NotRequirementsV2("This job does not use the requirements-v2 document.")
    analysis = analysis_json if isinstance(analysis_json, dict) else {}
    stored = analysis.get(REQUIREMENTS_KEY)
    if stored is None:
        raise NoRequirementsDocument("This job has no requirements document.")
    if revision != client_revision:
        raise RevisionConflict("The requirements changed since you loaded them. Reload and apply your edits again.")

    original_analysis = original_json if isinstance(original_json, dict) else {}
    planned = plan_operations(stored, original_analysis.get(REQUIREMENTS_KEY), retired or [], ops,
                              user_id=user.user_id, now=now, jd_text=job[0])

    cur.execute(_UPDATE_CRITERIA, {
        "doc": json.dumps(planned.document, ensure_ascii=False),
        "retired": json.dumps(planned.retired_item_ids),
        "uid": user.user_id,
        "jid": job_id,
        "client_revision": client_revision,
        "schema_version": REQUIREMENTS_SCHEMA_VERSION,
        **planned.weights,
    })
    if cur.rowcount != 1:
        raise RequirementsEditError("The save could not be written.", code="write_failed")
    (new_revision,) = cur.fetchone()
    return SaveResult(revision=new_revision, open_structural_finding_ids=planned.open_structural_finding_ids,
                      recorded_structure_edits=planned.recorded_structure_edits)
