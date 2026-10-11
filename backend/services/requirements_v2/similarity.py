"""
Duplicate / similarity WARNINGS for requirements-v2 (pure, stdlib only: unicodedata, re, difflib). No model, no
network, no database, nothing stored.

PURPOSE  Tell the recruiter that two items look related so that they can look. Nothing here merges, deletes,
reclassifies, re-weights or blocks anything, and the warnings are independent of classification acknowledgment,
readiness and the preferred-only / structure confirmations. They are recomputed from the items every time (so an edit
changes them at once) and are never persisted.

WHAT IT COMPARES  Every unordered pair of items, within a category and across categories, by their wording (plus the
structured OR alternatives / experience when present). Output per pair:
    kind "possible_duplicate"   the wording is the same after normalisation, or nearly so, and nothing known differs
    kind "similar_requirement"  overlapping wording, or a near-duplicate that differs in a number, alternatives, an
                                OR/AND connective, ... (listed in `differences`)
    related item ids + categories + importance, `same_category`, the two scores, and the `differences`.

METHOD  "lexical_v1": Unicode NFKC, case folding, Arabic diacritics / tatweel / alef-ya-ta-marbuta variants and
Arabic-Indic digits normalised; punctuation dropped; light suffix stripping (English plural "s", Arabic "ال"); a small
list of function words and year-units ignored. Two numbers are then computed on the remaining tokens:
    token_overlap  Jaccard similarity of the token sets
    string_ratio   difflib.SequenceMatcher ratio of the token sequences joined by spaces
THRESHOLDS (chosen on tests/fixtures/requirements_v2_similarity/pairs.json; see the table test in
tests/test_requirements_v2_similarity.py, which prints the scores):
    possible_duplicate   identical token sequences, or the same token SET (reordered words), or
                         token_overlap >= 0.85 and string_ratio >= 0.90      -- AND no known difference
    similar_requirement  token_overlap >= 0.60 and string_ratio >= 0.70      -- or a near-duplicate with a difference
    A single shared keyword never reaches either threshold; both items need >= 2 content tokens to be "similar"
    (single-token items are compared for exact equality only).

KNOWN DIFFERENCES (they stop "possible_duplicate" and are reported; the pair stays "similar_requirement" if it still
passes the similar threshold):
    numbers          the sets of numerals differ ("4 years" vs "7 years"); structured min_years differ
    alternatives     structured OR alternatives differ, or only one item has them
    connective       one wording uses OR and the other AND
    subject          structured experience subjects differ
NEGATION  If exactly one of the two wordings is negated ("no", "not", "without", "غير", "بدون", ...) the pair is NOT
reported at all: a near-identical wording with opposite meaning is not a duplicate and a "similar" label would mislead.

LIMITATIONS (this is lexical matching, not understanding):
  * Different languages are never compared: an Arabic-dominant item and a Latin-dominant item are skipped, so an
    Arabic requirement and its English translation are NOT detected. Mixed items compare on shared tokens only.
  * Synonyms and paraphrases ("developer" / "programmer", "bachelor" / "BSc") are not recognised; similar meaning with
    different words yields no warning. Same words in a different sense can still yield one.
  * Negation is detected only through a short word list; scope ("not only X but Y"), double negation and implicit
    negation are not understood. Number WORDS ("five") are not read as numbers, so "five years" vs "5 years" is
    reported as a difference-free mismatch of tokens, not as a duplicate.
  * OR / AND is read from the connective words and structured alternatives, not from meaning.
  * Experience durations are compared only as numerals; ranges, units (months vs years) and "at least" / "at most"
    are not interpreted.
  * Very short wordings that differ by one token (for example only a number: "4 years Planner" vs "7 years
    Planner") overlap by only 0.5 and are NOT reported; the structured min_years difference is only seen once the
    wordings are otherwise similar enough to pass the similar threshold.
  * Shared keywords alone never imply equivalence (the thresholds need most of both wordings to coincide), but a
    long wording that differs by one decisive word can still land above the thresholds; that is why the label is a
    warning for a person to review. The method makes no claim of semantic accuracy.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from services.requirements_v2.contract import CATEGORIES

METHOD = "lexical_v1"
KIND_DUPLICATE = "possible_duplicate"
KIND_SIMILAR = "similar_requirement"

DUP_OVERLAP, DUP_RATIO = 0.85, 0.90
SIM_OVERLAP, SIM_RATIO = 0.60, 0.70
MIN_TOKENS_FOR_SIMILAR = 2

DIFF_NUMBERS, DIFF_ALTERNATIVES, DIFF_CONNECTIVE, DIFF_SUBJECT = "numbers", "alternatives", "connective", "subject"

_STRIPPED = {0x0640, 0x0670, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x2060}
_STRIPPED |= set(range(0x064B, 0x0660)) | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_STOP_EN = {"a", "an", "the", "of", "in", "for", "to", "with", "on", "at", "by", "as", "is", "are", "be", "year",
            "years", "yr", "yrs"}
_STOP_AR = {"في", "من", "على", "الي", "عن", "مع", "هو", "هي", "سنه", "سنوات", "سنين", "عام", "اعوام"}
_OR = {"or", "او", "/"}
_AND = {"and", "و", "&"}
_NEGATION = {"no", "not", "non", "without", "never", "cannot", "dont", "doesnt", "cant", "wont", "isnt", "arent",
             "لا", "غير", "بدون", "دون", "ليس", "ليست", "عدم", "بلا"}
_TOKEN = re.compile(r"[^\W_]+|/|&", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def normalize(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if ord(ch) in _STRIPPED:
            continue
        out.append(ch)
    return "".join(out).casefold().replace("'", "").replace("\u2019", "").translate(_ARABIC_FOLD).translate(_DIGITS)


def _stem(tok: str) -> str:
    if tok.isascii() and len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    if len(tok) > 4 and tok.startswith("ال"):
        return tok[2:]
    return tok


@dataclass(frozen=True)
class Features:
    tokens: tuple[str, ...]            # content tokens, in order (connectives and negations included)
    numbers: frozenset[str]
    negated: bool
    has_or: bool
    has_and: bool
    script: str                        # "arabic" | "latin" | "none"


def _script(text: str) -> str:
    ar = sum(1 for c in text if "؀" <= c <= "ۿ")
    la = sum(1 for c in text if c.isascii() and c.isalpha())
    if ar == la == 0:
        return "none"
    return "arabic" if ar > la else "latin"


def features(text: str, alternatives: list[str] | None = None) -> Features:
    norm = normalize(text)
    raw = _TOKEN.findall(norm)
    content = []
    for t in raw:
        if t in _STOP_EN or t in _STOP_AR:
            continue
        content.append(_stem(t))
    nums = frozenset(n.replace(",", ".") for n in _NUMBER.findall(norm))
    return Features(tuple(content), nums, any(t in _NEGATION for t in raw),
                    any(t in _OR for t in raw) or bool(alternatives), any(t in _AND for t in raw), _script(norm))


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _alt_key(alternatives: list[str] | None) -> frozenset[str] | None:
    if not alternatives:
        return None
    return frozenset(" ".join(features(a).tokens) for a in alternatives)


@dataclass(frozen=True)
class PairResult:
    kind: str
    token_overlap: float
    string_ratio: float
    differences: tuple[str, ...]


def compare_items(a: dict, b: dict) -> PairResult | None:
    """Classify ONE pair of items, or None when they are not reported. Order of the arguments does not matter."""
    fa, fb = features(a["text"], a.get("alternatives")), features(b["text"], b.get("alternatives"))
    if not fa.tokens or not fb.tokens:
        return None
    if fa.script != fb.script and "none" not in (fa.script, fb.script):
        return None                                           # different languages are never compared
    if fa.negated != fb.negated:
        return None                                           # opposite polarity is not a duplicate
    sa, sb = set(fa.tokens), set(fb.tokens)
    overlap = _jaccard(sa, sb)
    ratio = SequenceMatcher(None, " ".join(fa.tokens), " ".join(fb.tokens), autojunk=False).ratio()
    identical = fa.tokens == fb.tokens or (sa == sb and len(fa.tokens) == len(fb.tokens))
    diffs: list[str] = []
    ea, eb = a.get("experience") or {}, b.get("experience") or {}
    if fa.numbers != fb.numbers or (ea.get("min_years") != eb.get("min_years") and (ea or eb)):
        diffs.append(DIFF_NUMBERS)
    ka, kb = _alt_key(a.get("alternatives")), _alt_key(b.get("alternatives"))
    if ka != kb:
        diffs.append(DIFF_ALTERNATIVES)
    if (fa.has_or and fb.has_and and not fb.has_or) or (fb.has_or and fa.has_and and not fa.has_or):
        diffs.append(DIFF_CONNECTIVE)
    if ea and eb and features(ea.get("subject") or "").tokens != features(eb.get("subject") or "").tokens:
        diffs.append(DIFF_SUBJECT)
    near_dup = identical or (overlap >= DUP_OVERLAP and ratio >= DUP_RATIO)
    similar = len(sa) >= MIN_TOKENS_FOR_SIMILAR and len(sb) >= MIN_TOKENS_FOR_SIMILAR \
        and overlap >= SIM_OVERLAP and ratio >= SIM_RATIO
    if near_dup and not diffs:
        return PairResult(KIND_DUPLICATE, round(overlap, 3), round(ratio, 3), ())
    if near_dup and (len(sa) >= MIN_TOKENS_FOR_SIMILAR or identical) or similar:
        return PairResult(KIND_SIMILAR, round(overlap, 3), round(ratio, 3), tuple(diffs))
    return None


def find_similar_items(doc: dict) -> list[dict]:
    """Warnings for a structurally valid document: one entry per related pair, in document order of the first item.
    Pure and idempotent; the document is not modified and nothing is stored."""
    flat = [(c, i) for c in CATEGORIES for i in doc["categories"][c]["items"]]
    out: list[dict] = []
    for x in range(len(flat)):
        for y in range(x + 1, len(flat)):
            (ca, ia), (cb, ib) = flat[x], flat[y]
            res = compare_items(ia, ib)
            if res is None:
                continue
            out.append({
                "id": f"{ia['id']}~{ib['id']}", "kind": res.kind,
                "items": [{"item_id": ia["id"], "category": ca, "importance": ia["importance"]},
                          {"item_id": ib["id"], "category": cb, "importance": ib["importance"]}],
                "same_category": ca == cb, "token_overlap": res.token_overlap, "string_ratio": res.string_ratio,
                "differences": list(res.differences), "method": METHOD,
            })
    return out
