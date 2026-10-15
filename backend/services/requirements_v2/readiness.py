"""
Readiness and the preferred-only confirmation. Pure functions; the caller supplies user id and timestamp.

State (compute_readiness):
  needs_review        the document breaks a final-save rule (malformed, unbalanced weights, over-limit extraction, ...)
  needs_items         valid but no items anywhere: the recruiter must add at least one before proceeding
  needs_classification_review
                      valid, but a classification warning (a Preferred item whose Preferred status the job
                      description does not establish) is unresolved and the policy requires acknowledgment. Evaluated
                      before the preferred-only confirmation: the recruiter settles the classification first.
  needs_confirmation  valid, only preferred items (all weights 0): proceeding without a numerical score needs an
                      explicit confirmation
  ready               valid with required items (weighted scoring), or preferred-only with a current confirmation
                      (scoring mode "none")

Policy: compute_readiness(doc, require_classification_acknowledgment=True) takes the admin setting EXPLICITLY (default:
acknowledgment required). With False, classification warnings are still reported (Readiness.open_warning_ids) but never
block. Only classification warnings are covered; no other extraction warning has any effect on readiness.
See acknowledgment.py for the warning / acknowledgment contract.

Confirmation (document["scoring_confirmation"]) is SERVER-OWNED:
  - it is created only by confirm_no_numeric_score(), called by trusted server code for an authenticated user;
  - carry_confirmation(stored, incoming) is the only way a save keeps it: whatever the client sent is ignored, and
    the stored one survives only while the job is still preferred-only AND the basis hash still matches;

  RULES FOR THE API STAGE (not implemented here):
    1. Every save discards the client's scoring_confirmation and builds the document to validate and persist with
       carry_confirmation(stored, incoming), where `stored` is read from the database inside the same locked
       transaction, never taken from the request.
    2. basis_hash is an unkeyed digest of the content: it detects that the content changed, it does not prove who
       confirmed. A matching hash alone is NOT authorization; validate_final accepting a document that carries a
       confirmation is not evidence the confirmation is genuine, so never run it on unprocessed client input.
    3. A confirmation is created only through confirm_no_numeric_score() in an endpoint that checks the caller's
       role, and that action is audit-logged.
  - basis_hash covers what the confirmation was given for: each item's category, id, importance, wording, OR
    alternatives and structured experience. Item order, weights and provenance are not part of it, so reordering
    does not invalidate, while wording, addition, removal, classification and structure changes do.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from services.requirements_v2.acknowledgment import (
    carry_classification_review, classification_status, unresolved_warnings,
)
from services.requirements_v2.contract import (
    CATEGORIES, CONFIRMATION_KIND, NEEDS_CLASSIFICATION_REVIEW, NEEDS_CONFIRMATION, NEEDS_ITEMS, NEEDS_REVIEW,
    NEEDS_STRUCTURE_REVIEW, READY, SCORING_NONE, SCORING_WEIGHTED, Issue, count_items,
)
from services.requirements_v2.structure import carry_structure_review, find_text_item, structure_status
from services.requirements_v2.validation import validate_final


class ConfirmationError(ValueError):
    """The job is not in a state that can be confirmed."""


@dataclass(frozen=True)
class Readiness:
    state: str                                    # READY | NEEDS_*
    scoring_mode: str | None                      # "weighted" | "none" when ready, else None
    reasons: tuple[Issue, ...] = ()               # why the job is not ready (empty when ready)
    open_warning_ids: tuple[str, ...] = ()        # classification warnings that still apply (visible under any policy)
    unresolved_warning_ids: tuple[str, ...] = ()  # ... of which not yet acknowledged (these block when policy is Yes)
    structure_review_item_ids: tuple[str, ...] = ()   # items whose wording / structure a person must still settle

    @property
    def can_proceed(self) -> bool:
        return self.state == READY


def is_preferred_only(doc: dict) -> bool:
    """At least one item and none of them required. Assumes a structurally valid document."""
    required, preferred = count_items(doc)
    return required == 0 and preferred > 0


def basis_hash(doc: dict) -> str:
    """Stable digest of the confirmed content (see module doc)."""
    rows = []
    for c in CATEGORIES:
        for i in sorted(doc["categories"][c]["items"], key=lambda x: x["id"]):
            rows.append([c, i["id"], i["importance"], i["text"], i.get("alternatives"), i.get("experience")])
    blob = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_readiness(doc: object, *, require_classification_acknowledgment: bool = True,
                      original: dict | None | str = "not_evaluated") -> Readiness:
    """`original` is the original AI analysis, the baseline for the structure review. Left out, the structure review
    is not evaluated (an extraction draft has nothing to review yet). Pass None for "no baseline available": every
    structured item without a record then needs review (fail closed). The editing API always passes it."""
    result = validate_final(doc)
    if not result.ok:
        return Readiness(NEEDS_REVIEW, None, result.errors)
    status = classification_status(doc)
    pending = structure_status(doc, original).needs_review if original != "not_evaluated" else ()  # type: ignore[arg-type]
    visible = dict(open_warning_ids=status.open, unresolved_warning_ids=status.unresolved,
                   structure_review_item_ids=pending)
    required, preferred = count_items(doc)           # type: ignore[arg-type]
    if required + preferred == 0:
        return Readiness(NEEDS_ITEMS, None, (Issue(
            "no_items", "There are no requirements. Add at least one before proceeding."),), **visible)
    if require_classification_acknowledgment and status.unresolved:
        return Readiness(NEEDS_CLASSIFICATION_REVIEW, None, tuple(
            Issue("classification_warning_unresolved", w["message"], category=w["category"], item_id=w["item_id"])
            for w in unresolved_warnings(doc)), **visible)
    if pending:
        return Readiness(NEEDS_STRUCTURE_REVIEW, None, tuple(
            Issue("structure_review_pending",
                  "The wording of this item changed (or its structure was never confirmed). Confirm that its OR "
                  "alternatives / experience still match, or correct them.",
                  category=find_text_item(doc, i)[0], item_id=i) for i in pending), **visible)
    if required > 0:
        return Readiness(READY, SCORING_WEIGHTED, **visible)
    if doc.get("scoring_confirmation") is not None:   # type: ignore[union-attr]
        return Readiness(READY, SCORING_NONE, **visible)  # validate_final already proved it is current
    return Readiness(NEEDS_CONFIRMATION, None, (Issue(
        "preferred_only_unconfirmed",
        "Only preferred items exist, so no numerical score will be produced. Explicit confirmation is required."),),
        **visible)


def confirm_no_numeric_score(doc: dict, *, user_id: str, confirmed_at: str,
                             require_classification_acknowledgment: bool = True,
                             original: dict | None | str = "not_evaluated") -> dict:
    """A new document carrying the confirmation. Allowed only while the job is preferred-only, valid and not
    already confirmed with a current basis. With the (default) policy that requires acknowledgment, unresolved
    classification warnings must be settled first: the recruiter decides what is Preferred before confirming that
    there will be no numerical score."""
    state = compute_readiness(doc, require_classification_acknowledgment=require_classification_acknowledgment,
                              original=original)
    if state.state != NEEDS_CONFIRMATION:
        raise ConfirmationError(f"Nothing to confirm (readiness is {state.state!r}).")
    if not user_id or not confirmed_at:
        raise ConfirmationError("user_id and confirmed_at are required.")
    out = copy.deepcopy(doc)
    out["scoring_confirmation"] = {
        "kind": CONFIRMATION_KIND, "user_id": str(user_id), "confirmed_at": str(confirmed_at),
        "basis_hash": basis_hash(doc),
    }
    return out


def carry_confirmation(stored: dict | None, incoming: dict) -> dict:
    """The document to validate and persist for a save: `incoming` with its confirmation replaced by the stored
    one if (and only if) it is still applicable. A confirmation supplied by the client is never trusted.

    `stored` must be the trusted, persisted document (never request data); anything that is not a dict, or
    carries no usable confirmation, counts as "no stored confirmation".
    A non-object `incoming` is returned as an unchanged copy so that validation rejects it (`not_an_object`);
    this function never raises for malformed input."""
    if not isinstance(incoming, dict):
        return copy.deepcopy(incoming)
    out = copy.deepcopy(incoming)
    out["scoring_confirmation"] = None
    prior = stored.get("scoring_confirmation") if isinstance(stored, dict) else None
    if not isinstance(prior, dict):
        return out
    try:
        applicable = is_preferred_only(out) and prior.get("basis_hash") == basis_hash(out)
    except (KeyError, TypeError, AttributeError, IndexError):   # incoming is malformed; validation rejects it
        return out
    if applicable:
        out["scoring_confirmation"] = copy.deepcopy(prior)
    return out


def carry_server_owned(stored: dict | None, incoming: dict) -> dict:
    """carry_confirmation + carry_classification_review + carry_structure_review: the single call an API save should make to rebuild all
    server-owned state from the TRUSTED stored document. Everything the client sent for these keys is discarded."""
    return carry_structure_review(stored, carry_classification_review(stored, carry_confirmation(stored, incoming)))
