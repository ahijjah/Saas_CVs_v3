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

No automatic weight redistribution: the server stores the weights the recruiter sent if (and only if) validate_final
accepts them; otherwise it answers 422 with the issues and stores nothing.

UNRESOLVED STRUCTURE-EDIT BEHAVIOR (reported, not decided)
  * Editing the wording of an item that carries OR alternatives or a structured experience (subject / min_years) keeps
    the structured fields as they were; the server cannot tell whether the new wording and the structure still agree.
    Such items are listed in `structure_unverified_item_ids` (derived from the original wording) and are NOT treated
    as verified.
  * Changing alternatives / experience through this API, supplying them on a new item, and moving an item to another
    category are refused (422) until the business decides how they should work.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from services.requirements_guard import is_requirements_v2
from services.requirements_v2 import (
    CATEGORIES, POLICY_KEY, SCHEMA_VERSION, AcknowledgmentError, ConfirmationError, Issue,
    acknowledge_classification_warning, classification_status, collect_item_ids, compute_readiness,
    confirm_no_numeric_score, edited_categories, make_item, new_item_id, parse_acknowledgment_policy,
    reconcile_classification_review, validate_final, validate_structure,
)
from services.requirements_v2.acknowledgment import REVIEW_KEY, get_review
from services.requirements_v2.contract import ORIGIN_RECRUITER_ADDED

EDIT_ROLES = ("admin", "hr_manager")                  # same rule as PUT /jobs/{id}/criteria/content

CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_NOT_V2 = "not_requirements_v2"
CODE_MISSING = "requirements_missing"
CODE_STORED_INVALID = "stored_requirements_invalid"
CODE_ORIGINAL_MISSING = "original_snapshot_missing"
CODE_REVISION_CONFLICT = "requirements_revision_conflict"
CODE_MIGRATION = "requirements_migration_missing"
CODE_INVALID = "invalid_requirements"
CODE_WARNING_NOT_FOUND = "classification_warning_not_found"
CODE_WARNING_STATE = "classification_warning_not_acknowledgeable"
CODE_NOT_CONFIRMABLE = "nothing_to_confirm"

ACTION_SAVED = "requirements_saved"
ACTION_ACKNOWLEDGED = "requirements_classification_acknowledged"
ACTION_ACK_INVALIDATED = "requirements_classification_ack_invalidated"
ACTION_WARNING_RESOLVED = "requirements_classification_warning_resolved"
ACTION_CONFIRMED = "requirements_preferred_only_confirmed"
ACTION_CONFIRMATION_INVALIDATED = "requirements_preferred_only_confirmation_invalidated"

WEIGHT_COLUMNS = {
    "skills": "weight_skills", "experience": "weight_experience", "education": "weight_education",
    "certifications": "weight_certifications", "soft_skills": "weight_soft_skills",
    "domain_knowledge": "weight_domain_knowledge", "other_requirements": "weight_other",
}

_DOC_INPUT_KEYS = frozenset({"schema_version", "categories", "scoring_confirmation", REVIEW_KEY})
_SERVER_OWNED_DOC_KEYS = ("scoring_confirmation", REVIEW_KEY)
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
                if any(raw.get(k) is not None for k in _STRUCTURED_KEYS):
                    raise bad("structured_field_edit_unsupported",
                              "OR alternatives and structured experience cannot be supplied through this API yet.", c)
                new_id = new_item_id(taken)
                taken.add(new_id)
                new_ids.append(new_id)
                items.append(make_item(raw["text"], raw["importance"], item_id=new_id, weight=raw.get("weight"),
                                       origin=ORIGIN_RECRUITER_ADDED, source_text=None))
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
            for k in _STRUCTURED_KEYS:
                if k in raw and raw[k] != prior.get(k):
                    raise bad("structured_field_edit_unsupported",
                              "OR alternatives and structured experience cannot be changed through this API yet.",
                              c, iid)
            for k in _SERVER_OWNED_ITEM_KEYS:
                if k in raw and raw[k] != prior.get(k):
                    discarded.append(f"categories.{c}.items[{n}].{k}")
            item = copy.deepcopy(prior)
            item["text"], item["importance"], item["weight"] = raw["text"], raw["importance"], raw.get("weight")
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


def _item_changes(stored: dict, doc: dict) -> dict[str, list[str]]:
    before, after = _index_items(stored), _index_items(doc)
    changed = [i for i in after if i in before and any(
        before[i][1].get(f) != after[i][1].get(f) for f in ("text", "importance", "weight"))]
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(changed)}


def plan_save(stored: dict, client: Any, *, reserved_ids: set[str], retired_before: list[str],
              original: dict) -> SavePlan:
    """Pure: everything a save does, short of I/O."""
    incoming = build_incoming(stored, client, reserved_ids)
    doc = _carry(stored, incoming.doc)
    result = validate_final(doc)
    if not result.ok:
        raise invalid(list(result.errors))
    ids_after = collect_item_ids(doc)
    retired = sorted(set(retired_before) | (collect_item_ids(stored) - ids_after))
    changes = _item_changes(stored, doc)
    audits = _reconcile_audits(stored, incoming.doc, doc)
    changed = doc != stored or retired != sorted(retired_before)
    if changed:
        audits.insert(0, (ACTION_SAVED, {
            **changes, "edited_categories": [c for c, f in edited_categories(original, doc).items() if f],
            "category_weights_changed": [c for c in CATEGORIES
                                         if doc["categories"][c]["weight"] != stored["categories"][c]["weight"]],
            "discarded_client_fields": incoming.discarded}))
    return SavePlan(doc, audits, changed, retired, incoming.new_item_ids, incoming.discarded)


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


def structure_unverified(original: dict | None, doc: dict) -> list[str]:
    """Items that carry OR alternatives / structured experience AND whose wording differs from the original wording.
    The structured fields were not re-checked against the new wording; they are reported, never treated as verified."""
    if original is None:
        return []
    before = _index_items(original)
    return sorted(i for i, (_, item) in _index_items(doc).items()
                  if (item.get("alternatives") or item.get("experience")) and i in before
                  and before[i][1]["text"] != item["text"])


def build_view(*, job_id: str, revision: int | None, doc: dict, original: dict | None, policy: bool,
               editable: bool, discarded: list[str] | None = None) -> dict:
    readiness = compute_readiness(doc, require_classification_acknowledgment=policy)
    conf = doc.get("scoring_confirmation")
    return {
        "job_id": str(job_id),
        "revision": revision,
        "requirements": _public(doc),
        "original": _public(original) if original is not None else None,
        "edited_categories": edited_categories(original, doc) if original is not None else None,
        "readiness": {
            "state": readiness.state, "scoring_mode": readiness.scoring_mode, "can_proceed": readiness.can_proceed,
            "reasons": issues_payload(readiness.reasons),
            "open_warning_ids": list(readiness.open_warning_ids),
            "unresolved_warning_ids": list(readiness.unresolved_warning_ids),
        },
        "classification_warnings": warnings_view(doc),
        "classification_policy": {"key": POLICY_KEY, "require_acknowledgment": policy},
        "preferred_only_confirmation": ({"confirmed": True, "user_id": conf["user_id"],
                                         "confirmed_at": conf["confirmed_at"]} if conf else {"confirmed": False}),
        "structure_unverified_item_ids": structure_unverified(original, doc),
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
           to_jsonb(jc) -> 'requirements_retired_item_ids' AS requirements_retired_item_ids
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
        raise ApiError(409, CODE_MISSING, "This job has no requirements document yet.")
    if validate_structure(stored):
        raise ApiError(409, CODE_STORED_INVALID, "The stored requirements are malformed and cannot be edited here.")
    original_analysis = _json(row["original_analysis_json"])
    original = original_analysis.get("requirements") if isinstance(original_analysis, dict) else None
    if not isinstance(original, dict) or validate_structure(original):
        original = None
    retired = _json(row["requirements_retired_item_ids"])
    retired = [x for x in retired if isinstance(x, str)] if isinstance(retired, list) else []
    revision = row["requirements_revision"]
    return Loaded(analysis, stored, original, int(revision) if revision is not None else None, retired)


async def get_requirements(db, user, job_id: str) -> dict:
    """Read-only; any user who can see the job. Takes no lock and writes nothing."""
    loaded = await _load(db, user, job_id, lock=False)
    policy = await load_policy(db)
    return build_view(job_id=job_id, revision=loaded.revision if loaded.revision is not None else 0,
                      doc=loaded.stored, original=loaded.original, policy=policy, editable=can_edit(user.role))


async def _mutate(db, user, job_id: str, expected_revision: int, work) -> dict:
    """The common write transaction. work(loaded, policy, now) -> (new_doc, audits, retired, discarded)."""
    from services.audit_service import log_action
    ensure_can_edit(user.role)
    try:
        loaded = await _load(db, user, job_id, lock=True)
        if loaded.revision is None:
            raise ApiError(503, CODE_MIGRATION, "Requirements editing needs migration 107, which has not been applied.")
        policy = await load_policy(db)
        if loaded.revision != expected_revision:
            raise ApiError(409, CODE_REVISION_CONFLICT,
                           "The requirements were changed by someone else. Reload and try again.",
                           current=build_view(job_id=job_id, revision=loaded.revision, doc=loaded.stored,
                                              original=loaded.original, policy=policy, editable=True))
        plan: SavePlan = work(loaded, policy, _now())
        revision = loaded.revision
        if plan.changed:
            analysis = copy.deepcopy(loaded.analysis)
            analysis["requirements"] = plan.doc
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
                      editable=True, discarded=plan.discarded)
    view["changed"] = plan.changed
    return view


async def save_requirements(db, user, job_id: str, expected_revision: int, requirements: Any) -> dict:
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        if loaded.original is None:
            raise ApiError(409, CODE_ORIGINAL_MISSING, "The original analysis snapshot is missing; refusing to save.")
        reserved = collect_item_ids(loaded.original) | set(loaded.retired)
        return plan_save(loaded.stored, requirements, reserved_ids=reserved, retired_before=loaded.retired,
                         original=loaded.original)
    return await _mutate(db, user, job_id, expected_revision, work)


async def acknowledge_warning(db, user, job_id: str, expected_revision: int, warning_id: str) -> dict:
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        try:
            doc = acknowledge_classification_warning(loaded.stored, warning_id, user_id=str(user.user_id),
                                                     acknowledged_at=now)
        except AcknowledgmentError as exc:
            if exc.code == "unknown_warning":
                raise ApiError(404, CODE_WARNING_NOT_FOUND, str(exc)) from exc
            raise ApiError(409, CODE_WARNING_STATE, str(exc), reason=exc.code) from exc
        ack = next(a for a in doc[REVIEW_KEY]["acknowledgments"] if a["warning_id"] == warning_id)
        audit = (ACTION_ACKNOWLEDGED, {"warning_id": warning_id, "code": ack["code"], "item_id": ack["item_id"],
                                       "item_state_hash": ack["item_state_hash"],
                                       "evidence_hash": ack["evidence_hash"], "acknowledged_at": now})
        return SavePlan(doc, [audit], True, loaded.retired, [], [])
    return await _mutate(db, user, job_id, expected_revision, work)


async def confirm_no_score(db, user, job_id: str, expected_revision: int) -> dict:
    def work(loaded: Loaded, policy: bool, now: str) -> SavePlan:
        try:
            doc = confirm_no_numeric_score(loaded.stored, user_id=str(user.user_id), confirmed_at=now,
                                           require_classification_acknowledgment=policy)
        except ConfirmationError as exc:
            state = compute_readiness(loaded.stored, require_classification_acknowledgment=policy)
            raise ApiError(409, CODE_NOT_CONFIRMABLE, str(exc), readiness_state=state.state,
                           reasons=issues_payload(state.reasons)) from exc
        audit = (ACTION_CONFIRMED, {"basis_hash": doc["scoring_confirmation"]["basis_hash"], "confirmed_at": now})
        return SavePlan(doc, [audit], True, loaded.retired, [], [])
    return await _mutate(db, user, job_id, expected_revision, work)
