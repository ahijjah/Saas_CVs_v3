-- Migrate existing jobs to use job_code-based platform_email
-- Old format: {job_id}@{email_domain}  (UUID)
-- New format: {job_code}@{email_domain} (human-readable)
UPDATE cv_analyzer.jobs j
SET platform_email = j.job_code || '@' || t.email_domain
FROM cv_analyzer.tenants t
WHERE j.tenant_id = t.tenant_id
  AND j.job_code IS NOT NULL;
