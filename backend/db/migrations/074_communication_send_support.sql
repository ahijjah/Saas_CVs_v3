-- Add email sending support to candidate_communications table
ALTER TABLE candidate_communications
ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Create index for sent status queries
CREATE INDEX IF NOT EXISTS idx_communication_status
    ON candidate_communications (status);
