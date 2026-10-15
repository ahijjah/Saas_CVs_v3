-- READ-ONLY inventory: tenants whose subscription has no validity date, and the work each one holds back. Run it before deploying, and
-- review every row with the platform team. It changes nothing (SELECT only). Every row is fail-closed for writes, AI and intake until a
-- platform provisioning records a future expiry (see BILLING_ACTIVATION.md). It does not extend or reactivate anything.
--
-- Run (against a restored or read-only copy, never by editing the live tenant rows):
--   psql -X -v ON_ERROR_STOP=1 -d <copy> -f inventory_legacy_validity.sql
SET search_path = cv_analyzer;

SELECT t.tenant_id, t.name, t.subscription_status, t.plan, t.pending_plan,
       t.trial_start_at, t.trial_end_at, t.subscription_ends_at,
       (SELECT count(*) FROM jobs j WHERE j.tenant_id = t.tenant_id) AS jobs,
       (SELECT count(*) FROM applications a WHERE a.tenant_id = t.tenant_id AND a.processing_status = 'queued') AS queued_applications,
       (SELECT count(*) FROM applications a WHERE a.tenant_id = t.tenant_id AND a.stopped_reason = 'access_paused') AS paused_applications,
       (SELECT count(*) FROM job_criteria jc JOIN jobs j ON j.job_id = jc.job_id
         WHERE j.tenant_id = t.tenant_id AND jc.criteria_extraction_status = 'pending') AS pending_extractions
FROM tenants t
WHERE (t.subscription_status = 'active' AND t.subscription_ends_at IS NULL)
   OR (t.subscription_status = 'trial' AND t.trial_end_at IS NULL)
   OR t.subscription_status IS NULL
ORDER BY t.name;
