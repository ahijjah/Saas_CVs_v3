-- Migration 110: tenant isolation for the re-analysis history (requirements_analysis_log). ADDITIVE and idempotent.
--
-- Before this migration the history had no row-level security: a session of the restricted runtime role could read every tenant's events.
-- The services filtered by job, which was the only protection. This adds the same tenant rule the other tenant tables use, with the same
-- super_admin rule the background paths use, and WITH CHECK so a session cannot append an event for another tenant.
--
-- Unchanged on purpose: the append-only triggers of migration 109 (UPDATE, DELETE and TRUNCATE are still refused for every role except one that
-- disables them, which is a privileged operation), and the table's grants. Nothing here deletes or rewrites rows.
--
-- Rollback (only if the feature is not in use; the history is never dropped by rollback): ALTER TABLE requirements_analysis_log DISABLE ROW LEVEL
-- SECURITY; DROP POLICY requirements_analysis_log_tenant ON requirements_analysis_log;

BEGIN;
SET search_path = cv_analyzer;

ALTER TABLE requirements_analysis_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS requirements_analysis_log_tenant ON requirements_analysis_log;
CREATE POLICY requirements_analysis_log_tenant ON requirements_analysis_log
    USING (tenant_id::text = current_setting('app.current_tenant_id', true)
           OR current_setting('app.current_role', true) = 'super_admin')
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true)
                OR current_setting('app.current_role', true) = 'super_admin');

COMMIT;
