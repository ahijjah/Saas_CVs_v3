-- Migration 065: Internal Comments & Mentions
-- Collaborative recruiter discussion threads per candidate application.
-- Separate from recruiter_notes (private notes) — comments are team-visible.

CREATE TABLE IF NOT EXISTS candidate_comments (
    comment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id UUID NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    tenant_id      UUID NOT NULL,
    user_id        UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    comment_text   TEXT NOT NULL CHECK (char_length(comment_text) >= 1 AND char_length(comment_text) <= 2000),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast retrieval of all comments for a given application in chronological order
CREATE INDEX IF NOT EXISTS idx_candidate_comments_app_id
    ON candidate_comments(application_id, created_at ASC);

-- Tenant isolation index
CREATE INDEX IF NOT EXISTS idx_candidate_comments_tenant
    ON candidate_comments(tenant_id);
