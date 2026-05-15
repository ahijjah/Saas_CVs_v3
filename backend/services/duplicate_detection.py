"""
Duplicate CV detection service — intra-job, two-layer, no LLM.

Layer 1 — Normalised content similarity (rapidfuzz token_sort_ratio)
  Threshold: 95 out of 100 (conservative vs spec's 98 to account for
  extraction differences between PDF and DOCX parsers of the same document).
  reason: "high_content_similarity"

Layer 2 — Candidate identity matching
  Fields: email (exact), phone (normalised exact), name (fuzzy >= 90).
  Trigger: at least 2 of the 3 present fields match.
  reason: "identity_match"

Never blocks or deletes — always sets possible_duplicate only.
Never compares across different job_ids.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
_CONTENT_THRESHOLD: float = 95.0   # token_sort_ratio out of 100
_NAME_FUZZY_THRESHOLD: float = 90.0


# ── Text normalisation ────────────────────────────────────────────────────────

def _normalise_cv_text(raw: str) -> str:
    """
    Aggressively normalise extracted CV text before similarity comparison.
    Handles PDF-vs-DOCX extraction artefacts, bullet symbols, repeated whitespace.
    """
    if not raw:
        return ""
    text = raw.lower()
    # Unicode NFC — normalise accented chars to single code-points
    text = unicodedata.normalize("NFC", text)
    # Replace common bullet / decoration chars with space
    text = re.sub(r"[•·▪►✓✗✘✔❖◦‣⁃\*]+", " ", text)
    # Normalise all dash/hyphen variants to ASCII hyphen
    text = re.sub(r"[‐-―−⁄]", "-", text)
    # Drop non-content punctuation (keep letters, digits, hyphens, Arabic block)
    text = re.sub(r"[^\w\s\-؀-ۿ]", " ", text, flags=re.UNICODE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalise_email(email: Optional[str]) -> str:
    return (email or "").lower().strip()


def _normalise_phone(phone: Optional[str]) -> str:
    """Strip all non-digit chars; remove common leading country codes (0, 00, +)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    # Strip leading international prefix: 00 or single 0
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits


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

    Must be called with an already-open AsyncSession that has RLS context set.
    The session is NOT committed here — caller is responsible for the commit.
    """
    from rapidfuzz import fuzz
    from sqlalchemy import text

    try:
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
