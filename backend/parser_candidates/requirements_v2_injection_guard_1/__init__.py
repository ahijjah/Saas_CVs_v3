"""Candidate safeguard `requirements-v2-injection-guard-1` (offline; NOT wired into any API, worker or UI; see README.md)."""
from parser_candidates.requirements_v2_injection_guard_1.guard import (  # noqa: F401
    GUARD_VERSION, NEEDS_INJECTION_REVIEW, carry_injection_review, detect, guarded_readiness, inspect_result,
    open_issues, reconcile, validate_review,
)
