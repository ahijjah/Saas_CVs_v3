"""
Original-versus-current comparison (the per-category "Edited" flag) and original immutability helpers.

A category is edited when its current content or weight differs from the original processed AI analysis:
  - the category weight, and
  - the ordered list of items compared on id, wording, importance, weight, OR alternatives and structured experience.
Source wording and origin are provenance of the extraction and never change, so they are not compared. Comparison is
derived on demand and never stored, so restoring the original values removes the flag by construction. An item that
is deleted and re-added gets a new id, so it still counts as a difference (identity is the id).

The original is never modified by anything in this package. original_digest() gives the persistence layer a cheap
way to assert that the stored original did not change.
"""
from __future__ import annotations

import copy
import hashlib
import json

from services.requirements_v2.contract import CATEGORIES

_COMPARED_ITEM_FIELDS = ("id", "text", "importance", "weight", "alternatives", "experience")


def _category_view(doc: dict, category: str) -> dict:
    cat = doc["categories"][category]
    return {
        "weight": cat["weight"],
        "items": [{f: i.get(f) for f in _COMPARED_ITEM_FIELDS} for i in cat["items"]],
    }


def edited_categories(original: dict, current: dict) -> dict[str, bool]:
    """{category: True if it differs from the original}, for all seven categories."""
    return {c: _category_view(original, c) != _category_view(current, c) for c in CATEGORIES}


def edited_category_names(original: dict, current: dict) -> list[str]:
    flags = edited_categories(original, current)
    return [c for c in CATEGORIES if flags[c]]


def snapshot_original(processed: dict) -> dict:
    """The immutable copy to store as the original: the processed AI analysis (ids and initial weights included),
    without any confirmation (which is server state, never part of the AI analysis)."""
    snap = copy.deepcopy(processed)
    snap["scoring_confirmation"] = None
    return snap


def original_digest(original: dict) -> str:
    blob = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
