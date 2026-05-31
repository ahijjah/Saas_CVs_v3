-- 069_tenant_workflow_policies.sql
-- Per-tenant configurable recruitment pipeline governance policies

CREATE TABLE IF NOT EXISTS tenant_workflow_policies (
    policy_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL UNIQUE REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    policies    JSONB NOT NULL DEFAULT '{
        "require_interview_before_offer": false,
        "require_approval_before_hire": false,
        "require_rejection_reason": false,
        "allow_bulk_reject": true,
        "require_interview_feedback": false,
        "required_approval_stages": []
    }',
    updated_by  UUID REFERENCES users(user_id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflow_policies_tenant
    ON tenant_workflow_policies(tenant_id);
