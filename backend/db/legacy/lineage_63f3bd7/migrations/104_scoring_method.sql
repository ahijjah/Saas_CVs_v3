-- Migration 104: P0-01 — Explicit scoring method + scoring_failed stop reason
--
-- 1. application_scores.scoring_method records which methodology produced the
--    stored score, so candidates scored by different methods are never
--    silently compared.
--
--    Values (validated by pattern, so future versions such as
--    'deterministic_v2' need no schema change):
--      deterministic_v1            D-01 LLM criteria mapping + F-01 deterministic engine
--      legacy_llm_v1               single-call LLM scorer (cv_scoring prompt)
--      gatekeeper_local_v1         Level-1 local gatekeeper rejection (no AI)
--      legacy_llm_det_backfill_v1  legacy-scored row that later received a
--                                  det_final_score from
--                                  scripts/backfill_deterministic_scores.py.
--                                  final_score, decision and narrative are
--                                  legacy; det_final_score (shown in lists via
--                                  COALESCE) is deterministic.
--      NULL                        evidence insufficient to classify
--
--    'deterministic_v1' names the methodology. The engine's own
--    det_score_json._schema ('det_score_v2') is a payload-schema version that
--    was bumped on 2026-06-13 when summary fields were added, not a
--    methodology change, so it is not reused here.
--
-- 2. applications.stopped_reason gains 'scoring_failed': deterministic scoring
--    was enabled but D-01/F-01 failed technically after all retries.
--    processing_status stays 'failed' (existing stopped/failed UI and counts).
--
-- Idempotent: safe to re-run. The backfill only touches rows where
-- scoring_method IS NULL. No scores, decisions or other columns are changed.

BEGIN;
SET search_path = cv_analyzer;

-- ── 1. Column + format check ─────────────────────────────────────────────────

ALTER TABLE application_scores
    ADD COLUMN IF NOT EXISTS scoring_method VARCHAR(50);

ALTER TABLE application_scores
    DROP CONSTRAINT IF EXISTS application_scores_scoring_method_check;

ALTER TABLE application_scores
    ADD CONSTRAINT application_scores_scoring_method_check
    CHECK (scoring_method IS NULL OR scoring_method ~ '^[a-z][a-z0-9_]*_v[0-9]+$');

COMMENT ON COLUMN application_scores.scoring_method IS
    'P0-01: methodology that produced this score. deterministic_v1 | legacy_llm_v1 | '
    'gatekeeper_local_v1 | legacy_llm_det_backfill_v1 | NULL (unclassifiable history).';

-- ── 2. Backfill existing rows (strongest evidence first) ─────────────────────

UPDATE application_scores
SET scoring_method = CASE
        WHEN scoring_provider = 'deterministic' AND det_final_score IS NOT NULL
            THEN 'deterministic_v1'
        WHEN scoring_provider = 'local'
            THEN 'gatekeeper_local_v1'
        WHEN scoring_provider NOT IN ('deterministic', 'local')
             AND det_final_score IS NOT NULL
            THEN 'legacy_llm_det_backfill_v1'
        WHEN scoring_provider NOT IN ('deterministic', 'local')
             AND det_final_score IS NULL
             AND final_score IS NOT NULL
            THEN 'legacy_llm_v1'
        ELSE NULL
    END
WHERE scoring_method IS NULL;

-- ── 3. stopped_reason: add scoring_failed ─────────────────────────────────────

ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_stopped_reason_check;

ALTER TABLE applications
    ADD CONSTRAINT applications_stopped_reason_check
    CHECK (stopped_reason IN (
        'security_blocked',
        'extraction_failed',
        'processing_error',
        'duplicate_blocked',
        'scoring_failed',
        'other'
    ));

COMMIT;
