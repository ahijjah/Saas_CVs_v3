-- Migration 002: Intelligence Upgrade
-- Adds Gatekeeper + bilingual analysis columns to existing tables.
-- Safe to run on a live database — all changes are additive (ADD COLUMN IF NOT EXISTS).
-- Run: psql -h localhost -U cv_app cv_analyzer_prod < migrations/002_intelligence_upgrade.sql

BEGIN;

SET search_path = cv_analyzer;

-- ── application_scores: new intelligence columns ─────────────────────────────

-- Local Gatekeeper results (computed on-device, no API cost)
ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS local_similarity_score  NUMERIC(5,2),   -- 0.00-100.00
    ADD COLUMN IF NOT EXISTS skill_match_ratio        NUMERIC(5,2),   -- 0.00-100.00
    ADD COLUMN IF NOT EXISTS matched_skills           TEXT[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS missing_skills           TEXT[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS cv_language              VARCHAR(10),    -- 'ar'|'en'|'mixed'
    ADD COLUMN IF NOT EXISTS gatekeeper_passed        BOOLEAN NOT NULL DEFAULT TRUE;

-- Bilingual AI output fields
ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS red_flags                TEXT[]  NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS reasoning                JSONB,          -- per-dimension reasoning text
    ADD COLUMN IF NOT EXISTS analysis_metadata        JSONB;          -- extensible bag: future fields

-- ── applications: gatekeeper short-circuit status ────────────────────────────
-- When gatekeeper_passed = FALSE the application is marked 'low_match'
-- without ever calling the LLM, saving API cost.
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS gatekeeper_passed        BOOLEAN;

-- ── system_config: new Gatekeeper default settings ───────────────────────────

INSERT INTO system_config (key, value, description) VALUES
    ('gatekeeper_semantic_threshold', '0.40',
        'Minimum semantic similarity (0-1) required to proceed to LLM scoring'),
    ('gatekeeper_enabled',            'true',
        'Master switch: set to false to disable Gatekeeper and always call LLM'),
    ('output_language',               'ar',
        'Language for AI-generated human-readable output (ar|en)'),
    ('embedding_model',               'paraphrase-multilingual-MiniLM-L12-v2',
        'Sentence-transformer model used for local semantic similarity'),
    ('skill_fuzzy_threshold',         '80',
        'rapidfuzz partial_ratio threshold (0-100) for skill matching')
ON CONFLICT (key) DO NOTHING;

-- ── applications: add 'low_match' to processing_status ───────────────────────
-- Extends the existing CHECK constraint to include the new status value.
-- PostgreSQL requires dropping and re-adding the constraint.
ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_processing_status_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_processing_status_check
    CHECK (processing_status IN ('pending', 'processing', 'scored', 'failed', 'low_match'));

-- ── decision: add 'low_match' as a possible decision ─────────────────────────
ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_decision_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_decision_check
    CHECK (decision IN ('qualified', 'partial', 'rejected', 'low_match'));

COMMIT;
