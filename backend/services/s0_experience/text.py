"""
S0 v2 — deterministic text normalisation (S0a).

The CV text is addressed by 1-based line numbers of ``extracted_text`` split on
line breaks. Lines are kept verbatim (only tabs -> spaces and trailing
whitespace removed) so every structurer reference can be checked against the
exact source. Blank lines keep their numbers; they are omitted from the prompt.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def split_lines(text: str) -> list[str]:
    return [ln.replace("\t", " ").rstrip() for ln in (text or "").splitlines()]


def norm(s: str) -> str:
    """Comparison form: NFKC, whitespace collapsed, case-folded."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", s or "")).strip().casefold()


def numbered_lines(lines: list[str]) -> str:
    width = max(4, len(str(len(lines))))
    return "\n".join(f"L{i:0{width}d}| {ln}" for i, ln in enumerate(lines, 1) if ln.strip())


def ocr_noise_indicator(lines: list[str]) -> dict:
    """Deterministic signals only; recorded as a flag, never acted on."""
    body = "".join(lines)
    chars = [c for c in body if not c.isspace()]
    if not chars:
        return {"ocr_suspected": False, "odd_char_ratio": 0.0, "single_char_token_ratio": 0.0}
    odd = sum(1 for c in chars if not (c.isalnum() or c in ".,;:()[]-–—/&'’\"%+#@•*·|"))
    tokens = [t for ln in lines for t in ln.split()]
    single = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    odd_ratio = round(odd / len(chars), 4)
    single_ratio = round(single / max(len(tokens), 1), 4)
    return {"ocr_suspected": odd_ratio > 0.08 or single_ratio > 0.25,
            "odd_char_ratio": odd_ratio, "single_char_token_ratio": single_ratio}
