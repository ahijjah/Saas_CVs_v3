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
import re
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Union

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from services.requirements_v2.contract import (
    CATEGORIES, IMPORTANCES, ITEM_ID_RE, ORIGIN_RECRUITER_ADDED, WEIGHT_COLUMN, Issue, collect_item_ids, is_int,
)
from services.requirements_v2.editing import (
    add_item, remove_item, reorder_items, set_category_weight, set_importance, set_item_weight, set_structure, set_text,
)
from services.requirements_v2.acknowledgment import (
    AcknowledgmentError, acknowledge_classification_warning, classification_status, get_review,
)
from services.requirements_v2.readiness import (
    ConfirmationError, basis_hash, carry_server_owned, compute_readiness, confirm_no_numeric_score, is_preferred_only,
)
from services.requirements_v2.structural_review import (
    DECISIONS, READINESS_INVALID, STATUS_OPEN, STRUCTURAL_KEY, StructuralResolutionError, resolve_finding, structural_status,
)
from services.requirements_v2.structure import StructureError, confirm_structure, record_structure_edits, structure_status
from services.requirements_v2.validation import validate_final, validate_structure

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Admin and HR manager edit; every tenant role reads (Recruiter and Viewer see the weights read-only). See auth/roles.py.
EDITOR_ROLES = frozenset({"admin", "hr_manager"})
READER_ROLES = frozenset({"admin", "hr_manager", "recruiter", "viewer"})
REQUIREMENTS_SCHEMA_VERSION = 2
REQUIREMENTS_KEY = "requirements"
MAX_OPERATIONS = 100
REUSED_RETIRED_ID = "retired_item_id_reused"
UNKNOWN_ITEM_ID = "unknown_item_id"

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

    def __init__(self, message: str, *, current_revision: int):
        super().__init__(message)
        self.current_revision = current_revision


class AuditWriteError(RequirementsEditError):
    """The audit event could not be recorded, so the save must not be committed. Raised from the database error."""
    code, status = "audit_failed", 500


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
    email: str | None = None


@dataclass(frozen=True)
class SaveAudit:
    """Safe metadata for one accepted save: ids, counts, kinds and states only. No text of any kind."""
    operation_counts: dict[str, int]
    touched_item_ids: tuple[str, ...]              # existing items whose wording, importance, weight or structure changed
    generated_item_ids: tuple[str, ...]            # ids created by add_item
    newly_retired_item_ids: tuple[str, ...]        # ids removed by this save
    structure_edits: tuple[tuple[str, str], ...]   # (item id, "corrected" | "entered")
    readiness_before: str
    readiness_after: str
    revision_before: int | None = None
    revision_after: int | None = None
    review_actions: tuple[tuple[str, str, str | None], ...] = ()   # (action kind, id, decision or None): no text


@dataclass(frozen=True)
class SaveResult:
    revision: int
    open_structural_finding_ids: tuple[str, ...]
    recorded_structure_edits: tuple[tuple[str, str], ...]
    audit: SaveAudit


@dataclass(frozen=True)
class PlannedSave:
    document: dict
    weights: dict[str, int]                          # weight column -> category weight
    retired_item_ids: list[str]
    open_structural_finding_ids: tuple[str, ...]
    recorded_structure_edits: tuple[tuple[str, str], ...]
    audit: SaveAudit


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


# Review actions. Each one is a statement by the signed-in user. The user id and the time are taken on the server, and the
# server computes every hash and stored block, so none of them can come from the request.
@dataclass(frozen=True)
class ConfirmNoNumericScore:
    pass


@dataclass(frozen=True)
class AcknowledgeClassificationWarning:
    warning_id: str


@dataclass(frozen=True)
class ConfirmStructure:
    item_id: str


@dataclass(frozen=True)
class ResolveStructuralFinding:
    finding_id: str
    decision: str
    note: str | None
    evidence_item_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ReorderItems:
    category: str
    item_ids: tuple[str, ...]


REVIEW_OPERATIONS = (ConfirmNoNumericScore, AcknowledgeClassificationWarning, ConfirmStructure, ResolveStructuralFinding,
                     ReorderItems)

Operation = Union[SetText, SetImportance, SetItemWeight, SetAlternatives, SetExperience, SetCategoryWeight,
                  AddItem, RemoveItem, ConfirmNoNumericScore, AcknowledgeClassificationWarning, ConfirmStructure,
                  ResolveStructuralFinding, ReorderItems]

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
    "confirm_no_numeric_score": frozenset(),
    "acknowledge_classification_warning": frozenset({"warning_id"}),
    "confirm_structure": frozenset({"item_id"}),
    "resolve_structural_finding": frozenset({"finding_id", "decision", "note", "evidence_item_ids"}),
    "reorder_items": frozenset({"category", "item_ids"}),
}
EXPERIENCE_FIELDS = frozenset({"subject", "min_years"})
OPERATION_NAMES: dict[type, str] = {
    SetText: "set_text", SetImportance: "set_importance", SetItemWeight: "set_item_weight",
    SetAlternatives: "set_alternatives", SetExperience: "set_experience", SetCategoryWeight: "set_category_weight",
    AddItem: "add_item", RemoveItem: "remove_item",
    ConfirmNoNumericScore: "confirm_no_numeric_score", AcknowledgeClassificationWarning: "acknowledge_classification_warning",
    ConfirmStructure: "confirm_structure", ResolveStructuralFinding: "resolve_structural_finding",
    ReorderItems: "reorder_items",
}


# ---- who may edit ----

def check_editor(role: Any) -> None:
    """Only admin and hr_manager may edit. Every other role, including a missing one, is refused."""
    if role not in EDITOR_ROLES:
        raise PermissionDenied("Only an admin or an HR manager can edit requirements.")


def check_reader(role: Any) -> None:
    """Every tenant role may read the requirements. A missing or unknown role (and super_admin) is refused."""
    if role not in READER_ROLES:
        raise PermissionDenied("This role cannot view requirements.")


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


_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
NOTE_LIMIT = 2000


def _token(value: object, field: str, pos: int) -> str:
    if not isinstance(value, str) or not _TOKEN.match(value):
        raise InvalidOperation(f"Operation {pos}: {field} is not a valid identifier.")
    return value


def _decision(value: object, pos: int) -> str:
    if value not in DECISIONS:
        raise InvalidOperation(f"Operation {pos}: decision must be one of {', '.join(DECISIONS)}.")
    return value


def _note(value: object, pos: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > NOTE_LIMIT:
        raise InvalidOperation(f"Operation {pos}: note must be text of at most {NOTE_LIMIT} characters, or null.")
    return value


def _evidence(value: object, pos: int) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise InvalidOperation(f"Operation {pos}: evidence_item_ids must be a list of item ids, or null.")
    return tuple(_item_id(v, pos) for v in value)


def _item_id_list(value: object, pos: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidOperation(f"Operation {pos}: item_ids must be a non-empty list of item ids.")
    return tuple(_item_id(v, pos) for v in value)


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
    "confirm_no_numeric_score": lambda r, p: ConfirmNoNumericScore(),
    "acknowledge_classification_warning": lambda r, p: AcknowledgeClassificationWarning(_token(r["warning_id"], "warning_id", p)),
    "confirm_structure": lambda r, p: ConfirmStructure(_item_id(r["item_id"], p)),
    "resolve_structural_finding": lambda r, p: ResolveStructuralFinding(
        _token(r["finding_id"], "finding_id", p), _decision(r["decision"], p), _note(r["note"], p),
        _evidence(r["evidence_item_ids"], p)),
    "reorder_items": lambda r, p: ReorderItems(_category(r["category"], p), _item_id_list(r["item_ids"], p)),
}


# ---- planning (pure) ----

def validated_retired_ids(value: object) -> list[str]:
    """The stored requirements_retired_item_ids, checked before anything reads it. It must be a JSON list whose entries are
    unique requirement ids. Anything else is refused: a NULL, a string, an object, a number, a non-text entry, a malformed id or a
    duplicate. None of these is read as an empty list, and a string is never iterated as characters. A valid list is returned
    sorted, so an unsorted store is normalised consistently."""
    if not isinstance(value, list):
        raise StoredDocumentInvalid("The stored retired item ids must be a list.",
                                    issues=(Issue("retired_ids_not_a_list", "requirements_retired_item_ids must be a JSON list."),))
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str) or not ITEM_ID_RE.fullmatch(entry):
            raise StoredDocumentInvalid("A stored retired item id is malformed.",
                                        issues=(Issue("retired_id_invalid", "Every retired item id must be a requirement id."),))
        if entry in seen:
            raise StoredDocumentInvalid("A stored retired item id appears twice.",
                                        issues=(Issue("retired_id_duplicate", "Retired item ids must be unique."),))
        seen.add(entry)
    return sorted(value)


def plan_retired_ids(stored: dict, after: dict, retired_before: object) -> list[str]:
    """Append-only: every id ever retired stays retired, and every item the save removed is added. Sorted for a stable store.
    `retired_before` is validated first."""
    removed = collect_item_ids(stored) - collect_item_ids(after)
    return sorted(set(validated_retired_ids(retired_before)) | removed)


def plan_operations(stored: dict, original: dict | None, retired_before: object, ops: tuple[Operation, ...], *,
                    user_id: str, now: str, jd_text: str | None) -> PlannedSave:
    """Rebuild the document from the trusted stored state with the operations applied, and check it. Pure: no database,
    and no input is mutated. Raises StoredDocumentInvalid, InvalidOperation or InvalidRequirements."""
    issues = validate_structure(stored)
    if issues:
        raise StoredDocumentInvalid("The stored requirements document is malformed.", issues=issues)
    retired = validated_retired_ids(retired_before)
    original = _baseline(original)

    edited = copy.deepcopy(stored)
    for pos, op in enumerate(ops):
        if not isinstance(op, REVIEW_OPERATIONS):
            edited = _apply(edited, op, retired, pos)

    carried = carry_server_owned(stored, edited)
    document, recorded = record_structure_edits(stored, carried, original, user_id=user_id, recorded_at=now)
    # Review actions are applied last, on the server-owned state that was just carried, through the canonical builders.
    for pos, op in enumerate(ops):
        if isinstance(op, REVIEW_OPERATIONS):
            document = _apply_review(document, op, pos, user_id=user_id, now=now, original=original, jd_text=jd_text)

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
        audit=_save_audit(stored, document, ops, recorded, original, jd_text),
    )


def _apply_review(doc: dict, op: Operation, pos: int, *, user_id: str, now: str, original: dict | None,
                  jd_text: str | None) -> dict:
    """One review action through its canonical builder. Every builder refuses an unknown or already-settled id, so a stale or
    invented id never changes the document."""
    try:
        if isinstance(op, ConfirmNoNumericScore):
            return confirm_no_numeric_score(doc, user_id=user_id, confirmed_at=now,
                                            require_classification_acknowledgment=ACKNOWLEDGMENT_REQUIRED,
                                            original=original, jd_text=jd_text)
        if isinstance(op, AcknowledgeClassificationWarning):
            return acknowledge_classification_warning(doc, op.warning_id, user_id=user_id, acknowledged_at=now)
        if isinstance(op, ConfirmStructure):
            return confirm_structure(doc, op.item_id, user_id=user_id, confirmed_at=now, original=original)
        if isinstance(op, ResolveStructuralFinding):
            return resolve_finding(doc, op.finding_id, decision=op.decision, user_id=user_id, resolved_at=now,
                                   jd_text=jd_text or "", note=op.note,
                                   evidence_item_ids=list(op.evidence_item_ids) if op.evidence_item_ids is not None else None)
        return reorder_items(doc, op.category, list(op.item_ids))
    except (AcknowledgmentError, ConfirmationError, StructureError, StructuralResolutionError) as exc:
        raise InvalidOperation(f"Operation {pos}: {exc}", code=exc.code if hasattr(exc, "code") else "invalid_review_action") from exc
    except ValueError as exc:
        raise InvalidOperation(f"Operation {pos}: {exc}", code="invalid_review_action") from exc
    except KeyError as exc:
        raise InvalidOperation(f"Operation {pos}: no requirement has that id.", code=UNKNOWN_ITEM_ID) from exc


def _baseline(original: object) -> dict | None:
    """The original AI analysis, or None when it is absent or malformed. None means fail closed for the structure review."""
    if isinstance(original, dict) and "categories" in original and not validate_structure(original):
        return original
    return None


def _items_by_id(doc: dict) -> dict[str, dict]:
    return {i["id"]: i for c in CATEGORIES for i in doc["categories"][c]["items"]}


def _item_content(item: dict) -> tuple:
    """What counts as a change to an existing item. Provenance and source wording never change through an operation."""
    return (item["text"], item["importance"], item["weight"], item["alternatives"], item["experience"])


def _save_audit(stored: dict, document: dict, ops: tuple[Operation, ...], recorded: tuple[tuple[str, str], ...],
                original: dict | None, jd_text: str | None) -> SaveAudit:
    before, after = _items_by_id(stored), _items_by_id(document)
    counts = {name: 0 for name in OPERATION_NAMES.values()}
    for op in ops:
        counts[OPERATION_NAMES[type(op)]] += 1
    touched = sorted(i for i in before.keys() & after.keys() if _item_content(before[i]) != _item_content(after[i]))
    return SaveAudit(
        operation_counts=counts,
        touched_item_ids=tuple(touched),
        generated_item_ids=tuple(sorted(after.keys() - before.keys())),
        newly_retired_item_ids=tuple(sorted(before.keys() - after.keys())),
        structure_edits=tuple(sorted(recorded)),
        readiness_before=compute_readiness(stored, original=original, jd_text=jd_text).state,
        readiness_after=compute_readiness(document, original=original, jd_text=jd_text).state,
        review_actions=tuple(_review_action(op) for op in ops if isinstance(op, REVIEW_OPERATIONS)),
    )


def _review_action(op: Operation) -> tuple[str, str, str | None]:
    """The audit entry for one review action: its kind and the id it acted on. Never the note or any text."""
    kind = OPERATION_NAMES[type(op)]
    if isinstance(op, AcknowledgeClassificationWarning):
        return kind, op.warning_id, None
    if isinstance(op, ConfirmStructure):
        return kind, op.item_id, None
    if isinstance(op, ResolveStructuralFinding):
        return kind, op.finding_id, op.decision
    if isinstance(op, ReorderItems):
        return kind, op.category, None
    return kind, "", None


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
# An AsyncSession from the application's engine (database.py, asyncpg). The caller owns the transaction: it begins it, commits
# on success and rolls back on any exception. This module never commits, so a refusal, or any failure after the call, leaves no
# trace once the caller's transaction rolls back.

_SET_CONTEXT = text(
    "SELECT set_config('app.current_tenant_id', :tid, true), set_config('app.current_role', :role, true)")
_LOAD_JOB = text("SELECT j.description FROM jobs j WHERE j.job_id = :jid AND j.tenant_id = :tid")
_LOCK_CRITERIA = text(
    "SELECT analysis_json, original_analysis_json, requirements_schema_version, requirements_revision,"
    " requirements_retired_item_ids FROM job_criteria WHERE job_id = :jid FOR UPDATE")
_UPDATE_CRITERIA = text(
    "UPDATE job_criteria SET"
    " analysis_json = jsonb_set(COALESCE(analysis_json, '{}'::jsonb), '{requirements}', CAST(:doc AS jsonb)),"
    " weight_skills = :weight_skills, weight_experience = :weight_experience,"
    " weight_education = :weight_education, weight_certifications = :weight_certifications,"
    " weight_soft_skills = :weight_soft_skills, weight_domain_knowledge = :weight_domain_knowledge,"
    " weight_other = :weight_other,"
    " requirements_retired_item_ids = CAST(:retired AS jsonb),"
    " requirements_revision = requirements_revision + 1,"
    " last_edited_by = CAST(:uid AS uuid), last_edited_at = now(), updated_at = now()"
    " WHERE job_id = :jid AND requirements_revision = :client_revision"
    " AND requirements_schema_version = :schema_version"
    " RETURNING requirements_revision")


async def save_operations(db: AsyncSession, user: EditingUser, job_id: str, *, client_revision: int,
                          ops: tuple[Operation, ...], now: str) -> SaveResult:
    """Apply a save inside the caller's transaction. Nothing is written unless every check passes. The revision is incremented
    only by the final UPDATE, and only when it still matches the revision read under the row lock."""
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

    await db.execute(_SET_CONTEXT, {"tid": user.tenant_id, "role": user.role})
    job = (await db.execute(_LOAD_JOB, {"jid": job_id, "tid": user.tenant_id})).first()
    if job is None:
        raise JobNotFound("Job not found.")

    criteria = (await db.execute(_LOCK_CRITERIA, {"jid": job_id})).first()
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
        raise RevisionConflict("The requirements changed since you loaded them. Reload and apply your edits again.",
                               current_revision=revision)

    original_analysis = original_json if isinstance(original_json, dict) else {}
    planned = plan_operations(stored, original_analysis.get(REQUIREMENTS_KEY), retired, ops,
                              user_id=user.user_id, now=now, jd_text=job[0])

    written = (await db.execute(_UPDATE_CRITERIA, {
        "doc": json.dumps(planned.document, ensure_ascii=False),
        "retired": json.dumps(planned.retired_item_ids),
        "uid": user.user_id,
        "jid": job_id,
        "client_revision": client_revision,
        "schema_version": REQUIREMENTS_SCHEMA_VERSION,
        **planned.weights,
    })).first()
    if written is None:
        raise RequirementsEditError("The save could not be written.", code="write_failed")
    new_revision = written[0]
    return SaveResult(
        revision=new_revision,
        open_structural_finding_ids=planned.open_structural_finding_ids,
        recorded_structure_edits=planned.recorded_structure_edits,
        audit=replace(planned.audit, revision_before=client_revision, revision_after=new_revision),
    )


# ---- read-only edit view (PR A: unexposed) ----
# The only data an editor may read back. Every value is either a whitelisted stored field, a derived status, a safe id or
# a fixed server message. Stored free-form messages, evidence, reviewer identities, hashes, retired ids and unknown keys are
# never returned.

NEUTRAL_MESSAGE = "This requirements document needs review before it can be used."
ACKNOWLEDGMENT_REQUIRED = True            # the admin policy is not wired on main yet; the fail-closed default applies

READINESS_MESSAGES: dict[str, str] = {
    "no_items": "There are no requirements. Add at least one before proceeding.",
    "classification_warning_unresolved": "A Preferred classification needs acknowledgment.",
    "structure_review_pending": "This item's wording changed, or its structure was never confirmed.",
    "structural_review_open": "A structural finding needs a reviewer decision.",
    "structural_review_invalid": "The structural review is invalid and must be corrected.",
    "preferred_only_unconfirmed": "Only preferred items exist, so explicit confirmation is required before proceeding.",
    "confirmation_stale": "The items changed after the scoring confirmation was given.",
    "confirmation_not_applicable": "A scoring confirmation exists but the document is not preferred-only.",
    "category_weights_total": "Category weights must total 100% when there are required items.",
    "required_weights_total": "Required item weights in a category must total 100%.",
    "required_weight_invalid": "A required item needs a whole-percent weight from 1 to 100.",
    "category_weight_not_positive": "A category with required items needs a weight of at least 1%.",
    "category_weight_without_required_items": "A category without required items must have weight 0.",
    "too_many_required_items": "A category has more required items than the maximum allowed.",
    "preferred_item_has_weight": "Preferred items carry no weight.",
}

STRUCTURAL_MESSAGES: dict[str, str] = {
    "alternatives_split_across_items": "These items may be one requirement with alternatives.",
    "independent_items_merged": "This item may combine independent requirements.",
    "importance_conflict_unresolved": "The importance of this requirement is unclear in the job description.",
}

CONFIRMATION_NOT_REQUIRED = "not_required"
CONFIRMATION_REQUIRED = "required"
CONFIRMATION_CONFIRMED = "confirmed"
CONFIRMATION_STALE = "stale"
STRUCTURAL_NONE = "none"
STRUCTURAL_VALID = "valid"
STRUCTURAL_INVALID = "invalid"
ITEM_FIELDS = ("id", "text", "importance", "weight", "origin", "source_text", "alternatives")


def readiness_message(code: str) -> str:
    """The fixed server message for a readiness reason. An unknown code gets the neutral fallback, never stored text."""
    return READINESS_MESSAGES.get(code, NEUTRAL_MESSAGE)


@dataclass(frozen=True)
class ReasonView:
    code: str
    message: str
    category: str | None
    item_id: str | None


@dataclass(frozen=True)
class ReadinessView:
    state: str
    can_proceed: bool
    scoring_mode: str | None
    reasons: tuple[ReasonView, ...]


@dataclass(frozen=True)
class ConfirmationView:
    status: str
    confirmed_at: str | None


@dataclass(frozen=True)
class WarningView:
    id: str
    code: str
    item_id: str
    category: str
    acknowledged: bool


@dataclass(frozen=True)
class ClassificationView:
    acknowledgment_required: bool
    open_warnings: tuple[WarningView, ...]


@dataclass(frozen=True)
class StructureView:
    pending_item_ids: tuple[str, ...]


@dataclass(frozen=True)
class FindingView:
    id: str
    code: str
    item_ids: tuple[str, ...]
    source_text: str
    message: str


@dataclass(frozen=True)
class StructuralView:
    status: str
    open_findings: tuple[FindingView, ...]


@dataclass(frozen=True)
class EditView:
    job_id: str
    requirements_revision: int
    schema_version: int
    document: dict
    readiness: ReadinessView
    confirmation: ConfirmationView
    classification: ClassificationView
    structure: StructureView
    structural: StructuralView


def _project_document(doc: dict) -> dict:
    """Only schema_version, categories, and per item the whitelisted fields. Experience keeps subject and min_years only."""
    categories = {}
    for c in CATEGORIES:
        items = []
        for item in doc["categories"][c]["items"]:
            projected = {k: copy.deepcopy(item[k]) for k in ITEM_FIELDS}
            exp = item.get("experience")
            projected["experience"] = None if exp is None else {"subject": exp["subject"], "min_years": exp["min_years"]}
            items.append(projected)
        categories[c] = {"weight": doc["categories"][c]["weight"], "items": items}
    projected = {"schema_version": doc["schema_version"], "categories": categories}
    context = doc.get("context")
    if isinstance(context, dict):          # optional, non-scoring: the whitelisted fields only
        projected["context"] = {k: context.get(k) for k in ("location", "work_arrangement", "reports_to")}
    return projected


def _readiness_view(doc: dict, original: dict | None, jd_text: str | None) -> ReadinessView:
    readiness = compute_readiness(doc, require_classification_acknowledgment=ACKNOWLEDGMENT_REQUIRED,
                                  original=original, jd_text=jd_text)
    return ReadinessView(
        state=readiness.state,
        can_proceed=readiness.can_proceed,
        scoring_mode=readiness.scoring_mode,
        reasons=tuple(ReasonView(code=i.code, message=readiness_message(i.code), category=i.category, item_id=i.item_id)
                      for i in readiness.reasons),
    )


def _confirmation_view(doc: dict) -> ConfirmationView:
    """A stored confirmation is read first. It is confirmed only while the document is preferred-only and its basis hash
    still matches; any other stored confirmation is stale, which agrees with readiness (confirmation_not_applicable or
    confirmation_stale). Without a stored confirmation: preferred-only needs one, and every other document needs none."""
    stored = doc.get("scoring_confirmation")
    if stored is not None:
        if is_preferred_only(doc) and stored.get("basis_hash") == basis_hash(doc):
            return ConfirmationView(CONFIRMATION_CONFIRMED, stored["confirmed_at"])
        return ConfirmationView(CONFIRMATION_STALE, None)
    if is_preferred_only(doc):
        return ConfirmationView(CONFIRMATION_REQUIRED, None)
    return ConfirmationView(CONFIRMATION_NOT_REQUIRED, None)


def _classification_view(doc: dict) -> ClassificationView:
    status = classification_status(doc)
    by_id = {w["id"]: w for w in (get_review(doc) or {}).get("warnings", [])}
    warnings = tuple(
        WarningView(id=w["id"], code=w["code"], item_id=w["item_id"], category=w["category"],
                    acknowledged=w["id"] in status.acknowledged)
        for w in (by_id[wid] for wid in status.open)
    )
    return ClassificationView(acknowledgment_required=ACKNOWLEDGMENT_REQUIRED, open_warnings=warnings)


def _structure_view(doc: dict, original: dict | None) -> StructureView:
    return StructureView(pending_item_ids=tuple(structure_status(doc, original).needs_review))


def _structural_view(doc: dict, jd_text: str | None) -> StructuralView:
    if STRUCTURAL_KEY not in doc:
        return StructuralView(STRUCTURAL_NONE, ())
    status = structural_status(doc, jd_text)
    if any(i.code == READINESS_INVALID for i in status.issues):
        return StructuralView(STRUCTURAL_INVALID, ())
    findings = tuple(
        FindingView(id=f["id"], code=f["code"], item_ids=tuple(f["item_ids"]), source_text=f["source_text"],
                    message=STRUCTURAL_MESSAGES.get(f["code"], NEUTRAL_MESSAGE))
        for f in doc[STRUCTURAL_KEY]["findings"] if f["status"] == STATUS_OPEN
    )
    return StructuralView(STRUCTURAL_VALID, findings)


def build_edit_view(stored: dict, *, job_id: str, revision: int, schema_version: int, original: dict | None,
                    jd_text: str | None) -> EditView:
    """Pure projection of a stored document that has already passed validate_structure. Reads only; mutates nothing.
    `original` is the baseline for the structure review (None means fail closed)."""
    return EditView(
        job_id=job_id,
        requirements_revision=revision,
        schema_version=schema_version,
        document=_project_document(stored),
        readiness=_readiness_view(stored, original, jd_text),
        confirmation=_confirmation_view(stored),
        classification=_classification_view(stored),
        structure=_structure_view(stored, original),
        structural=_structural_view(stored, jd_text),
    )


_LOAD_FOR_EDIT = text(
    "SELECT j.description, c.job_id AS criteria_job_id, c.analysis_json, c.original_analysis_json,"
    " c.requirements_schema_version, c.requirements_revision"
    " FROM jobs j LEFT JOIN job_criteria c ON c.job_id = j.job_id"
    " WHERE j.job_id = :jid AND j.tenant_id = :tid")


def _check_ids(job_id: str, tenant_id: str) -> None:
    try:
        uuid.UUID(str(job_id))
        uuid.UUID(str(tenant_id))
    except (TypeError, ValueError) as exc:
        raise JobNotFound("Job not found.") from exc


async def load_for_edit(db: AsyncSession, user: EditingUser, job_id: str) -> EditView:
    """Read a v2 document for editing. Caller-owned transaction; one statement, no row lock. A missing job and another
    tenant's job are indistinguishable (both return no row)."""
    check_reader(user.role)
    _check_ids(job_id, user.tenant_id)
    await db.execute(_SET_CONTEXT, {"tid": user.tenant_id, "role": user.role})
    row = (await db.execute(_LOAD_FOR_EDIT, {"jid": job_id, "tid": user.tenant_id})).first()
    if row is None:
        raise JobNotFound("Job not found.")
    description, criteria_job_id, analysis_json, original_json, schema_version, revision = row
    if criteria_job_id is None:
        raise NoRequirementsDocument("This job has no criteria record.")
    if schema_version != REQUIREMENTS_SCHEMA_VERSION:
        raise NotRequirementsV2("This job does not use the requirements-v2 document.")
    analysis = analysis_json if isinstance(analysis_json, dict) else {}
    stored = analysis.get(REQUIREMENTS_KEY)
    if stored is None:
        raise NoRequirementsDocument("This job has no requirements document.")
    issues = validate_structure(stored)
    if issues:
        raise StoredDocumentInvalid("The stored requirements document is malformed.", issues=issues)
    original_analysis = original_json if isinstance(original_json, dict) else {}
    return build_edit_view(stored, job_id=job_id, revision=revision, schema_version=schema_version,
                           original=_baseline(original_analysis.get(REQUIREMENTS_KEY)), jd_text=description)


# ---- strict audit writer ----

AUDIT_ACTION_SAVE = "requirements_v2_saved"
ALLOWED_AUDIT_ACTIONS = frozenset({AUDIT_ACTION_SAVE})
_AUDIT_INSERT = text(
    "INSERT INTO audit_logs (tenant_id, user_id, user_email, action, resource_type, resource_id, details)"
    " VALUES (:tid, :uid, :email, :action, 'job', :jid, CAST(:details AS jsonb))")


def audit_details(audit: SaveAudit) -> dict:
    """The audit payload, built field by field from the typed metadata. No free text can reach it."""
    return {
        "operation_counts": dict(audit.operation_counts),
        "touched_item_ids": list(audit.touched_item_ids),
        "generated_item_ids": list(audit.generated_item_ids),
        "newly_retired_item_ids": list(audit.newly_retired_item_ids),
        "structure_edits": [{"item_id": item_id, "kind": kind} for item_id, kind in audit.structure_edits],
        "revision_before": audit.revision_before,
        "revision_after": audit.revision_after,
        "readiness": {"before": audit.readiness_before, "after": audit.readiness_after},
        "review_actions": [{"kind": kind, "id": ident, **({"decision": decision} if decision else {})}
                           for kind, ident, decision in audit.review_actions],
    }


async def write_audit_event(db: AsyncSession, user: EditingUser, job_id: str, *, action: str, details: SaveAudit) -> None:
    """Insert one audit row in the caller's transaction. Never commits. A database failure is raised as AuditWriteError,
    chained to the original exception, and nothing is swallowed. The caller must roll back on any exception."""
    if action not in ALLOWED_AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    if not isinstance(details, SaveAudit):
        raise TypeError("details must be SaveAudit")
    params = {
        "tid": user.tenant_id,
        "uid": user.user_id,
        "email": user.email,
        "action": action,
        "jid": job_id,
        "details": json.dumps(audit_details(details), ensure_ascii=False),
    }
    try:
        await db.execute(_AUDIT_INSERT, params)
    except SQLAlchemyError as exc:
        raise AuditWriteError("The audit event could not be recorded.") from exc
