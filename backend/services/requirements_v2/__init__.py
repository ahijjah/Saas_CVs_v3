"""
Requirements v2: contract, validation, weight arithmetic, readiness and edit-tracking (stage 1).

ISOLATED: pure functions over plain dicts. Not imported by any router, worker, prompt, scoring or intake path; no
database, no model call, no configuration. Legacy job analysis is unaffected by this package.
"""
from services.requirements_v2.acknowledgment import (
    CLASSIFICATION_WARNING_CODES, POLICY_KEY, AcknowledgmentError, ClassificationStatus, ReconcileResult,
    acknowledge_classification_warning, carry_classification_review, classification_status,
    parse_acknowledgment_policy, reconcile_classification_review,
)
from services.requirements_v2.comparison import (
    edited_categories, edited_category_names, original_digest, snapshot_original,
)
from services.requirements_v2.contract import (
    CATEGORIES, MAX_REQUIRED_PER_CATEGORY, NEEDS_CLASSIFICATION_REVIEW, SCHEMA_VERSION, Issue, ValidationResult,
    collect_item_ids, empty_requirements, make_item, new_item_id,
)
from services.requirements_v2.editing import (
    add_item, remove_item, set_category_weight, set_importance, set_item_weight, set_text,
)
from services.requirements_v2.readiness import (
    ConfirmationError, Readiness, basis_hash, carry_confirmation, carry_server_owned, compute_readiness,
    confirm_no_numeric_score, is_preferred_only,
)
from services.requirements_v2.validation import validate_draft, validate_final
from services.requirements_v2.weights import (
    NormalizeResult, OverLimitError, apply_category_weights, equal_category_weights, equalize_category,
    equalize_weights, normalize_category_weights,
)

__all__ = [
    "AcknowledgmentError", "CLASSIFICATION_WARNING_CODES", "ClassificationStatus", "NEEDS_CLASSIFICATION_REVIEW",
    "POLICY_KEY", "ReconcileResult", "acknowledge_classification_warning", "carry_classification_review",
    "carry_server_owned", "classification_status", "parse_acknowledgment_policy", "reconcile_classification_review",
    "CATEGORIES", "MAX_REQUIRED_PER_CATEGORY", "SCHEMA_VERSION", "ConfirmationError", "Issue", "NormalizeResult",
    "OverLimitError", "Readiness", "ValidationResult", "add_item", "apply_category_weights", "basis_hash",
    "carry_confirmation", "collect_item_ids", "compute_readiness", "confirm_no_numeric_score", "edited_categories",
    "edited_category_names", "empty_requirements", "equal_category_weights", "equalize_category",
    "equalize_weights", "is_preferred_only", "make_item", "new_item_id", "normalize_category_weights",
    "original_digest", "remove_item", "set_category_weight", "set_importance", "set_item_weight", "set_text",
    "snapshot_original", "validate_draft", "validate_final",
]
