-- ROLLBACK GUARD for the access pause. VERIFY ONLY: this script reads the database, raises an error when the state is not safe to roll back,
-- and changes NOTHING. It never clears access_paused. Paused work is the intent of the tenant's pause: clearing it would drop the work, and
-- rolling back the code would make an old watchdog fail it. See BILLING_ACTIVATION.md, "Rollback".
--
-- Run (on the live database as the operator, or on a restored copy for the drill):
--   psql -X -v ON_ERROR_STOP=1 -d <db> -f rollback_access_pause_guard.sql
--
-- Export the paused-work inventory BEFORE rolling back, with psql on the same database (a client-side copy, not part of this script):
--   \copy (SELECT application_id, job_id, tenant_id, queued_at FROM cv_analyzer.applications WHERE stopped_reason = 'access_paused') TO 'paused_applications.csv' CSV HEADER
SET search_path = cv_analyzer;

DO $$
DECLARE
    allowed text;
    paused_apps integer;
    paused_stale integer;
    bad_status integer;
BEGIN
    -- 1. the marker is still a legal value (migration 113 applied), so the paused rows remain valid
    SELECT pg_get_constraintdef(oid) INTO allowed FROM pg_constraint
     WHERE conrelid = 'cv_analyzer.applications'::regclass AND conname = 'applications_stopped_reason_check';
    IF allowed IS NULL OR position('access_paused' IN allowed) = 0 THEN
        RAISE EXCEPTION 'applications_stopped_reason_check does not allow access_paused: refusing; do not drop the constraint while paused rows exist';
    END IF;

    -- 2. a paused row must still be queued (a paused row that a worker moved is an inconsistency to resolve, not roll back over)
    SELECT count(*) INTO bad_status FROM applications WHERE stopped_reason = 'access_paused' AND processing_status <> 'queued';
    IF bad_status > 0 THEN
        RAISE EXCEPTION '% paused application(s) are not queued: resolve them before rolling back', bad_status;
    END IF;

    -- 3. report what the rollback will keep (informational)
    SELECT count(*) INTO paused_apps FROM applications WHERE stopped_reason = 'access_paused';
    SELECT count(*) INTO paused_stale FROM applications
     WHERE stopped_reason = 'access_paused' AND queued_at < now() - interval '15 minutes';   -- the watchdog's window (workers/watchdog.py _STUCK_TIMEOUT_MINUTES)
    RAISE NOTICE 'paused applications kept as they are: %; of which older than the 15-minute watchdog window (the OLD watchdog would fail them): %',
        paused_apps, paused_stale;
    RAISE NOTICE 'the old watchdog MUST be disabled for the whole rollback window, or it fails paused work';
END $$;
