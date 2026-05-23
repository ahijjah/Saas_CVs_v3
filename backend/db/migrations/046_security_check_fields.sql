BEGIN;
SET search_path = cv_analyzer;

-- Add security check result columns to applications.
-- All nullable — NULL means the check has not run yet (pre-migration rows,
-- or applications where the feature was disabled at processing time).

ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS security_check_status    TEXT
        CHECK (security_check_status IN ('passed', 'warning', 'blocked')),
    ADD COLUMN IF NOT EXISTS security_risk_level      TEXT
        CHECK (security_risk_level IN ('low', 'medium', 'high')),
    ADD COLUMN IF NOT EXISTS security_risk_score      INTEGER,
    ADD COLUMN IF NOT EXISTS security_reason_codes    TEXT[],
    ADD COLUMN IF NOT EXISTS security_detected_patterns TEXT[],
    ADD COLUMN IF NOT EXISTS security_checked_at      TIMESTAMPTZ;

-- ── Platform config: Security / Prompt Injection Checks ──────────────────────
-- Valid categories: scoring|ai|email|queue|subscription|security|general

INSERT INTO system_config (key, value, type, category, description, editable)
VALUES
    ('security_prompt_injection_check_enabled',
     'true', 'boolean', 'security',
     'Enable prompt injection and jailbreak detection before AI scoring.', TRUE),

    ('security_prompt_injection_block_high_risk',
     'true', 'boolean', 'security',
     'Block (do not score) applications flagged as high risk.', TRUE),

    ('security_prompt_injection_medium_risk_action',
     'allow_with_warning', 'string', 'security',
     'Action for medium-risk detections: allow_with_warning or block_for_review.', TRUE),

    ('security_prompt_injection_fuzzy_threshold',
     '85', 'number', 'security',
     'Minimum RapidFuzz partial_ratio score (0-100) to count as a fuzzy pattern match.', TRUE),

    ('security_prompt_injection_high_risk_threshold',
     '70', 'number', 'security',
     'Cumulative risk score at or above which the result is HIGH risk.', TRUE),

    ('security_prompt_injection_medium_risk_threshold',
     '30', 'number', 'security',
     'Cumulative risk score at or above which the result is MEDIUM risk (below high threshold).', TRUE),

    ('security_prompt_injection_max_scan_chars',
     '50000', 'number', 'security',
     'Maximum number of characters to scan per application (prevents DoS on giant files).', TRUE),

    ('security_prompt_injection_patterns',
     '[]', 'json', 'security',
     'Optional JSON array of extra pattern strings to add to the built-in detection list.', TRUE)
ON CONFLICT (key) DO NOTHING;

COMMIT;
