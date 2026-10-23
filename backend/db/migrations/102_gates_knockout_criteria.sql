-- 102_gates_knockout_criteria.sql
--
-- Gates / Knockout Criteria: one eligibility model on top of the existing Requirements v2 document and knockout questions.
--
--   * The requirement side lives in the Requirements v2 document (job_criteria.analysis_json), not in a new table. Nothing is
--     changed there by this migration.
--   * job_knockout_questions gets an explicit eligibility control. EVERY EXISTING QUESTION STAYS ADVISORY: the new column defaults to
--     false, so no question becomes an enforced gate unless a person turns it on.
--   * applications gets the derived eligibility status. Existing rows are 'not_applicable' until gates are evaluated; the score and the
--     decision columns are not touched.
--   * application_gate_results holds one row per application and gate: the system's result, the effective result, the evidence source,
--     and the override. application_gate_result_events is the append-only history (evaluation, recorded result, override): the original
--     result, the new result, the user and the time.
--
-- Additive and idempotent. A rollback script that undoes exactly this lives in db/rollback/102_gates_knockout_criteria.down.sql
-- (not a migration; it is run by hand against a restored copy first, see docs/GATES_KNOCKOUT_CRITERIA.md).

BEGIN;

ALTER TABLE cv_analyzer.job_knockout_questions
    ADD COLUMN IF NOT EXISTS is_eligibility_gate  boolean     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS gate_failure_action  varchar(20) NOT NULL DEFAULT 'not_eligible';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'job_knockout_questions_gate_failure_action_check'
                   AND conrelid = 'cv_analyzer.job_knockout_questions'::regclass) THEN
        ALTER TABLE cv_analyzer.job_knockout_questions
            ADD CONSTRAINT job_knockout_questions_gate_failure_action_check
            CHECK (gate_failure_action IN ('not_eligible', 'hold_for_review'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_knockout_questions_gate
    ON cv_analyzer.job_knockout_questions (job_id) WHERE is_eligibility_gate;

ALTER TABLE cv_analyzer.applications
    ADD COLUMN IF NOT EXISTS eligibility_status       varchar(20) NOT NULL DEFAULT 'not_applicable',
    ADD COLUMN IF NOT EXISTS eligibility_evaluated_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'applications_eligibility_status_check'
                   AND conrelid = 'cv_analyzer.applications'::regclass) THEN
        ALTER TABLE cv_analyzer.applications
            ADD CONSTRAINT applications_eligibility_status_check
            CHECK (eligibility_status IN ('not_applicable', 'eligible', 'not_eligible', 'review_required'));
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS cv_analyzer.application_gate_results (
    gate_result_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id       uuid        NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id            uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    job_id               uuid        NOT NULL REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE,
    gate_key             text        NOT NULL,                   -- 'req:<requirement item id>' or 'q:<question id>'
    gate_source          varchar(20) NOT NULL,
    requirement_item_id  text,
    question_id          uuid,
    gate_label           text        NOT NULL,                   -- the requirement or question wording when it was evaluated
    verification_method  varchar(20) NOT NULL,
    failure_action       varchar(20) NOT NULL,
    requirements_revision integer,
    computed_result      varchar(20) NOT NULL,                   -- what the system, a recorded assessment or a person established
    result               varchar(20) NOT NULL,                   -- the effective result: computed_result, or 'overridden'
    evidence_source      varchar(20) NOT NULL DEFAULT 'none',
    evidence_summary     text,
    confidence           numeric(4,3),
    evaluated_at         timestamptz NOT NULL DEFAULT now(),
    recorded_by          uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    override_reason      text,
    overridden_by        uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    overridden_at        timestamptz,
    CONSTRAINT application_gate_results_unique UNIQUE (application_id, gate_key),
    CONSTRAINT application_gate_results_source_check CHECK (gate_source IN ('requirement', 'question')),
    CONSTRAINT application_gate_results_method_check
        CHECK (verification_method IN ('cv_evidence', 'candidate_question', 'assessment', 'manual')),
    CONSTRAINT application_gate_results_action_check CHECK (failure_action IN ('not_eligible', 'hold_for_review')),
    CONSTRAINT application_gate_results_computed_check
        CHECK (computed_result IN ('passed', 'failed', 'needs_verification', 'not_assessed')),
    CONSTRAINT application_gate_results_result_check
        CHECK (result IN ('passed', 'failed', 'needs_verification', 'not_assessed', 'overridden')),
    CONSTRAINT application_gate_results_evidence_check
        CHECK (evidence_source IN ('cv', 'candidate_answer', 'assessment', 'manual', 'none')),
    CONSTRAINT application_gate_results_override_check
        CHECK ((result = 'overridden') = (override_reason IS NOT NULL AND overridden_at IS NOT NULL)),
    CONSTRAINT application_gate_results_confidence_check CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE INDEX IF NOT EXISTS idx_gate_results_application ON cv_analyzer.application_gate_results (application_id);
CREATE INDEX IF NOT EXISTS idx_gate_results_tenant_job ON cv_analyzer.application_gate_results (tenant_id, job_id);

CREATE TABLE IF NOT EXISTS cv_analyzer.application_gate_result_events (
    event_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    gate_result_id  uuid        NOT NULL REFERENCES cv_analyzer.application_gate_results(gate_result_id) ON DELETE CASCADE,
    application_id  uuid        NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id       uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    event_type      varchar(24) NOT NULL,
    original_result varchar(20),
    new_result      varchar(20) NOT NULL,
    user_id         uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT application_gate_result_events_type_check
        CHECK (event_type IN ('evaluated', 'recorded', 'overridden', 'override_cleared')),
    CONSTRAINT application_gate_result_events_original_check
        CHECK (original_result IS NULL OR original_result IN ('passed', 'failed', 'needs_verification', 'not_assessed', 'overridden')),
    CONSTRAINT application_gate_result_events_new_check
        CHECK (new_result IN ('passed', 'failed', 'needs_verification', 'not_assessed', 'overridden')),
    CONSTRAINT application_gate_result_events_override_reason_check
        CHECK (event_type <> 'overridden' OR (reason IS NOT NULL AND length(btrim(reason)) > 0 AND user_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_gate_events_result ON cv_analyzer.application_gate_result_events (gate_result_id, created_at);
CREATE INDEX IF NOT EXISTS idx_gate_events_application ON cv_analyzer.application_gate_result_events (application_id, created_at);

-- Row-level security, the same shape as every other tenant table (migration 100): the tenant of the session, or the platform
-- administration role. A worker has no tenant context, so it gets one named policy per table, as elsewhere.
ALTER TABLE cv_analyzer.application_gate_results       ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.application_gate_result_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_scope_application_gate_results ON cv_analyzer.application_gate_results;
CREATE POLICY tenant_scope_application_gate_results ON cv_analyzer.application_gate_results FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
DROP POLICY IF EXISTS tenant_scope_application_gate_result_events ON cv_analyzer.application_gate_result_events;
CREATE POLICY tenant_scope_application_gate_result_events ON cv_analyzer.application_gate_result_events FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS cv_worker_access_application_gate_results ON cv_analyzer.application_gate_results;
CREATE POLICY cv_worker_access_application_gate_results ON cv_analyzer.application_gate_results FOR ALL TO cv_worker
    USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS cv_worker_access_application_gate_result_events ON cv_analyzer.application_gate_result_events;
CREATE POLICY cv_worker_access_application_gate_result_events ON cv_analyzer.application_gate_result_events FOR ALL TO cv_worker
    USING (true) WITH CHECK (true);

COMMIT;
