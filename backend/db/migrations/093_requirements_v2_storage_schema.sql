-- Migration 093: requirements-v2 storage foundation (schema only).
--
-- Adds the columns that a requirements-v2 job needs to store and edit its document. Nothing here creates a v2 job, changes a
-- stored legacy job, writes settings, or adds a route, a service or a worker. Every existing row keeps its meaning: a NULL marker
-- is a legacy job, and its weights rule is exactly the rule that was in force before this migration.
--
-- 1. job_criteria.requirements_schema_version  SMALLINT NULL, and only NULL or 2 is accepted.
--      NULL = legacy job (every existing row), 2 = requirements-v2 job.
-- 2. job_criteria.requirements_revision        INTEGER NOT NULL DEFAULT 0, and never negative.
--      A monotonic token that an editor checks inside a row lock.
-- 3. job_criteria.requirements_retired_item_ids JSONB NOT NULL DEFAULT '[]', and always a JSON array.
--      Ids of recruiter-removed requirements, so that an id is never reused.
-- 4. weights_sum_100 becomes version-aware:
--      legacy (marker NULL)  the total must be exactly 100 (unchanged)
--      v2     (marker = 2)   the total must be 100 (weighted) or 0 (preferred-only)
--
-- Not changed here: applications.stopped_reason, system_config, the job_criteria row-level security policy, and grants.
-- Requires the current chain up to migration 092 (job_criteria and its weights_sum_100 constraint).

BEGIN;

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_schema_version SMALLINT NULL;
ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_schema_version_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_schema_version_check
    CHECK (requirements_schema_version IS NULL OR requirements_schema_version = 2);

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_revision_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_revision_check
    CHECK (requirements_revision >= 0);

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_retired_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_retired_item_ids_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_retired_item_ids_check
    CHECK (jsonb_typeof(requirements_retired_item_ids) = 'array');

-- The weight columns are NOT NULL, so the total is never NULL. For a legacy row the CASE takes the ELSE branch, which is the
-- original rule, exactly. Only a row marked 2 may total 0.
ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS weights_sum_100;
ALTER TABLE job_criteria
    ADD CONSTRAINT weights_sum_100 CHECK (
        CASE
            WHEN requirements_schema_version = 2 THEN
                weight_skills + weight_experience + weight_education + weight_certifications
                + weight_soft_skills + weight_domain_knowledge + weight_other IN (0, 100)
            ELSE
                weight_skills + weight_experience + weight_education + weight_certifications
                + weight_soft_skills + weight_domain_knowledge + weight_other = 100
        END
    );

COMMIT;
