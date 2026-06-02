-- Recruiter workspace: saved candidate filter views (user-scoped)
CREATE TABLE IF NOT EXISTS candidate_saved_views (
    saved_view_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID        NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    user_id       UUID        NOT NULL REFERENCES users(user_id)     ON DELETE CASCADE,
    name          TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    filters_json  JSONB       NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_candidate_saved_views_user
    ON candidate_saved_views (user_id, tenant_id);
