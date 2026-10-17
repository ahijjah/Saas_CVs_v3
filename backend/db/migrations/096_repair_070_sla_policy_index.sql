-- 096_repair_070_sla_policy_index.sql
--
-- Replaces 070_sla_thresholds_config.sql. The original is superseded and is never executed.
--
-- Why: 070 created its index with an unparenthesised expression, which is a syntax error in CREATE INDEX:
--     USING GIN (policies->'sla_thresholds')
-- The whole file rolled back, so the SLA defaults were never back-filled either.
--
-- What this file does: back-fills the default SLA thresholds into tenant_workflow_policies.policies, then creates the GIN
-- index on the parenthesised expression. An existing index with the same name must have the expected definition.

SET search_path = cv_analyzer;

-- 1. Preconditions.
DO $$
BEGIN
    IF to_regclass('cv_analyzer.tenant_workflow_policies') IS NULL THEN
        RAISE EXCEPTION '096: tenant_workflow_policies must exist before this migration';
    END IF;
    IF (SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = 'cv_analyzer.tenant_workflow_policies'::regclass
          AND a.attname = 'policies' AND NOT a.attisdropped) IS DISTINCT FROM 'jsonb' THEN
        RAISE EXCEPTION '096: tenant_workflow_policies.policies must be jsonb';
    END IF;
END $$;

-- 2. Back-fill the default SLA thresholds. Rows that already carry sla_thresholds are left unchanged.
UPDATE cv_analyzer.tenant_workflow_policies
SET policies = jsonb_set(
    policies,
    '{sla_thresholds}',
    '{"review_days": 14, "interview_feedback_days": 7, "approval_days": 10, "offer_response_days": 5}'
)
WHERE (policies -> 'sla_thresholds') IS NULL;

-- 3. The GIN index. An existing index of that name must be a non-unique GIN index on tenant_workflow_policies.
DO $$
BEGIN
    IF to_regclass('cv_analyzer.idx_workflow_policies_sla') IS NULL THEN
        CREATE INDEX idx_workflow_policies_sla
            ON cv_analyzer.tenant_workflow_policies USING GIN ((policies -> 'sla_thresholds'));
    ELSIF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_am am ON am.oid = c.relam
        WHERE i.indexrelid = 'cv_analyzer.idx_workflow_policies_sla'::regclass
          AND i.indrelid = 'cv_analyzer.tenant_workflow_policies'::regclass
          AND am.amname = 'gin'
          AND NOT i.indisunique
          AND i.indnatts = 1
          AND i.indexprs IS NOT NULL
    ) THEN
        RAISE EXCEPTION '096: idx_workflow_policies_sla exists with a different definition';
    END IF;
END $$;

-- 4. Postcondition: every row carries the SLA thresholds.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cv_analyzer.tenant_workflow_policies WHERE (policies -> 'sla_thresholds') IS NULL) THEN
        RAISE EXCEPTION '096: rows without sla_thresholds remain after the back-fill';
    END IF;
END $$;
