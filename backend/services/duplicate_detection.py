"""
Duplicate CV detection service — intra-job, two-layer, no LLM.

Exact-match duplicate detection (primary, O(1)):
  Three independent checks in sequence — first match wins:
    1. file_hash          — binary identity (SHA-256 of raw file bytes)
    2. normalized_text_hash  — content identity after light normalisation
    3. canonical_text_fingerprint — order-independent bag-of-words hash
       (cross-format: PDF vs DOCX of the same CV)

High-similarity fallback (last resort, O(n)):
  token_set_ratio >= 97% on normalised text.
  Handles residual PDF/DOCX extraction differences that survive fingerprinting.
  reason: "content_similarity_fallback"

Fuzzy identity matching (legacy path, no longer triggers delete):
  Fields: email (exact), phone (normalised exact), name (fuzzy >= 90).
  reason: "identity_match"
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
_CONTENT_THRESHOLD: float = 95.0
_CONTENT_STOP_THRESHOLD: float = 90.0
_NAME_FUZZY_THRESHOLD: float = 90.0
_HIGH_SIMILARITY_THRESHOLD: float = 97.0  # token_set_ratio for cross-format fallback

# ── Arabic / digit normalisation tables ──────────────────────────────────────

# Arabic-Indic (U+0660–U+0669) + Extended Persian (U+06F0–U+06F9) → ASCII 0–9
# NFKD does NOT decompose these — explicit translation required.
_INDIC_DIGIT_TABLE = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

# Tokens excluded from the canonical fingerprint.
# All entries are in post-normalisation form (after _normalise_for_canonical):
#   ى → ي  |  ة → ه  |  أإآ → ا (via NFKD+combining strip)  |  ٱ → ا
# These tokens appear in virtually every CV regardless of format and
# add no discriminating power; removing them prevents false positives
# when two different CVs share only section headings and boilerplate.
_CV_STOP_TOKENS: frozenset[str] = frozenset({
    # English function words
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "been",
    "have", "has", "had", "will", "would", "could", "should", "may", "can",
    "not", "but", "also", "other", "than", "into", "over", "such", "any",
    "all", "more", "its", "our", "your", "their", "very", "well",
    "who", "how", "did", "per", "etc", "page",
    # Universal CV section headings / boilerplate
    "education", "experience", "skills", "summary", "profile", "objective",
    "languages", "certifications", "references", "contact", "information",
    "work", "employment", "history", "professional", "technical", "personal",
    "available", "upon", "request", "curriculum", "vitae", "resume",
    # Arabic function words (post-normalisation forms)
    "الي",    # إلى  after NFKD + ى→ي
    "علي",    # على  after ى→ي
    "هذا", "هذه",
    "التي", "الذي",
    "وقد", "ولقد",
    # Arabic CV section headings (post-normalisation)
    "التعليم",
    "الخبرات", "الخبره",   # الخبرة → الخبره
    "المهارات",
    "اللغات",
    "الملخص",
    "المهني",
    "الشخصيه",             # الشخصية → الشخصيه
    "التوظيف",
})


# ── Text normalisation ────────────────────────────────────────────────────────

def _normalise_cv_text(raw: str) -> str:
    """
    Light normalisation for similarity comparison and normalized_text_hash.
    Preserves token order; handles common PDF/DOCX extraction artefacts.
    """
    if not raw:
        return ""
    text = raw.lower()
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[•·▪►✓✗✘✔❖◦‣⁃\*]+", " ", text)
    text = re.sub(r"[‐-―−⁄]", "-", text)
    text = re.sub(r"[^\w\s\-؀-ۿ]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalise_for_canonical(raw: str) -> str:
    """
    Aggressive normalisation for canonical cross-format fingerprint generation.

    More destructive than _normalise_cv_text — designed to absorb PDF/DOCX
    extraction artefacts rather than preserve readability:

    NFKD + combining-char removal
      Expands typographic ligatures (ﬁ→fi, ﬂ→fl, ﬃ→ffi) and resolves Unicode
      compatibility characters.  Strips diacritical marks and Arabic harakat
      that differ between PDF font rendering and LibreOffice DOCX→PDF output.

    Arabic normalisation (explicit, after NFKD)
      · Residual Alef variants أإآٱ → ا  (ٱ U+0671 not handled by NFKD)
      · Alef Maqsura ى → ي  (no NFKD decomposition)
      · Ta Marbuta  ة → ه  (no NFKD decomposition)
      · Arabic-Indic / Extended Persian digits → ASCII 0–9

    Intra-letter hyphen removal
      "pre-screening" → "prescreening" absorbs line-break hyphenation artefacts
      (one parser joins the word across a line break; the other does not).
      Only letter-to-letter hyphens are removed; "2020-2024" is preserved.

    Invisible characters
      Soft hyphens (U+00AD), zero-width spaces (U+200B–U+200F), line/paragraph
      separators (U+2028/U+2029) and BOM (U+FEFF) are stripped.
    """
    if not raw:
        return ""

    # 1. NFKD — expands ligatures, resolves compatibility chars
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(c for c in text if not unicodedata.combining(c))

    # 2. Explicit Arabic normalisations not resolved by NFKD+strip
    text = re.sub(r"[أإآٱ]", "ا", text)    # residual Alef variants incl. U+0671
    text = re.sub(r"ى", "ي", text)           # Alef Maqsura → Ya
    text = re.sub(r"ة", "ه", text)           # Ta Marbuta → Ha
    text = text.translate(_INDIC_DIGIT_TABLE)

    # 3. Lowercase
    text = text.lower()

    # 4. Remove invisible / zero-width control characters
    text = re.sub(r"[­​‌‍‎‏  ﻿]", "", text)

    # 5. Remove intra-letter hyphens (line-break hyphenation artefacts).
    #    Only between Unicode letters — digit ranges like "2020-2024" are kept.
    text = re.sub(r"(?<=[^\W\d_])-(?=[^\W\d_])", "", text, flags=re.UNICODE)

    # 6. Bullets and decoration → space
    text = re.sub(r"[•·▪►✓✗✘✔❖◦‣⁃*|]+", " ", text)

    # 7. Drop all remaining punctuation; keep letters, digits, Arabic, space
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)

    # 8. Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _normalise_email(email: Optional[str]) -> str:
    return (email or "").lower().strip()


def _normalise_phone(phone: Optional[str]) -> str:
    """Strip all non-digit chars; remove common leading country codes (0, 00, +)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits


# ── Exact content duplicate (primary pipeline path) ──────────────────────────

def compute_normalized_text_hash(raw_text: str) -> str:
    """SHA-256 of lightly-normalised CV text (order-preserving, all tokens)."""
    normalized = _normalise_cv_text(raw_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _compute_canonical_tokens(canonical_text: str) -> str:
    """
    Sorted, deduplicated, stop-word-filtered token string from canonical text.

    Three filter passes on the output of _normalise_for_canonical():
      1. len < 3 — noise, OCR artefacts, Arabic two-letter function words
      2. pure digit < 4 chars — page numbers; 4-digit years are kept
      3. _CV_STOP_TOKENS — boilerplate common to every CV in both formats
    """
    tokens = canonical_text.split()
    filtered = []
    for t in tokens:
        if len(t) < 3:
            continue
        if t.isdigit() and len(t) < 4:
            continue
        if t in _CV_STOP_TOKENS:
            continue
        filtered.append(t)
    return " ".join(sorted(set(filtered)))


def compute_canonical_text_fingerprint(raw_text: str) -> str:
    """
    Order-independent canonical fingerprint for cross-format duplicate detection.

    Pipeline: _normalise_for_canonical → _compute_canonical_tokens → SHA-256

    Handles the PDF vs DOCX case where parsers extract identical vocabulary
    in different order due to column layout / bounding-box reading differences.
    The sorted deduplicated token set is invariant to extraction order.

    Returns a SHA-256 hex digest of the sorted canonical token string.
    """
    canonical_text = _normalise_for_canonical(raw_text)
    canonical_tokens = _compute_canonical_tokens(canonical_text)
    return hashlib.sha256(canonical_tokens.encode("utf-8")).hexdigest()


async def check_exact_file_hash_duplicate(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    file_hash: str,
) -> dict | None:
    """
    Return the first application in the same job whose intake log entry has an
    identical file_hash (exact binary duplicate), or None if no match.

    Looks up application_intake_log which is populated by all intake methods
    (manual_upload, public_apply, email_forwarding, platform_email).

    Must be called with an open AsyncSession that has RLS context set.
    """
    from sqlalchemy import text

    if not file_hash:
        return None

    row = await db.execute(
        text("""
            SELECT ail.application_id, a.candidate_name, a.candidate_email
            FROM application_intake_log ail
            JOIN applications a ON a.application_id = ail.application_id
            WHERE ail.file_hash        = :hash
              AND ail.job_id           = :job_id
              AND ail.tenant_id        = :tenant_id
              AND ail.application_id  IS NOT NULL
              AND ail.application_id  != :self_id
              AND ail.status           = 'RECEIVED_SUCCESSFULLY'
            LIMIT 1
        """),
        {
            "hash":      file_hash,
            "job_id":    job_id,
            "tenant_id": tenant_id,
            "self_id":   application_id,
        },
    )
    result = row.mappings().first()
    if result:
        return {
            "application_id": str(result["application_id"]),
            "candidate_name":  result["candidate_name"],
            "candidate_email": result["candidate_email"],
        }
    return None


async def check_exact_content_duplicate(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    normalized_hash: str,
) -> dict | None:
    """
    Return the first application in the same job whose application_files row
    has an identical normalized_text_hash, or None if no match.

    Must be called with an open AsyncSession that has RLS context set.
    The session is NOT committed here — caller is responsible.

    Returns a dict with keys: application_id, candidate_name, candidate_email.
    """
    from sqlalchemy import text

    if not normalized_hash:
        return None

    row = await db.execute(
        text("""
            SELECT a.application_id, a.candidate_name, a.candidate_email
            FROM application_files af
            JOIN applications a ON a.application_id = af.application_id
            WHERE af.normalized_text_hash = :hash
              AND a.job_id               = :job_id
              AND a.tenant_id            = :tenant_id
              AND af.application_id      != :self_id
            LIMIT 1
        """),
        {
            "hash":      normalized_hash,
            "job_id":    job_id,
            "tenant_id": tenant_id,
            "self_id":   application_id,
        },
    )
    result = row.mappings().first()
    if result:
        return {
            "application_id": str(result["application_id"]),
            "candidate_name":  result["candidate_name"],
            "candidate_email": result["candidate_email"],
        }
    return None


async def check_exact_canonical_fingerprint_duplicate(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    canonical_fp: str,
) -> dict | None:
    """
    Return the first application in the same job whose application_files row has
    an identical canonical_text_fingerprint, or None if no match.

    canonical_text_fingerprint is order-independent (sorted deduplicated tokens)
    so it detects the same CV submitted as PDF and as DOCX even when the parsers
    produce tokens in a different sequence.

    Must be called with an open AsyncSession that has RLS context set.
    The session is NOT committed here — caller is responsible.
    """
    from sqlalchemy import text

    if not canonical_fp:
        return None

    row = await db.execute(
        text("""
            SELECT a.application_id, a.candidate_name, a.candidate_email
            FROM application_files af
            JOIN applications a ON a.application_id = af.application_id
            WHERE af.canonical_text_fingerprint = :fp
              AND a.job_id                      = :job_id
              AND a.tenant_id                   = :tenant_id
              AND af.application_id             != :self_id
            LIMIT 1
        """),
        {
            "fp":        canonical_fp,
            "job_id":    job_id,
            "tenant_id": tenant_id,
            "self_id":   application_id,
        },
    )
    result = row.mappings().first()
    if result:
        return {
            "application_id": str(result["application_id"]),
            "candidate_name":  result["candidate_name"],
            "candidate_email": result["candidate_email"],
        }
    return None


async def check_high_similarity_duplicate(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    extracted_text: str,
    threshold: float = _HIGH_SIMILARITY_THRESHOLD,
) -> dict | None:
    """
    Cross-format fallback: compare normalised CV text against all scored CVs in
    the same job using rapidfuzz token_set_ratio.

    Used when all three exact fingerprint checks (file_hash, normalized_text_hash,
    canonical_text_fingerprint) miss — a last resort for residual PDF/DOCX
    extraction differences that survive the canonical normalisation pipeline.

    token_set_ratio handles extra tokens well (headers, footers, page numbers
    added by LibreOffice DOCX→PDF conversion) by computing the ratio on the
    common-token intersection rather than the full strings.

    Threshold default is 97.0 (very conservative) to avoid false positives.
    Minimum normalised text length of 500 chars guards against short CVs where
    any two documents may score unreliably high.

    Must be called with an open AsyncSession that has RLS context set.
    The session is NOT committed here.

    Returns dict with keys: application_id, candidate_name, candidate_email,
    similarity_score — or None if no match found.
    """
    from rapidfuzz import fuzz
    from sqlalchemy import text

    if not extracted_text:
        return None

    norm_new = _normalise_cv_text(extracted_text)
    if len(norm_new) < 500:
        return None

    rows = await db.execute(
        text("""
            SELECT a.application_id, a.candidate_name, a.candidate_email,
                   af.extracted_text
            FROM application_files af
            JOIN applications a ON a.application_id = af.application_id
            WHERE a.job_id           = :jid
              AND a.tenant_id        = :tid
              AND af.application_id  != :self_id
              AND af.extraction_status = 'done'
              AND af.extracted_text IS NOT NULL
        """),
        {"jid": job_id, "tid": tenant_id, "self_id": application_id},
    )

    best_score = 0.0
    best_match: dict | None = None

    for row in rows.mappings():
        existing_norm = _normalise_cv_text(row["extracted_text"] or "")
        if len(existing_norm) < 500:
            continue
        score = fuzz.token_set_ratio(norm_new, existing_norm)
        if score >= threshold and score > best_score:
            best_score = score
            best_match = {
                "application_id":  str(row["application_id"]),
                "candidate_name":  row["candidate_name"],
                "candidate_email": row["candidate_email"],
                "similarity_score": float(score),
            }

    return best_match


# ── Priority ordering (higher = stronger, must not be overwritten by weaker) ──
_PRIORITY: dict[str | None, int] = {
    "high_content_similarity": 2,
    "identity_match":          1,
    "not_duplicate":           0,
    None:                      0,
}


# ── Main detection function ───────────────────────────────────────────────────

async def detect_possible_duplicate(
    db,
    application_id: str,
    job_id: str,
    tenant_id: str,
    candidate_name: str,
    candidate_email: Optional[str],
    candidate_phone: Optional[str],
    extracted_text: str,
) -> None:
    """
    Compare the new application against all scored/pending siblings in the same job.
    Updates applications.duplicate_* columns in-place; never raises (errors are logged).

    Priority is preserved across multiple runs:
      high_content_similarity (strongest) > identity_match > not_duplicate
    A weaker result from a later run never overwrites a stronger earlier result.

    Must be called with an already-open AsyncSession that has RLS context set.
    The session is NOT committed here — caller is responsible for the commit.
    """
    from rapidfuzz import fuzz
    from sqlalchemy import text

    try:
        # ── Fetch current duplicate state (to enforce priority on re-runs) ────
        cur = await db.execute(
            text("SELECT duplicate_reason FROM applications WHERE application_id = :aid"),
            {"aid": application_id},
        )
        cur_row = cur.mappings().first()
        current_reason: str | None = cur_row["duplicate_reason"] if cur_row else None

        # Already at maximum priority — nothing can improve it
        if current_reason == "high_content_similarity":
            logger.debug(
                "Skipping duplicate re-check for %s — already high_content_similarity",
                application_id,
            )
            return

        # ── Load existing applications for this job ───────────────────────────
        rows = await db.execute(
            text("""
                SELECT
                    a.application_id,
                    a.candidate_name,
                    a.candidate_email,
                    a.candidate_phone_from_cv,
                    af.extracted_text
                FROM applications a
                LEFT JOIN application_files af
                    ON af.application_id = a.application_id
                    AND af.extraction_status = 'done'
                WHERE a.job_id    = :jid
                  AND a.tenant_id = :tid
                  AND a.application_id != :aid
            """),
            {"jid": job_id, "tid": tenant_id, "aid": application_id},
        )
        existing = rows.mappings().all()

        if not existing:
            await _mark_checked(db, application_id, "not_duplicate", None, None, None)
            return

        norm_new_text = _normalise_cv_text(extracted_text)
        norm_new_email = _normalise_email(candidate_email)
        norm_new_phone = _normalise_phone(candidate_phone)
        norm_new_name = (candidate_name or "").lower().strip()

        best_ref_id: Optional[str] = None
        best_score: Optional[float] = None
        best_reason: Optional[str] = None

        for row in existing:
            ref_id = str(row["application_id"])

            # ── Layer 1: content similarity ───────────────────────────────────
            if norm_new_text and row["extracted_text"]:
                norm_existing_text = _normalise_cv_text(row["extracted_text"])
                if norm_existing_text:
                    content_score = fuzz.token_sort_ratio(norm_new_text, norm_existing_text)
                    if content_score >= _CONTENT_THRESHOLD:
                        logger.info(
                            "Duplicate detected (content) app=%s ref=%s score=%.1f",
                            application_id, ref_id, content_score,
                        )
                        if best_score is None or content_score > best_score:
                            best_ref_id = ref_id
                            best_score = float(content_score)
                            best_reason = "high_content_similarity"
                        continue  # skip Layer 2 for this row — already matched

            # ── Layer 2: identity matching ────────────────────────────────────
            matches = 0
            fields_compared = 0

            # email
            ref_email = _normalise_email(row["candidate_email"])
            if norm_new_email and ref_email:
                fields_compared += 1
                if norm_new_email == ref_email:
                    matches += 1

            # phone
            ref_phone = _normalise_phone(row["candidate_phone_from_cv"])
            if norm_new_phone and ref_phone:
                fields_compared += 1
                if norm_new_phone == ref_phone:
                    matches += 1

            # name
            ref_name = (row["candidate_name"] or "").lower().strip()
            if norm_new_name and ref_name:
                fields_compared += 1
                name_score = fuzz.ratio(norm_new_name, ref_name)
                if name_score >= _NAME_FUZZY_THRESHOLD:
                    matches += 1

            if fields_compared >= 2 and matches >= 2:
                identity_score = round(matches / fields_compared * 100, 1)
                logger.info(
                    "Duplicate detected (identity) app=%s ref=%s matches=%d/%d",
                    application_id, ref_id, matches, fields_compared,
                )
                if best_reason != "high_content_similarity":
                    if best_score is None or identity_score > best_score:
                        best_ref_id = ref_id
                        best_score = identity_score
                        best_reason = "identity_match"

        new_reason = best_reason if best_ref_id else "not_duplicate"

        # Enforce priority: never overwrite a stronger result with a weaker one.
        # e.g. second run finds identity_match but first run found high_content_similarity
        # → the early-return above handles the identity→high case already, but this
        #   guards identity_match → not_duplicate downgrades too.
        if _PRIORITY.get(new_reason, 0) < _PRIORITY.get(current_reason, 0):
            logger.debug(
                "Skipping duplicate update for %s — new reason '%s' weaker than current '%s'",
                application_id, new_reason, current_reason,
            )
            return

        if best_ref_id:
            await _mark_checked(db, application_id, "possible_duplicate",
                                 best_ref_id, best_score, best_reason)
        else:
            await _mark_checked(db, application_id, "not_duplicate", None, None, None)

    except Exception as exc:
        logger.error(
            "duplicate_detection failed for application %s: %s",
            application_id, exc, exc_info=True,
        )


async def find_content_duplicate(
    db,
    job_id: str,
    tenant_id: str,
    extracted_text: str,
) -> Optional[dict]:
    """
    Synchronous-style content check run at upload time (before an application record
    is created).  Uses a lower threshold (_CONTENT_STOP_THRESHOLD = 90%) than the
    scoring-path check so that near-identical CVs are caught early and excluded from
    the scoring queue entirely.

    Returns a dict with keys:
        application_id, candidate_name, candidate_email, similarity_score
    or None if no match found.

    Must be called with an open AsyncSession that has RLS context set.
    """
    from rapidfuzz import fuzz
    from sqlalchemy import text

    if not extracted_text:
        return None
    norm_text = _normalise_cv_text(extracted_text)
    if len(norm_text) < 200:  # guard against near-empty extractions producing false positives
        return None

    rows = await db.execute(
        text("""
            SELECT
                a.application_id,
                a.candidate_name,
                a.candidate_email,
                af.extracted_text
            FROM applications a
            JOIN application_files af
                ON af.application_id = a.application_id
               AND af.extraction_status = 'done'
            WHERE a.job_id    = :jid
              AND a.tenant_id = :tid
              AND af.extracted_text IS NOT NULL
        """),
        {"jid": job_id, "tid": tenant_id},
    )

    best_score = 0.0
    best_match: Optional[dict] = None

    for row in rows.mappings():
        existing_norm = _normalise_cv_text(row["extracted_text"] or "")
        if not existing_norm:
            continue
        score = fuzz.token_sort_ratio(norm_text, existing_norm)
        if score >= _CONTENT_STOP_THRESHOLD and score > best_score:
            best_score = score
            best_match = {
                "application_id": str(row["application_id"]),
                "candidate_name":  row["candidate_name"],
                "candidate_email": row["candidate_email"],
                "similarity_score": float(score),
            }

    return best_match


async def _mark_checked(
    db,
    application_id: str,
    status: str,
    ref_id: Optional[str],
    score: Optional[float],
    reason: Optional[str],
) -> None:
    from sqlalchemy import text

    await db.execute(
        text("""
            UPDATE applications SET
                duplicate_status                   = :status,
                duplicate_reference_application_id = :ref_id,
                duplicate_similarity_score         = :score,
                duplicate_reason                   = :reason,
                duplicate_checked_at               = now()
            WHERE application_id = :aid
        """),
        {
            "status": status,
            "ref_id": ref_id,
            "score":  score,
            "reason": reason,
            "aid":    application_id,
        },
    )
