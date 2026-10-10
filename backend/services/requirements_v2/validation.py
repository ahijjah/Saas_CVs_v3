"""
Validation for requirements v2 documents. Two entry points that share one structural check and one rule check:

  validate_draft(doc)   A reviewable extraction draft. Only MALFORMED input is an error. Rule violations (missing or
                        unbalanced weights, an over-limit category, an empty job) come back as `review` issues:
                        they are kept visible and nothing is dropped or repaired.
  validate_final(doc)   Final saved requirements. Structural problems and every rule violation are errors.

Rules (final):
  - required item weight: whole percent 1..100; preferred item weight: None
  - a category's required weights total exactly 100
  - at most MAX_REQUIRED_PER_CATEGORY required items per category
  - a category with required items has category weight >= 1; a category without has weight 0
  - any required item anywhere -> category weights total exactly 100; none -> all weights 0
  - duplicate items are allowed (same text, in or across categories); only ids must be unique
  - an empty job is a valid saved state (it just is not ready, see readiness.py)
  - a stored preferred-only confirmation must still match the current items
"""
from __future__ import annotations

from typing import Any

from services.requirements_v2.contract import (
    CATEGORIES, CONFIRMATION_KEYS, CONFIRMATION_KIND, EXPERIENCE_CATEGORY, IMPORTANCE_PREFERRED,
    IMPORTANCE_REQUIRED, IMPORTANCES, ITEM_ID_RE, ITEM_KEYS, ITEM_REQUIRED_KEYS, MAX_REQUIRED_PER_CATEGORY,
    ORIGINS, SCHEMA_VERSION, Issue, ValidationResult, count_items, is_int,
)


def _structure_issues(doc: Any) -> list[Issue]:
    """Malformed-input problems. Only reads `doc`; stops early when the skeleton is unusable."""
    out: list[Issue] = []
    if not isinstance(doc, dict):
        return [Issue("not_an_object", "Requirements must be an object.")]
    if doc.get("schema_version") != SCHEMA_VERSION:
        out.append(Issue("bad_schema_version", f"schema_version must be {SCHEMA_VERSION}."))
    cats = doc.get("categories")
    if not isinstance(cats, dict):
        out.append(Issue("bad_categories", "categories must be an object."))
        return out
    for c in CATEGORIES:
        if c not in cats:
            out.append(Issue("missing_category", f"Category {c!r} is missing.", category=c))
    for c in cats:
        if c not in CATEGORIES:
            out.append(Issue("unknown_category", f"Unknown category {c!r}.", category=str(c)))

    seen_ids: set[str] = set()
    for c in CATEGORIES:
        cat = cats.get(c)
        if cat is None:
            continue
        if not isinstance(cat, dict) or set(cat) != {"weight", "items"}:
            out.append(Issue("bad_category", "A category must be an object with exactly 'weight' and 'items'.",
                             category=c))
            continue
        w = cat["weight"]
        if not is_int(w) or not 0 <= w <= 100:
            out.append(Issue("bad_category_weight", "Category weight must be a whole number from 0 to 100.",
                             category=c))
        if not isinstance(cat["items"], list):
            out.append(Issue("bad_items", "items must be a list.", category=c))
            continue
        for item in cat["items"]:
            out.extend(_item_structure_issues(item, c, seen_ids))

    from services.requirements_v2.acknowledgment import REVIEW_KEY, review_block_issues
    out.extend(review_block_issues(doc.get(REVIEW_KEY)))
    from services.requirements_v2.structure import STRUCTURE_KEY, block_issues
    out.extend(block_issues(doc.get(STRUCTURE_KEY)))

    conf = doc.get("scoring_confirmation")
    if conf is not None:
        if not isinstance(conf, dict) or set(conf) != CONFIRMATION_KEYS or conf.get("kind") != CONFIRMATION_KIND \
                or not all(isinstance(conf.get(k), str) and conf.get(k) for k in CONFIRMATION_KEYS):
            out.append(Issue("bad_confirmation", "scoring_confirmation is malformed."))
    return out


def _item_structure_issues(item: Any, category: str, seen_ids: set[str]) -> list[Issue]:
    if not isinstance(item, dict):
        return [Issue("bad_item", "An item must be an object.", category=category)]
    iid = item.get("id") if isinstance(item.get("id"), str) else None
    out: list[Issue] = []

    def bad(code: str, msg: str) -> None:
        out.append(Issue(code, msg, category=category, item_id=iid))

    missing = ITEM_REQUIRED_KEYS - set(item)
    unknown = set(item) - ITEM_KEYS
    if missing:
        bad("item_missing_fields", f"Missing fields: {sorted(missing)}.")
    if unknown:
        bad("item_unknown_fields", f"Unknown fields: {sorted(unknown)}.")
    if missing:
        return out

    if iid is None or not ITEM_ID_RE.match(iid):
        bad("bad_item_id", "Item id must look like 'req_<letters/digits>'.")
    elif iid in seen_ids:
        bad("duplicate_item_id", "Item ids must be unique across the whole document.")
    else:
        seen_ids.add(iid)
    if not isinstance(item["text"], str) or not item["text"].strip():
        bad("empty_text", "Item text must be a non-empty string.")
    if item["importance"] not in IMPORTANCES:
        bad("bad_importance", "importance must be 'required' or 'preferred'.")
    if item["origin"] not in ORIGINS:
        bad("bad_origin", f"origin must be one of {list(ORIGINS)}.")
    w = item["weight"]
    if w is not None and not is_int(w):
        bad("bad_weight_type", "weight must be a whole number or null.")
    if item["importance"] == IMPORTANCE_PREFERRED and w is not None:
        bad("preferred_item_has_weight", "Preferred items carry no weight (must be null).")
    st = item.get("source_text")
    if st is not None and not isinstance(st, str):
        bad("bad_source_text", "source_text must be a string or null.")

    alts = item.get("alternatives")
    if alts is not None and (not isinstance(alts, list) or len(alts) < 2
                             or not all(isinstance(a, str) and a.strip() for a in alts)):
        bad("bad_alternatives", "alternatives must be null or a list of at least two non-empty strings.")

    exp = item.get("experience")
    if exp is not None:
        if category != EXPERIENCE_CATEGORY:
            bad("experience_outside_experience_category", "Structured experience belongs to the experience category.")
        elif not isinstance(exp, dict) or set(exp) != {"subject", "min_years"}:
            bad("bad_experience", "experience must be {'subject', 'min_years'}.")
        else:
            subj, yrs = exp["subject"], exp["min_years"]
            if subj is not None and (not isinstance(subj, str) or not subj.strip()):
                bad("bad_experience", "experience.subject must be a non-empty string or null.")
            elif yrs is not None and (not is_int(yrs) or yrs < 0):
                bad("bad_experience", "experience.min_years must be a non-negative whole number or null.")
            elif subj is None and yrs is None:
                bad("bad_experience", "experience must carry a subject, a duration, or both.")
    return out


def _rule_issues(doc: dict) -> list[Issue]:
    """Weight and state rules. Requires a structurally valid document."""
    out: list[Issue] = []
    any_required = False
    category_total = 0
    for c in CATEGORIES:
        cat = doc["categories"][c]
        required = [i for i in cat["items"] if i["importance"] == IMPORTANCE_REQUIRED]
        category_total += cat["weight"]
        if required:
            any_required = True
            if cat["weight"] < 1:
                out.append(Issue("category_weight_not_positive",
                                 "A category with required items needs a category weight of at least 1%.",
                                 category=c, params={"weight": cat["weight"]}))
        elif cat["weight"] != 0:
            out.append(Issue("category_weight_without_required_items",
                             "A category without required items must have weight 0.", category=c,
                             params={"weight": cat["weight"]}))

        if len(required) > MAX_REQUIRED_PER_CATEGORY:
            out.append(Issue("too_many_required_items",
                             f"{len(required)} required items exceed the maximum of {MAX_REQUIRED_PER_CATEGORY} "
                             "per category; reclassify or remove items. Nothing was dropped.", category=c,
                             params={"n": len(required), "max": MAX_REQUIRED_PER_CATEGORY}))
            continue                                   # item weights cannot be judged for this category
        for i in required:
            w = i["weight"]
            if w is None or not 1 <= w <= 100:
                out.append(Issue("required_weight_invalid",
                                 "A required item needs a whole-percent weight from 1 to 100.",
                                 category=c, item_id=i["id"], params={"weight": w}))
        if required and all(i["weight"] is not None and 1 <= i["weight"] <= 100 for i in required):
            total = sum(i["weight"] for i in required)
            if total != 100:
                out.append(Issue("required_weights_total", f"Required item weights total {total}%, not 100%.",
                                 category=c, params={"total": total, "expected": 100, "difference": total - 100}))
    if any_required and category_total != 100:
        out.append(Issue("category_weights_total", f"Category weights total {category_total}%, not 100%.",
                         params={"total": category_total, "expected": 100, "difference": category_total - 100}))
    return out


def _confirmation_issues(doc: dict) -> list[Issue]:
    conf = doc.get("scoring_confirmation")
    if conf is None:
        return []
    from services.requirements_v2.readiness import basis_hash, is_preferred_only   # local: avoids an import cycle
    if not is_preferred_only(doc):
        return [Issue("confirmation_not_applicable",
                      "A no-numeric-score confirmation exists but the job is not preferred-only.")]
    if conf["basis_hash"] != basis_hash(doc):
        return [Issue("confirmation_stale", "The items changed after the confirmation was given.")]
    return []


def validate_structure(doc: Any) -> tuple[Issue, ...]:
    """Malformed-input problems only (no weight / state rules, no confirmation freshness). For a persistence layer
    that must know whether a stored or incoming document can be read safely at all."""
    return tuple(_structure_issues(doc))


def validate_draft(doc: Any) -> ValidationResult:
    """Reviewable extraction draft: malformed input is an error, rule violations are review issues."""
    structure = _structure_issues(doc)
    if structure:
        return ValidationResult(errors=tuple(structure))
    if doc.get("scoring_confirmation") is not None:
        return ValidationResult(errors=(Issue(
            "confirmation_not_allowed_in_draft",
            "Confirmation is server-owned and cannot be part of an extraction draft."),))
    from services.requirements_v2.acknowledgment import REVIEW_KEY
    if (doc.get(REVIEW_KEY) or {}).get("acknowledgments"):
        return ValidationResult(errors=(Issue(
            "acknowledgment_not_allowed_in_draft",
            "Acknowledgments are server-owned and cannot be part of an extraction draft."),))
    from services.requirements_v2.structure import STRUCTURE_KEY
    if (doc.get(STRUCTURE_KEY) or {}).get("records"):
        return ValidationResult(errors=(Issue(
            "structure_record_not_allowed_in_draft",
            "Structure confirmations are server-owned and cannot be part of an extraction draft."),))
    review = _rule_issues(doc)
    if sum(count_items(doc)) == 0:
        review.append(Issue("no_items", "No requirements were found; the recruiter must add at least one."))
    return ValidationResult(errors=(), review=tuple(review))


def validate_final(doc: Any) -> ValidationResult:
    """Final saved requirements: every structural problem and rule violation is an error."""
    structure = _structure_issues(doc)
    if structure:
        return ValidationResult(errors=tuple(structure))
    return ValidationResult(errors=tuple(_rule_issues(doc) + _confirmation_issues(doc)))
