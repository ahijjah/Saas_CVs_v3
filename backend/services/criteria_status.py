"""Legacy criteria extraction status: the fixed messages, the intake blocking set, and the status shown to the API.

The database stores only pending, processing, completed or failed (the job_criteria CHECK). Insufficient content and an exhausted
retry budget are therefore not stored states: they are failed rows, told apart by the fixed message and the retry counter. Only the
constants below are ever written to criteria_extraction_error, so no AI-generated text reaches that field.
"""
from __future__ import annotations

INSUFFICIENT_MESSAGE = "The job description does not contain enough specific requirements. Update it and retry."
ENQUEUE_FAILED_MESSAGE = "Criteria extraction could not be started. Retry later."
WORKER_FAILED_MESSAGE = "Criteria extraction failed. Retry later."
MODEL_NOT_CONFIGURED_MESSAGE = "AI criteria extraction is not configured. Ask an administrator to set the criteria model."

# A processing row older than this, with no progress, is treated as abandoned by a crashed worker.
STALE_PROCESSING_MINUTES = 30

# Every legacy writer carries this predicate. A requirements-v2 row (schema version 2) is never touched by a legacy writer.
LEGACY_ONLY_PREDICATE = "requirements_schema_version IS NULL"

# Requirements v2 is the default for every new job. Its rows carry this marker and this predicate; the v2 worker claims only these.
REQUIREMENTS_V2_SCHEMA_VERSION = 2
REQUIREMENTS_V2_PREDICATE = "requirements_schema_version = 2"

LEGACY_REFUSAL_DETAIL = "This job uses the requirements-v2 document. Edit it through the requirements API."

# Statuses that refuse CV intake and copy-to-job. A missing criteria row is treated as pending by every caller.
CRITERIA_BLOCKING_STATUSES = frozenset({"pending", "processing", "failed", "insufficient", "blocked"})


def display_status(stored: str, error: str | None, retry_count: int, max_retries: int) -> str:
    """The status the API reports, derived without a write.

    failed at the retry cap -> blocked; failed with the fixed insufficient message -> insufficient; any other failed -> failed.
    Every other stored status is reported unchanged.
    """
    if stored != "failed":
        return stored
    if retry_count >= max_retries:
        return "blocked"
    if error == INSUFFICIENT_MESSAGE:
        return "insufficient"
    return "failed"
