-- 068_candidate_approvals.sql
-- Multi-step approval workflow for candidates

CREATE TABLE IF NOT EXISTS candidate_approvals (
    approval_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id  UUID NOT NULL REFERENCES applications(application_id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    approval_stage  VARCHAR(100) NOT NULL,
    stage_order     SMALLINT NOT NULL DEFAULT 0,
    approver_id     UUID REFERENCES users(user_id) ON DELETE SET NULL,
    decision        VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending','approved','rejected','needs_revision')),
    comment         TEXT,
    decided_at      TIMESTAMPTZ,
    requested_by    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_approvals_application
    ON candidate_approvals(application_id, stage_order ASC);
CREATE INDEX IF NOT EXISTS idx_approvals_approver
    ON candidate_approvals(tenant_id, approver_id)
    WHERE approver_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_approvals_pending
    ON candidate_approvals(tenant_id, approver_id, decision)
    WHERE decision = 'pending';
