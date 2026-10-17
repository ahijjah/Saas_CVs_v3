"""Server-owned structural expectations for one job description (schema version 1). Pure validation: no detection, no storage.

    expectation_block = {"schema_version": 1, "jd_sha256": <64 lowercase hex>, "expectations": [expectation, ...]}
    expectation       = {id, kind, source_text, source_start, source_end, tokens}
      kind      alternatives_together | independent_items
      tokens    two or more unique tokens that occur as words in source_text

A block is bound to one complete job description by its SHA-256. It is valid only when every expectation's source_text is
exactly the description text at its offsets. Anything malformed, duplicated, unsupported or mismatched fails closed with
ExpectationError. An empty expectation list is valid and means no structural findings.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

EXPECTATION_SCHEMA_VERSION = 1
ALTERNATIVES_TOGETHER = "alternatives_together"
INDEPENDENT_ITEMS = "independent_items"
KINDS = (ALTERNATIVES_TOGETHER, INDEPENDENT_ITEMS)
BLOCK_KEYS = frozenset({"schema_version", "jd_sha256", "expectations"})
EXPECTATION_KEYS = frozenset({"id", "kind", "source_text", "source_start", "source_end", "tokens"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ExpectationError(ValueError):
    """A structural expectation block that is malformed, duplicated, unsupported or not the description's own text."""


@dataclass(frozen=True)
class Expectation:
    id: str
    kind: str
    source_text: str
    source_start: int
    source_end: int
    tokens: tuple[str, ...]


def jd_sha256(jd_text: str) -> str:
    """SHA-256 of the complete job description, UTF-8 encoded."""
    return hashlib.sha256(jd_text.encode("utf-8")).hexdigest()


def has_token(text: str, token: str) -> bool:
    """Whole-token, case-insensitive. Only ASCII letters and digits extend a token, so 'وHTML' still matches 'HTML'."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", text, re.IGNORECASE) is not None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_expectations(block: object, jd_text: str) -> tuple[Expectation, ...]:
    """The validated expectations of `block` for `jd_text`, or ExpectationError. Nothing is inferred and nothing is repaired."""
    if not isinstance(jd_text, str):
        raise ExpectationError("the job description text is required to validate structural expectations")
    if not isinstance(block, dict) or set(block) != BLOCK_KEYS:
        raise ExpectationError("an expectation block must have exactly schema_version, jd_sha256 and expectations")
    if not _is_int(block["schema_version"]) or block["schema_version"] != EXPECTATION_SCHEMA_VERSION:
        raise ExpectationError(f"expectation schema_version must be {EXPECTATION_SCHEMA_VERSION}")
    sha = block["jd_sha256"]
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise ExpectationError("jd_sha256 must be 64 lowercase hexadecimal characters")
    if sha != jd_sha256(jd_text):
        raise ExpectationError("the expectation block is bound to a different job description (jd_sha256 does not match)")
    raw = block["expectations"]
    if not isinstance(raw, list):
        raise ExpectationError("expectations must be a list")

    out: list[Expectation] = []
    ids: set[str] = set()
    spans: set[tuple[int, int]] = set()
    for pos, e in enumerate(raw):
        where = f"expectation {pos}"
        if not isinstance(e, dict) or set(e) != EXPECTATION_KEYS:
            raise ExpectationError(f"{where} must have exactly the expectation fields")
        eid = e["id"]
        if not isinstance(eid, str) or not eid.strip() or eid in ids:
            raise ExpectationError(f"{where} id must be a unique non-empty string")
        ids.add(eid)
        if e["kind"] not in KINDS:
            raise ExpectationError(f"{where} kind {e['kind']!r} is not supported")
        text, start, end = e["source_text"], e["source_start"], e["source_end"]
        if not isinstance(text, str) or not text.strip():
            raise ExpectationError(f"{where} source_text must be a non-empty string")
        if not (_is_int(start) and _is_int(end) and 0 <= start < end <= len(jd_text)):
            raise ExpectationError(f"{where} offsets must be integers with 0 <= start < end <= the description length")
        if jd_text[start:end] != text:
            raise ExpectationError(f"{where} source_text is not the description text at its offsets")
        if (start, end) in spans:
            raise ExpectationError(f"{where} repeats the source span of another expectation")
        spans.add((start, end))
        tokens = e["tokens"]
        if not isinstance(tokens, list) or len(tokens) < 2 or not all(isinstance(t, str) and t.strip() for t in tokens):
            raise ExpectationError(f"{where} needs two or more non-empty tokens")
        if len(set(tokens)) != len(tokens):
            raise ExpectationError(f"{where} repeats a token")
        absent = [t for t in tokens if not has_token(text, t)]
        if absent:
            raise ExpectationError(f"{where} tokens {absent} do not occur as words in its source_text")
        out.append(Expectation(eid, e["kind"], text, start, end, tuple(tokens)))
    return tuple(out)
