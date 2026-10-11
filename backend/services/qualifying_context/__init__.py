"""
Qualifying-context analysis service (Architecture C, phase P1).

Shared, production-quality implementation of the frozen, validated candidate_qc-1 extraction: schema, strict
parsing, consistency and verbatim-grounding validation, and the pinned model runner. NOT wired into the
criteria worker, the database, job analysis, S1, S2, scoring or the UI. Importing this package never imports
OpenAI and never makes a model call.
"""
from services.qualifying_context.runner import (
    PROMPT_SHA256, PROMPT_VERSION, QC_CONFIG, PromptIntegrityError, build_request, run_qualifying_context,
)
from services.qualifying_context.schema import QCRunResult, QualifyingContext
from services.qualifying_context.validation import parse_response, validate_response

__all__ = ["PROMPT_SHA256", "PROMPT_VERSION", "QC_CONFIG", "PromptIntegrityError", "QCRunResult",
           "QualifyingContext", "build_request", "parse_response", "run_qualifying_context", "validate_response"]
