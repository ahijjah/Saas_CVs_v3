"""
Converts a criteria_extraction_v2 AI response into a requirements-v2 DRAFT (offline; no model call, no database).

    parse_response(raw_text, jd_text, finish_reason=None, id_factory=new_item_id) -> ExtractionResult

What code does:
  * parses the JSON strictly (no repair); truncated, non-JSON or structureless output FAILS -- nothing is guessed
  * builds one requirements-v2 item per AI item, in the AI's order, with code-generated ids
  * stores each item's source wording as the EXACT slice of the job description that the AI quoted (found after
    whitespace / case / Arabic-diacritic normalization); a quotation that is not in the job description is not stored
    (source_text None) and is reported
  * importance: the AI's classification is PRESERVED. Unspecified or invalid importance defaults to Required (reported).
    A Preferred item is checked, never changed: the cue the AI cites must be given, must occur in the job description AND
    must be tied to this item (inside the item's own wording, or in the heading that governs it). Anything less puts an
    item-specific "Needs review" warning on the item; a cue that merely exists elsewhere in the job description is not
    proof that it applies here. Preferred items carry no weight, so a flagged item stays out of the numeric score until
    the recruiter confirms or changes it.
  * gives required items equal whole-number weights (100 // n, remainder in list order); over-limit categories keep
    every item, leave those weights unset and are reported
  * normalizes the AI's category weights (explicit, proportional); an unusable proposal applies NOTHING and is reported
    together with a suggested fallback that the caller must choose to apply
  * keeps the original processed result (ids and initial weights included) unchanged for later comparison

What code deliberately does NOT do: no keyword reclassification, no weak-word removal, no deduplication or merging of
similar items, no splitting, no invented requirements. Anything the AI returned that cannot be represented is kept in
`unmapped` and reported; nothing is dropped silently.

Review issues (ExtractionResult.review) are for the recruiter; they never block the draft.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from services.requirements_v2.comparison import original_digest, snapshot_original
from services.requirements_v2.contract import (
    CATEGORIES, EXPERIENCE_CATEGORY, IMPORTANCE_PREFERRED, IMPORTANCE_REQUIRED, MAX_REQUIRED_PER_CATEGORY,
    ORIGIN_FROM_RESPONSIBILITIES, ORIGIN_STATED, SCHEMA_VERSION, Issue, is_int, make_item, new_item_id,
)
from services.requirements_v2.extraction.prompt import PROMPT_CODE, PROMPT_SHA256, PROMPT_VERSION
from services.requirements_v2.extraction.text import (
    contains_phrase, cue_relationship, locate_quote, locate_span, numbers_in,
)
from services.requirements_v2.readiness import Readiness, compute_readiness
from services.requirements_v2.validation import validate_draft
from services.requirements_v2.weights import (
    NORMALIZE_FALLBACK_REQUIRED, NORMALIZE_NO_ELIGIBLE, OverLimitError, apply_category_weights, equalize_weights,
    normalize_category_weights,
)

STATUS_DRAFT = "draft"
STATUS_FAILED = "failed"

SCOREABILITY_STATUSES = ("scoreable", "open_broad", "insufficient")
CONDITION_LISTS = ("non_scoreable_requirements", "post_hiring_conditions", "informational_items")
_ITEM_FIELDS = {"text", "importance", "importance_cue", "source_text", "origin", "alternatives", "experience"}
_QUOTE_PREVIEW = 80

NEEDS_REVIEW = "Needs review: "
CUE_MISSING = "preferred_cue_missing"
CUE_NOT_IN_JD = "preferred_cue_not_in_job_description"
CUE_NOT_LINKED = "preferred_cue_not_linked_to_item"
IMPORTANCE_CUE_REVIEW_CODES = (CUE_MISSING, CUE_NOT_IN_JD, CUE_NOT_LINKED)


@dataclass(frozen=True)
class ExtractionResult:
    status: str                                              # "draft" | "failed"
    prompt_code: str = PROMPT_CODE
    prompt_version: str = PROMPT_VERSION
    prompt_sha256: str = PROMPT_SHA256
    requirements: dict | None = None                         # the v2 draft document (current working copy)
    original: dict | None = None                             # immutable copy of the processed result, for comparison
    original_digest: str | None = None
    readiness: Readiness | None = None
    review: tuple[Issue, ...] = ()                           # for the recruiter; never blocks the draft
    errors: tuple[Issue, ...] = ()                           # why status == "failed"
    category_weights: dict | None = None                     # {"proposed", "status", "applied", "suggested_fallback"}
    conditions: dict = field(default_factory=dict)           # the three non-scoreable lists, normalized
    scoreability: dict | None = None
    ai_warnings: tuple[str, ...] = ()
    unmapped: tuple[dict, ...] = ()                          # AI content that could not be represented
    raw_ai_output: dict | None = None                        # the parsed AI JSON exactly as received

    @property
    def ok(self) -> bool:
        return self.status == STATUS_DRAFT

    @property
    def item_review(self) -> dict[str, tuple[Issue, ...]]:
        """Review issues that belong to one item, keyed by item id (what the recruiter must look at per item)."""
        grouped: dict[str, list[Issue]] = {}
        for issue in self.review:
            if issue.item_id:
                grouped.setdefault(issue.item_id, []).append(issue)
        return {k: tuple(v) for k, v in grouped.items()}

    @property
    def analysis(self) -> dict | None:
        """The analysis_json envelope a later stage would store (not stored anywhere by this module)."""
        if not self.ok:
            return None
        return {
            "requirements": copy.deepcopy(self.requirements),
            **{k: copy.deepcopy(self.conditions.get(k, [])) for k in CONDITION_LISTS},
            "warnings": list(self.ai_warnings),
            "scoreability": copy.deepcopy(self.scoreability),
            "extraction": {
                "prompt_code": self.prompt_code, "prompt_version": self.prompt_version,
                "prompt_sha256": self.prompt_sha256, "category_weights": copy.deepcopy(self.category_weights),
                "review": [{"code": i.code, "message": i.message, "category": i.category, "item_id": i.item_id}
                           for i in self.review],
                "unmapped": copy.deepcopy(list(self.unmapped)),
            },
        }


def _failed(code: str, message: str, raw: dict | None = None) -> ExtractionResult:
    return ExtractionResult(status=STATUS_FAILED, errors=(Issue(code, message),), raw_ai_output=raw)


def _preview(text: Any) -> str:
    s = text if isinstance(text, str) else repr(text)
    s = " ".join(s.split())
    return s if len(s) <= _QUOTE_PREVIEW else s[:_QUOTE_PREVIEW] + "..."


# ── item conversion ───────────────────────────────────────────────────────────

def _convert_item(raw: Any, category: str, position: int, jd: str, new_id: Callable[[], str],
                  review: list[Issue], unmapped: list[dict]) -> dict | None:
    """One AI item -> one requirements-v2 item, or None (recorded in `unmapped`) when no item can be built."""
    where = f"{category}[{position}]"

    def note(code: str, message: str, item_id: str | None = None, prefix: str = "") -> None:
        review.append(Issue(code, f"{prefix}{where}: {message}", category=category, item_id=item_id))

    plain_string = isinstance(raw, str)
    if plain_string:
        raw = {"text": raw}
    if not isinstance(raw, dict):
        unmapped.append({"category": category, "position": position, "reason": "item_not_an_object", "raw": raw})
        note("item_unusable", "the AI item is not an object; it is kept in the unmapped list")
        return None
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        unmapped.append({"category": category, "position": position, "reason": "item_text_missing", "raw": raw})
        note("item_unusable", "the AI item has no text; it is kept in the unmapped list")
        return None
    iid = new_id()
    if plain_string:
        note("item_is_plain_string", "the AI returned a bare string instead of an item object; "
                                     "importance defaults to required and no source wording is available", iid)

    extra = sorted(set(raw) - _ITEM_FIELDS)
    if extra:
        note("item_fields_ignored", f"unexpected fields ignored: {extra} (item weights are set by code)", iid)

    # source wording: the job description's own characters
    claimed = raw.get("source_text")
    source, span = None, None
    if claimed is None or (isinstance(claimed, str) and not claimed.strip()):
        if not plain_string:
            note("source_text_missing", "no source wording was given", iid)
    else:
        span = locate_span(jd, claimed) if isinstance(claimed, str) else None
        source = jd[span[0]:span[1]] if span else None
        if source is None:
            note("source_text_not_found",
                 f"the quoted wording was not found in the job description: \"{_preview(claimed)}\"", iid)

    # importance: the job description controls it; unspecified means required
    importance = IMPORTANCE_REQUIRED
    declared = raw.get("importance")
    declared_norm = declared.strip().lower() if isinstance(declared, str) else None
    if declared_norm == IMPORTANCE_REQUIRED:
        pass
    elif declared_norm == IMPORTANCE_PREFERRED:
        importance = IMPORTANCE_PREFERRED                  # the AI's classification is kept; support is only checked
        problem = _cue_problem(raw.get("importance_cue"), jd, span)
        if problem:
            note(problem[0], problem[1], iid, prefix=NEEDS_REVIEW)
    elif declared is None and not plain_string:
        note("importance_missing_defaulted_required", "no importance was given; set to required", iid)
    elif not plain_string:
        note("importance_invalid_defaulted_required",
             f"importance {_preview(declared)} is not 'required' or 'preferred'; set to required", iid)

    # origin
    origin = raw.get("origin")
    if origin not in (ORIGIN_STATED, ORIGIN_FROM_RESPONSIBILITIES):
        if origin is not None or not plain_string:
            note("origin_defaulted_stated", f"origin {_preview(origin)} is not valid; set to 'stated'", iid)
        origin = ORIGIN_STATED
    if origin == ORIGIN_FROM_RESPONSIBILITIES and category != EXPERIENCE_CATEGORY:
        note("responsibility_outside_experience_category",
             "a responsibility was placed outside the experience category; kept as returned", iid)

    # OR alternatives stay inside the one item
    alternatives = raw.get("alternatives")
    if alternatives is not None:
        if (isinstance(alternatives, list) and len(alternatives) >= 2
                and all(isinstance(a, str) and a.strip() for a in alternatives)):
            alternatives = [a.strip() for a in alternatives]
        else:
            note("alternatives_invalid_dropped",
                 f"alternatives must be a list of at least two strings; got {_preview(alternatives)}; "
                 "the wording stays in the item text", iid)
            alternatives = None

    experience = _convert_experience(raw.get("experience"), category, source or claimed, note, iid)

    return make_item(text.strip(), importance, item_id=iid, origin=origin, source_text=source,
                     alternatives=alternatives, experience=experience)


def _cue_problem(cue: Any, jd: str, span: tuple[int, int] | None) -> tuple[str, str] | None:
    """Why a Preferred classification is not established by the job description, or None when it is."""
    if not isinstance(cue, str) or not cue.strip():
        return (CUE_MISSING, "classified Preferred by the AI but no cue (the job description's own optional / "
                             "advantage wording) was given; confirm it is Preferred or change it to Required")
    if not contains_phrase(jd, cue):
        return (CUE_NOT_IN_JD, f"classified Preferred on the cue \"{_preview(cue)}\", which does not appear in the "
                               "job description; confirm it is Preferred or change it to Required")
    if span is None:
        return (CUE_NOT_LINKED, f"the cue \"{_preview(cue)}\" is in the job description but this item's source "
                                "wording was not found, so the cue cannot be tied to it; confirm it is Preferred or "
                                "change it to Required")
    if cue_relationship(jd, span, cue) is None:
        return (CUE_NOT_LINKED, f"the cue \"{_preview(cue)}\" appears in the job description but not in this item's "
                                "own wording or in the heading above it, so it may belong to another requirement; "
                                "confirm it is Preferred or change it to Required")
    return None


def _convert_experience(raw: Any, category: str, evidence: Any, note: Callable, iid: str) -> dict | None:
    if raw is None:
        return None
    if category != EXPERIENCE_CATEGORY:
        note("experience_structure_outside_experience_category",
             "structured experience given outside the experience category was not kept", iid)
        return None
    if not isinstance(raw, dict):
        note("experience_structure_invalid", f"experience must be an object; got {_preview(raw)}", iid)
        return None
    subject, years = raw.get("subject"), raw.get("min_years")
    if subject is not None and (not isinstance(subject, str) or not subject.strip()):
        note("experience_subject_invalid", f"subject {_preview(subject)} is not usable; set to null", iid)
        subject = None
    elif isinstance(subject, str):
        subject = subject.strip()
    if years is not None:
        if isinstance(years, float) and math.isfinite(years) and years == int(years):
            years = int(years)
        elif isinstance(years, str) and years.strip().isdigit():
            years = int(years.strip())
            note("experience_years_converted", "min_years was a string; converted to a number", iid)
        if not is_int(years) or years < 0:
            note("experience_years_invalid", f"min_years {_preview(raw.get('min_years'))} is not a whole number "
                                             "of years; set to null (the wording stays in the item text)", iid)
            years = None
    if subject is None and years is None:
        note("experience_structure_empty", "experience carried neither a subject nor a duration; not kept", iid)
        return None
    if years is not None and subject is None:
        note("experience_subject_missing", "a duration was given without a subject", iid)
    if years is not None and isinstance(evidence, str):
        found = numbers_in(evidence)
        if found and years not in found:
            note("experience_years_not_in_source_text",
                 f"min_years {years} does not match the numbers in the quoted wording {sorted(found)}", iid)
    return {"subject": subject, "min_years": years}


# ── conditions (non-scoreable / post-hiring / informational) ──────────────────

def _convert_conditions(raw: Any, list_name: str, jd: str, review: list[Issue], unmapped: list[dict]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        unmapped.append({"list": list_name, "reason": "not_a_list", "raw": raw})
        review.append(Issue("conditions_not_a_list", f"{list_name} is not a list; kept in the unmapped list"))
        return []
    out = []
    for pos, entry in enumerate(raw):
        if isinstance(entry, str):
            entry = {"text": entry}
        text = entry.get("text") if isinstance(entry, dict) else None
        if not isinstance(text, str) or not text.strip():
            unmapped.append({"list": list_name, "position": pos, "reason": "condition_unusable", "raw": entry})
            review.append(Issue("condition_unusable", f"{list_name}[{pos}] has no text; kept in the unmapped list"))
            continue
        quote = entry.get("source_text")
        source = locate_quote(jd, quote) if isinstance(quote, str) and quote.strip() else None
        if isinstance(quote, str) and quote.strip() and source is None:
            review.append(Issue("source_text_not_found",
                                f"{list_name}[{pos}]: quoted wording not found in the job description: "
                                f"\"{_preview(quote)}\""))
        out.append({"text": text.strip(), "category": entry.get("category") if isinstance(entry.get("category"), str)
                    else "other", "reason": entry.get("reason") if isinstance(entry.get("reason"), str) else "",
                    "source_text": source, "is_scoreable": False})
    return out


# ── main entry ────────────────────────────────────────────────────────────────

def parse_response(raw_text: Any, jd_text: str, finish_reason: str | None = None, *,
                   id_factory: Callable[[Iterable[str]], str] = new_item_id) -> ExtractionResult:
    """Convert the raw model output for `jd_text` into an ExtractionResult (see the module doc)."""
    if finish_reason == "length":
        return _failed("output_truncated", "The model output was cut off (finish_reason 'length'); it is not trusted.")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return _failed("no_response", "The model returned no content.")
    try:
        ai = json.loads(raw_text)
    except ValueError as exc:
        return _failed("invalid_json", f"The model output is not valid JSON: {exc}")
    return process_ai_output(ai, jd_text, id_factory=id_factory)


def process_ai_output(ai: Any, jd_text: str, *,
                      id_factory: Callable[[Iterable[str]], str] = new_item_id) -> ExtractionResult:
    if not isinstance(ai, dict):
        return _failed("not_an_object", "The model output is not a JSON object.")
    raw_copy = copy.deepcopy(ai)
    cats = ai.get("categories")
    if not isinstance(cats, dict):
        return _failed("missing_categories", "The model output has no 'categories' object.", raw_copy)
    jd = jd_text if isinstance(jd_text, str) else ""

    review: list[Issue] = []
    unmapped: list[dict] = []
    used_ids: set[str] = set()

    def new_id() -> str:
        iid = id_factory(used_ids)
        used_ids.add(iid)
        return iid

    categories: dict[str, dict] = {}
    for category in CATEGORIES:
        value = cats.get(category)
        if category not in cats:
            review.append(Issue("category_missing_in_output", f"the AI output has no '{category}' list; treated as empty",
                                category=category))
            value = []
        elif not isinstance(value, list):
            unmapped.append({"category": category, "reason": "category_not_a_list", "raw": value})
            review.append(Issue("category_not_a_list", f"'{category}' is not a list; kept in the unmapped list",
                                category=category))
            value = []
        items = [i for pos, raw in enumerate(value)
                 if (i := _convert_item(raw, category, pos, jd, new_id, review, unmapped)) is not None]
        categories[category] = {"weight": 0, "items": items}
    for name in cats:
        if name not in CATEGORIES:
            unmapped.append({"category": name, "reason": "unknown_category", "raw": cats[name]})
            review.append(Issue("unknown_category", f"the AI returned an unknown category '{name}'; kept in the "
                                                    "unmapped list", category=str(name)))

    # equal whole-number weights for required items (over-limit: keep every item, leave weights for review)
    for category, block in categories.items():
        required = [i for i in block["items"] if i["importance"] == IMPORTANCE_REQUIRED]
        try:
            for item, w in zip(required, equalize_weights(len(required))):
                item["weight"] = w
        except OverLimitError:
            review.append(Issue("too_many_required_items",
                                f"{len(required)} required items exceed the maximum of {MAX_REQUIRED_PER_CATEGORY}; "
                                "all items were kept and their weights left unset until the recruiter resolves it",
                                category=category))

    doc = {"schema_version": SCHEMA_VERSION, "categories": categories, "scoring_confirmation": None}

    # category weights: AI proposal, normalized by code, never replaced by an unapproved fallback
    eligible = [c for c in CATEGORIES if any(i["importance"] == IMPORTANCE_REQUIRED for i in categories[c]["items"])]
    proposed = ai.get("category_weights")
    norm = normalize_category_weights(proposed, eligible)
    weights_info: dict[str, Any] = {"proposed": copy.deepcopy(proposed), "status": norm.status, "applied": None,
                                    "suggested_fallback": None}
    review.extend(Issue(w.code, w.message, category=w.category) for w in norm.warnings)
    if norm.status == NORMALIZE_FALLBACK_REQUIRED:
        weights_info["suggested_fallback"] = norm.suggested_fallback
        review.append(Issue("category_weights_unusable",
                            "The AI's category weights are unusable. No weights were applied and no fallback was "
                            "chosen; set the category weights explicitly (a suggested equal split is attached)."))
    else:
        weights_info["applied"] = dict(norm.weights)
        doc = apply_category_weights(doc, norm.weights)
        if norm.status == NORMALIZE_NO_ELIGIBLE and isinstance(proposed, dict) and any(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in proposed.values()):
            review.append(Issue("category_weights_ignored_no_required_items",
                                "The AI proposed category weights but no category has a required item; all weights are 0."))

    checked = validate_draft(doc)
    if checked.errors:                                    # defensive: the builder above must not produce these
        return _failed("draft_contract_violation", "; ".join(f"{e.code}: {e.message}" for e in checked.errors), raw_copy)
    seen = {(i.code, i.category, i.item_id) for i in review}
    review.extend(i for i in checked.review if (i.code, i.category, i.item_id) not in seen)

    conditions = {name: _convert_conditions(ai.get(name), name, jd, review, unmapped) for name in CONDITION_LISTS}
    scoreability = _scoreability(ai.get("scoreability"), doc, review)
    ai_warnings = tuple(w for w in ai["warnings"] if isinstance(w, str)) if isinstance(ai.get("warnings"), list) else ()

    original = snapshot_original(doc)
    return ExtractionResult(
        status=STATUS_DRAFT, requirements=doc, original=original, original_digest=original_digest(original),
        readiness=compute_readiness(doc), review=tuple(review), category_weights=weights_info,
        conditions=conditions, scoreability=scoreability, ai_warnings=ai_warnings,
        unmapped=tuple(unmapped), raw_ai_output=raw_copy,
    )


def _scoreability(raw: Any, doc: dict, review: list[Issue]) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("status") not in SCOREABILITY_STATUSES:
        review.append(Issue("scoreability_invalid", f"scoreability {_preview(raw)} is not usable; ignored"))
        return None
    count = sum(len(doc["categories"][c]["items"]) for c in CATEGORIES)
    if raw["status"] == "insufficient" and count:
        review.append(Issue("scoreability_inconsistent", "the AI called the description insufficient but returned "
                                                         f"{count} requirement(s); they are kept for review"))
    if raw["status"] == "scoreable" and not count:
        review.append(Issue("scoreability_inconsistent", "the AI called the description scoreable but returned no "
                                                         "requirements"))
    return {"status": raw["status"], "reason": raw.get("reason") if isinstance(raw.get("reason"), str) else ""}
