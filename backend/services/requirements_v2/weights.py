"""
Integer weight arithmetic: required-item Equalize and category Normalize. Pure functions, no I/O.

Equalize     100 // n for every required item of ONE category, the remainder handed out one point at a time in list
             order (3 items -> 34/33/33). Preferred items are untouched (weight stays None).
Normalize    EXPLICIT, proportional rescaling of the category weights to whole percentages totalling 100:
               - ineligible categories (no required items) -> 0
               - eligible categories stay >= 1
               - largest-remainder rounding, ties broken by CATEGORIES order (deterministic)
             An unusable proposal (nothing positive for the eligible categories, or not numbers) is NOT silently
             replaced: the result says "fallback_required", carries warnings and an explicit suggested equal split
             that the caller may choose to apply.
Nothing here is called automatically when items are added, removed or reclassified.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable, Mapping

from services.requirements_v2.contract import (
    CATEGORIES, IMPORTANCE_REQUIRED, MAX_REQUIRED_PER_CATEGORY, Issue, is_int,
)

NORMALIZE_OK = "ok"
NORMALIZE_NO_ELIGIBLE = "no_eligible_categories"
NORMALIZE_FALLBACK_REQUIRED = "fallback_required"


class OverLimitError(ValueError):
    """More required items than positive whole-percent weights can cover (see MAX_REQUIRED_PER_CATEGORY)."""


def equalize_weights(n: int) -> list[int]:
    """Weights for n required items: 100 // n each, remainder to the first items in list order."""
    if not is_int(n) or n < 0:
        raise ValueError("n must be a non-negative integer")
    if n == 0:
        return []
    if n > MAX_REQUIRED_PER_CATEGORY:
        raise OverLimitError(
            f"{n} required items cannot all receive a positive whole-percent weight "
            f"(maximum {MAX_REQUIRED_PER_CATEGORY})")
    base, remainder = divmod(100, n)
    return [base + 1 if i < remainder else base for i in range(n)]


def equalize_category(doc: dict, category: str) -> dict:
    """A new document in which the required items of `category` carry the equalized weights.
    Nothing else changes (not the category weight, not other categories, not preferred items)."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    out = copy.deepcopy(doc)
    required = [i for i in out["categories"][category]["items"] if i["importance"] == IMPORTANCE_REQUIRED]
    for item, w in zip(required, equalize_weights(len(required))):
        item["weight"] = w
    return out


@dataclass(frozen=True)
class NormalizeResult:
    status: str                                   # NORMALIZE_*
    weights: dict[str, int] | None                # None when status == fallback_required
    warnings: tuple[Issue, ...] = ()
    suggested_fallback: dict[str, int] | None = None   # only with fallback_required; applying it is the caller's choice


def _usable(value: Any) -> Fraction | None:
    """A proposal value as an exact non-negative Fraction, or None when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0:
        return None
    return Fraction(value)


def _largest_remainder(shares: dict[str, Fraction], total: int) -> dict[str, int]:
    """Floor every share, then give the missing points to the largest fractional parts (ties: CATEGORIES order)."""
    floors = {c: int(s) for c, s in shares.items()}                       # int() floors non-negative Fractions
    missing = total - sum(floors.values())
    order = sorted(shares, key=lambda c: (-(shares[c] - floors[c]), CATEGORIES.index(c)))
    for c in order[:missing]:
        floors[c] += 1
    return floors


def _enforce_minimum_one(weights: dict[str, int]) -> list[str]:
    """Raise every 0 to 1, each time taking the point from the heaviest category (ties: CATEGORIES order).
    Returns the categories that were raised, in CATEGORIES order."""
    raised: list[str] = []
    for c in sorted(weights, key=CATEGORIES.index):
        if weights[c] >= 1:
            continue
        donors = [d for d in weights if weights[d] > 1]
        if not donors:                                                    # cannot happen for <= 7 categories
            raise ValueError("cannot keep every eligible category positive")
        donor = min(donors, key=lambda d: (-weights[d], CATEGORIES.index(d)))
        weights[donor] -= 1
        weights[c] = 1
        raised.append(c)
    return raised


def equal_category_weights(eligible: Iterable[str]) -> dict[str, int]:
    """Equal split of 100 across the eligible categories (remainder in CATEGORIES order); the rest get 0.
    This is the suggested fallback offered when a proposal is unusable. It is never applied implicitly."""
    elig = [c for c in CATEGORIES if c in set(eligible)]
    out = {c: 0 for c in CATEGORIES}
    for c, w in zip(elig, equalize_weights(len(elig))):
        out[c] = w
    return out


def normalize_category_weights(proposed: Mapping[str, Any] | None, eligible: Iterable[str]) -> NormalizeResult:
    """Proportionally rescale `proposed` category weights to whole percentages totalling 100 (see module doc).
    `eligible` are the categories that contain at least one required item."""
    eligible_set = {c for c in eligible if c in CATEGORIES}
    elig = [c for c in CATEGORIES if c in eligible_set]
    if not elig:
        return NormalizeResult(NORMALIZE_NO_ELIGIBLE, {c: 0 for c in CATEGORIES})

    proposed = proposed if isinstance(proposed, Mapping) else {}
    warnings: list[Issue] = []
    values: dict[str, Fraction] = {}
    for c in elig:
        v = _usable(proposed.get(c))
        if v is None:
            warnings.append(Issue("proposal_value_unusable",
                                  f"No usable proposed weight for {c!r}; treated as 0.", category=c))
            v = Fraction(0)
        values[c] = v
    for c in CATEGORIES:
        if c not in eligible_set and _usable(proposed.get(c)):
            warnings.append(Issue("proposal_for_ineligible_category",
                                  f"Proposed weight for {c!r} ignored: the category has no required items.",
                                  category=c))

    total = sum(values.values(), Fraction(0))
    if total <= 0:
        warnings.append(Issue("proposal_unusable",
                              "The proposed category weights contain nothing positive for the categories with "
                              "required items. No rule was applied; choose how to set them."))
        return NormalizeResult(NORMALIZE_FALLBACK_REQUIRED, None, tuple(warnings),
                               suggested_fallback=equal_category_weights(elig))

    shares = {c: values[c] * 100 / total for c in elig}
    weights = _largest_remainder(shares, 100)
    for c in _enforce_minimum_one(weights):
        warnings.append(Issue("category_raised_to_minimum",
                              f"{c!r} has required items but received less than 1%; raised to 1%.", category=c))
    result = {c: weights.get(c, 0) for c in CATEGORIES}
    return NormalizeResult(NORMALIZE_OK, result, tuple(warnings))


def apply_category_weights(doc: dict, weights: Mapping[str, int]) -> dict:
    """A new document with the given category weights (explicit action; used after Normalize)."""
    out = copy.deepcopy(doc)
    for c in CATEGORIES:
        out["categories"][c]["weight"] = int(weights[c])
    return out
