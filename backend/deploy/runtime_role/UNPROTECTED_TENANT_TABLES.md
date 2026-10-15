# Tenant tables without row-level security: access paths and recommended policies

Status: RECOMMENDATION. No policy is applied by this document. Each table has a `tenant_id` column and no `ENABLE ROW LEVEL SECURITY` in
`db/schema.sql` or any migration (static scan; `inventory.sql` reports the same list live). Under the restricted runtime role these tables
are protected only by the application's own `WHERE tenant_id = ...` filters. A route that forgets the filter leaks across tenants.

| Table | Read / written by (routes and services) | Runtime grant today |
|---|---|---|
| ai_usage_log | routers/ai_usage.py; services/ai_usage_service.py; services/requirements_v2_extraction.py (worker) | INSERT, SELECT |
| candidate_approvals | routers/approvals.py; routers/applications.py; routers/analytics.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_comments | routers/comments.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_communications | routers/communication.py; services/communication_events.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_interview_feedback | routers/interviews.py; routers/applications.py; routers/analytics.py | INSERT, SELECT, UPDATE |
| candidate_interviews | routers/interviews.py; routers/applications.py; routers/analytics.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_message_templates | routers/communication.py; services/communication_events.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_saved_views | routers/saved_views.py | DELETE, INSERT, SELECT, UPDATE |
| candidate_tags | routers/candidate_tags.py; routers/applications.py; main.py | DELETE, INSERT, SELECT |
| communication_automation_rules | routers/communication.py; services/communication_events.py | DELETE, INSERT, SELECT, UPDATE |
| job_knockout_questions | routers/applications.py; services/knockout_*; services/screening_validation_service.py; services/bulk_upload_import_service.py | DELETE, INSERT, SELECT |
| tenant_feature_audit | services/feature_service.py (audit insert only) | INSERT |
| tenant_workflow_policies | routers/workflow_policies.py; routers/applications.py; routers/approvals.py; routers/analytics.py | INSERT, SELECT, UPDATE (per generated grants) |

## Recommendation

1. Enable RLS on each table with a **tenant-only** policy, in a new migration (applied migrations are not edited):

       ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
       CREATE POLICY <t>_tenant ON <t>
           USING (tenant_id::text = current_setting('app.current_tenant_id', true))
           WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', true));

   No `super_admin` branch: the proposal (PRIVILEGED_OPERATIONS_PROPOSAL.md, section 2.1) removes that branch for the request identity. Cross-tenant
   admin reads go through the admin identity, not through this policy.
2. Tables that are written from the background (ai_usage_log, tenant_feature_audit, candidate_communications, communication_automation_rules,
   job_knockout_questions) must be checked first: the worker sets the tenant context before each write (as cv_score and the requirements
   worker do). A write with no context is refused after the change, which is the intended failure, but it must be tested per path.
3. Before applying each table: add a test that a tenant-2 session cannot read or write tenant-1 rows, and a test that each route listed above
   passes under cv_runtime (tests/test_runtime_affected_paths.py is the pattern).
4. Order: start with candidate_* (interviews, communications, approvals, comments, saved views, tags), which the candidate UI reaches on every
   request; then the policy and workflow tables; ai_usage_log last, because the worker path writes it.

## Not decided here

- Whether a table has a platform-wide reader (analytics across tenants). If so, that reader moves to the admin identity; it is not a reason to keep
  the tenant branch open.
- The viewer role: today the candidate-movement routes admit any role (see tests/test_runtime_affected_paths.py, strict xfail). Policies do not fix
  that; a role check in the routes does.
