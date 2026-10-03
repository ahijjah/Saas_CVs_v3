"""
Conservative gender metadata extraction from CV text.

Rules:
  - Only infer from explicit CV text signals: labeled gender fields,
    stated titles (Mr/Mrs/Ms), or stated pronouns (she/her, he/him).
  - Default to unknown when no clear signal exists.
  - Never infer from: photos, images, nationality, religion, or address.
  - Always sets gender_used_for_scoring = False.

Arabic support: السيد (Mr) → male, السيدة (Mrs/Ms) → female,
الجنس: ذكر → male, الجنس: أنثى → female.
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

# ── Explicit labeled field (searched over FULL cv_text) ───────────────────────
#
# Matches all common CV personal-info field formats:
#   Gender: Male      Gender: Female
#   Gender : Male     Gender : Female
#   Gender- Male      Gender - Female
#   Gender Male       (label + value, no separator)
#   Sex: Male         Sex: Female
#   Sex : Female      Sex-Male
#   الجنس: ذكر        الجنس: أنثى
#   الجنس ذكر         الجنس أنثى
#   الجنس : ذكر       الجنس : أنثى
#
# Separator pattern: optional whitespace, then optionally one of : – -,
# then optional whitespace.  The (?<!\w) / (?!\w) guards prevent
# matching field labels that are part of a larger word.
#
# Note: Arabic \w includes Arabic letters, so (?!\w) correctly blocks
# ذكري (masculine adjective) while allowing ذكر (male).

_SEP = r"[ \t]*[:\-–]?[ \t]*"

_LABELED_MALE = re.compile(
    r"(?i)(?<!\w)(?:gender|sex|الجنس)" + _SEP + r"(?:male|ذكر)(?!\w)"
)
_LABELED_FEMALE = re.compile(
    r"(?i)(?<!\w)(?:gender|sex|الجنس)" + _SEP + r"(?:female|أنثى)(?!\w)"
)

# Standalone value on its own line: "Male\n", "Female\n", "ذكر\n", "أنثى\n"
# Confidence 1.0 — the word is entirely alone, unambiguous context.
_STANDALONE_MALE = re.compile(r"(?im)^[ \t]*(?:male|ذكر)[ \t]*$")
_STANDALONE_FEMALE = re.compile(r"(?im)^[ \t]*(?:female|أنثى)[ \t]*$")

# ── Title patterns (first 2000 chars — appear in CV header) ──────────────────
# "Mr." (with dot) or "Mr " (with space) — NOT "Mrs" / "Mrsomething".
# السيد uses negative lookahead for ة to avoid matching السيدة.
_MALE_TITLE = re.compile(
    r"(?i)(\bmr(?:\.|(?=\s))\s*[a-zA-Zأ-ي]|\bmister\s+[a-zA-Zأ-ي]"
    r"|السيد(?!ة)\s*[أ-ي]|\bsir\s+[a-zA-Z])",
)
_FEMALE_TITLE = re.compile(
    r"(?i)(\bmrs\.?\s*[a-zA-Zأ-ي]|\bms\.?\s*[a-zA-Zأ-ي]|\bmiss\s+[a-zA-Z]"
    r"|\bmadam[e]?\s*[a-zA-Z]|السيدة\s*[أ-ي]|الآنسة\s*[أ-ي])",
)

# ── Pronoun patterns (first 2000 chars — appear in CV header) ────────────────
_MALE_PRONOUN = re.compile(
    r"(?i)\b(he/him|he/his|pronouns?[:\s]+he)\b"
    r"|\bpronoun[s]?\s*[:\-–]\s*he\b",
)
_FEMALE_PRONOUN = re.compile(
    r"(?i)\b(she/her|she/hers|pronouns?[:\s]+she)\b"
    r"|\bpronoun[s]?\s*[:\-–]\s*she\b",
)


def infer_gender(cv_text: str) -> GenderInference:
    """Infer gender from CV text using only explicit textual signals.

    Priority order:
      1. Labeled gender field anywhere in the CV — confidence 1.0
         "Gender: Male", "الجنس: أنثى", "Sex - Female", …
      2. Standalone gender value on its own line — confidence 1.0
         A line containing only "Male" / "Female" / "ذكر" / "أنثى"
      3. Formal title in CV header — confidence 0.90
         "Mr.", "Mrs.", "Ms.", "السيد", "السيدة", …
      4. Stated pronouns in CV header — confidence 0.88
         "Pronouns: she/her", "he/him", …
      5. Default: unknown, confidence 0.0

    Never raises. Always returns a GenderInference with used_for_scoring=False.
    """
    if not cv_text or not cv_text.strip():
        return _UNKNOWN

    # Explicit labeled field — search full text because gender fields can
    # appear in personal-info sections anywhere in the document.
    if _LABELED_MALE.search(cv_text):
        return GenderInference(
            value=GENDER_MALE,
            confidence=1.0,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )
    if _LABELED_FEMALE.search(cv_text):
        return GenderInference(
            value=GENDER_FEMALE,
            confidence=1.0,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )

    # Standalone gender value on its own line
    if _STANDALONE_MALE.search(cv_text):
        return GenderInference(
            value=GENDER_MALE,
            confidence=1.0,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )
    if _STANDALONE_FEMALE.search(cv_text):
        return GenderInference(
            value=GENDER_FEMALE,
            confidence=1.0,
            basis=BASIS_EXPLICIT,
            source=SOURCE_CV_TEXT,
            used_for_scoring=False,
        )

    # Title and pronoun signals are reliably in the header — check first 2000 chars
    sample = cv_text[:2000]

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
