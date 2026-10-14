-- 111: row-level-security policies that read settings the application never sets.
--
-- The application sets app.current_tenant_id and app.current_role (database.py, set_rls_context). Three earlier policies read other names, so
-- for any role without BYPASSRLS they evaluated to NULL and matched nothing:
--   * application_workflow_history.tenant_isolation (057) read app.tenant_id: no tenant could read or write its own workflow history
--     (the single and the bulk workflow moves insert into it).
--   * plan_features_super_admin and plan_features_tenant_read (023) read app.role: the plan-feature table read empty for tenants and
--     super_admin alike.
-- Only the setting names change in the super_admin and tenant clauses; 057 and 023 are not edited. The tenant role list follows the roles the
-- application issues (routers/tenant.py ALLOWED_ROLES plus viewer); the old list named 'tenant_admin', which no code path issues.
-- Additive and idempotent: drops and recreates the named policies only.
BEGIN;
SET search_path = cv_analyzer;

DROP POLICY IF EXISTS tenant_isolation ON application_workflow_history;
CREATE POLICY tenant_isolation ON application_workflow_history
    USING (tenant_id::text = current_setting('app.current_tenant_id', true)
           OR current_setting('app.current_role', true) = 'super_admin')
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true)
                OR current_setting('app.current_role', true) = 'super_admin');

DROP POLICY IF EXISTS plan_features_super_admin ON plan_features;
CREATE POLICY plan_features_super_admin ON plan_features
    USING (current_setting('app.current_role', true) = 'super_admin');

DROP POLICY IF EXISTS plan_features_tenant_read ON plan_features;
CREATE POLICY plan_features_tenant_read ON plan_features
    FOR SELECT
    USING (current_setting('app.current_role', true) IN ('admin', 'hr_manager', 'recruiter', 'viewer'));

COMMIT;
