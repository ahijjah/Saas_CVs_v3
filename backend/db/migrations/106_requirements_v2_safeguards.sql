-- Migration 106: requirements-v2 safeguards (NOT APPLIED automatically; review before running).
--
-- 1. job_criteria.requirements_schema_version  SMALLINT NULL
--      NULL = legacy job (every existing row). 2 = requirements-v2 job. Only the value 2 is accepted, so an unknown
--      version cannot be written. No backfill, no default, no change to existing rows.
--
-- 2. weights_sum_100 (category weights) becomes version-aware:
--      legacy (marker NULL)  total must be exactly 100   -> unchanged rule
--      v2     (marker = 2)   total must be 100 (weighted analysis) OR 0 (preferred-only / incomplete analysis)
--    Which v2 state is valid for a given job, item weights, readiness and confirmation stay enforced by the v2
--    validator in application code, not here.
--    The legacy branch is exactly the old expression, so every existing row still satisfies the constraint.
--    NULL-safety: `marker IS NOT NULL AND total = 0` is FALSE (not NULL) for a legacy row, so a legacy row with all
--    weights 0 is rejected as before.
--
-- 3. applications.stopped_reason gains 'evaluation_unsupported' (a requirements-v2 job reached candidate evaluation,
--    which does not support it yet). The new list is a strict superset of migration 104's.
--
-- Pre-checks (read-only):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'cv_analyzer.job_criteria'::regclass AND contype = 'c';        -- expect weights_sum_100
--   SELECT DISTINCT stopped_reason FROM cv_analyzer.applications;                     -- subset of migration 104's list
--
-- Rollback (only while no v2 rows exist):
--   ALTER TABLE job_criteria DROP CONSTRAINT weights_sum_100;
--   ALTER TABLE job_criteria ADD CONSTRAINT weights_sum_100 CHECK (weight_skills + weight_experience +
--       weight_education + weight_certifications + weight_soft_skills + weight_domain_knowledge + weight_other = 100);
--   ALTER TABLE job_criteria DROP CONSTRAINT job_criteria_requirements_schema_version_check;
--   ALTER TABLE job_criteria DROP COLUMN requirements_schema_version;
--   (and re-create applications_stopped_reason_check without 'evaluation_unsupported', as in migration 104)
--
-- Idempotent: safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

-- ── 1. marker ────────────────────────────────────────────────────────────────
ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_schema_version SMALLINT NULL;

ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_schema_version_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_schema_version_check
    CHECK (requirements_schema_version IS NULL OR requirements_schema_version = 2);

COMMENT ON COLUMN job_criteria.requirements_schema_version IS
    'NULL = legacy job analysis. 2 = requirements-v2 analysis. Set when the job is created; never used to convert '
    'an existing job.';

-- ── 2. category-weight total ─────────────────────────────────────────────────
ALTER TABLE job_criteria DROP CONSTRAINT IF EXISTS weights_sum_100;
ALTER TABLE job_criteria
    ADD CONSTRAINT weights_sum_100
    CHECK (
        (weight_skills + weight_experience + weight_education + weight_certifications
            + weight_soft_skills + weight_domain_knowledge + weight_other = 100)
        OR
        (requirements_schema_version IS NOT NULL
            AND weight_skills + weight_experience + weight_education + weight_certifications
                + weight_soft_skills + weight_domain_knowledge + weight_other = 0)
    );

-- ── 3. stopped_reason ────────────────────────────────────────────────────────
ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_stopped_reason_check;
ALTER TABLE applications
    ADD CONSTRAINT applications_stopped_reason_check
    CHECK (stopped_reason IN (
        'security_blocked',
        'extraction_failed',
        'processing_error',
        'duplicate_blocked',
        'scoring_failed',
        'evaluation_unsupported',
        'other'
    ));

COMMIT;
