"""Explicit logical order of the cv_analyzer migration chain.

Every file under db/migrations must appear here exactly once; the runner refuses a directory whose contents differ from this
list. The order below is the execution order. A superseded original is never executed: its replacement runs at the
original's position, immediately after it in this list.

Replacement pairs (see db/runner/runner.py for the ledger rules):
    010_platform_control.sql             superseded by 095_repair_010_platform_control.sql
    070_sla_thresholds_config.sql        superseded by 096_repair_070_sla_policy_index.sql
    087_autosave_deterministic_answers.sql  superseded by 097_repair_087_knockout_answer_tenant.sql
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    file: str
    replaces: str | None = None        # set on a replacement: the original file it supersedes
    superseded_by: str | None = None   # set on an original: the replacement that runs in its place


MANIFEST: tuple[Entry, ...] = (
    Entry("002_intelligence_upgrade.sql"),
    Entry("003_cv_receiving.sql"),
    Entry("004_job_fields.sql"),
    Entry("005_job_code_alias.sql"),
    Entry("006_async_criteria.sql"),
    Entry("008_scoring_batch.sql"),
    Entry("009_evaluation_stages.sql"),
    Entry("010_platform_control.sql", superseded_by="095_repair_010_platform_control.sql"),
    Entry("095_repair_010_platform_control.sql", replaces="010_platform_control.sql"),
    Entry("010_platform_secrets_ai_prompts.sql"),
    Entry("011_additional_secrets.sql"),
    Entry("012_scoring_ingestion_audit_enhancements.sql"),
    Entry("013_fix_ingestion_confirmation_ai_comparison.sql"),
    Entry("014_fix_restrict_ai_comparison_alias_domain.sql"),
    Entry("015_runtime_email_config_from_db.sql"),
    Entry("017_level1_gatekeeper_and_duplicate_logs.sql"),
    Entry("018_duplicate_detection.sql"),
    Entry("019_duplicate_cv_file_storage.sql"),
    Entry("020_intake_metadata.sql"),
    Entry("021_duplicate_logs_manual_upload.sql"),
    Entry("022_notification_log.sql"),
    Entry("023_plan_features.sql"),
    Entry("024_plan_features_seed.sql"),
    Entry("025_job_metadata_fields.sql"),
    Entry("026_criteria_content_edit.sql"),
    Entry("027_public_apply_max_applications.sql"),
    Entry("028_add_public_apply_source.sql"),
    Entry("029_gatekeeper_system_config.sql"),
    Entry("030_hr_manager_role.sql"),
    Entry("031_intake_operational_timestamps.sql"),
    Entry("032_application_intake_log.sql"),
    Entry("033_scoring_enhancements.sql"),
    Entry("034_agency_client_model.sql"),
    Entry("035_subscription_mvp_fields.sql"),
    Entry("036_billing_cycle.sql"),
    Entry("037_pending_plan.sql"),
    Entry("038_email_verification_force_password.sql"),
    Entry("039_expand_status_column_lengths.sql"),
    Entry("040_job_application_controls_enabled.sql"),
    Entry("041_criteria_retry_control.sql"),
    Entry("042_normalized_text_hash.sql"),
    Entry("043_canonical_text_fingerprint.sql"),
    Entry("044_job_knockout_questions.sql"),
    Entry("045_knockout_passing_criteria.sql"),
    Entry("046_security_check_fields.sql"),
    Entry("047_security_detected_snippets.sql"),
    Entry("049_knockout_answers_unique_constraint.sql"),
    Entry("050_application_stopped_reason.sql"),
    Entry("051_duplicate_blocked_stopped_reason.sql"),
    Entry("052_ai_usage_tracking.sql"),
    Entry("053_ai_model_registry.sql"),
    Entry("054_ai_stage_rename_llm_only.sql"),
    Entry("055_job_campaigns.sql"),
    Entry("056_campaign_maturity.sql"),
    Entry("057_application_workflow.sql"),
    Entry("058_fix_workflow_pollution.sql"),
    Entry("059_rename_scored_to_ai_scored.sql"),
    Entry("060_advanced_workflow_move.sql"),
    Entry("061_tenant_advanced_move_setting.sql"),
    Entry("062_job_advanced_workflow_move.sql"),
    Entry("063_candidate_saved_views.sql"),
    Entry("064_candidate_assignment.sql"),
    Entry("065_candidate_comments.sql"),
    Entry("066_saved_views_shared.sql"),
    Entry("067_candidate_interviews.sql"),
    Entry("068_candidate_approvals.sql"),
    Entry("069_tenant_workflow_policies.sql"),
    Entry("070_sla_thresholds_config.sql", superseded_by="096_repair_070_sla_policy_index.sql"),
    Entry("096_repair_070_sla_policy_index.sql", replaces="070_sla_thresholds_config.sql"),
    Entry("071_candidate_tags.sql"),
    Entry("072_talent_pool_foundation.sql"),
    Entry("073_communication_templates.sql"),
    Entry("074_communication_send_support.sql"),
    Entry("075_preferred_contact_email.sql"),
    Entry("076_communication_automation.sql"),
    Entry("077_clean_stopped_workflow_pollution.sql"),
    Entry("078_knockout_answer_audit.sql"),
    Entry("079_knockout_answer_suggestions.sql"),
    Entry("080_knockout_analysis_prompt_seed.sql"),
    Entry("081_fix_knockout_answer_value_encoding.sql"),
    Entry("082_knockout_answer_provenance.sql"),
    Entry("083_email_body_storage.sql"),
    Entry("084_fix_knockout_provenance_not_found.sql"),
    Entry("085_fix_knockout_suggestions_invalid_answers.sql"),
    Entry("086_fix_knockout_answer_method_not_found.sql"),
    Entry("087_autosave_deterministic_answers.sql", superseded_by="097_repair_087_knockout_answer_tenant.sql"),
    Entry("097_repair_087_knockout_answer_tenant.sql", replaces="087_autosave_deterministic_answers.sql"),
    Entry("088_screening_answer_validation.sql"),
    Entry("089_recruiter_approved_method.sql"),
    Entry("090_validation_runs.sql"),
    Entry("091_fix_null_workflow_status_ai_scored.sql"),
    Entry("092_repair_legacy_new_workflow_status.sql"),
    Entry("093_requirements_v2_storage_schema.sql"),
    Entry("094_candidate_reuse_submission_source.sql"),
    Entry("098_application_scores_requirements_revision.sql"),
    Entry("099_platform_secret_accessors.sql"),
    Entry("100_runtime_rls_identity.sql"),
)
