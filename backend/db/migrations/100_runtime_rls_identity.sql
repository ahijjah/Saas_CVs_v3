-- 100_runtime_rls_identity.sql
--
-- Row-level security decides cross-tenant access by the PostgreSQL role of the session, never by a variable the application sets.
--
--   * Tenant access: every session sees rows whose tenant matches app.current_tenant_id (cv_app). A session with no tenant context
--     matches nothing, so a missing context fails closed.
--   * Platform administration: cv_admin sees all tenants on the tables it administers. Identity is current_user, which a session
--     cannot change (no runtime role may SET ROLE, and none is a member of another role).
--   * Workers: cv_worker has named, table-specific policies that grant cross-tenant access. The verbs it may use on each table are
--     limited by the operator grant script (db/ops/0005), not by these policies.
--   * app.current_role is no longer consulted by any policy. Setting it grants nothing.
--
-- Migrations run as the migrator role, which owns these tables.

BEGIN;

-- 1. Remove the role-variable bypass from every existing policy. Each rewritten clause must be exact; a clause that is not rewritten
--    stops the migration, so no policy keeps trusting the role variable.
DO $$
DECLARE
    r        record;
    v_qual   text;
    v_check  text;
    v_roles  text;
    v_sql    text;
BEGIN
    FOR r IN
        SELECT p.policyname, p.tablename, p.permissive, p.roles, p.cmd, p.qual, p.with_check
        FROM pg_policies p
        WHERE p.schemaname = 'cv_analyzer'
          AND (coalesce(p.qual, '') LIKE '%app.current_role%' OR coalesce(p.with_check, '') LIKE '%app.current_role%')
    LOOP
        v_qual := regexp_replace(coalesce(r.qual, ''),
                                 'current_setting\(''app\.current_role''::text, true\) = ''super_admin''::text',
                                 'current_user = ''cv_admin''::name', 'g');
        v_check := regexp_replace(coalesce(r.with_check, ''),
                                  'current_setting\(''app\.current_role''::text, true\) = ''super_admin''::text',
                                  'current_user = ''cv_admin''::name', 'g');
        IF v_qual LIKE '%app.current_role%' OR v_check LIKE '%app.current_role%' THEN
            RAISE EXCEPTION 'policy %.% still reads app.current_role after rewrite; migration stopped',
                r.tablename, r.policyname;
        END IF;

        SELECT string_agg(CASE WHEN x = 'public' THEN 'PUBLIC' ELSE quote_ident(x) END, ', ') INTO v_roles
        FROM unnest(r.roles) AS x;
        v_sql := format('DROP POLICY %I ON cv_analyzer.%I', r.policyname, r.tablename);
        EXECUTE v_sql;
        v_sql := format('CREATE POLICY %I ON cv_analyzer.%I AS %s FOR %s TO %s USING (%s)%s',
                        r.policyname, r.tablename,
                        r.permissive,
                        r.cmd, v_roles, v_qual,
                        CASE WHEN r.with_check IS NULL THEN '' ELSE format(' WITH CHECK (%s)', v_check) END);
        EXECUTE v_sql;
    END LOOP;
END
$$;

-- 2. Tenant tables that had no row-level security. Enabling it with a tenant policy makes the table fail closed.
--    Direct tenant column:
ALTER TABLE cv_analyzer.ai_usage_log                ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_approvals         ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_comments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_communications    ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_interview_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_interviews        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_message_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_saved_views       ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.candidate_tags              ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.communication_automation_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.job_knockout_questions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.tenant_workflow_policies    ENABLE ROW LEVEL SECURITY;
--    Tenant reached through the application:
ALTER TABLE cv_analyzer.application_candidate_tags  ENABLE ROW LEVEL SECURITY;
ALTER TABLE cv_analyzer.application_knockout_answers ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_scope_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_approvals ON cv_analyzer.candidate_approvals FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_comments ON cv_analyzer.candidate_comments FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_communications ON cv_analyzer.candidate_communications FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_interview_feedback ON cv_analyzer.candidate_interview_feedback FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_interviews ON cv_analyzer.candidate_interviews FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_message_templates ON cv_analyzer.candidate_message_templates FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_saved_views ON cv_analyzer.candidate_saved_views FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_candidate_tags ON cv_analyzer.candidate_tags FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_communication_automation_rules ON cv_analyzer.communication_automation_rules FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_job_knockout_questions ON cv_analyzer.job_knockout_questions FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));
CREATE POLICY tenant_scope_tenant_workflow_policies ON cv_analyzer.tenant_workflow_policies FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true))
    WITH CHECK (current_user = 'cv_admin' OR tenant_id::text = current_setting('app.current_tenant_id', true));

CREATE POLICY tenant_scope_application_candidate_tags ON cv_analyzer.application_candidate_tags FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR application_id IN (
        SELECT a.application_id FROM cv_analyzer.applications a
        WHERE a.tenant_id::text = current_setting('app.current_tenant_id', true)))
    WITH CHECK (current_user = 'cv_admin' OR application_id IN (
        SELECT a.application_id FROM cv_analyzer.applications a
        WHERE a.tenant_id::text = current_setting('app.current_tenant_id', true)));
CREATE POLICY tenant_scope_application_knockout_answers ON cv_analyzer.application_knockout_answers FOR ALL TO PUBLIC
    USING (current_user = 'cv_admin' OR application_id IN (
        SELECT a.application_id FROM cv_analyzer.applications a
        WHERE a.tenant_id::text = current_setting('app.current_tenant_id', true)))
    WITH CHECK (current_user = 'cv_admin' OR application_id IN (
        SELECT a.application_id FROM cv_analyzer.applications a
        WHERE a.tenant_id::text = current_setting('app.current_tenant_id', true)));

-- 3. Workers: one named, table-specific policy per table the worker writes or reads across tenants. Verbs are limited by the grant
--    script. A worker session has no tenant context, so these are the only grants of cross-tenant access it has.
CREATE POLICY cv_worker_access_ai_usage_log ON cv_analyzer.ai_usage_log FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_files ON cv_analyzer.application_files FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_intake_log ON cv_analyzer.application_intake_log FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_knockout_answer_suggestions ON cv_analyzer.application_knockout_answer_suggestions FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_knockout_answers ON cv_analyzer.application_knockout_answers FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_score_comparisons ON cv_analyzer.application_score_comparisons FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_application_scores ON cv_analyzer.application_scores FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_applications ON cv_analyzer.applications FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_audit_logs ON cv_analyzer.audit_logs FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_duplicate_application_logs ON cv_analyzer.duplicate_application_logs FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_email_ingest_log ON cv_analyzer.email_ingest_log FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_job_campaigns ON cv_analyzer.job_campaigns FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_job_criteria ON cv_analyzer.job_criteria FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_job_knockout_questions ON cv_analyzer.job_knockout_questions FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_jobs ON cv_analyzer.jobs FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_notification_log ON cv_analyzer.notification_log FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_tenant_usage_monthly ON cv_analyzer.tenant_usage_monthly FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_tenants ON cv_analyzer.tenants FOR ALL TO cv_worker USING (true) WITH CHECK (true);
CREATE POLICY cv_worker_access_users ON cv_analyzer.users FOR ALL TO cv_worker USING (true) WITH CHECK (true);

COMMIT;
