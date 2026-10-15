"""
S0 v2 — schema of the CV-level experience-structure artifact
(``_schema = "s0_experience_v1"``).

Ownership is POSITIVE-ONLY: a CV line is owned by an entry only when a
validated entry claims it (header or body). Every other line — summary text,
lines the structurer left out, lines of entries it marked ``uncertain``, and
every line when structure_status is ``unverified`` — is unowned. Nothing in
S0 assigns an unowned line heuristically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.s0_experience.dates import Anchor

S0_SCHEMA = "s0_experience_v1"
S0_VERSION = "1.1.0"

# structure_status
STRUCTURE_VALIDATED = "validated"
STRUCTURE_REPAIRED = "repaired"
STRUCTURE_UNVERIFIED = "unverified"
STRUCTURE_FAILED = "failed"
TRUSTED_STRUCTURE = frozenset({STRUCTURE_VALIDATED, STRUCTURE_REPAIRED})

# date_status
DATES_DATED = "dated"
DATES_PARTIAL = "partially_dated"
DATES_UNDATED = "undated"

# why a structure is unverified / failed
REASON_AI_UNAVAILABLE = "ai_unavailable"          # retryable: not cached
REASON_VALIDATION_FAILED = "validation_failed"    # deterministic outcome: cached
REASON_EXCEEDS_MODEL_CONTEXT = "exceeds_model_context"   # deterministic: cached
REASON_OUTPUT_TRUNCATED = "output_truncated"             # deterministic at temperature 0: cached
REASON_NO_TEXT = "no_text"
REASON_INTERNAL_ERROR = "internal_error"
RETRYABLE_REASONS = frozenset({REASON_AI_UNAVAILABLE, REASON_INTERNAL_ERROR})

EXPERIENCE_KINDS = ("employment", "freelance", "internship", "volunteer", "project")
NON_EXPERIENCE_KINDS = ("education", "training", "other")
ENTRY_KINDS = EXPERIENCE_KINDS + NON_EXPERIENCE_KINDS
DISPOSITIONS = ("inside_responsibility", "education", "training", "certification",
                "non_employment_project", "other")
OWNERSHIP_CERTAIN = "certain"
OWNERSHIP_UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class LineText:
    line: int
    text: str

    def to_dict(self) -> dict:
        return {"line": self.line, "text": self.text}


@dataclass(frozen=True)
class S0Entry:
    """A validated, ownership-certain entry. entry_id E1..En in document order."""
    entry_id: str
    anchor_id: str | None
    kind: str
    title: LineText | None
    employer: LineText | None
    header_lines: tuple[int, ...]
    body_lines: tuple[tuple[int, int], ...]      # inclusive ranges
    shared_header_lines: tuple[int, ...]         # validated shared employer header line(s)
    undated_reason: str | None
    interval: tuple[int, int] | None          # closed ranges only (None when is_current)
    duration_months: int | None               # closed ranges only (None when is_current)
    source_text: str
    flags: tuple[str, ...] = ()
    is_current: bool = False                  # valid present/open range: duration at scoring as_of

    @property
    def duration_known(self) -> bool:
        """A duration is determinable (independent of any as_of)."""
        return self.is_current or self.duration_months is not None

    @property
    def is_experience(self) -> bool:
        return self.kind in EXPERIENCE_KINDS

    def owned_lines(self) -> list[int]:
        out = set(self.header_lines)
        for a, b in self.body_lines:
            out.update(range(a, b + 1))
        return sorted(out)

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id, "anchor_id": self.anchor_id, "kind": self.kind,
            "title": self.title.to_dict() if self.title else None,
            "employer": self.employer.to_dict() if self.employer else None,
            "header_lines": list(self.header_lines),
            "body_lines": [list(r) for r in self.body_lines],
            "shared_header_lines": list(self.shared_header_lines),
            "undated_reason": self.undated_reason,
            "interval": list(self.interval) if self.interval else None,
            "duration_months": self.duration_months, "is_current": self.is_current,
            "duration_known": self.duration_known,
            "source_text": self.source_text, "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "S0Entry":
        lt = (lambda x: LineText(x["line"], x["text"]) if x else None)
        return cls(
            entry_id=d["entry_id"], anchor_id=d.get("anchor_id"), kind=d["kind"],
            title=lt(d.get("title")), employer=lt(d.get("employer")),
            header_lines=tuple(d.get("header_lines") or ()),
            body_lines=tuple(tuple(r) for r in d.get("body_lines") or ()),
            shared_header_lines=tuple(d.get("shared_header_lines") or ()),
            undated_reason=d.get("undated_reason"),
            interval=tuple(d["interval"]) if d.get("interval") else None,
            duration_months=d.get("duration_months"), source_text=d.get("source_text", ""),
            flags=tuple(d.get("flags") or ()), is_current=bool(d.get("is_current", False)))


@dataclass
class S0Document:
    text_sha256: str
    line_count: int
    built_as_of: str                             # "YYYY-MM" build month: parse-time validity only,
                                                 # NEVER used for durations
    structure_status: str
    date_status: str
    anchors: list[Anchor]
    unparsed_date_texts: list[dict]
    entries: list[S0Entry] = field(default_factory=list)
    ignored_anchors: list[dict] = field(default_factory=list)
    # Display/audit only — never ownership:
    uncertain_entries: list[dict] = field(default_factory=list)
    candidate_blocks: list[dict] = field(default_factory=list)
    status_reason: str | None = None
    retryable: bool = False
    structurer: dict = field(default_factory=dict)
    ocr: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    cache_key: str = ""
    s0_version: str = S0_VERSION
    schema: str = S0_SCHEMA

    @property
    def trusted(self) -> bool:
        return self.structure_status in TRUSTED_STRUCTURE

    def experience_entries(self) -> list[S0Entry]:
        """Entries S2/S4 may use — empty unless the structure is trusted."""
        return [e for e in self.entries if e.is_experience] if self.trusted else []

    def owned_line_map(self) -> dict[int, tuple[str, ...]]:
        """line -> owning entry IDs (several only for a validated shared header).
        Empty unless the structure is trusted (positive-only ownership)."""
        if not self.trusted:
            return {}
        out: dict[int, list[str]] = {}
        for e in self.entries:
            for ln in e.owned_lines():
                out.setdefault(ln, []).append(e.entry_id)
            for ln in e.shared_header_lines:
                if e.entry_id not in out.setdefault(ln, []):
                    out[ln].append(e.entry_id)
        return {k: tuple(v) for k, v in out.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "_schema": self.schema, "s0_version": self.s0_version, "cache_key": self.cache_key,
            "text_sha256": self.text_sha256, "line_count": self.line_count,
            "built_as_of": self.built_as_of,
            "structure_status": self.structure_status, "date_status": self.date_status,
            "status_reason": self.status_reason, "retryable": self.retryable,
            "structurer": dict(self.structurer), "ocr": dict(self.ocr),
            "anchors": [a.to_dict() for a in self.anchors],
            "unparsed_date_texts": list(self.unparsed_date_texts),
            "entries": [e.to_dict() for e in self.entries],
            "ignored_anchors": list(self.ignored_anchors),
            "uncertain_entries": list(self.uncertain_entries),
            "candidate_blocks": list(self.candidate_blocks),
            "validation": dict(self.validation),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "S0Document":
        if d.get("_schema") != S0_SCHEMA:
            raise ValueError(f"not an {S0_SCHEMA} document: {d.get('_schema')!r}")
        return cls(
            text_sha256=d["text_sha256"], line_count=d["line_count"], built_as_of=d["built_as_of"],
            structure_status=d["structure_status"], date_status=d["date_status"],
            anchors=[Anchor.from_dict(a) for a in d.get("anchors") or []],
            unparsed_date_texts=list(d.get("unparsed_date_texts") or []),
            entries=[S0Entry.from_dict(e) for e in d.get("entries") or []],
            ignored_anchors=list(d.get("ignored_anchors") or []),
            uncertain_entries=list(d.get("uncertain_entries") or []),
            candidate_blocks=list(d.get("candidate_blocks") or []),
            status_reason=d.get("status_reason"), retryable=bool(d.get("retryable")),
            structurer=dict(d.get("structurer") or {}), ocr=dict(d.get("ocr") or {}),
            validation=dict(d.get("validation") or {}), cache_key=d.get("cache_key", ""),
            s0_version=d.get("s0_version", S0_VERSION))


def compute_date_status(structure_status: str, entries: list[S0Entry], anchors: list[Anchor],
                        unparsed: list[dict]) -> str:
    """Trusted structure: over experience-kind entries. Otherwise (or when there
    are no experience entries): over the deterministic anchors."""
    if structure_status in TRUSTED_STRUCTURE:
        exp = [e for e in entries if e.is_experience]
        if exp:
            n_dated = sum(e.duration_known for e in exp)
            if n_dated == len(exp) and not unparsed:
                return DATES_DATED
            return DATES_PARTIAL if n_dated else DATES_UNDATED
    ok = [a for a in anchors if a.duration_known]
    if not ok:
        return DATES_UNDATED
    if unparsed or len(ok) < len(anchors):
        return DATES_PARTIAL
    return DATES_DATED
