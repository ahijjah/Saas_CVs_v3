-- Rollback of 102_gates_knockout_criteria.sql. NOT a migration: the runner never reads this directory, and the ledger row for 102 is
-- not removed by it. Use it only on a restored COPY first (docs/GATES_KNOCKOUT_CRITERIA.md), and only together with deploying the code
-- that predates 102. Migrations are otherwise forward-only; the normal rollback is restoring the pre-upgrade backup.
--
-- What it removes is exactly what 102 added. It loses every gate result and its history, and the eligibility status and the gate
-- settings of questions; the applications, the scores, the decisions, the questions, the answers and the requirements documents are
-- untouched. Confirmed gates and suggestions inside job_criteria.analysis_json stay in the document: the older validator refuses an unknown
-- item key, so a job that carries them cannot be edited by the older code until they are removed (the strip statement at the end of this
-- file does that on a copy; it is commented out because it changes requirement documents and needs a decision).

BEGIN;
DROP TABLE IF EXISTS cv_analyzer.application_gate_result_events;
DROP TABLE IF EXISTS cv_analyzer.application_gate_results;
ALTER TABLE cv_analyzer.applications DROP CONSTRAINT IF EXISTS applications_eligibility_status_check;
ALTER TABLE cv_analyzer.applications DROP COLUMN IF EXISTS eligibility_evaluated_at, DROP COLUMN IF EXISTS eligibility_status;
DROP INDEX IF EXISTS cv_analyzer.idx_knockout_questions_gate;
ALTER TABLE cv_analyzer.job_knockout_questions DROP CONSTRAINT IF EXISTS job_knockout_questions_gate_failure_action_check;
ALTER TABLE cv_analyzer.job_knockout_questions DROP COLUMN IF EXISTS gate_failure_action, DROP COLUMN IF EXISTS is_eligibility_gate;
COMMIT;

-- Optional, needs a decision (it rewrites requirement documents and bumps nothing else):
-- UPDATE cv_analyzer.job_criteria SET analysis_json = jsonb_set(analysis_json, '{requirements,categories}', (
--     SELECT jsonb_object_agg(c.key, jsonb_set(c.value, '{items}', (
--         SELECT coalesce(jsonb_agg(i - 'gate' - 'gate_suggestion' ORDER BY o), '[]'::jsonb)
--         FROM jsonb_array_elements(c.value->'items') WITH ORDINALITY AS t(i, o))))
--     FROM jsonb_each(analysis_json->'requirements'->'categories') AS c))
-- WHERE analysis_json->'requirements'->'categories' IS NOT NULL;
