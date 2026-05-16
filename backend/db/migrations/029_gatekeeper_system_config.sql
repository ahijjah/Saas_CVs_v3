-- Migration 029: Gatekeeper system configuration
-- Ensures all Level 1 Gatekeeper parameters are present in system_config
-- with correct type, category, and description metadata so the Super Admin
-- UI can surface and update them.

BEGIN;
SET search_path = cv_analyzer;

-- Canonical gatekeeper parameter names (with gatekeeper_ prefix for clarity).
-- ON CONFLICT DO NOTHING is safe: re-running this migration is idempotent.
INSERT INTO system_config (key, value, type, category, description, editable) VALUES
    (
        'gatekeeper_skill_fuzzy_threshold',
        '80',
        'number',
        'scoring',
        'rapidfuzz partial_ratio threshold (0–100) for bilingual skill matching in the Level 1 gatekeeper. Lower values = more lenient matching. Alias of legacy key skill_fuzzy_threshold.',
        true
    ),
    (
        'gatekeeper_min_skill_ratio',
        '0',
        'number',
        'scoring',
        'Minimum percentage of required skills (0–100) that must be found in the CV for it to pass the Level 1 gatekeeper. Set to 0 (default) to gate on semantic similarity only.',
        true
    )
ON CONFLICT (key) DO NOTHING;

-- Ensure the existing semantic threshold row has correct type/category/description.
UPDATE system_config
SET
    type        = 'number',
    category    = 'scoring',
    description = 'Minimum semantic similarity score (0.0–1.0) required to pass the Level 1 gatekeeper. CVs below this threshold are rejected locally without an AI call (cost saving).'
WHERE key = 'gatekeeper_semantic_threshold';

-- Ensure the enable/disable flag is in scoring category with a clear description.
UPDATE system_config
SET
    category    = 'scoring',
    description = 'Enable or disable the Level 1 local gatekeeper. When false, all CVs proceed to AI scoring regardless of similarity (higher cost, higher recall).'
WHERE key = 'level1_gatekeeper_enabled';

COMMIT;
