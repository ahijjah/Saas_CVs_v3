-- Migration 093: Add Scoring V2 Phase 2A evidence columns to application_scores
--
-- Purpose:
--   Adds two nullable JSONB columns to cv_analyzer.application_scores to
--   persist the outputs of the Phase 2A evidence-based scoring pipeline:
--
--     cv_facts_json       Layer 1 output — a CVFacts object (serialised by
--                         evidence_serialiser.cvfacts_to_dict) containing
--                         structured CV evidence extracted before the LLM
--                         call: skills, experience, education, certifications,
--                         soft-skill signals, and domain signals.
--
--     match_results_json  Layer 2 output — a MatchResult object (serialised by
--                         evidence_serialiser.matchresult_to_dict) containing
--                         per-criterion match results, gap candidates, and the
--                         algorithmic score baseline that will bound LLM
--                         scoring deltas in Phase 3.
--
-- Constraints:
--   Both columns are nullable.  Existing rows remain valid; NULL means the
--   row was scored before Phase 2A was deployed.  No backfill is performed.
--
-- Indexes:
--   None added at this stage.  Both columns are NULL for all existing rows
--   and for new rows until Batch 2A-6 (worker integration) is deployed.
--   A partial index (WHERE col IS NOT NULL) will be evaluated once live data
--   exists and JSONB query patterns are confirmed.
--
-- Safe to re-run:
--   ADD COLUMN IF NOT EXISTS (PostgreSQL 9.6+) makes this migration
--   idempotent — running it a second time is a no-op.
--
-- Run:
--   psql -h localhost -U cv_app cv_analyzer_prod \
--       -c "SET search_path = cv_analyzer" \
--       < 093_scoring_v2_evidence_columns.sql

BEGIN;

SET search_path = cv_analyzer;

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS cv_facts_json      JSONB,  -- Layer 1: CVFacts  (evidence_serialiser.cvfacts_to_dict)
    ADD COLUMN IF NOT EXISTS match_results_json JSONB;  -- Layer 2: MatchResult (evidence_serialiser.matchresult_to_dict)

COMMENT ON COLUMN application_scores.cv_facts_json IS
    'Scoring V2 Phase 2A — Layer 1 CVFacts. '
    'Structured CV evidence (skills, experience, education, certifications, '
    'soft-skill signals, domain signals) extracted before the LLM call. '
    'Schema version stored in _schema key. NULL for pre-Phase-2A rows.';

COMMENT ON COLUMN application_scores.match_results_json IS
    'Scoring V2 Phase 2A — Layer 2 MatchResult. '
    'Per-criterion match results, gap candidates, and algorithmic score '
    'baseline computed from CVFacts and job criteria. '
    'Used in Phase 3 to bound LLM scoring deltas. '
    'Schema version stored in _schema key. NULL for pre-Phase-2A rows.';

COMMIT;
