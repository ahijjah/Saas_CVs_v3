-- PROPOSAL (not applied anywhere): tenant isolation for the re-analysis history. Today requirements_analysis_log has NO row-level security, so any
-- session of the runtime role can read every tenant's history. This adds the same tenant rule the other tenant tables use, plus the super_admin
-- rule the background paths use. Apply only after the runtime role is adopted, and only with the operator's approval (it is a policy change).
-- Tested on a disposable database: tests/test_runtime_role_plan.py and the recovery suite in runtime mode.
SET search_path = cv_analyzer;
ALTER TABLE requirements_analysis_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS requirements_analysis_log_tenant ON requirements_analysis_log;
CREATE POLICY requirements_analysis_log_tenant ON requirements_analysis_log
    USING (tenant_id::text = current_setting('app.current_tenant_id', true)
           OR current_setting('app.current_role', true) = 'super_admin')
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true)
                OR current_setting('app.current_role', true) = 'super_admin');
