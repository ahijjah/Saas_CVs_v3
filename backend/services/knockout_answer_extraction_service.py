"""
Deterministic knockout answer extraction from email body content.

Architecture
────────────
1. Parse email_body_plain into (label, value) candidate pairs using
   multiple pattern strategies:
     A. "Label: Value" or "Label - Value"  — HTML-table-extracted rows (Formsite, etc.)
     B. Question line + standalone X/✓ + answer line  — Formsite plain-text format
     C. Inline "[x] Answer" / "✓ Answer" lines  — checkbox/radio HTML
     D. Question line immediately followed by answer  — proximity fallback

2. Fuzzy-match each extracted label to configured knockout question texts
   using RapidFuzz token_set_ratio (handles word reordering and partial matches).

3. Validate and normalise each matched answer against the question type
   (yes_no → "yes"/"no"; number → digit string; single_choice → closest option).

4. Return one ExtractionResult per question:
     resolved=True  → confidence ≥ 0.80, validated answer ready for storage
     resolved=False → no reliable match; question goes to AI fallback

Confidence scoring
──────────────────
  base            = label_fuzzy_score / 100          (range: 0.70–1.00 after threshold)
  +0.03           if pattern is "label_colon_value"  (structured HTML table source)
  +0.02           if explicit selection marker (X, ✓, [x], etc.)
  +0.02           if answer passes type validation cleanly
  cap at 1.00

Thresholds
──────────
  ≥ 0.90 → HIGH   confidence  — resolved, very likely correct
  ≥ 0.80 → MEDIUM confidence  — resolved, recruiter should review
  < 0.80 → unresolved          — sent to AI fallback

Data safety
───────────
This module NEVER writes to application_knockout_answers (final answers).
It returns ExtractionResult objects; the caller (knockout_analysis_service)
decides whether and how to persist them as suggestions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

_SCORE_HIGH   = 90    # ≥ 0.90 after confidence calc
_SCORE_MEDIUM = 80    # ≥ 0.80 after confidence calc
_SCORE_MIN    = 70    # minimum fuzzy score to consider at all

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    question_id:        str
    question_text:      str
    question_type:      str
    options:            list[str] | None
    suggested_answer:   str | None      # None → unresolved
    confidence:         float           # 0.00–1.00
    evidence_text:      str | None      # excerpt from email body
    label_match_score:  float           # raw RapidFuzz score 0–100
    extraction_pattern: str            # which pattern matched ("label_colon_value", etc.)
    resolved:           bool           # True if confidence ≥ _SCORE_MEDIUM / 100


# ── Pattern helpers ───────────────────────────────────────────────────────────

# A standalone selection marker occupying its own line (Formsite plain-text)
_STANDALONE_MARKER_RE = re.compile(r'^[Xx✓✔☑☒]$')

# An inline checkbox marker anywhere in the line
_INLINE_MARKER_RE = re.compile(
    r'(?:\[x\]|\[X\]|\[✓\]|\[✔\]|✓|✔|☑|☒)\s*([^\[\n]{1,150})',
    re.IGNORECASE,
)

# "Label: Value" or "Label - Value"  (labels 5–150 chars, value up to 500 chars)
_LABEL_VALUE_RE = re.compile(
    r'^(.{5,150}?)\s*(?::|[-–—])\s*(.{1,500})$',
)


# ── Answer normalisation ──────────────────────────────────────────────────────

_YES_TOKENS = frozenset({
    "yes", "y", "true", "1", "✓", "✔", "x", "checked", "selected",
    "نعم", "صح", "موافق",
})
_NO_TOKENS = frozenset({
    "no", "n", "false", "0", "unchecked", "not selected",
    "لا", "كلا",
})


def _normalize_yes_no(raw: str) -> str | None:
    v = raw.strip().lower()
    if v in _YES_TOKENS:
        return "yes"
    if v in _NO_TOKENS:
        return "no"
    return None


def _normalize_number(raw: str) -> str | None:
    m = re.search(r'(\d+(?:\.\d+)?)', raw.replace(",", ""))
    return m.group(1) if m else None


def _normalize_choice(raw: str, options: list[str]) -> str | None:
    """Return the best-matching option (exact then fuzzy ≥ 85), or raw value if no options."""
    if not options:
        cleaned = raw.strip()
        return cleaned or None
    raw_lower = raw.strip().lower()
    # Exact match
    for opt in options:
        if raw_lower == opt.strip().lower():
            return opt
    # Fuzzy match using partial_ratio — best for cases where the candidate value
    # is a shortened form of the full option label (e.g. "bachelors degree" →
    # "Bachelor's Degree or equivalent").  Threshold 85 keeps precision high.
    try:
        from rapidfuzz import fuzz as rff
        opts_lower = [o.strip().lower() for o in options]
        scored = [(rff.partial_ratio(raw_lower, ol), i) for i, ol in enumerate(opts_lower)]
        best_score, best_idx = max(scored, key=lambda x: x[0])
        if best_score >= 85:
            return options[best_idx]
    except ImportError:
        pass
    return None


def _validate_answer(
    raw: str,
    q_type: str,
    options: list[str] | None,
) -> tuple[str | None, float]:
    """
    Validate and normalise `raw` for the given question type.
    Returns (normalised_answer_or_None, type_validity_bonus).
    """
    raw = raw.strip()
    if not raw:
        return None, 0.0
    if q_type == "yes_no":
        norm = _normalize_yes_no(raw)
        return norm, 0.02 if norm else 0.0
    if q_type == "number":
        norm = _normalize_number(raw)
        return norm, 0.02 if norm else 0.0
    if q_type == "single_choice":
        norm = _normalize_choice(raw, options or [])
        return norm, 0.02 if norm else 0.0
    # text / open question — keep as-is
    return raw[:500], 0.01


# ── Body parser ───────────────────────────────────────────────────────────────

@dataclass
class _RawPair:
    label:    str
    value:    str
    pattern:  str    # diagnostic label for which extraction pattern found this
    marked:   bool   # explicit selection marker present
    evidence: str    # raw text snippet for evidence_text field


def _parse_body(body: str) -> list[_RawPair]:
    """
    Extract (label, value) candidate pairs from email body text using four
    pattern strategies.  Each label is deduplicated (first winning match kept).
    """
    pairs: list[_RawPair] = []
    seen_labels: set[str] = set()

    def _add(p: _RawPair) -> None:
        key = p.label.strip().lower()
        if key and p.value.strip() and key not in seen_labels:
            seen_labels.add(key)
            pairs.append(p)

    lines = body.splitlines()
    n     = len(lines)

    # ── Pattern A: "Label: Value" or "Label - Value" ─────────────────────────
    # Primary output of our HTML table extractor (_TextExtractor in cv_intake.py).
    # Also common in plain-text form notifications.
    for ln in lines:
        m = _LABEL_VALUE_RE.match(ln.strip())
        if m:
            label = m.group(1).strip().rstrip("?").strip()
            value = m.group(2).strip()
            if "://" not in value:  # skip URL lines mistaken as label:value
                _add(_RawPair(
                    label=label, value=value,
                    pattern="label_colon_value", marked=False, evidence=ln.strip(),
                ))

    # ── Pattern B: Question + standalone X/✓ + answer ────────────────────────
    # Formsite plain-text format:
    #   "What is your highest education level?\nX\nBachelor's Degree"
    i = 0
    while i < n:
        ln = lines[i].strip()
        if (ln
                and not _LABEL_VALUE_RE.match(ln)
                and i + 2 < n
                and _STANDALONE_MARKER_RE.match(lines[i + 1].strip())):
            answer_line = lines[i + 2].strip()
            if answer_line:
                label = ln.rstrip("?").strip()
                _add(_RawPair(
                    label=label, value=answer_line,
                    pattern="question_x_answer", marked=True,
                    evidence="\n".join(lines[i:i + 3]),
                ))
                i += 3
                continue
        i += 1

    # ── Pattern C: Inline "[x] Answer" / "✓ Answer" ──────────────────────────
    # HTML checkboxes/radio buttons whose state was preserved in text.
    for i, ln in enumerate(lines):
        m = _INLINE_MARKER_RE.search(ln)
        if not m:
            continue
        value = m.group(1).strip()
        # Find the nearest preceding non-empty non-checkbox line as the label
        label = ""
        for j in range(i - 1, max(i - 6, -1), -1):
            cand = lines[j].strip()
            if cand and not _INLINE_MARKER_RE.search(cand):
                label = cand.rstrip("?").strip()
                break
        if label and value:
            _add(_RawPair(
                label=label, value=value,
                pattern="inline_checkbox", marked=True,
                evidence=f"{label}\n{ln.strip()}",
            ))

    # ── Pattern D: Question line immediately followed by answer ──────────────
    # Lower-confidence proximity fallback — only applies when the answer is
    # short (< 200 chars) and on the very next line.
    for i in range(n - 1):
        ln  = lines[i].strip()
        nxt = lines[i + 1].strip()
        if (ln.endswith("?")
                and nxt
                and not _STANDALONE_MARKER_RE.match(nxt)
                and not _LABEL_VALUE_RE.match(nxt)
                and len(nxt) < 200):
            label = ln.rstrip("?").strip()
            _add(_RawPair(
                label=label, value=nxt,
                pattern="question_followed_by_answer", marked=False,
                evidence=f"{ln}\n{nxt}",
            ))

    return pairs


# ── Fuzzy label → question matching ──────────────────────────────────────────

def _match_label(
    label: str,
    questions: list[dict],
) -> tuple[dict | None, float]:
    """
    Return (best_matching_question, score 0–100) using RapidFuzz token_set_ratio.
    Returns (None, 0) if no question reaches _SCORE_MIN.
    """
    if not questions:
        return None, 0.0
    try:
        from rapidfuzz import process as rfp, fuzz as rff
    except ImportError:
        logger.warning("rapidfuzz not installed — deterministic extraction unavailable")
        return None, 0.0

    q_texts = [q["question_text"] for q in questions]
    result  = rfp.extractOne(
        label,
        q_texts,
        scorer=rff.token_set_ratio,
        score_cutoff=_SCORE_MIN,
    )
    if not result:
        return None, 0.0
    _matched_text, score, idx = result
    return questions[idx], float(score)


# ── Main public function ──────────────────────────────────────────────────────

async def extract_knockout_answers_from_email(
    db: AsyncSession,
    application_id: str,
    job_id: str,
    *,
    questions: list[dict] | None = None,
) -> list[ExtractionResult]:
    """
    Deterministically extract knockout answers from the stored email body.

    Args:
        questions: pre-loaded list of knockout question dicts (avoids a DB round-trip
                   if already fetched by the caller). If None, loaded from DB.

    Returns:
        One ExtractionResult per knockout question.
        resolved=True  → validated answer ready for storage as a suggestion.
        resolved=False → no reliable extraction; forward to AI fallback.

    Never raises — returns [] (all unresolved) on any unexpected error.
    """
    try:
        return await _extract(db, application_id, job_id, questions=questions)
    except Exception as exc:
        logger.error(
            "[%s] knockout_extraction failed unexpectedly: %s",
            application_id, exc, exc_info=True,
        )
        return []


# ── Private implementation ────────────────────────────────────────────────────

async def _extract(
    db: AsyncSession,
    application_id: str,
    job_id: str,
    *,
    questions: list[dict] | None,
) -> list[ExtractionResult]:

    # ── Load questions (or use pre-loaded list) ───────────────────────────────
    if questions is None:
        q_rows = await db.execute(
            text("""
                SELECT question_id, question_text, question_type, is_required, options
                FROM job_knockout_questions
                WHERE job_id = :jid
                ORDER BY display_order, created_at
            """),
            {"jid": job_id},
        )
        questions = [dict(r) for r in q_rows.mappings()]

    if not questions:
        return []

    # ── Load email body ───────────────────────────────────────────────────────
    body_row = await db.execute(
        text("""
            SELECT email_body_plain
            FROM application_intake_log
            WHERE application_id = CAST(:aid AS uuid)
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"aid": application_id},
    )
    body = body_row.scalar_one_or_none()

    if not body or not body.strip():
        logger.info(
            "[%s] knockout_extraction: no email body — all questions unresolved",
            application_id,
        )
        return _all_unresolved(questions)

    logger.info(
        "[%s] knockout_extraction: email_body_chars=%d questions=%d",
        application_id, len(body), len(questions),
    )

    # ── Parse body into raw pairs ─────────────────────────────────────────────
    raw_pairs = _parse_body(body)
    logger.info(
        "[%s] knockout_extraction: %d raw (label, value) pairs extracted",
        application_id, len(raw_pairs),
    )

    # ── Match pairs to questions ──────────────────────────────────────────────
    matched: dict[str, ExtractionResult] = {}   # qid → best result so far

    for pair in raw_pairs:
        question, label_score = _match_label(pair.label, questions)
        if question is None:
            continue

        qid    = str(question["question_id"])
        q_type = question["question_type"]
        opts   = question.get("options") or []
        if isinstance(opts, str):
            # Safety: options stored as comma-separated string in some schemas
            opts = [o.strip() for o in opts.split(",") if o.strip()]

        normalised, type_bonus = _validate_answer(pair.value, q_type, opts)
        if normalised is None:
            continue

        # Confidence: base from fuzzy score + structural bonuses
        base       = label_score / 100.0
        structured = 0.03 if pair.pattern == "label_colon_value" else 0.0
        marked     = 0.02 if pair.marked else 0.0
        confidence = min(1.0, base + structured + marked + type_bonus)

        existing = matched.get(qid)
        if existing is None or confidence > existing.confidence:
            matched[qid] = ExtractionResult(
                question_id        = qid,
                question_text      = question["question_text"],
                question_type      = q_type,
                options            = opts or None,
                suggested_answer   = normalised,
                confidence         = confidence,
                evidence_text      = (pair.evidence or pair.label)[:300],
                label_match_score  = label_score,
                extraction_pattern = pair.pattern,
                resolved           = confidence >= (_SCORE_MEDIUM / 100.0),
            )

    # ── Build output: one ExtractionResult per question ───────────────────────
    results: list[ExtractionResult] = []
    resolved_count   = 0
    unresolved_count = 0

    for q in questions:
        qid = str(q["question_id"])
        if qid in matched:
            r = matched[qid]
            results.append(r)
            if r.resolved:
                resolved_count += 1
                logger.debug(
                    "[%s] knockout_extraction resolved qid=%s answer=%r "
                    "confidence=%.2f pattern=%s score=%.0f",
                    application_id, qid, r.suggested_answer,
                    r.confidence, r.extraction_pattern, r.label_match_score,
                )
            else:
                unresolved_count += 1
        else:
            unresolved_count += 1
            results.append(ExtractionResult(
                question_id        = qid,
                question_text      = q["question_text"],
                question_type      = q["question_type"],
                options            = q.get("options") or None,
                suggested_answer   = None,
                confidence         = 0.0,
                evidence_text      = None,
                label_match_score  = 0.0,
                extraction_pattern = "none",
                resolved           = False,
            ))

    logger.info(
        "[%s] knockout_extraction: %d resolved, %d unresolved (of %d questions)",
        application_id, resolved_count, unresolved_count, len(questions),
    )
    return results


def _all_unresolved(questions: list[dict]) -> list[ExtractionResult]:
    return [
        ExtractionResult(
            question_id        = str(q["question_id"]),
            question_text      = q["question_text"],
            question_type      = q["question_type"],
            options            = q.get("options") or None,
            suggested_answer   = None,
            confidence         = 0.0,
            evidence_text      = None,
            label_match_score  = 0.0,
            extraction_pattern = "none",
            resolved           = False,
        )
        for q in questions
    ]
