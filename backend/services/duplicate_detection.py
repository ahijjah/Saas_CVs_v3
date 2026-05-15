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
_CONTENT_THRESHOLD: float = 95.0   # token_sort_ratio — marks possible_duplicate during scoring
_CONTENT_STOP_THRESHOLD: float = 90.0  # token_sort_ratio — reject at upload, do not score
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
