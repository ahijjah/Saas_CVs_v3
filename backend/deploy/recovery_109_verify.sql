-- Verification after migration 109. READ-ONLY. Fails (error) when the switch is on, the table or triggers are missing, or the application role
-- can change the history. Reports (WARNING) when the application role is a superuser: that role bypasses privileges and triggers, so the
-- append-only guarantee is then administrative, not enforced against that role, and recovery must not be enabled until the role model is decided
-- (see the runbook).
-- Run:  psql -v ON_ERROR_STOP=1 -U <owner> -d cv_analyzer_prod -f deploy/recovery_109_verify.sql
SET search_path = cv_analyzer;

DO $$
DECLARE
    tbl constant text := 'cv_analyzer.requirements_analysis_log';
    switch_value text;
    trig_count integer;
    is_super boolean;
BEGIN
    IF to_regclass(tbl) IS NULL THEN
        RAISE EXCEPTION 'requirements_analysis_log is missing: migration 109 is not applied';
    END IF;
    SELECT count(*) INTO trig_count FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.oid = to_regclass(tbl) AND NOT t.tgisinternal AND t.tgname IN ('requirements_analysis_log_no_change', 'requirements_analysis_log_no_truncate');
    IF trig_count <> 2 THEN
        RAISE EXCEPTION 'the immutability triggers are missing (found %, expected 2)', trig_count;
    END IF;
    SELECT value INTO switch_value FROM system_config WHERE key = 'requirements_v2.recovery_enabled';
    IF switch_value IS DISTINCT FROM 'false' THEN
        RAISE EXCEPTION 'requirements_v2.recovery_enabled is % (must be false at deployment)', coalesce(switch_value, 'missing');
    END IF;
    IF NOT has_table_privilege('cv_app', tbl, 'SELECT') OR NOT has_table_privilege('cv_app', tbl, 'INSERT') THEN
        RAISE EXCEPTION 'cv_app cannot read and append the history';
    END IF;
    IF has_table_privilege('cv_app', tbl, 'UPDATE') OR has_table_privilege('cv_app', tbl, 'DELETE') OR has_table_privilege('cv_app', tbl, 'TRUNCATE') THEN
        RAISE EXCEPTION 'cv_app can update, delete or truncate the history';
    END IF;
    SELECT rolsuper INTO is_super FROM pg_roles WHERE rolname = 'cv_app';
    IF is_super THEN
        RAISE WARNING 'cv_app is a SUPERUSER: it bypasses privileges and row-level security, and it can disable the immutability triggers (ALTER TABLE ... DISABLE TRIGGER, or SET session_replication_role). Do not enable re-analysis until the role model is decided.';
    END IF;
    RAISE NOTICE 'verification ok: table, 2 triggers, switch false, cv_app read+append only (history rows: %)',
        (SELECT count(*) FROM requirements_analysis_log);
END $$;
