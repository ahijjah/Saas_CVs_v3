"""Production requirements-v2 pipeline service (guards + warning adapter + record lifecycle). Pure; used only by services/requirements_api.py.
Promoted from parser_candidates/requirements_v2_pipeline_1 (which stays unchanged); see core.py."""
from services.requirements_pipeline.core import (  # noqa: F401
    CLIENT_FORBIDDEN_KEYS, CONTRACT_VERSION, NOT_ACKNOWLEDGEABLE_GATES, PIPELINE_VERSION, RECORD_VERSION, STATE_RECORD_INVALID, STORAGE_KEY, PipelineError,
    acknowledge_gate, assemble_state, component_versions, evaluate, issue_details, model_conflicts, reconcile_records, to_record, verify_record, with_records,
)
