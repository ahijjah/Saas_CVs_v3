-- 103_item_level_scoring.sql
--
-- Requirements V2 item-level candidate scoring.
--
--   * application_scoring_runs: one row per scoring run of an application (the first score and every explicit rescore). A run is tied to
--     the exact requirements_revision it evaluated, snapshots the category weights it used, and carries the deterministic totals the
--     application code computed (final score, provisional range, status). Earlier runs are kept (is_current = false), never deleted.
--   * application_item_evaluations: one row per requirement item of a run: the model's result and evidence, the effective result (the
--     model's, or a human resolution), and who resolved it and why. A Required item is met / partially_met / not_met / unclear; a Preferred
--     item is yes / no / not_clear and never carries a weight.
--   * application_item_evaluation_events: append-only history of evaluations and human resolutions.
--   * applications.scoring_status / item_final_score: the displayed outcome of the CURRENT run, so that lists, exports and analytics need no
--     join. scoring_status is NULL for every application scored before this migration (legacy category scoring): nothing is converted, and
--     the legacy score in application_scores keeps its meaning. A preferred-only job is 'not_scored' and its item_final_score is NULL, not 0.
--
-- Additive and idempotent. The rollback script is db/rollback/103_item_level_scoring.down.sql (not a migration).

BEGIN;

ALTER TABLE cv_analyzer.applications
    ADD COLUMN IF NOT EXISTS scoring_status   varchar(20),
    ADD COLUMN IF NOT EXISTS item_final_score smallint;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'applications_scoring_status_check'
                   AND conrelid = 'cv_analyzer.applications'::regclass) THEN
        ALTER TABLE cv_analyzer.applications
            ADD CONSTRAINT applications_scoring_status_check
            CHECK (scoring_status IS NULL OR scoring_status IN ('scored', 'review_required', 'not_scored'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'applications_item_final_score_check'
                   AND conrelid = 'cv_analyzer.applications'::regclass) THEN
        ALTER TABLE cv_analyzer.applications
            ADD CONSTRAINT applications_item_final_score_check
            CHECK (item_final_score IS NULL OR item_final_score BETWEEN 0 AND 100);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS cv_analyzer.application_scoring_runs (
    run_id                  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id          uuid        NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id               uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    job_id                  uuid        NOT NULL REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE,
    requirements_revision   integer     NOT NULL,
    scoring_method          varchar(24) NOT NULL DEFAULT 'item_level_v2',
    status                  varchar(20) NOT NULL,
    is_current              boolean     NOT NULL DEFAULT true,
    category_weights        jsonb       NOT NULL,                  -- {category: weight} the run was computed with
    final_score_raw         numeric(8,3),                          -- exact; NULL unless status = 'scored'
    final_score_min         numeric(8,3),                          -- provisional range while review is required
    final_score_max         numeric(8,3),
    final_score             smallint,                              -- the one rounded whole number (half up); NULL unless 'scored'
    decision                varchar(20),                           -- the stored scoring decision; NULL unless 'scored'
    qualified_threshold_used integer,
    partial_threshold_used  integer,
    model                   varchar(100),
    narrative               jsonb,                                 -- text only: summary, strengths, gaps, red flags, interview questions
    gatekeeper_hint         jsonb,                                 -- the local gatekeeper's routing hint; never a rejection for a v2 job
    requested_by            uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    superseded_at           timestamptz,
    CONSTRAINT application_scoring_runs_status_check CHECK (status IN ('scored', 'review_required', 'not_scored')),
    CONSTRAINT application_scoring_runs_method_check CHECK (scoring_method IN ('item_level_v2')),
    CONSTRAINT application_scoring_runs_decision_check CHECK (decision IS NULL OR decision IN ('qualified', 'partial', 'rejected')),
    CONSTRAINT application_scoring_runs_score_check CHECK (final_score IS NULL OR final_score BETWEEN 0 AND 100),
    CONSTRAINT application_scoring_runs_final_only_when_scored CHECK (
        (status = 'scored') = (final_score IS NOT NULL AND final_score_raw IS NOT NULL AND decision IS NOT NULL)),
    CONSTRAINT application_scoring_runs_range_when_review CHECK (
        status <> 'review_required' OR (final_score_min IS NOT NULL AND final_score_max IS NOT NULL AND final_score_min <= final_score_max)),
    CONSTRAINT application_scoring_runs_current_not_superseded CHECK (NOT is_current OR superseded_at IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scoring_runs_one_current
    ON cv_analyzer.application_scoring_runs (application_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_scoring_runs_application ON cv_analyzer.application_scoring_runs (application_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scoring_runs_tenant_job ON cv_analyzer.application_scoring_runs (tenant_id, job_id);

CREATE TABLE IF NOT EXISTS cv_analyzer.application_item_evaluations (
    evaluation_id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  uuid        NOT NULL REFERENCES cv_analyzer.application_scoring_runs(run_id) ON DELETE CASCADE,
    application_id          uuid        NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id               uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    requirements_revision   integer     NOT NULL,
    position                integer     NOT NULL DEFAULT 0,         -- the item's place in the document, so the view keeps the document order
    item_id                 text        NOT NULL,                  -- the requirement id in the Requirements V2 document
    category                varchar(24) NOT NULL,
    importance              varchar(10) NOT NULL,
    item_text               text        NOT NULL,                  -- the wording that was evaluated
    item_weight             smallint,                              -- whole percent inside the category; NULL for a Preferred item
    ai_result               varchar(20) NOT NULL,                  -- what the model (after evidence validation) concluded
    result                  varchar(20) NOT NULL,                  -- the effective result: ai_result, or a human resolution
    evidence                jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- quotations that were verified against the CV text
    explanation             text,
    confidence              numeric(4,3),
    evaluation_source       varchar(100) NOT NULL,
    evaluated_at            timestamptz NOT NULL DEFAULT now(),
    resolved_by             uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    resolved_at             timestamptz,
    resolution_reason       text,
    CONSTRAINT application_item_evaluations_unique UNIQUE (run_id, item_id),
    CONSTRAINT application_item_evaluations_importance_check CHECK (importance IN ('required', 'preferred')),
    CONSTRAINT application_item_evaluations_result_check CHECK (
        (importance = 'required' AND ai_result IN ('met', 'partially_met', 'not_met', 'unclear')
                                 AND result    IN ('met', 'partially_met', 'not_met', 'unclear'))
        OR (importance = 'preferred' AND ai_result IN ('yes', 'no', 'not_clear') AND result IN ('yes', 'no', 'not_clear'))),
    CONSTRAINT application_item_evaluations_weight_check CHECK (
        (importance = 'required' AND item_weight IS NOT NULL AND item_weight BETWEEN 1 AND 100) OR (importance = 'preferred' AND item_weight IS NULL)),
    CONSTRAINT application_item_evaluations_confidence_check CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CONSTRAINT application_item_evaluations_resolution_check CHECK (
        (resolved_by IS NULL AND resolved_at IS NULL AND resolution_reason IS NULL AND result = ai_result)
        OR (resolved_at IS NOT NULL AND resolution_reason IS NOT NULL AND length(btrim(resolution_reason)) > 0))
);

CREATE INDEX IF NOT EXISTS idx_item_evaluations_application ON cv_analyzer.application_item_evaluations (application_id);
CREATE INDEX IF NOT EXISTS idx_item_evaluations_tenant ON cv_analyzer.application_item_evaluations (tenant_id, run_id);

CREATE TABLE IF NOT EXISTS cv_analyzer.application_item_evaluation_events (
    event_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id   uuid        NOT NULL REFERENCES cv_analyzer.application_item_evaluations(evaluation_id) ON DELETE CASCADE,
    run_id          uuid        NOT NULL REFERENCES cv_analyzer.application_scoring_runs(run_id) ON DELETE CASCADE,
    application_id  uuid        NOT NULL REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE,
    tenant_id       uuid        NOT NULL REFERENCES cv_analyzer.tenants(tenant_id),
    event_type      varchar(16) NOT NULL,
    previous_result varchar(20),
    new_result      varchar(20) NOT NULL,
    user_id         uuid        REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT application_item_evaluation_events_type_check CHECK (event_type IN ('evaluated', 'resolved')),
    CONSTRAINT application_item_evaluation_events_resolved_check CHECK (
        event_type <> 'resolved' OR (reason IS NOT NULL AND length(btrim(reason)) > 0 AND user_id IS NOT NULL AND previous_result IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_item_evaluation_events_evaluation ON cv_analyzer.application_item_evaluation_events (evaluation_id, created_at);

-- Row-level security: the same shape as every other tenant table (migration 100) plus one named worker policy per table.
ALTER TABLE cv_analyzer.application_scoring_runs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.application_item_evaluations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.application_item_evaluation_events  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_scope_application_scoring_runs ON cv_analyzer.application_scoring_runs;
CREATE POLICY tenant_scope_application_scoring_runs ON cv_analyzer.application_scoring_runs FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
DROP POLICY IF EXISTS tenant_scope_application_item_evaluations ON cv_analyzer.application_item_evaluations;
CREATE POLICY tenant_scope_application_item_evaluations ON cv_analyzer.application_item_evaluations FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
DROP POLICY IF EXISTS tenant_scope_application_item_evaluation_events ON cv_analyzer.application_item_evaluation_events;
CREATE POLICY tenant_scope_application_item_evaluation_events ON cv_analyzer.application_item_evaluation_events FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));

DROP POLICY IF EXISTS cv_worker_access_application_scoring_runs ON cv_analyzer.application_scoring_runs;
CREATE POLICY cv_worker_access_application_scoring_runs ON cv_analyzer.application_scoring_runs FOR ALL TO cv_worker
    USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS cv_worker_access_application_item_evaluations ON cv_analyzer.application_item_evaluations;
CREATE POLICY cv_worker_access_application_item_evaluations ON cv_analyzer.application_item_evaluations FOR ALL TO cv_worker
    USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS cv_worker_access_application_item_evaluation_events ON cv_analyzer.application_item_evaluation_events;
CREATE POLICY cv_worker_access_application_item_evaluation_events ON cv_analyzer.application_item_evaluation_events FOR ALL TO cv_worker
    USING (true) WITH CHECK (true);

COMMIT;
