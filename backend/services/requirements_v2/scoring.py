"""Requirements v2 scoring inputs: the adapter from a stored document to the scoring pipeline's criteria and weights, the readiness
gate, and the revision guard that keeps a score tied to the document revision it was computed from.

    scoring_inputs(...)   the criteria, weights and revision for one v2 job; raises ScoringNotReady unless the document may be scored
    lock_revision(db, job_id, revision)   called inside the score's own transaction, immediately before the score is written

The adapter keeps the scoring pipeline's existing inputs: the category names are the legacy dimension names, so the gatekeeper and the
scoring prompt receive the same keys they always did. Only required items carry weight; preferred items are listed separately and are
never weighted. Nothing here calls a model.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from services.requirements_v2.contract import CATEGORIES, IMPORTANCE_REQUIRED, WEIGHT_COLUMN
from services.requirements_v2.readiness import compute_readiness
from services.requirements_v2.validation import validate_structure

PREFERRED_KEY = "preferred_requirements"


class ScoringNotReady(RuntimeError):
    """The document may not be scored: its readiness does not allow it, or it is not a usable document."""

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


class RequirementsRevisionChanged(RuntimeError):
    """The requirements changed after the score's document was read, so the score must not be written."""


@dataclass(frozen=True)
class ScoringInputs:
    criteria: dict[str, Any]          # the legacy dimension keys, plus PREFERRED_KEY
    weights: dict[str, int]           # the seven weight columns
    revision: int                     # the requirements revision the criteria were built from


def _item_text(item: dict) -> str:
    """A required item as the scorer reads it: its wording, its OR alternatives, and its experience requirement when stated."""
    parts = [item["text"]]
    if item.get("alternatives"):
        parts.append("(one of: " + " / ".join(item["alternatives"]) + ")")
    experience = item.get("experience") or {}
    if experience.get("min_years") is not None:
        parts.append(f"(minimum {experience['min_years']} years" + (f" of {experience['subject']})" if experience.get("subject") else ")"))
    return " ".join(parts)


def criteria_for_scoring(doc: dict) -> dict[str, Any]:
    """The scoring criteria for a valid document. Order is the document's order, so the same document always gives the same criteria."""
    criteria: dict[str, Any] = {c: [] for c in CATEGORIES}
    preferred: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for category in CATEGORIES:
        for item in doc["categories"][category]["items"]:
            if item["importance"] == IMPORTANCE_REQUIRED:
                criteria[category].append(_item_text(item))
            else:
                preferred[category].append(item["text"])
    criteria[PREFERRED_KEY] = {c: v for c, v in preferred.items() if v}
    return criteria


def weights_for_scoring(doc: dict) -> dict[str, int]:
    """The seven weight columns, from the document's category weights."""
    return {WEIGHT_COLUMN[c]: int(doc["categories"][c]["weight"]) for c in CATEGORIES}


def scoring_inputs(analysis_json: Any, original_analysis_json: Any, description: str, revision: int) -> ScoringInputs:
    """The inputs for one scoring run, from one row's values read together. Raises ScoringNotReady when the document is absent, invalid,
    or its readiness does not allow scoring. The original is the baseline for the structure review, as the editing API uses it."""
    doc = analysis_json.get("requirements") if isinstance(analysis_json, dict) else None
    if not isinstance(doc, dict):
        raise ScoringNotReady("no_document")
    if validate_structure(doc):
        raise ScoringNotReady("invalid_document")
    original = original_analysis_json.get("requirements") if isinstance(original_analysis_json, dict) else None
    readiness = compute_readiness(copy.deepcopy(doc), require_classification_acknowledgment=True, original=original,
                                  jd_text=description)
    if not readiness.can_proceed:
        raise ScoringNotReady(readiness.state)
    return ScoringInputs(criteria=criteria_for_scoring(doc), weights=weights_for_scoring(doc), revision=int(revision))


_LOCK_REVISION = text("SELECT requirements_revision FROM job_criteria WHERE job_id = :jid FOR SHARE")


async def lock_revision(db, job_id: str, revision: int) -> None:
    """Hold the job's criteria row in share mode and check that the revision is still the one the score used. An edit takes the row
    lock exclusively, so it waits until this transaction ends, and a score written here is always tied to the revision it read."""
    current = (await db.execute(_LOCK_REVISION, {"jid": job_id})).scalar_one_or_none()
    if current != revision:
        raise RequirementsRevisionChanged(f"requirements revision {revision} is no longer current ({current})")
