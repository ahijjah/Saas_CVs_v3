-- Communication automation rules: tenant-level rules that trigger draft/send on workflow events
CREATE TABLE IF NOT EXISTS communication_automation_rules (
    rule_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    event_type      VARCHAR(50) NOT NULL,
    category        VARCHAR(30) NOT NULL DEFAULT 'workflow',
    template_id     UUID REFERENCES candidate_message_templates(template_id) ON DELETE SET NULL,
    mode            VARCHAR(20) NOT NULL DEFAULT 'draft_only',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    delay_minutes   INTEGER NOT NULL DEFAULT 0,
    created_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_automation_mode CHECK (mode IN ('disabled', 'draft_only', 'auto_send')),
    CONSTRAINT uq_automation_event_per_tenant UNIQUE (tenant_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_automation_rules_tenant_active
    ON communication_automation_rules (tenant_id, is_active);

-- Add automation tracking columns to candidate_communications
ALTER TABLE candidate_communications
    ADD COLUMN IF NOT EXISTS event_type         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS automation_rule_id UUID REFERENCES communication_automation_rules(rule_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS is_automated       BOOLEAN NOT NULL DEFAULT FALSE;

-- Index for deduplication check (find existing automated records by application + event)
CREATE INDEX IF NOT EXISTS idx_comm_automated_event
    ON candidate_communications (application_id, event_type)
    WHERE event_type IS NOT NULL AND is_automated = TRUE;
