-- Communication message templates per tenant
CREATE TABLE IF NOT EXISTS candidate_message_templates (
    template_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'general',
    subject       TEXT NOT NULL,
    body          TEXT NOT NULL,
    language      TEXT NOT NULL DEFAULT 'en',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_template_tenant_name
    ON candidate_message_templates (tenant_id, lower(name));
CREATE INDEX IF NOT EXISTS idx_template_tenant
    ON candidate_message_templates (tenant_id);
CREATE INDEX IF NOT EXISTS idx_template_tenant_category
    ON candidate_message_templates (tenant_id, category);

-- Communication history per candidate (no email sending — log/draft only)
CREATE TABLE IF NOT EXISTS candidate_communications (
    communication_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    application_id   UUID NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    candidate_email  TEXT,
    candidate_name   TEXT,
    channel          TEXT NOT NULL DEFAULT 'email',
    direction        TEXT NOT NULL DEFAULT 'outbound',
    subject          TEXT,
    body             TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',
    template_id      UUID REFERENCES candidate_message_templates(template_id) ON DELETE SET NULL,
    created_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_communication_application
    ON candidate_communications (application_id);
CREATE INDEX IF NOT EXISTS idx_communication_tenant
    ON candidate_communications (tenant_id);
CREATE INDEX IF NOT EXISTS idx_communication_tenant_created
    ON candidate_communications (tenant_id, created_at DESC);
