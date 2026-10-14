-- 113: applications.stopped_reason gains 'access_paused'.
--
-- Scoring is PAUSED (never failed) while a tenant has no current access (suspended, expired, not activated, or without an end date). The
-- application stays 'queued' and is marked access_paused; resume (platform provisioning) clears the marker and re-dispatches it once.
-- See services/paused_work_service.py and services/entitlement_service.py. The list is migration 106's list plus 'access_paused'.
--
-- Rollback (only after the application no longer writes this value, and with no row carrying it):
--   UPDATE applications SET stopped_reason = NULL WHERE stopped_reason = 'access_paused';
--   then re-create applications_stopped_reason_check from migration 106's list (without 'access_paused').
BEGIN;

ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_stopped_reason_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_stopped_reason_check
    CHECK (stopped_reason IN (
        'security_blocked',
        'extraction_failed',
        'processing_error',
        'duplicate_blocked',
        'scoring_failed',
        'evaluation_unsupported',
        'other',
        'access_paused'
    ));

COMMIT;
