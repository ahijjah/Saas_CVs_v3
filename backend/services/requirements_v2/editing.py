"""
Recruiter edit operations as pure functions: each takes a document and returns a NEW one (inputs are never mutated).

Rules enforced here:
  - NO automatic redistribution. Adding, removing or reclassifying an item never touches any other weight; the
    recruiter uses Equalize (weights.py) or edits weights by hand, and the final-save validation decides validity.
  - The one forced change: when a category loses its last required item (removal or reclassification), its own
    category weight becomes 0 (a category without required items has no weight). The freed points are NOT moved.
  - Preferred items never keep a weight; a reclassified item to required starts unweighted.
  - Editing the wording keeps every other field, including source wording, provenance, OR alternatives and the
    structured experience subject/duration; nothing structured is discarded or rewritten on a text edit.
  - Ids are stable; new items get a fresh code-generated id.
The confirmation is left as it is: whether it survives is decided at save time by readiness.carry_confirmation.
"""
from __future__ import annotations

import copy

from services.requirements_v2.contract import (
    CATEGORIES, IMPORTANCE_PREFERRED, IMPORTANCE_REQUIRED, IMPORTANCES, ORIGIN_RECRUITER_ADDED,
    is_int, make_item, new_item_id,
)


def _locate(doc: dict, item_id: str) -> tuple[str, int]:
    for c in CATEGORIES:
        for idx, i in enumerate(doc["categories"][c]["items"]):
            if i["id"] == item_id:
                return c, idx
    raise KeyError(f"unknown item id {item_id!r}")


def _all_ids(doc: dict) -> list[str]:
    return [i["id"] for c in CATEGORIES for i in doc["categories"][c]["items"]]


def _zero_weight_if_no_required(doc: dict, category: str) -> None:
    cat = doc["categories"][category]
    if not any(i["importance"] == IMPORTANCE_REQUIRED for i in cat["items"]):
        cat["weight"] = 0


def add_item(doc: dict, category: str, text: str, importance: str, *, origin: str = ORIGIN_RECRUITER_ADDED,
             source_text: str | None = None, alternatives: list[str] | None = None,
             experience: dict | None = None, weight: int | None = None) -> tuple[dict, str]:
    """Append an item. Returns (new document, new item id). A required item without a weight leaves the
    document unbalanced until the recruiter sets weights."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if importance not in IMPORTANCES:
        raise ValueError("importance must be 'required' or 'preferred'")
    if importance == IMPORTANCE_PREFERRED and weight is not None:
        raise ValueError("preferred items carry no weight")
    out = copy.deepcopy(doc)
    item = make_item(text, importance, item_id=new_item_id(_all_ids(out)), weight=weight, origin=origin,
                     source_text=source_text, alternatives=alternatives, experience=experience)
    out["categories"][category]["items"].append(item)
    return out, item["id"]


def remove_item(doc: dict, item_id: str) -> dict:
    out = copy.deepcopy(doc)
    category, idx = _locate(out, item_id)
    del out["categories"][category]["items"][idx]
    _zero_weight_if_no_required(out, category)
    return out


def set_importance(doc: dict, item_id: str, importance: str) -> dict:
    if importance not in IMPORTANCES:
        raise ValueError("importance must be 'required' or 'preferred'")
    out = copy.deepcopy(doc)
    category, idx = _locate(out, item_id)
    item = out["categories"][category]["items"][idx]
    if item["importance"] != importance:
        item["importance"] = importance
        item["weight"] = None                      # preferred: no weight; new required: unweighted until set
        _zero_weight_if_no_required(out, category)
    return out


def set_text(doc: dict, item_id: str, text: str) -> dict:
    """Change the wording only. Source wording, provenance, alternatives and experience are kept untouched."""
    out = copy.deepcopy(doc)
    category, idx = _locate(out, item_id)
    out["categories"][category]["items"][idx]["text"] = text
    return out


def set_item_weight(doc: dict, item_id: str, weight: int | None) -> dict:
    out = copy.deepcopy(doc)
    category, idx = _locate(out, item_id)
    item = out["categories"][category]["items"][idx]
    if item["importance"] == IMPORTANCE_PREFERRED and weight is not None:
        raise ValueError("preferred items carry no weight")
    if weight is not None and not is_int(weight):
        raise ValueError("weight must be a whole number")
    item["weight"] = weight
    return out


def set_category_weight(doc: dict, category: str, weight: int) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if not is_int(weight):
        raise ValueError("weight must be a whole number")
    out = copy.deepcopy(doc)
    out["categories"][category]["weight"] = weight
    return out
