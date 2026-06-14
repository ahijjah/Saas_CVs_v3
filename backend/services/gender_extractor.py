"""
Conservative gender metadata extraction from CV text.

Rules:
  - Only infer from explicit CV text signals: titles (Mr/Mrs/Ms), pronouns
    (he/she), or direct self-statements.
  - Default to unknown when no clear signal exists.
  - Never infer from: photos, images, nationality, religion, or address.
  - Always sets gender_used_for_scoring = False.

Arabic title support: السيد (Mr) → male, السيدة (Mrs/Ms) → female.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── Allowed values ────────────────────────────────────────────────────────────

GENDER_MALE    = "male"
GENDER_FEMALE  = "female"
GENDER_UNKNOWN = "unknown"

BASIS_NAME         = "name"
BASIS_PRONOUN      = "pronoun"
BASIS_TITLE        = "title"
BASIS_EXPLICIT     = "explicit_cv_text"
BASIS_UNKNOWN      = "unknown"

SOURCE_CV_TEXT = "cv_text"


@dataclass(frozen=True)
class GenderInference:
    value:               str    # male | female | unknown
    confidence:          float  # 0.0 – 1.0
    basis:               str    # title | pronoun | explicit_cv_text | unknown
    source:              str    # always "cv_text"
    used_for_scoring:    bool   # always False


_UNKNOWN = GenderInference(
    value=GENDER_UNKNOWN,
    confidence=0.0,
    basis=BASIS_UNKNOWN,
    source=SOURCE_CV_TEXT,
    used_for_scoring=False,
)

# ── Title patterns ────────────────────────────────────────────────────────────
# Match at word boundary or start of string, followed by optional punctuation
# and whitespace, then a letter (to avoid matching isolated tokens like "Dr").
_MALE_TITLE = re.compile(
    # "Mr." (with dot) or "Mr " (with space) — but NOT "Mrs" or "Mrsomething".
    # Achieved by: mr followed by (dot then optional space) OR (space).
    # السيد uses negative lookahead for ة.
    r"(?i)(\bmr(?:\.|(?=\s))\s*[a-zA-Zأ-ي]|\bmister\s+[a-zA-Zأ-ي]|السيد(?!ة)\s*[أ-ي]|\bsir\s+[a-zA-Z])",
)
_FEMALE_TITLE = re.compile(
    r"(?i)(\bmrs\.?\s*[a-zA-Zأ-ي]|\bms\.?\s*[a-zA-Zأ-ي]|\bmiss\s+[a-zA-Z]|\bmadam[e]?\s*[a-zA-Z]|السيدة\s*[أ-ي]|الآنسة\s*[أ-ي])",
)

# ── Pronoun patterns ──────────────────────────────────────────────────────────
# First-person self-references using gendered pronouns.
# "I am he" is rare/unusual; focus on reflexive + possessive constructions
# that strongly imply the author's gender.
_MALE_PRONOUN = re.compile(
    r"(?i)\b(he/him|he\/his|pronouns?[:\s]+he)\b"
    r"|I\s+am\s+a?\s*male\s+professional"
    r"|\bpronoun[s]?\s*[:\-–]\s*he\b",
    re.IGNORECASE,
)
_FEMALE_PRONOUN = re.compile(
    r"(?i)\b(she/her|she\/hers|pronouns?[:\s]+she)\b"
    r"|I\s+am\s+a?\s*female\s+professional"
    r"|\bpronoun[s]?\s*[:\-–]\s*she\b",
    re.IGNORECASE,
)

# ── Explicit self-statement ───────────────────────────────────────────────────
_EXPLICIT_MALE = re.compile(
    r"(?i)\b(I\s+am\s+a\s+male|I\s+identify\s+as\s+male|gender\s*[:\-–]\s*male|sex\s*[:\-–]\s*male|ذكر)\b",
)
_EXPLICIT_FEMALE = re.compile(
    r"(?i)\b(I\s+am\s+a\s+female|I\s+identify\s+as\s+female|gender\s*[:\-–]\s*female|sex\s*[:\-–]\s*female|أنثى|female\s*candidate)\b",
)


def infer_gender(cv_text: str) -> GenderInference:
    """Infer gender from CV text using only explicit textual signals.

    Checks in priority order:
      1. Explicit self-statement (highest confidence — 0.95)
      2. Formal title like Mr./Mrs. in front of a name (confidence — 0.90)
      3. Stated pronouns e.g. "Pronouns: she/her" (confidence — 0.88)
      4. Default: unknown (confidence — 0.0)

    Never raises. Always returns a GenderInference with used_for_scoring=False.
    """
    if not cv_text or not cv_text.strip():
        return _UNKNOWN

    # Limit search to first 2000 chars (header/contact section where signals appear)
    sample = cv_text[:2000]

    # ── 1. Explicit self-statement ────────────────────────────────────────────
    if _EXPLICIT_MALE.search(sample):
        return GenderInference(
            value=GENDER_MALE,
            confidence=0.95,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )
    if _EXPLICIT_FEMALE.search(sample):
        return GenderInference(
            value=GENDER_FEMALE,
            confidence=0.95,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )

    # ── 2. Formal title ───────────────────────────────────────────────────────
    if _MALE_TITLE.search(sample):
        return GenderInference(
            value=GENDER_MALE,
            confidence=0.90,
            basis=BASIS_TITLE,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )
    if _FEMALE_TITLE.search(sample):
        return GenderInference(
            value=GENDER_FEMALE,
            confidence=0.90,
            basis=BASIS_TITLE,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )

    # ── 3. Stated pronouns ────────────────────────────────────────────────────
    if _MALE_PRONOUN.search(sample):
        return GenderInference(
            value=GENDER_MALE,
            confidence=0.88,
            basis=BASIS_PRONOUN,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )
    if _FEMALE_PRONOUN.search(sample):
        return GenderInference(
            value=GENDER_FEMALE,
            confidence=0.88,
            basis=BASIS_PRONOUN,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )

    return _UNKNOWN
