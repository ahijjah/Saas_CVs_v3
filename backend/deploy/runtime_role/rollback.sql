-- Rollback of the runtime role (cv_runtime). Run ONLY after the application's DATABASE_URL points back to cv_app and no connection uses cv_runtime.
-- It revokes exactly what grants.sql granted, then drops the role. It does not touch cv_app, the owners, the policies, the settings or the history.
-- Run:  psql -v ON_ERROR_STOP=1 -U <admin> -d cv_analyzer_prod -f rollback.sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_stat_activity WHERE usename = 'cv_runtime') THEN
        RAISE EXCEPTION 'cv_runtime still has open connections: point the application back to cv_app and wait for them to close';
    END IF;
END $$;
SET search_path = cv_analyzer;
REVOKE ALL ON ALL TABLES IN SCHEMA cv_analyzer FROM cv_runtime;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA cv_analyzer FROM cv_runtime;
REVOKE USAGE ON SCHEMA cv_analyzer FROM cv_runtime;
DROP ROLE IF EXISTS cv_runtime;
