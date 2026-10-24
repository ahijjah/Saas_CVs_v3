-- 104_candidate_matrix.sql
--
-- Final Candidate Matrix: saved layouts and one explicit review reason on item evaluations.
--
--   * candidate_matrix_configs holds the matrix layout at three scopes: the tenant default, a job's shared layout, and one user's
--     personal view of a job. A layout is an ordered list of stable column ids plus the sort and filters (validated by the API against
--     a fixed column registry; the table stores only what the API accepted). Precedence at read time: personal, job, tenant, system.
--     Every row is tenant-scoped (row-level security) and versioned for optimistic concurrency.
--   * application_item_evaluations.review_reason records WHY an evaluated item needs a person: its quotation could not be verified in the
--     CV, the model's confidence was too low, or the evaluator marked the evidence as conflicting. It is only a label for the matrix flags;
--     it never changes a result, a score or a gate. Existing rows are labelled once from the downgrade note the evaluator already wrote.
--
-- Additive and idempotent. Nothing existing is dropped or rewritten except the new column. The rollback script is
-- db/rollback/104_candidate_matrix.down.sql (not a migration; run by hand on a restored copy first, see docs/FINAL_CANDIDATE_MATRIX.md).

BEGIN;

CREATE TABLE IF NOT EXISTS cv_analyzer.candidate_matrix_configs (
    config_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    scope       varchar(10) NOT NULL,
    job_id      uuid        REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE,
    user_id     uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE CASCADE,
    config      jsonb       NOT NULL,
    version     integer     NOT NULL DEFAULT 1,
    created_by  uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    updated_by  uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT candidate_matrix_configs_scope_check CHECK (
        (scope = 'tenant'   AND job_id IS NULL     AND user_id IS NULL)
     OR (scope = 'job'      AND job_id IS NOT NULL AND user_id IS NULL)
     OR (scope = 'personal' AND job_id IS NOT NULL AND user_id IS NOT NULL)),
    CONSTRAINT candidate_matrix_configs_config_check CHECK (jsonb_typeof(config) = 'object' AND pg_column_size(config) <= 32768),
    CONSTRAINT candidate_matrix_configs_version_check CHECK (version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_config_tenant   ON cv_analyzer.candidate_matrix_configs (tenant_id)         WHERE scope = 'tenant';
CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_config_job      ON cv_analyzer.candidate_matrix_configs (job_id)            WHERE scope = 'job';
CREATE UNIQUE INDEX IF NOT EXISTS uq_matrix_config_personal ON cv_analyzer.candidate_matrix_configs (job_id, user_id)  WHERE scope = 'personal';
CREATE INDEX IF NOT EXISTS idx_matrix_config_tenant ON cv_analyzer.candidate_matrix_configs (tenant_id);

ALTER TABLE cv_analyzer.candidate_matrix_configs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope_candidate_matrix_configs ON cv_analyzer.candidate_matrix_configs;
CREATE POLICY tenant_scope_candidate_matrix_configs ON cv_analyzer.candidate_matrix_configs FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));

ALTER TABLE cv_analyzer.application_item_evaluations
    ADD COLUMN IF NOT EXISTS review_reason varchar(24);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'application_item_evaluations_review_reason_check'
                   AND conrelid = 'cv_analyzer.application_item_evaluations'::regclass) THEN
        ALTER TABLE cv_analyzer.application_item_evaluations
            ADD CONSTRAINT application_item_evaluations_review_reason_check
            CHECK (review_reason IS NULL OR review_reason IN ('no_verified_evidence', 'low_confidence', 'conflicting_evidence'));
    END IF;
END
$$;

-- One-time labelling of evaluations written before this column existed, from the exact note services/item_scoring/evaluator.py writes.
UPDATE cv_analyzer.application_item_evaluations
   SET review_reason = 'no_verified_evidence'
 WHERE review_reason IS NULL AND explanation LIKE 'Downgraded to %: no quotation from the CV could be verified%';
UPDATE cv_analyzer.application_item_evaluations
   SET review_reason = 'low_confidence'
 WHERE review_reason IS NULL AND explanation LIKE 'Downgraded to %: the model''s confidence is too low%';

CREATE INDEX IF NOT EXISTS idx_item_evaluations_run_item ON cv_analyzer.application_item_evaluations (run_id, importance, result);

COMMIT;
