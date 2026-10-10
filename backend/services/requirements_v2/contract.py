"""
Requirements v2 contract: constants, result types and small constructors.

The canonical representation is a plain JSON-compatible dict (it is stored as JSONB later); every function in this
package treats its inputs as read-only and returns new objects.

    {
      "schema_version": 2,
      "categories": {                       # exactly the seven categories in CATEGORIES
        "<category>": {
          "weight": int,                    # whole percent, 0..100
          "items": [ITEM, ...]              # order is meaningful (Equalize hands remainders out in list order)
        }
      },
      "scoring_confirmation": None | {      # SERVER-OWNED, see readiness.py
        "kind": "no_numeric_score", "user_id": str, "confirmed_at": str, "basis_hash": str
      }
    }

    classification_review (optional, SERVER-OWNED, see acknowledgment.py) holds the extraction's classification warnings and
    the recruiters' acknowledgments of them; it is never client input either.

    scoring_confirmation is never client input. basis_hash is an unkeyed digest of the confirmed content, so anyone
    can compute a matching one: a matching hash proves nothing about WHO confirmed. Only readiness.confirm_no_numeric_score
    (trusted server code, authenticated user) creates it, and every save must go through readiness.carry_confirmation.

    ITEM = {
      "id": "req_<alnum>",                  # code-generated, stable, never reused
      "text": str,                          # wording shown to the recruiter; free-form, authoritative
      "importance": "required" | "preferred",
      "weight": int | None,                 # required: whole percent within the category; preferred: None
      "origin": "stated" | "from_responsibilities" | "recruiter_added",
      "source_text": str | None,            # verbatim JD wording (None for recruiter-added items)
      "alternatives": [str, ...] | None,    # OR alternatives kept inside ONE item (>= 2 entries)
      "experience": {"subject": str | None, "min_years": int | None} | None   # experience category only
    }

NOT WIRED into any production path (extraction, API, UI, scoring, intake). Importing this package has no side
effects and never touches the database or a model.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

SCHEMA_VERSION = 2

# The seven existing categories, in the fixed order used for deterministic tie-breaking.
CATEGORIES: tuple[str, ...] = (
    "skills", "experience", "education", "certifications",
    "soft_skills", "domain_knowledge", "other_requirements",
)

IMPORTANCE_REQUIRED = "required"
IMPORTANCE_PREFERRED = "preferred"
IMPORTANCES = (IMPORTANCE_REQUIRED, IMPORTANCE_PREFERRED)

ORIGIN_STATED = "stated"
ORIGIN_FROM_RESPONSIBILITIES = "from_responsibilities"
ORIGIN_RECRUITER_ADDED = "recruiter_added"
ORIGINS = (ORIGIN_STATED, ORIGIN_FROM_RESPONSIBILITIES, ORIGIN_RECRUITER_ADDED)

EXPERIENCE_CATEGORY = "experience"

# Positive integer weights summing to 100 make more than 100 required items in one category impossible.
MAX_REQUIRED_PER_CATEGORY = 100

CONFIRMATION_KIND = "no_numeric_score"
CONFIRMATION_KEYS = frozenset({"kind", "user_id", "confirmed_at", "basis_hash"})

ITEM_REQUIRED_KEYS = frozenset({"id", "text", "importance", "weight", "origin"})
ITEM_OPTIONAL_KEYS = frozenset({"source_text", "alternatives", "experience"})
ITEM_KEYS = ITEM_REQUIRED_KEYS | ITEM_OPTIONAL_KEYS

ITEM_ID_RE = re.compile(r"^req_[A-Za-z0-9]{1,32}$")

# Readiness states (readiness.py)
READY = "ready"
NEEDS_ITEMS = "needs_items"                  # no items anywhere: the recruiter must add at least one
NEEDS_CONFIRMATION = "needs_confirmation"    # preferred-only: explicit confirmation to proceed without a score
NEEDS_REVIEW = "needs_review"                # structurally or numerically invalid (incl. over-limit extraction)
NEEDS_CLASSIFICATION_REVIEW = "needs_classification_review"   # unresolved classification warnings, policy requires acknowledgment
NEEDS_STRUCTURE_REVIEW = "needs_structure_review"   # an item's wording changed while its OR alternatives / experience did not settle
READINESS_STATES = (READY, NEEDS_ITEMS, NEEDS_CLASSIFICATION_REVIEW, NEEDS_STRUCTURE_REVIEW, NEEDS_CONFIRMATION,
                    NEEDS_REVIEW)

SCORING_WEIGHTED = "weighted"
SCORING_NONE = "none"


@dataclass(frozen=True)
class Issue:
    """One finding. `code` is stable and machine-readable; `message` is for people. `params` carries the numbers behind the message (totals,
    expected value, difference, counts) so the editor can say exactly what is wrong; it never changes which findings exist."""
    code: str
    message: str
    category: str | None = None
    item_id: str | None = None
    params: dict | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class ValidationResult:
    """errors: the document is malformed or (final mode) breaks a rule and must not be saved.
    review: (draft mode only) rule violations that are kept visible for the recruiter instead of blocking."""
    errors: tuple[Issue, ...] = ()
    review: tuple[Issue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> list[str]:
        return [i.code for i in self.errors] + [i.code for i in self.review]


def collect_item_ids(doc: dict) -> set[str]:
    """Every item id in a document (use it to reserve the ids of the original or of deleted items)."""
    return {i["id"] for c in CATEGORIES for i in doc["categories"][c]["items"]}


def new_item_id(existing: Iterable[str] = ()) -> str:
    """A fresh code-generated item id, distinct from `existing`."""
    taken = set(existing)
    while True:
        candidate = "req_" + uuid.uuid4().hex[:12]
        if candidate not in taken:
            return candidate


def make_item(text: str, importance: str, *, item_id: str | None = None, weight: int | None = None,
              origin: str = ORIGIN_STATED, source_text: str | None = None,
              alternatives: list[str] | None = None, experience: dict | None = None) -> dict:
    """Build an item dict. Does not validate (validation.py does)."""
    return {
        "id": item_id or new_item_id(),
        "text": text,
        "importance": importance,
        "weight": weight,
        "origin": origin,
        "source_text": source_text,
        "alternatives": list(alternatives) if alternatives is not None else None,
        "experience": dict(experience) if experience is not None else None,
    }


def empty_requirements() -> dict:
    """A well-formed document with no items and all weights 0."""
    return {
        "schema_version": SCHEMA_VERSION,
        "categories": {c: {"weight": 0, "items": []} for c in CATEGORIES},
        "scoring_confirmation": None,
    }


def is_int(value: Any) -> bool:
    """True for real integers; bool is excluded on purpose."""
    return isinstance(value, int) and not isinstance(value, bool)


def items_of(doc: dict, category: str) -> list[dict]:
    return doc["categories"][category]["items"]


def required_items(doc: dict, category: str) -> list[dict]:
    return [i for i in items_of(doc, category) if i["importance"] == IMPORTANCE_REQUIRED]


def count_items(doc: dict) -> tuple[int, int]:
    """(required, preferred) item counts across all categories."""
    req = pre = 0
    for c in CATEGORIES:
        for i in items_of(doc, c):
            if i["importance"] == IMPORTANCE_REQUIRED:
                req += 1
            else:
                pre += 1
    return req, pre
