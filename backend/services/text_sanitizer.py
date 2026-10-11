"""
UTF-8 sanitization for CV extracted text before PostgreSQL persistence.

PostgreSQL UTF-8 rejects:
  - Lone Unicode surrogates (U+D800–U+DFFF) — not valid UTF-8 scalar values
  - Null bytes (\x00) — rejected even inside valid TEXT columns

PyMuPDF (fitz) can return strings containing lone surrogates when it
encounters broken font encoding tables or binary content near image streams.
This module provides a single sanitization point used by the extraction
pipeline before any text reaches the database.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Non-printable control characters to strip (keep \t=0x09, \n=0x0a, \r=0x0d)
_BAD_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SanitizationResult(NamedTuple):
    text: str
    chars_removed: int
    had_surrogates: bool
    had_null_bytes: bool
    had_control_chars: bool


def sanitize_text_for_db(
    raw: str,
    *,
    application_id: str | None = None,
    source: str = "unknown",
) -> str:
    """Return a PostgreSQL-safe version of *raw*.

    Removes lone Unicode surrogates, null bytes, and non-printable control
    characters while preserving Arabic, English, numbers, punctuation,
    line breaks, and all other valid Unicode.

    Never raises — returns the best-effort cleaned string.

    Args:
        raw:            The raw extracted text string.
        application_id: For structured log context only.
        source:         Label for log messages (e.g. "pdf_page_3", "docx").
    """
    if not raw:
        return raw

    result = _sanitize(raw)

    if result.chars_removed:
        logger.info(
            "text_sanitizer source=%s application_id=%s chars_removed=%d "
            "had_surrogates=%s had_null_bytes=%s had_control_chars=%s",
            source,
            application_id or "?",
            result.chars_removed,
            result.had_surrogates,
            result.had_null_bytes,
            result.had_control_chars,
        )

    return result.text


def _sanitize(raw: str) -> SanitizationResult:
    """Perform the actual sanitization; return full diagnostic result."""
    original_len = len(raw)
    text = raw

    # ── Step 1: lone surrogates ───────────────────────────────────────────────
    # Encode to UTF-8 replacing unencodable code points (surrogates → '?'),
    # then decode back.  All valid Unicode scalar values are round-tripped
    # unchanged because they have valid UTF-8 representations.
    encoded = text.encode("utf-8", errors="replace")
    text_after_surrogates = encoded.decode("utf-8")
    had_surrogates = text_after_surrogates != text
    text = text_after_surrogates

    # ── Step 2: null bytes ────────────────────────────────────────────────────
    # PostgreSQL TEXT columns reject \x00 even in otherwise valid UTF-8.
    text_after_null = text.replace("\x00", "")
    had_null_bytes = text_after_null != text
    text = text_after_null

    # ── Step 3: non-printable control characters ──────────────────────────────
    # Strip everything in C0/C1 control range except \t \n \r.
    text_after_ctrl = _BAD_CONTROL.sub("", text)
    had_control_chars = text_after_ctrl != text
    text = text_after_ctrl

    chars_removed = original_len - len(text)
    return SanitizationResult(
        text=text,
        chars_removed=chars_removed,
        had_surrogates=had_surrogates,
        had_null_bytes=had_null_bytes,
        had_control_chars=had_control_chars,
    )
