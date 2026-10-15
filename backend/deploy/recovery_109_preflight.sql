-- Preflight for migration 109 (re-analysis history). READ-ONLY: it changes nothing and stops with an error when a prerequisite is missing.
-- Run before 109:  psql -v ON_ERROR_STOP=1 -U <owner> -d cv_analyzer_prod -f deploy/recovery_109_preflight.sql
SET search_path = cv_analyzer;

DO $$
DECLARE
    missing text[] := ARRAY[]::text[];
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'cv_analyzer' AND table_name = 'job_criteria' AND column_name = 'requirements_schema_version') THEN
        missing := missing || 'migration 106 (job_criteria.requirements_schema_version)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'cv_analyzer' AND table_name = 'job_criteria' AND column_name = 'requirements_revision') THEN
        missing := missing || 'migration 107 (job_criteria.requirements_revision)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'cv_analyzer' AND table_name = 'job_criteria' AND column_name = 'requirements_extraction_token') THEN
        missing := missing || 'migration 108 (job_criteria.requirements_extraction_token)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM system_config WHERE key = 'requirements_v2.enabled') THEN
        missing := missing || 'migration 108 (system_config requirements_v2.enabled)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ai_prompts WHERE prompt_code = 'criteria_extraction_v2' AND version = 3) THEN
        missing := missing || 'migration 108 (ai_prompts criteria_extraction_v2 version 3)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cv_app') THEN
        missing := missing || 'application role cv_app';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'cv_analyzer' AND tablename = 'requirements_analysis_log') THEN
        RAISE EXCEPTION 'migration 109 is already applied (requirements_analysis_log exists); nothing to do';
    END IF;
    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION 'prerequisites missing, 109 not applied: %', array_to_string(missing, '; ');
    END IF;
    RAISE NOTICE 'preflight ok: 106, 107 and 108 are in place; 109 is not applied';
END $$;

-- Live values, as they are on the server now. Seed defaults do not overwrite them: 108 and 109 insert with ON CONFLICT DO NOTHING.
SELECT key, value FROM system_config WHERE key IN ('requirements_v2.enabled', 'requirements_v2.recovery_enabled', 'job_analysis.require_classification_acknowledgment') ORDER BY key;
SELECT prompt_code, version, is_active FROM ai_prompts WHERE prompt_code = 'criteria_extraction_v2' ORDER BY version;
SELECT (SELECT count(*) FROM jobs) AS jobs, (SELECT count(*) FROM job_criteria) AS job_criteria, (SELECT count(*) FROM audit_logs) AS audit_logs;
SELECT rolname, rolsuper, rolcreaterole FROM pg_roles WHERE rolname IN ('cv_app', current_user) ORDER BY rolname;
