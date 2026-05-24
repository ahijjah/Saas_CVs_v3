BEGIN;
SET search_path = cv_analyzer;

-- Classify why an application stopped before AI scoring.
-- NULL = unknown (all historical rows). No backfill.
-- duplicate_blocked and knockout_failed are reserved for future phases.
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS stopped_reason VARCHAR(50)
    CHECK (stopped_reason IN (
        'security_blocked',
        'extraction_failed',
        'processing_error',
        'other'
    ));

COMMIT;
