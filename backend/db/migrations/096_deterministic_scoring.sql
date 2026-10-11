-- Migration 096: F-01 — Deterministic Scoring Engine (Silent Mode)
--
-- Adds two new nullable columns to application_scores for the deterministic
-- score that runs beside the LLM score.  Neither column affects existing
-- final_score, decision, or recruiter-facing behavior — they are silent
-- audit/calibration outputs only.
--
-- Also seeds 6 platform-level configuration keys in system_config.
-- All keys default to the conservative values hard-coded in
-- DeterministicScoringConfig and can be tuned live without a code deploy.
--
-- Idempotent:
--   ALTER TABLE … ADD COLUMN IF NOT EXISTS
--   INSERT … ON CONFLICT (key) DO NOTHING
--
-- Apply:
--   psql -h localhost -U cv_app cv_analyzer_prod \
--       -c "SET search_path = cv_analyzer" \
--       < 096_deterministic_scoring.sql
-- Or via Docker:
--   docker compose exec -T postgres psql -U cv_app cv_analyzer_prod \
--       -f /migrations/096_deterministic_scoring.sql

BEGIN;

SET search_path = cv_analyzer;

-- ── New columns on application_scores ────────────────────────────────────────

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS det_final_score INTEGER NULL;

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS det_score_json JSONB NULL;

COMMENT ON COLUMN application_scores.det_final_score IS
    'F-01 deterministic score (0–100). Silent-mode calibration only — '
    'does NOT affect final_score or decision. NULL when det engine disabled or not yet run.';

COMMENT ON COLUMN application_scores.det_score_json IS
    'F-01 deterministic scoring explainability payload (_schema: det_score_v1). '
    'Contains per-dimension breakdown, per-criterion credits, and overqualification flags. '
    'Silent-mode calibration only.';

-- ── system_config seeds for F-01 tunable parameters ──────────────────────────

INSERT INTO system_config (key, value, description) VALUES
    (
        'scoring_v2.det_partial_credit',
        '0.50',
        'F-01: Credit fraction awarded for PARTIAL criterion match (0.0–1.0). '
        'Default 0.50 means a partial match contributes half the credit of a full match.'
    ),
    (
        'scoring_v2.det_required_weight',
        '0.70',
        'F-01: Weight applied to the required-criteria average when computing '
        'the dimension score. Must sum to 1.0 with det_preferred_weight. Default 0.70.'
    ),
    (
        'scoring_v2.det_preferred_weight',
        '0.30',
        'F-01: Weight applied to the preferred-criteria average when computing '
        'the dimension score. Must sum to 1.0 with det_required_weight. Default 0.30.'
    ),
    (
        'scoring_v2.det_enable_required_absent_floor',
        'false',
        'F-01: When true, caps a dimension score at det_required_absent_floor_cap '
        'if more than det_required_absent_floor_threshold fraction of required '
        'criteria are ABSENT. Disabled by default (false).'
    ),
    (
        'scoring_v2.det_required_absent_floor_threshold',
        '0.50',
        'F-01: Fraction threshold (0.0–1.0) of ABSENT required criteria that '
        'triggers the required-absent floor cap. Only active when '
        'det_enable_required_absent_floor = true. Default 0.50.'
    ),
    (
        'scoring_v2.det_required_absent_floor_cap',
        '0.40',
        'F-01: Maximum dimension score (0.0–1.0) when the required-absent floor '
        'is triggered. Only active when det_enable_required_absent_floor = true. '
        'Default 0.40.'
    )
ON CONFLICT (key) DO NOTHING;

COMMIT;
