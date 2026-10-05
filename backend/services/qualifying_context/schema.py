"""
Qualifying-context data structures (Architecture C, phase P1 — not wired into any production flow).

    experience.qualifying_context = {"state": "identified" | "none" | "uncertain",
                                     "contexts": [verbatim JD phrase, ...],   # ANDed, same experience entry
                                     "source": "analysis" | "recruiter"}

  identified  a JD phrase restricts WHICH otherwise relevant experience counts (>= 1 context)
  none        no qualifying restriction (contexts == [])
  uncertain   a possible restriction cannot be represented safely (contexts = candidate phrases, may be [])
An ABSENT field means legacy / not assessed — never "none". A model run that fails never yields an object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATE_IDENTIFIED = "identified"
STATE_NONE = "none"
STATE_UNCERTAIN = "uncertain"
STATES = (STATE_IDENTIFIED, STATE_NONE, STATE_UNCERTAIN)

SOURCE_ANALYSIS = "analysis"
SOURCE_RECRUITER = "recruiter"
SOURCES = (SOURCE_ANALYSIS, SOURCE_RECRUITER)

QC_KEYS = frozenset({"state", "contexts", "source"})

# run outcome
RUN_OK = "ok"
RUN_FAILED_TECHNICAL = "failed_technical"      # API error, timeout, no client, ...
RUN_FAILED_VALIDATION = "failed_validation"    # the model answered, but the answer is not acceptable
RUN_STATUSES = (RUN_OK, RUN_FAILED_TECHNICAL, RUN_FAILED_VALIDATION)

# validation error codes (parse codes match the frozen evaluator's parse_qc codes)
ERR_NO_RESPONSE = "no_response"
ERR_INVALID_JSON = "invalid_json"
ERR_NOT_AN_OBJECT = "not_an_object"
ERR_BAD_KEYS = "bad_keys"
ERR_BAD_STATE = "bad_state"
ERR_BAD_SOURCE = "bad_source"
ERR_BAD_CONTEXTS = "bad_contexts"
ERR_DUPLICATE_CONTEXTS = "duplicate_contexts"
ERR_INCONSISTENT_STATE = "inconsistent_state"      # identified without contexts / none with contexts
ERR_UNGROUNDED_CONTEXT = "ungrounded_context"      # a context is not a verbatim JD phrase
ERR_OUTPUT_TRUNCATED = "output_truncated"          # finish_reason == "length": never trusted


@dataclass(frozen=True)
class QualifyingContext:
    state: str
    contexts: tuple[str, ...]
    source: str

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"invalid state {self.state!r}")
        if self.source not in SOURCES:
            raise ValueError(f"invalid source {self.source!r}")
        if self.state == STATE_IDENTIFIED and not self.contexts:
            raise ValueError("identified requires at least one context")
        if self.state == STATE_NONE and self.contexts:
            raise ValueError("none requires contexts == []")

    def to_dict(self) -> dict[str, Any]:
        """Exactly the frozen three-key object."""
        return {"state": self.state, "contexts": list(self.contexts), "source": self.source}


@dataclass(frozen=True)
class QCRunResult:
    """Outcome of one pinned model run. ``qualifying_context`` is set if and only if status == "ok"."""
    status: str
    qualifying_context: QualifyingContext | None
    error: str | None
    prompt_version: str
    prompt_sha256: str
    model: str
    temperature: float
    max_tokens: int
    jd_sha256: str
    raw: str | None = None
    finish_reason: str | None = None
    response_model: str | None = None
    usage: dict | None = None
    ungrounded: tuple[str, ...] = field(default=())

    def __post_init__(self):
        if self.status not in RUN_STATUSES:
            raise ValueError(f"invalid run status {self.status!r}")
        if (self.status == RUN_OK) != (self.qualifying_context is not None):
            raise ValueError("a qualifying_context object exists if and only if the run is ok")
        if self.status == RUN_OK and self.error is not None:
            raise ValueError("an ok run carries no error")
        if self.status != RUN_OK and not self.error:
            raise ValueError("a failed run must carry an error")

    @property
    def ok(self) -> bool:
        return self.status == RUN_OK

    def to_audit(self) -> dict[str, Any]:
        """Audit record (no persistence happens in phase P1)."""
        return {"status": self.status, "error": self.error, "prompt_version": self.prompt_version,
                "prompt_sha256": self.prompt_sha256, "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "jd_sha256": self.jd_sha256,
                "raw": None if self.raw is None else self.raw[:2000], "finish_reason": self.finish_reason,
                "response_model": self.response_model, "usage": self.usage, "ungrounded": list(self.ungrounded)}
