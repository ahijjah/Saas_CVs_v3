-- Migration 098: record the requirements revision a score was computed from (schema only).
--
-- A requirements-v2 score is computed from one stored document at one revision. This column keeps that revision on the score, so an
-- audit can tell which document a score used and a later edit cannot be mistaken for it. Legacy scores keep NULL: they were never
-- computed from a requirements-v2 document, and no existing row changes meaning.
--
-- Not changed here: grants, row-level security, functions, other tables and any stored value.
-- Requires the current chain up to migration 097 (application_scores).

BEGIN;

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS requirements_revision_used INTEGER NULL;
ALTER TABLE application_scores
    DROP CONSTRAINT IF EXISTS application_scores_requirements_revision_used_check;
ALTER TABLE application_scores
    ADD CONSTRAINT application_scores_requirements_revision_used_check
    CHECK (requirements_revision_used IS NULL OR requirements_revision_used >= 0);

COMMIT;
