"""Candidate `requirements-v2-pipeline-1` (offline; NOT wired into any API, worker or UI; see README.md)."""
from parser_candidates.requirements_v2_pipeline_1.pipeline import (  # noqa: F401
    CONTRACT_VERSION, PIPELINE_VERSION, PipelineError, acknowledge, confirm_no_numeric_score, confirm_structure, evaluate, extract, reconcile, validate_state,
)
