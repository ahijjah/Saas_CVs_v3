"""Deterministic item-level score arithmetic. Pure: no model, no database, no I/O, no floating point.

Required item result   score factor
    met                 1      (100%)
    partially_met       1/2    (50%, fixed: not configurable)
    not_met             0
    unclear             no automatic score; a person resolves it

Per category (a category's Required item weights total exactly 100):
    earned(item)            = item_weight x factor
    category_score          = sum of earned(item)                           (0 .. 100)
    category_contribution   = category_score x category_weight / 100
Final score                 = sum of category contributions                (0 .. 100)

Exactness: every value is a fractions.Fraction. With whole-number weights and the 1/2 factor a contribution is always a multiple of 1/200, so
it has an exact three-decimal form (exact_decimal refuses anything else). Nothing is rounded until display: the single rounding rule is
ROUND HALF UP of the FINAL score to a whole number (round_final: 73.5 -> 74, 84.1 -> 84). The stored decision compares that whole number
with the qualified and partial thresholds.

Unclear Required items: while any exists there is NO final score and NO decision (status review_required). The provisional range counts every
unclear item as 0 (minimum) or as its full weight (maximum). Once every Required item has a result, the status is scored.
A job without Required items is not_scored: the score is absent, never 0.

Preferred items (yes / no / not_clear) have no weight and never enter any number here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

MET, PARTIALLY_MET, NOT_MET, UNCLEAR = "met", "partially_met", "not_met", "unclear"
REQUIRED_RESULTS = (MET, PARTIALLY_MET, NOT_MET, UNCLEAR)
YES, NO, NOT_CLEAR = "yes", "no", "not_clear"
PREFERRED_RESULTS = (YES, NO, NOT_CLEAR)

PARTIAL_FACTOR = Fraction(1, 2)          # fixed by the product rule; deliberately not read from any configuration
FACTORS: Mapping[str, Fraction] = {MET: Fraction(1), PARTIALLY_MET: PARTIAL_FACTOR, NOT_MET: Fraction(0)}

SCORED, REVIEW_REQUIRED, NOT_SCORED = "scored", "review_required", "not_scored"
CATEGORIES = ("skills", "experience", "education", "certifications", "soft_skills", "domain_knowledge", "other_requirements")
DECIMAL_PLACES = 3


class ScoringInputError(ValueError):
    """The inputs cannot be scored: an unknown result, a bad weight, or a category whose Required weights do not total 100."""


@dataclass(frozen=True)
class ItemInput:
    item_id: str
    category: str
    weight: int
    result: str


@dataclass(frozen=True)
class ItemScore:
    item_id: str
    weight: int
    result: str
    earned: Fraction | None                 # None for an unclear item


@dataclass(frozen=True)
class CategoryScore:
    category: str
    weight: int
    items: tuple[ItemScore, ...]
    score: Fraction | None                  # None while any item is unclear
    score_min: Fraction
    score_max: Fraction
    contribution: Fraction | None
    contribution_min: Fraction
    contribution_max: Fraction


@dataclass(frozen=True)
class Breakdown:
    status: str                             # scored | review_required | not_scored
    categories: tuple[CategoryScore, ...]
    final_raw: Fraction | None              # exact; None unless scored
    final_min: Fraction | None              # None when not_scored
    final_max: Fraction | None
    unclear_item_ids: tuple[str, ...]

    @property
    def final_score(self) -> int | None:
        return None if self.final_raw is None else round_final(self.final_raw)


def round_final(value: Fraction) -> int:
    """The single rounding rule: half up, to a whole number, applied once to the final score."""
    return int((value + Fraction(1, 2)) // 1)


def exact_decimal(value: Fraction, places: int = DECIMAL_PLACES) -> Decimal:
    """The exact decimal of a Fraction. Raises ArithmeticError rather than rounding when it has no exact form with `places` decimals."""
    scaled = value * (10 ** places)
    if scaled.denominator != 1:
        raise ArithmeticError(f"{value} has no exact {places}-decimal form")
    return Decimal(scaled.numerator).scaleb(-places)


def format_number(value: Fraction | None) -> str | None:
    """A score as text with its exact value and no trailing zeros ('84.1', '27', '73.5'). Display only."""
    if value is None:
        return None
    text = format(exact_decimal(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _check(category_weights: Mapping[str, int], items: Sequence[ItemInput]) -> None:
    for category, weight in category_weights.items():
        if category not in CATEGORIES or isinstance(weight, bool) or not isinstance(weight, int) or not 0 <= weight <= 100:
            raise ScoringInputError(f"bad category weight for {category!r}")
    seen: set[str] = set()
    totals: dict[str, int] = {}
    for item in items:
        if item.item_id in seen:
            raise ScoringInputError(f"item {item.item_id!r} appears twice")
        seen.add(item.item_id)
        if item.category not in CATEGORIES:
            raise ScoringInputError(f"unknown category {item.category!r}")
        if item.result not in REQUIRED_RESULTS:
            raise ScoringInputError(f"{item.result!r} is not a Required-item result")
        if isinstance(item.weight, bool) or not isinstance(item.weight, int) or not 1 <= item.weight <= 100:
            raise ScoringInputError(f"bad item weight for {item.item_id!r}")
        totals[item.category] = totals.get(item.category, 0) + item.weight
    for category, total in totals.items():
        if total != 100:
            raise ScoringInputError(f"the Required item weights of {category!r} total {total}, not 100")
        if category_weights.get(category, 0) < 1:
            raise ScoringInputError(f"category {category!r} has Required items but no weight")
    if totals and sum(category_weights.get(c, 0) for c in totals) != 100 or any(
            w and c not in totals for c, w in category_weights.items()):
        raise ScoringInputError("the category weights of the categories with Required items must total exactly 100")


def compute(category_weights: Mapping[str, int], items: Iterable[ItemInput]) -> Breakdown:
    """The category scores, the final score or its provisional range, and the status. Deterministic and exact."""
    rows = list(items)
    _check(category_weights, rows)
    by_category: dict[str, list[ItemInput]] = {}
    for item in rows:
        by_category.setdefault(item.category, []).append(item)
    if not by_category:
        return Breakdown(NOT_SCORED, (), None, None, None, ())

    categories: list[CategoryScore] = []
    unclear: list[str] = []
    for category in CATEGORIES:
        group = by_category.get(category)
        if not group:
            continue
        weight = category_weights[category]
        scored_items: list[ItemScore] = []
        decided = Fraction(0)
        open_weight = Fraction(0)
        for item in group:
            if item.result == UNCLEAR:
                unclear.append(item.item_id)
                open_weight += item.weight
                scored_items.append(ItemScore(item.item_id, item.weight, item.result, None))
            else:
                earned = item.weight * FACTORS[item.result]
                decided += earned
                scored_items.append(ItemScore(item.item_id, item.weight, item.result, earned))
        score_min, score_max = decided, decided + open_weight
        resolved = open_weight == 0
        scale = Fraction(weight, 100)
        categories.append(CategoryScore(
            category=category, weight=weight, items=tuple(scored_items),
            score=score_min if resolved else None, score_min=score_min, score_max=score_max,
            contribution=score_min * scale if resolved else None,
            contribution_min=score_min * scale, contribution_max=score_max * scale))

    final_min = sum((c.contribution_min for c in categories), Fraction(0))
    final_max = sum((c.contribution_max for c in categories), Fraction(0))
    if unclear:
        return Breakdown(REVIEW_REQUIRED, tuple(categories), None, final_min, final_max, tuple(unclear))
    return Breakdown(SCORED, tuple(categories), final_min, final_min, final_max, ())


def preferred_summary(results: Iterable[str]) -> dict[str, int]:
    """{'yes': n, 'no': n, 'not_clear': n}. Preferred results never enter compute()."""
    counts = {YES: 0, NO: 0, NOT_CLEAR: 0}
    for result in results:
        if result not in counts:
            raise ScoringInputError(f"{result!r} is not a Preferred-item result")
        counts[result] += 1
    return counts
