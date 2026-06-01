-- 070_sla_thresholds_config.sql
-- Add SLA threshold configuration to tenant workflow policies
-- SLA thresholds are stored in policies JSONB under 'sla_thresholds' key
-- Default values provided for backward compatibility

-- Backfill existing policies with default SLA thresholds
UPDATE tenant_workflow_policies
SET policies = jsonb_set(
  policies,
  '{sla_thresholds}',
  '{"review_days": 14, "interview_feedback_days": 7, "approval_days": 10, "offer_response_days": 5}'
)
WHERE (policies->'sla_thresholds') IS NULL;

-- Create index for fast SLA threshold lookup (optional optimization)
CREATE INDEX IF NOT EXISTS idx_workflow_policies_sla
  ON tenant_workflow_policies USING GIN ((policies->'sla_thresholds'));
