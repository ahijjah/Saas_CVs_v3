-- Add preferred contact email as canonical outreach address for candidates
ALTER TABLE applications
  ADD COLUMN IF NOT EXISTS preferred_contact_email      VARCHAR(255),
  ADD COLUMN IF NOT EXISTS preferred_contact_source     VARCHAR(30),
  ADD COLUMN IF NOT EXISTS preferred_contact_confidence NUMERIC(3,2),
  ADD COLUMN IF NOT EXISTS preferred_contact_locked     BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: set preferred from existing email fields, priority: intake form → AI extracted → IMAP sender
-- Do not overwrite locked rows (none exist yet, but respects future runs)
UPDATE applications
SET
  preferred_contact_email = CASE
    WHEN candidate_email IS NOT NULL AND candidate_email != '' THEN candidate_email
    WHEN candidate_email_from_cv IS NOT NULL AND candidate_email_from_cv != '' THEN candidate_email_from_cv
    WHEN email_sender_address IS NOT NULL AND email_sender_address != '' THEN email_sender_address
    ELSE NULL
  END,
  preferred_contact_source = CASE
    WHEN candidate_email IS NOT NULL AND candidate_email != '' THEN submission_source
    WHEN candidate_email_from_cv IS NOT NULL AND candidate_email_from_cv != '' THEN 'cv_extracted'
    WHEN email_sender_address IS NOT NULL AND email_sender_address != '' THEN 'email_sender'
    ELSE NULL
  END
WHERE preferred_contact_locked = FALSE;

CREATE INDEX IF NOT EXISTS idx_applications_preferred_contact_email
  ON applications (preferred_contact_email)
  WHERE preferred_contact_email IS NOT NULL;
