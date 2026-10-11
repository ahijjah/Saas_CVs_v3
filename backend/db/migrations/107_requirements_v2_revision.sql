-- Migration 107: requirements-v2 editing API support (NOT APPLIED automatically; review before running).
-- Requires migration 106 (requirements_schema_version marker, version-aware weights_sum_100) to be applied first.
--
-- Revisions, retired item ids and original snapshots were inspected before this migration was written:
--   * revision          nothing persisted one. job_criteria only has last_edited_at/by (a timestamp is not a safe
--                       concurrency token) and the qualifying-context editor compares content instead. The editing API
--                       needs a monotonic token it can check inside a row lock  ->  requirements_revision.
--   * retired item ids  the ids of the ORIGINAL snapshot are recoverable from original_analysis_json, but an id that
--                       belonged to a recruiter-added item which was later deleted is recorded nowhere. Item ids must
--                       never be reused  ->  requirements_retired_item_ids (ids removed by a save; append-only).
--   * original snapshot already stored: job_criteria.original_analysis_json (migration 026), key "requirements".
--                       The editing API never writes that column.
--
-- 1. job_criteria.requirements_revision           INTEGER NOT NULL DEFAULT 0   (+1 on every requirements write)
-- 2. job_criteria.requirements_retired_item_ids   JSONB   NOT NULL DEFAULT '[]' (a JSON array of item-id strings)
-- 3. system_config row job_analysis.require_classification_acknowledgment = 'true' (boolean, editable). Platform-wide;
--    changed only through PUT /admin/platform-config/{key} (super_admin). There is no tenant override table.
--
-- Additive only: no existing row changes meaning (every existing job is legacy; the new columns are never read for a
-- legacy job). NOT NULL DEFAULT of a constant is a metadata-only change on PostgreSQL 11+.
--
-- Rollback (only while no v2 job exists):
--   ALTER TABLE job_criteria DROP COLUMN requirements_retired_item_ids;
--   ALTER TABLE job_criteria DROP COLUMN requirements_revision;
--   DELETE FROM system_config WHERE key = 'job_analysis.require_classification_acknowledgment';
--
-- Idempotent: safe to re-run.

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_revision_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_revision_check CHECK (requirements_revision >= 0);

ALTER TABLE job_criteria
    ADD COLUMN IF NOT EXISTS requirements_retired_item_ids JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE job_criteria
    DROP CONSTRAINT IF EXISTS job_criteria_requirements_retired_item_ids_check;
ALTER TABLE job_criteria
    ADD CONSTRAINT job_criteria_requirements_retired_item_ids_check
    CHECK (jsonb_typeof(requirements_retired_item_ids) = 'array');

COMMENT ON COLUMN job_criteria.requirements_revision IS
    'Optimistic-concurrency token of the requirements-v2 document; incremented by every write through the editing API.';
COMMENT ON COLUMN job_criteria.requirements_retired_item_ids IS
    'Ids of requirements-v2 items removed by a recruiter save. Append-only; ids are never reused.';

INSERT INTO system_config (key, value, type, category, editable, description)
VALUES ('job_analysis.require_classification_acknowledgment', 'true', 'boolean', 'general', true,
        'Require recruiters to correct or acknowledge each flagged Preferred classification before a requirements-v2 '
        'job can proceed. true = required (default); false = warnings stay visible but do not block. Platform-wide.')
ON CONFLICT (key) DO NOTHING;

COMMIT;
