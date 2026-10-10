"""
Requirements-v2 editing and review API (service layer). The only production module that imports
services.requirements_v2; routers/job_requirements.py is a thin shell around it.

What it does (v2 jobs only; a legacy job is refused with 409 and never touched):
  get_requirements       current requirements, original AI snapshot, readiness, classification warnings, Edited flags
  save_requirements      the recruiter's edited document, validated by the shared validator
  acknowledge_warning    accept ONE flagged classification (server stamps user and time)
  confirm_no_score       confirm a preferred-only job proceeds without a numerical score

Every write runs in ONE transaction: row lock (SELECT ... FOR UPDATE on job_criteria) -> role / tenant / job access ->
expected-revision check -> pure transition -> UPDATE (analysis JSON, the seven category-weight columns, revision,
retired ids) -> strict audit rows -> commit. Any failure rolls the whole thing back (an audit row that cannot be
written aborts the change).

Trust boundaries
  * Server-owned and never taken from the client: scoring_confirmation, classification_review (warnings and
    acknowledgments), item origin and source wording, item ids (new items get a server-generated id; an id the stored
    document does not contain -- forged, retired or deleted -- is refused), the revision, timestamps and user ids.
    Whatever the client sent for these is discarded and listed in `discarded_client_fields`.
  * The saved document is rebuilt as carry_server_owned(STORED, incoming) from the locked row, never from the request.
  * The original snapshot (original_analysis_json) is never written here.

PIPELINE (services/requirements_pipeline, see its README): when analysis_json carries a server-owned `requirements_pipeline` record, readiness, issues
and the injection / split-OR / model-conflict gates are recomputed from it and the CURRENT document on every call; saves reconcile the record in the same
UPDATE; injection and split-OR cannot be acknowledged. Without a record the view says so explicitly (readiness.guarded = false) and nothing is invented.

No automatic weight redistribution: the server stores the weights the recruiter sent if (and only if) validate_final
accepts them; otherwise it answers 422 with the issues and stores nothing.

STRUCTURED FIELDS (OR alternatives, experience subject / min_years) -- see services/requirements_v2/structure.py
  * Recruiters may change them on existing items and supply them on new items; nothing is interpreted or normalised.
  * Whether wording and structure still AGREE cannot be verified automatically and is never claimed. After a
    wording-only edit the item needs structure review (readiness `needs_structure_review`) until a person confirms
    the existing structure (POST .../structure-review/confirm) or corrects it. Saving the same structured values is
    not a confirmation. The record (server-stamped user, time, wording + structure confirmed) is server-owned and
    invalid after any later wording / structure change.
  * Moving an item to another category is still refused (422): outside this stage.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from services import requirements_pipeline as pipe
from services.requirements_guard import is_requirements_v2
from services.requirements_v2 import (
    CATEGORIES, POLICY_KEY, SCHEMA_VERSION, AcknowledgmentError, ConfirmationError, Issue,
    acknowledge_classification_warning, classification_status, collect_item_ids, compute_readiness,
    StructureError, confirm_no_numeric_score, confirm_structure, edited_categories, make_item, new_item_id,
    parse_acknowledgment_policy, reconcile_classification_review, reconcile_structure_review, record_structure_edits,
    find_similar_items, structure_status, validate_final, validate_structure,
)
from services.requirements_v2.acknowledgment import REVIEW_KEY, get_review
from services.requirements_v2.contract import ORIGIN_RECRUITER_ADDED
from services.requirements_v2.similarity import METHOD as SIMILARITY_METHOD
from services.requirements_v2.structure import STRUCTURE_KEY, get_block as get_structure_block

EDIT_ROLES = ("admin", "hr_manager")                  # same rule as PUT /jobs/{id}/criteria/content

CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_NOT_V2 = "not_requirements_v2"
CODE_MISSING = "requirements_missing"
CODE_NOT_READY = "requirements_extraction_not_ready"
CODE_FEATURE_OFF = "requirements_v2_disabled"
CODE_RETRY_NOT_ALLOWED = "requirements_extraction_retry_not_allowed"
CODE_STORED_INVALID = "stored_requirements_invalid"
CODE_ORIGINAL_MISSING = "original_snapshot_missing"
CODE_REVISION_CONFLICT = "requirements_revision_conflict"
CODE_MIGRATION = "requirements_migration_missing"
CODE_MARKER_MISSING = "requirements_schema_marker_missing"
CODE_INVALID = "invalid_requirements"
CODE_WARNING_NOT_FOUND = "classification_warning_not_found"
CODE_WARNING_STATE = "classification_warning_not_acknowledgeable"
CODE_NOT_CONFIRMABLE = "nothing_to_confirm"
CODE_STRUCTURE_NOT_FOUND = "structure_item_not_found"
CODE_STRUCTURE_STATE = "structure_not_confirmable"
CODE_PIPELINE_RECORD_INVALID = "pipeline_record_invalid"
CODE_PIPELINE_MISSING = "pipeline_data_missing"
CODE_GATE_NOT_ACKNOWLEDGEABLE = "gate_not_acknowledgeable"
CODE_UNKNOWN_GATE = "unknown_gate"

ACTION_SAVED = "requirements_saved"
ACTION_ACKNOWLEDGED = "requirements_classification_acknowledged"
ACTION_ACK_INVALIDATED = "requirements_classification_ack_invalidated"
ACTION_WARNING_RESOLVED = "requirements_classification_warning_resolved"
ACTION_CONFIRMED = "requirements_preferred_only_confirmed"
ACTION_CONFIRMATION_INVALIDATED = "requirements_preferred_only_confirmation_invalidated"
ACTION_STRUCTURE_CONFIRMED = "requirements_structure_confirmed"
ACTION_STRUCTURE_RECORDED = "requirements_structure_recorded"
ACTION_STRUCTURE_INVALIDATED = "requirements_structure_confirmation_invalidated"
ACTION_CONFLICT_ACKNOWLEDGED = "requirements_conflict_acknowledged"
# guard / adapter events from the pipeline reconcile are audited as requirements_<component>_<event>
# (requirements_injection_guard_resolved, requirements_injection_guard_reopened, requirements_split_or_guard_resolved,
#  requirements_warning_adapter_resolved, requirements_warning_adapter_acknowledgment_invalidated, ...)

WEIGHT_COLUMNS = {
    "skills": "weight_skills", "experience": "weight_experience", "education": "weight_education",
    "certifications": "weight_certifications", "soft_skills": "weight_soft_skills",
    "domain_knowledge": "weight_domain_knowledge", "other_requirements": "weight_other",
}

_SERVER_OWNED_DOC_KEYS = ("scoring_confirmation", REVIEW_KEY, STRUCTURE_KEY, *pipe.CLIENT_FORBIDDEN_KEYS)     # accepted, discarded, listed
_DOC_INPUT_KEYS = frozenset({"schema_version", "categories", *_SERVER_OWNED_DOC_KEYS})
_ITEM_INPUT_KEYS = frozenset({"id", "text", "importance", "weight", "origin", "source_text", "alternatives", "experience"})
_SERVER_OWNED_ITEM_KEYS = ("origin", "source_text")
_STRUCTURED_KEYS = ("alternatives", "experience")


class ApiError(Exception):
    """A refusal with an HTTP status and a stable machine-readable code."""

    def __init__(self, http_status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.http_status, self.code, self.message, self.extra = http_status, code, message, extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def issues_payload(issues) -> list[dict]:
    return [{"code": i.code, "message": i.message, "category": i.category, "item_id": i.item_id} for i in issues]


def invalid(issues: list[Issue]) -> ApiError:
    return ApiError(422, CODE_INVALID, "The requirements cannot be saved.", issues=issues_payload(issues))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> Any:
    """jsonb arrives as a dict/list from some drivers and as text from others."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def can_edit(role: str | None) -> bool:
    return (role or "").lower() in EDIT_ROLES


def ensure_can_edit(role: str | None) -> None:
    if not can_edit(role):
        raise ApiError(403, CODE_FORBIDDEN, "Only tenant admins and HR managers can edit job requirements")


# ══ pure: building the incoming document from the client's request ═══════════════════════════════════════════════

@dataclass
class Incoming:
    doc: dict                                          # no server-owned state yet (confirmation None, no review block)
    new_item_ids: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)


def _index_items(doc: dict) -> dict[str, tuple[str, dict]]:
    return {i["id"]: (c, i) for c in CATEGORIES for i in doc["categories"][c]["items"]}


def build_incoming(stored: dict, client: Any, reserved_ids: set[str]) -> Incoming:
    """The client's categories rebuilt on top of the STORED items. Raises ApiError(422) for anything refused.

    Existing items (matched by id) keep every field the client may not change: provenance, source wording, OR
    alternatives and structured experience. New items (no id) get a server-generated id that avoids every id in
    `reserved_ids` (stored, original, retired) and are recruiter-added with no source wording."""
    def bad(code: str, message: str, category: str | None = None, item_id: str | None = None) -> ApiError:
        return invalid([Issue(code, message, category=category, item_id=item_id)])

    if not isinstance(client, dict):
        raise bad("not_an_object", "requirements must be an object.")
    unknown = sorted(str(k) for k in set(client) - _DOC_INPUT_KEYS)
    if unknown:
        raise bad("unknown_field", f"Unknown field(s) in requirements: {', '.join(unknown)}.")
    if client.get("schema_version") != SCHEMA_VERSION:
        raise bad("bad_schema_version", f"schema_version must be {SCHEMA_VERSION}.")
    cats = client.get("categories")
    if not isinstance(cats, dict) or set(cats) != set(CATEGORIES):
        raise bad("bad_categories", "categories must contain exactly the seven categories.")

    discarded = [k for k in _SERVER_OWNED_DOC_KEYS if client.get(k) is not None]
    index = _index_items(stored)
    taken = set(index) | set(reserved_ids)
    seen: set[str] = set()
    new_ids: list[str] = []
    out_cats: dict[str, dict] = {}
    for c in CATEGORIES:
        cat = cats[c]
        if not isinstance(cat, dict) or set(cat) != {"weight", "items"} or not isinstance(cat["items"], list):
            raise bad("bad_category", "A category must be an object with exactly 'weight' and 'items'.", c)
        items: list[dict] = []
        for n, raw in enumerate(cat["items"]):
            if not isinstance(raw, dict):
                raise bad("bad_item", "An item must be an object.", c)
            extra = sorted(str(k) for k in set(raw) - _ITEM_INPUT_KEYS)
            if extra:
                raise bad("unknown_field", f"Unknown item field(s): {', '.join(extra)}.", c)
            if "text" not in raw or "importance" not in raw:
                raise bad("bad_item", "An item needs 'text' and 'importance'.", c)
            iid = raw.get("id")
            if iid is None:                                                    # a NEW item
                new_id = new_item_id(taken)
                taken.add(new_id)
                new_ids.append(new_id)
                new_item = make_item(raw["text"], raw["importance"], item_id=new_id, weight=raw.get("weight"),
                                     origin=ORIGIN_RECRUITER_ADDED, source_text=None)
                for k in _STRUCTURED_KEYS:                      # taken as given; the validator checks the shape
                    new_item[k] = copy.deepcopy(raw.get(k))
                items.append(new_item)
                if raw.get("origin") not in (None, ORIGIN_RECRUITER_ADDED) or raw.get("source_text") is not None:
                    discarded.append(f"categories.{c}.items[{n}].origin/source_text")
                continue
            if not isinstance(iid, str) or iid not in index:
                raise bad("unknown_item_id", "This item id does not belong to the stored requirements. Ids of deleted "
                                             "or invented items cannot be used; omit the id to add a new item.", c)
            if iid in seen:
                raise bad("duplicate_item_id", "An item id appears more than once.", c, iid)
            seen.add(iid)
            prior_cat, prior = index[iid]
            if prior_cat != c:
                raise bad("item_category_change_unsupported",
                          "Moving an item to another category is not supported yet.", c, iid)
            for k in _SERVER_OWNED_ITEM_KEYS:
                if k in raw and raw[k] != prior.get(k):
                    discarded.append(f"categories.{c}.items[{n}].{k}")
            item = copy.deepcopy(prior)
            item["text"], item["importance"], item["weight"] = raw["text"], raw["importance"], raw.get("weight")
            for k in _STRUCTURED_KEYS:                          # absent = keep; present = the recruiter's value
                if k in raw:
                    item[k] = copy.deepcopy(raw[k])
            items.append(item)
        out_cats[c] = {"weight": cat["weight"], "items": items}
    doc = {"schema_version": SCHEMA_VERSION, "categories": out_cats, "scoring_confirmation": None}
    return Incoming(doc, new_ids, discarded)


@dataclass
class SavePlan:
    doc: dict
    audits: list[tuple[str, dict]]
    changed: bool
    retired: list[str]
    new_item_ids: list[str]
    discarded: list[str]
    record: dict | None = None                         # the pipeline record to store (None = the job has none / keep as stored)


def _pipeline_audits(events: list[dict]) -> list[tuple[str, dict]]:
    return [(f"requirements_{e['component']}_{e['event']}", {k: v for k, v in e.items() if k not in ("component", "event")}) for e in events]


def _item_changes(stored: dict, doc: dict) -> dict[str, list[str]]:
    before, after = _index_items(stored), _index_items(doc)
    changed = [i for i in after if i in before and any(
        before[i][1].get(f) != after[i][1].get(f) for f in ("text", "importance", "weight"))]
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(changed)}


def plan_save(stored: dict, client: Any, *, reserved_ids: set[str], retired_before: list[str],
              original: dict, user_id: str = "", now: str = "", record: dict | None = None,
              body_discarded: list[str] | None = None) -> SavePlan:
    """Pure: everything a save does, short of I/O."""
    incoming = build_incoming(stored, client, reserved_ids)
    doc = _carry(stored, incoming.doc)
    # structure edits made by THIS save are recorded by the server (never by the client); a wording-only change or
    # the same structured values again records nothing, so it cannot settle a structure review.
    doc, recorded = record_structure_edits(stored, doc, original, user_id=user_id, recorded_at=now)
    result = validate_final(doc)
    if not result.ok:
        raise invalid(list(result.errors))
    ids_after = collect_item_ids(doc)
    retired = sorted(set(retired_before) | (collect_item_ids(stored) - ids_after))
    changes = _item_changes(stored, doc)
    audits = _reconcile_audits(stored, incoming.doc, doc)
    audits += [(ACTION_STRUCTURE_RECORDED, {"item_id": i, "kind": k}) for i, k in recorded]
    new_record = None
    if record is not None:                         # guard records follow the document the server is about to store (never the client's)
        records, events = pipe.reconcile_records(record["review_records"], doc, user_id=user_id, at=now)
        new_record = pipe.with_records(record, records)
        audits += _pipeline_audits(events)
        if new_record == record:
            new_record = None
    changed = doc != stored or retired != sorted(retired_before) or new_record is not None
    discarded = list(incoming.discarded) + [f"body.{k}" for k in (body_discarded or [])]
    if changed:
        audits.insert(0, (ACTION_SAVED, {
            **changes, "edited_categories": [c for c, f in edited_categories(original, doc).items() if f],
            "category_weights_changed": [c for c in CATEGORIES
                                         if doc["categories"][c]["weight"] != stored["categories"][c]["weight"]],
            "discarded_client_fields": discarded}))
    return SavePlan(doc, audits, changed, retired, incoming.new_item_ids, discarded, new_record)


def _carry(stored: dict, incoming: dict) -> dict:
    from services.requirements_v2 import carry_server_owned
    return carry_server_owned(stored, incoming)


def _reconcile_audits(stored: dict, incoming: dict, carried: dict) -> list[tuple[str, dict]]:
    """What the carry dropped or resolved, with reasons (the carry itself only returns the result)."""
    audits: list[tuple[str, dict]] = []
    prior = get_review(stored)
    if prior:
        probe = copy.deepcopy(incoming)
        probe[REVIEW_KEY] = copy.deepcopy(prior)
        report = reconcile_classification_review(probe)
        if report.doc.get(REVIEW_KEY) != carried.get(REVIEW_KEY):          # the two must agree: a bug, not input
            raise RuntimeError("classification review reconciliation is inconsistent")
        audits += [(ACTION_ACK_INVALIDATED, {"warning_id": w, "reason": r}) for w, r in report.invalidated]
        audits += [(ACTION_WARNING_RESOLVED, {"warning_id": w, "resolution": r}) for w, r in report.resolved]
    prior_structure = get_structure_block(stored)
    if prior_structure:
        probe = copy.deepcopy(incoming)
        probe[STRUCTURE_KEY] = copy.deepcopy(prior_structure)
        report_s = reconcile_structure_review(probe)
        audits += [(ACTION_STRUCTURE_INVALIDATED, {"item_id": i, "reason": r}) for i, r in report_s.invalidated]
    if stored.get("scoring_confirmation") is not None and carried.get("scoring_confirmation") is None:
        audits.append((ACTION_CONFIRMATION_INVALIDATED, {"reason": "items_changed"}))
    return audits


# ══ pure: the view ═══════════════════════════════════════════════════════════════════════════════════════════════

def _public(doc: dict) -> dict:
    return {"schema_version": doc["schema_version"],
            "categories": copy.deepcopy(doc["categories"])}


def warnings_view(doc: dict) -> list[dict]:
    block = get_review(doc)
    if not block:
        return []
    st = classification_status(doc)
    acks = {a["warning_id"]: a for a in block["acknowledgments"]}
    out = []
    for w in block["warnings"]:
        if w["id"] in st.acknowledged:
            state = "acknowledged"
        elif w["id"] in st.unresolved:
            state = "unresolved"
        elif w["id"] in st.inactive:
            state = "inactive"
        else:
            state = "resolved"
        ack = acks.get(w["id"]) if w["id"] in st.acknowledged else None
        out.append({
            "id": w["id"], "code": w["code"], "item_id": w["item_id"], "category": w["category"],
            "message": w["message"], "evidence": copy.deepcopy(w["evidence"]), "state": state,
            "stored_status": w["status"], "resolution": w["resolution"],
            "acknowledgment": ({"user_id": ack["user_id"], "acknowledged_at": ack["acknowledged_at"]}
                               if ack else None),
        })
    return out


def structure_view(doc: dict, original: dict | None) -> dict:
    """Every item that has structured fields, with how its structure was settled (or that it still needs review).
    Nothing here claims that wording and structure agree: `original` means "as the AI read it, unedited"."""
    st = structure_status(doc, original)
    block = get_structure_block(doc) or {"records": []}
    records = {r["item_id"]: r for r in block["records"]}
    index = _index_items(doc)
    items = []
    for state in ("original", "confirmed", "corrected", "entered", "needs_review"):
        for iid in getattr(st, state):
            cat, it = index[iid]
            rec = records.get(iid) if state in ("confirmed", "corrected", "entered") else None
            items.append({"item_id": iid, "category": cat, "state": state, "alternatives": copy.deepcopy(it.get("alternatives")),
                          "experience": copy.deepcopy(it.get("experience")),
                          "record": ({"kind": rec["kind"], "user_id": rec["user_id"], "recorded_at": rec["recorded_at"]}
                                     if rec else None)})
    order = {i: n for n, i in enumerate(index)}
    items.sort(key=lambda x: order[x["item_id"]])
    return {"needs_review_item_ids": list(st.needs_review), "items": items}


PIPELINE_OK, PIPELINE_UNAVAILABLE, PIPELINE_INVALID = "ok", "unavailable", "invalid_record"
_MSG_UNAVAILABLE = ("This job has no pipeline record (it was created without the guarded extraction pipeline). The readiness shown is the frozen "
                    "readiness only: the injection, split-OR and model-conflict checks have NOT been applied (readiness.guarded is false). "
                    "The original analysis snapshot is under `original`. No provenance is claimed.")
_MSG_INVALID = ("The stored pipeline record failed its integrity check; the job cannot proceed and cannot be changed until it is repaired. "
                "The requirements and the original snapshot are still readable.")


@dataclass
class PipelineContext:
    status: str
    record: dict | None = None
    errors: list[str] = field(default_factory=list)
    state: dict | None = None                          # evaluated pipeline state (only when status == ok)


def pipeline_context(record: Any, doc: dict, original: dict | None, policy: bool) -> PipelineContext:
    """Everything derived is recomputed here from (stored record, current document, original snapshot, policy); nothing stored is trusted for it."""
    if record is None:
        return PipelineContext(PIPELINE_UNAVAILABLE)
    errors = pipe.verify_record(record, original)
    if errors:
        return PipelineContext(PIPELINE_INVALID, record if isinstance(record, dict) else None, errors)
    state = pipe.evaluate(pipe.assemble_state(record, doc, original), require_classification_acknowledgment=policy)
    return PipelineContext(PIPELINE_OK, record, [], state)


def _pipeline_block(ctx: PipelineContext, include_raw: bool) -> dict:
    out: dict = {"status": ctx.status, "available": ctx.status == PIPELINE_OK, "errors": list(ctx.errors)}
    if ctx.status == PIPELINE_UNAVAILABLE:
        out["message"] = _MSG_UNAVAILABLE
        return out
    if ctx.status == PIPELINE_INVALID:
        out["message"] = _MSG_INVALID
        return out
    rec = ctx.record
    raw = rec["raw_response"]
    out.update(contract_version=pipe.CONTRACT_VERSION, record_version=rec["record_version"], component_versions=copy.deepcopy(rec["component_versions"]),
               provenance=copy.deepcopy(rec["provenance"]), job_description_sha256=rec["job_description_sha256"],
               raw_response={"sha256": raw.get("sha256"), "finish_reason": raw.get("finish_reason"),
                             "bytes": len(raw["text"].encode("utf-8")) if isinstance(raw.get("text"), str) else None})
    if include_raw:                                     # editors only: the AI's raw text and parsed output, exactly as stored
        out["raw_response"]["text"] = raw.get("text")
        out["raw_ai_output"] = copy.deepcopy(rec["raw_ai_output"])
    return out


def build_view(*, job_id: str, revision: int | None, doc: dict, original: dict | None, policy: bool,
               editable: bool, discarded: list[str] | None = None, pipeline_record: Any = None) -> dict:
    readiness = compute_readiness(doc, require_classification_acknowledgment=policy, original=original)
    conf = doc.get("scoring_confirmation")
    ctx = pipeline_context(pipeline_record, doc, original, policy)
    ready_view = {
        "state": readiness.state, "scoring_mode": readiness.scoring_mode, "can_proceed": readiness.can_proceed,
        "reasons": issues_payload(readiness.reasons),
        "open_warning_ids": list(readiness.open_warning_ids),
        "unresolved_warning_ids": list(readiness.unresolved_warning_ids),
        "structure_review_item_ids": list(readiness.structure_review_item_ids),
        "guarded": False, "basis": "frozen_only",
    }
    if ctx.status == PIPELINE_OK:
        pr = ctx.state["readiness"]
        ready_view.update(state=pr["state"], scoring_mode=pr["scoring_mode"], can_proceed=pr["can_proceed"], reasons=pr["reasons"],
                          guarded=True, basis="pipeline", by_policy=pr["by_policy"])
    elif ctx.status == PIPELINE_INVALID:
        ready_view.update(state=pipe.STATE_RECORD_INVALID, scoring_mode=None, can_proceed=False, basis="pipeline_record_invalid",
                          reasons=[{"code": "pipeline_record_invalid", "message": _MSG_INVALID, "category": None, "item_id": None}])
    state = ctx.state or {}
    return {
        "job_id": str(job_id),
        "revision": revision,
        "requirements": _public(doc),
        "original": _public(original) if original is not None else None,
        "edited_categories": edited_categories(original, doc) if original is not None else None,
        "readiness": ready_view,
        "pipeline": _pipeline_block(ctx, editable),
        # null (not []) without a usable pipeline record: an empty list would claim "no issues"
        "unresolved_issues": ([{**i, "details": pipe.issue_details(state).get(i["id"])} for i in state["unresolved_issues"]] if state else None),
        "model_conflicts": pipe.model_conflicts(state) if state else None,
        "gates": state.get("gates"),
        "normalized_warnings": state.get("normalized_warnings"),
        "informational": ({"generic_model_notes": state["informational"]["generic_model_notes"],
                           "parser_review": state["informational"]["parser_review"]} if state else None),
        "classification_warnings": warnings_view(doc),
        "classification_policy": {"key": POLICY_KEY, "require_acknowledgment": policy},
        "preferred_only_confirmation": ({"confirmed": True, "user_id": conf["user_id"],
                                         "confirmed_at": conf["confirmed_at"]} if conf else {"confirmed": False}),
        "structure_review": structure_view(doc, original),
        # Informational only, derived from the items on every call, never stored, never part of readiness
        "similarity_warnings": find_similar_items(doc),
        "similarity_method": SIMILARITY_METHOD,
        "discarded_client_fields": list(discarded or []),
        "can_edit": editable,
    }


# ══ database ═════════════════════════════════════════════════════════════════════════════════════════════════════

_JOB_ACCESS_SQL = """
    SELECT j.job_id FROM jobs j
    WHERE j.job_id = CAST(:jid AS uuid) AND j.tenant_id = CAST(:tid AS uuid)
      AND (
        :is_admin = TRUE
        OR j.client_organization_id IS NULL
        OR EXISTS (
            SELECT 1 FROM agency_user_clients auc
            WHERE auc.user_id = CAST(:uid AS uuid)
              AND auc.client_organization_id = j.client_organization_id
              AND auc.tenant_id = CAST(:tid AS uuid)
        )
      )
"""

# to_jsonb(jc) reads the optional columns by key, so a read before migrations 106/107 are applied returns NULLs
# instead of failing; a write refuses (CODE_MIGRATION) unless the revision column exists.
_CRITERIA_SQL = """
    SELECT jc.analysis_json, jc.original_analysis_json,
           to_jsonb(jc) ->> 'requirements_schema_version' AS requirements_schema_version,
           to_jsonb(jc) ->> 'requirements_revision' AS requirements_revision,
           to_jsonb(jc) -> 'requirements_retired_item_ids' AS requirements_retired_item_ids,
           to_jsonb(jc) ->> 'criteria_extraction_status' AS criteria_extraction_status,
           to_jsonb(jc) ->> 'criteria_extraction_error' AS criteria_extraction_error
    FROM job_criteria jc WHERE jc.job_id = CAST(:jid AS uuid)
"""

_UPDATE_SQL = """
    UPDATE job_criteria SET
        analysis_json = CAST(:aj AS jsonb),
        weight_skills = :w_skills, weight_experience = :w_experience, weight_education = :w_education,
        weight_certifications = :w_certifications, weight_soft_skills = :w_soft_skills,
        weight_domain_knowledge = :w_domain_knowledge, weight_other = :w_other_requirements,
        requirements_retired_item_ids = CAST(:retired AS jsonb),
        requirements_revision = requirements_revision + 1,
        last_edited_by = CAST(:uid AS uuid), last_edited_at = now()
    WHERE job_id = CAST(:jid AS uuid) AND requirements_revision = :rev
"""

_POLICY_SQL = "SELECT value FROM system_config WHERE key = :k"


@dataclass
class Loaded:
    analysis: dict
    stored: dict
    original: dict | None
    revision: int | None
    retired: list[str]
    marker: Any = None                  # job_criteria.requirements_schema_version (NULL = not set / column absent)
    extraction_status: str | None = None   # v2 jobs: pending | processing | completed | failed (job_criteria.criteria_extraction_status)
    extraction_error: str | None = None
    retired_column_present: bool = True
    pipeline_record: Any = None         # analysis_json["requirements_pipeline"] as stored (None = the job has none)


async def load_policy(db) -> bool:
    """The platform-wide setting. Missing / unreadable / unrecognised -> Yes (fail closed)."""
    row = await db.execute(text(_POLICY_SQL), {"k": POLICY_KEY})
    return parse_acknowledgment_policy(row.scalar_one_or_none())


async def _load(db, user, job_id: str, *, lock: bool) -> Loaded:
    from database import set_rls_context
    try:
        uuid.UUID(str(job_id))
    except ValueError:
        raise ApiError(404, CODE_NOT_FOUND, "Job not found") from None
    await set_rls_context(db, user.tenant_id, user.role)
    access = await db.execute(text(_JOB_ACCESS_SQL), {
        "jid": str(job_id), "tid": str(user.tenant_id), "uid": str(user.user_id),
        "is_admin": (user.role or "").lower() == "admin"})
    if not access.first():
        raise ApiError(404, CODE_NOT_FOUND, "Job not found")          # also for another tenant's job: no leak
    sql = _CRITERIA_SQL + (" FOR UPDATE" if lock else "")
    row = (await db.execute(text(sql), {"jid": str(job_id)})).mappings().first()
    if not row:
        raise ApiError(404, CODE_NOT_FOUND, "Criteria not found for this job")
    analysis = _json(row["analysis_json"])
    analysis = analysis if isinstance(analysis, dict) else {}
    if not is_requirements_v2(row["requirements_schema_version"], analysis):
        raise ApiError(409, CODE_NOT_V2, "This job uses the legacy criteria format; its requirements cannot be "
                                         "read or edited through the requirements API.")
    stored = analysis.get("requirements")
    if not isinstance(stored, dict):
        if row["requirements_schema_version"] is None:
            raise ApiError(409, CODE_MISSING, "This job has no requirements document yet.")
        stored = None                                          # a v2 job whose extraction has not produced a document yet (pending / failed)
    elif validate_structure(stored):
        raise ApiError(409, CODE_STORED_INVALID, "The stored requirements are malformed and cannot be edited here.")
    original_analysis = _json(row["original_analysis_json"])
    original = original_analysis.get("requirements") if isinstance(original_analysis, dict) else None
    if not isinstance(original, dict) or validate_structure(original):
        original = None
    retired_raw = _json(row["requirements_retired_item_ids"])
    retired = [x for x in retired_raw if isinstance(x, str)] if isinstance(retired_raw, list) else []
    revision = row["requirements_revision"]
    marker = row["requirements_schema_version"]
    marker = int(marker) if marker is not None and str(marker).strip() != "" else None   # the column is read as text through to_jsonb()
    return Loaded(analysis, stored, original, int(revision) if revision is not None else None, retired,
                  marker=marker, retired_column_present=retired_raw is not None,
                  pipeline_record=analysis.get(pipe.STORAGE_KEY), extraction_status=row.get("criteria_extraction_status"),
                  extraction_error=row.get("criteria_extraction_error"))


def build_pending_view(*, job_id: str, revision: int, status: str | None, error: str | None, editable: bool) -> dict:
    """The view of a requirements-v2 job whose extraction has not produced a document: nothing is shown as checked, nothing can be edited."""
    failed = status == "failed"
    state = "extraction_failed" if failed else "extraction_pending"
    reason = {"code": "extraction_failed" if failed else "extraction_pending", "message": error or "", "category": None, "item_id": None}
    return {
        "job_id": str(job_id), "revision": revision, "requirements": {"schema_version": 2, "categories": {c: {"weight": 0, "items": []} for c in CATEGORIES}},
        "original": None, "edited_categories": None,
        "readiness": {"state": state, "scoring_mode": None, "can_proceed": False, "reasons": [reason], "open_warning_ids": [], "unresolved_warning_ids": [],
                      "structure_review_item_ids": [], "guarded": False, "basis": "extraction"},
        "pipeline": {"status": "not_extracted", "available": False, "errors": []},
        "unresolved_issues": None, "gates": None, "model_conflicts": None, "normalized_warnings": None, "informational": None,
        "classification_warnings": [], "classification_policy": {"key": POLICY_KEY, "require_acknowledgment": True},
        "preferred_only_confirmation": {"confirmed": False}, "structure_review": {"needs_review_item_ids": [], "items": []},
        "similarity_warnings": [], "similarity_method": SIMILARITY_METHOD, "discarded_client_fields": [],
        "extraction": {"status": status, "error": error, "retry_available": failed and editable},
        "can_edit": False,
    }


async def get_requirements(db, user, job_id: str) -> dict:
    """Read-only; any user who can see the job. Takes no lock and writes nothing."""
    loaded = await _load(db, user, job_id, lock=False)
    if loaded.stored is None:
        return build_pending_view(job_id=job_id, revision=loaded.revision or 0, status=loaded.extraction_status, error=loaded.extraction_error,
                                  editable=can_edit(user.role))
    policy = await load_policy(db)
    view = build_view(job_id=job_id, revision=loaded.revision if loaded.revision is not None else 0,
                      doc=loaded.stored, original=loaded.original, policy=policy, editable=can_edit(user.role),
                      pipeline_record=loaded.pipeline_record)
    if loaded.marker == 2:
        view["extraction"] = {"status": loaded.extraction_status, "error": loaded.extraction_error, "retry_available": False}
    return view


async def request_extraction_retry(db, user, job_id: str) -> dict:
    """Start a new v2 extraction attempt for a failed (or stuck) job. Admin / HR manager only, feature switch on, the job's own access. The task is queued
    AFTER the commit; a queued task that finds its token superseded does nothing."""
    from services import requirements_v2_extraction as ext
    ensure_can_edit(user.role)
    loaded = await _load(db, user, job_id, lock=False)
    if loaded.marker != 2:
        raise ApiError(409, CODE_NOT_V2, "Only requirements-v2 jobs have an extraction to retry.")
    if not await ext.feature_enabled(db):
        raise ApiError(409, CODE_FEATURE_OFF, "The requirements-v2 feature is switched off; no extraction can be started.")
    token = await ext.request_retry(db, job_id=str(job_id), actor_user=str(user.user_id), actor_tenant=str(user.tenant_id))
    if token is None:
        raise ApiError(409, CODE_RETRY_NOT_ALLOWED, "This job is not waiting for an extraction retry (it has a document, or an attempt is running).")
    # the retry sends the SAME job context the first attempt sent (the stored job fields are the ones the creation request carried)
    job = (await db.execute(text("""
        SELECT description, title, department, experience_level, location, job_type, work_mode
        FROM jobs WHERE job_id = CAST(:jid AS uuid)"""), {"jid": str(job_id)})).mappings().one()
    job_meta = {"title": job["title"], "department": job["department"], "experience_level": job["experience_level"], "location": job["location"],
                "job_type": job["job_type"], "work_mode": job["work_mode"]}
    await db.commit()
    from workers.requirements_v2_extraction_worker import extract_requirements_v2_task
    extract_requirements_v2_task.delay(str(job_id), token, job["description"], job_meta)
    return {"job_id": str(job_id), "extraction": {"status": "pending"}}


async def _mutate(db, user, job_id: str, expected_revision: int, work, client_discarded: list[str] | None = None) -> dict:
    """The common write transaction. work(loaded, policy, now) -> (new_doc, audits, retired, discarded)."""
    from services.audit_service import log_action
    ensure_can_edit(user.role)
    try:
        loaded = await _load(db, user, job_id, lock=True)
        if loaded.revision is None or not loaded.retired_column_present:
            # Both migration-107 columns are NOT NULL, so NULL here means the column does not exist. Refuse before any write.
            raise ApiError(503, CODE_MIGRATION, "Requirements editing needs migration 107 "
                                                "(requirements_revision, requirements_retired_item_ids), which has not been applied.")
        if loaded.marker is None or loaded.marker == "":
            # Recognised as v2 by the analysis shape only. The marker is never set implicitly: migration 106's weight
            # constraint depends on it, and setting it is a job-creation concern, not an edit.
            raise ApiError(409, CODE_MARKER_MISSING, "This job's analysis is requirements-v2 but job_criteria."
                                                     "requirements_schema_version is not set; it cannot be edited until "
                                                     "the marker is set.")
        if loaded.stored is None:
            raise ApiError(409, CODE_NOT_READY, "The requirements are not available yet: the extraction has not finished. Nothing was changed.",
                           extraction={"status": loaded.extraction_status, "error": loaded.extraction_error})
        if loaded.original is None:
            raise ApiError(409, CODE_ORIGINAL_MISSING, "The original analysis snapshot is missing; refusing to write.")
        policy = await load_policy(db)
        if loaded.pipeline_record is not None:
            problems = pipe.verify_record(loaded.pipeline_record, loaded.original)
            if problems:                                       # fail closed: never fall back to unguarded behaviour for a record that exists
                raise ApiError(409, CODE_PIPELINE_RECORD_INVALID, _MSG_INVALID, errors=problems)
        if loaded.revision != expected_revision:
            raise ApiError(409, CODE_REVISION_CONFLICT,
                           "The requirements were changed by someone else. Reload and try again.",
                           current=build_view(job_id=job_id, revision=loaded.revision, doc=loaded.stored,
                                              original=loaded.original, policy=policy, editable=True,
                                              pipeline_record=loaded.pipeline_record))
        plan: SavePlan = work(loaded, policy, _now())
        revision = loaded.revision
        if plan.changed:
            analysis = copy.deepcopy(loaded.analysis)
            analysis["requirements"] = plan.doc
            if plan.record is not None:
                analysis[pipe.STORAGE_KEY] = plan.record      # next to, never instead of, the single editable document
            params = {"aj": json.dumps(analysis, ensure_ascii=False), "retired": json.dumps(plan.retired),
                      "uid": str(user.user_id), "jid": str(job_id), "rev": loaded.revision,
                      **{f"w_{c}": plan.doc["categories"][c]["weight"] for c in CATEGORIES}}
            result = await db.execute(text(_UPDATE_SQL), params)
            if getattr(result, "rowcount", 1) != 1:
                raise ApiError(409, CODE_REVISION_CONFLICT, "The requirements were changed by someone else.")
            revision = loaded.revision + 1
            for action, details in plan.audits:
                await log_action(db, str(user.tenant_id), str(user.user_id), getattr(user, "email", None), action,
                                 resource_type="job", resource_id=str(job_id), strict=True,
                                 details={"job_id": str(job_id), "previous_revision": loaded.revision,
                                          "revision": revision, **details})
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    view = build_view(job_id=job_id, revision=revision, doc=plan.doc, original=loaded.original, policy=policy,
                      editable=True, discarded=plan.discarded + [f"body.{k}" for k in (client_discarded or [])],
                      pipeline_record=plan.record if plan.record is not None else loaded.pipeline_record)
    view["changed"] = plan.changed
    return view


async def save_requirements(db, user, job_id: str, expected_revision: int, requirements: Any,
                            client_discarded: list[str] | None = None) -> dict:
    """Invalid structure or weights: 422 and nothing is written (validate_final, unchanged). A valid document is saved even while review blockers
    (injection, split-OR, classification, conflict, structure, confirmation) are unresolved; readiness.can_proceed then stays false."""
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        reserved = collect_item_ids(loaded.original) | set(loaded.retired)
        return plan_save(loaded.stored, requirements, reserved_ids=reserved, retired_before=loaded.retired,
                         original=loaded.original, user_id=str(user.user_id), now=now, record=loaded.pipeline_record)
    return await _mutate(db, user, job_id, expected_revision, work, client_discarded)


async def acknowledge_warning(db, user, job_id: str, expected_revision: int, warning_id: str, gate: str = "classification",
                              client_discarded: list[str] | None = None) -> dict:
    """Acknowledge ONE classification warning (frozen lifecycle) or ONE model importance-conflict warning (pipeline lifecycle). Injection, split-OR and
    every other gate have no acknowledgment, under either policy."""
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        if gate in pipe.NOT_ACKNOWLEDGEABLE_GATES:
            raise ApiError(409, CODE_GATE_NOT_ACKNOWLEDGEABLE, f"The {gate} gate cannot be acknowledged; correct the requirements instead.", gate=gate)
        if gate not in ("classification", "conflict"):
            raise ApiError(422, CODE_UNKNOWN_GATE, f"Unknown gate {gate!r}.", gate=gate)
        try:
            if gate == "classification":
                doc = acknowledge_classification_warning(loaded.stored, warning_id, user_id=str(user.user_id), acknowledged_at=now)
                ack = next(a for a in doc[REVIEW_KEY]["acknowledgments"] if a["warning_id"] == warning_id)
                audit = (ACTION_ACKNOWLEDGED, {"warning_id": warning_id, "code": ack["code"], "item_id": ack["item_id"],
                                               "item_state_hash": ack["item_state_hash"],
                                               "evidence_hash": ack["evidence_hash"], "acknowledged_at": now})
                return SavePlan(doc, [audit], True, loaded.retired, [], [])
            if loaded.pipeline_record is None:
                raise ApiError(409, CODE_PIPELINE_MISSING, "This job has no pipeline record, so there is no model conflict to acknowledge.")
            records = pipe.acknowledge_gate(loaded.pipeline_record["review_records"], loaded.stored, "conflict", warning_id,
                                            user_id=str(user.user_id), at=now)
        except AcknowledgmentError as exc:
            if exc.code == "unknown_warning":
                raise ApiError(404, CODE_WARNING_NOT_FOUND, str(exc)) from exc
            raise ApiError(409, CODE_WARNING_STATE, str(exc), reason=exc.code) from exc
        ack = next(a for a in records["model_warnings"]["acknowledgments"] if a["warning_id"] == warning_id)
        audit = (ACTION_CONFLICT_ACKNOWLEDGED, {"warning_id": warning_id, "item_id": ack["item_id"], "item_state_hash": ack["item_state_hash"],
                                                "evidence_hash": ack["evidence_hash"], "acknowledged_at": now})
        return SavePlan(loaded.stored, [audit], True, loaded.retired, [], [], pipe.with_records(loaded.pipeline_record, records))
    return await _mutate(db, user, job_id, expected_revision, work, client_discarded)


async def confirm_no_score(db, user, job_id: str, expected_revision: int, client_discarded: list[str] | None = None) -> dict:
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        ctx = pipeline_context(loaded.pipeline_record, loaded.stored, loaded.original, policy)
        if ctx.status == PIPELINE_OK and ctx.state["readiness"]["state"] != "needs_confirmation":
            # an open injection / split-OR / (policy Yes) classification or conflict review keeps the confirmation out of reach
            raise ApiError(409, CODE_NOT_CONFIRMABLE, "Nothing to confirm yet: the requirements are not waiting for this confirmation.",
                           readiness_state=ctx.state["readiness"]["state"], reasons=ctx.state["readiness"]["reasons"])
        try:
            doc = confirm_no_numeric_score(loaded.stored, user_id=str(user.user_id), confirmed_at=now,
                                           require_classification_acknowledgment=policy, original=loaded.original)
        except ConfirmationError as exc:
            state = compute_readiness(loaded.stored, require_classification_acknowledgment=policy,
                                      original=loaded.original)
            raise ApiError(409, CODE_NOT_CONFIRMABLE, str(exc), readiness_state=state.state,
                           reasons=issues_payload(state.reasons)) from exc
        audit = (ACTION_CONFIRMED, {"basis_hash": doc["scoring_confirmation"]["basis_hash"], "confirmed_at": now})
        return SavePlan(doc, [audit], True, loaded.retired, [], [])
    return await _mutate(db, user, job_id, expected_revision, work, client_discarded)


async def confirm_structure_review(db, user, job_id: str, expected_revision: int, item_id: str,
                                   client_discarded: list[str] | None = None) -> dict:
    """The recruiter states that the item's CURRENT structure (OR alternatives / experience) still matches its CURRENT
    wording. Nothing checks that statement; the record names who made it, when, and exactly what was confirmed."""
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        try:
            doc = confirm_structure(loaded.stored, item_id, user_id=str(user.user_id), confirmed_at=now,
                                    original=loaded.original)
        except StructureError as exc:
            if exc.code == "unknown_item":
                raise ApiError(404, CODE_STRUCTURE_NOT_FOUND, str(exc)) from exc
            raise ApiError(409, CODE_STRUCTURE_STATE, str(exc), reason=exc.code) from exc
        rec = next(r for r in doc[STRUCTURE_KEY]["records"] if r["item_id"] == item_id)
        audit = (ACTION_STRUCTURE_CONFIRMED, {"item_id": item_id, "kind": rec["kind"], "basis_hash": rec["basis_hash"],
                                              "basis": rec["basis"], "confirmed_at": now})
        return SavePlan(doc, [audit], True, loaded.retired, [], [])
    return await _mutate(db, user, job_id, expected_revision, work, client_discarded)
