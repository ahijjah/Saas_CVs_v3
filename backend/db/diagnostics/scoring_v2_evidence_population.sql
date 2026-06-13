-- scoring_v2_evidence_population.sql
--
-- Diagnostic: report population of Scoring V2 Phase 2A evidence columns.
-- Use to verify that Batch 2A-6 (worker integration) is writing both
-- columns correctly and to track rollout progress.
--
-- Run as:
--   docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod \
--       -f /path/to/scoring_v2_evidence_population.sql
-- Or:
--   psql -h localhost -U cv_app cv_analyzer_prod \
--       -c "SET search_path = cv_analyzer" \
--       < scoring_v2_evidence_population.sql

SET search_path TO cv_analyzer;

-- ── Overall population counts ─────────────────────────────────────────────────
\echo '=== application_scores: evidence column population ==='
SELECT
    COUNT(*)                                                        AS total_scores,
    COUNT(*) FILTER (WHERE cv_facts_json IS NOT NULL)              AS with_cv_facts,
    COUNT(*) FILTER (WHERE match_results_json IS NOT NULL)         AS with_match_results,
    COUNT(*) FILTER (WHERE cv_facts_json IS NOT NULL
                       AND match_results_json IS NOT NULL)         AS with_both,
    COUNT(*) FILTER (WHERE cv_facts_json IS NULL
                       AND match_results_json IS NULL)             AS with_neither,
    ROUND(
        COUNT(*) FILTER (WHERE cv_facts_json IS NOT NULL)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                               AS cv_facts_pct,
    ROUND(
        COUNT(*) FILTER (WHERE match_results_json IS NOT NULL)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                               AS match_results_pct
FROM application_scores;

-- ── Extractor version distribution (populated rows only) ─────────────────────
\echo '=== cv_facts_json: extractor_version distribution ==='
SELECT
    cv_facts_json ->> 'extractor_version'   AS extractor_version,
    cv_facts_json ->> '_schema'             AS schema_version,
    COUNT(*)                                AS row_count
FROM application_scores
WHERE cv_facts_json IS NOT NULL
GROUP BY extractor_version, schema_version
ORDER BY row_count DESC;

-- ── Matcher version distribution (populated rows only) ───────────────────────
\echo '=== match_results_json: matcher_version distribution ==='
SELECT
    match_results_json ->> 'matcher_version'    AS matcher_version,
    match_results_json ->> '_schema'            AS schema_version,
    COUNT(*)                                    AS row_count
FROM application_scores
WHERE match_results_json IS NOT NULL
GROUP BY matcher_version, schema_version
ORDER BY row_count DESC;

-- ── Extraction method breakdown ───────────────────────────────────────────────
\echo '=== cv_facts_json: extraction_method distribution ==='
SELECT
    cv_facts_json ->> 'extraction_method'   AS extraction_method,
    COUNT(*)                                AS row_count
FROM application_scores
WHERE cv_facts_json IS NOT NULL
GROUP BY extraction_method
ORDER BY row_count DESC;

-- ── Language breakdown from cv_facts_json ────────────────────────────────────
\echo '=== cv_facts_json: language distribution ==='
SELECT
    cv_facts_json ->> 'language'    AS cv_language,
    COUNT(*)                        AS row_count
FROM application_scores
WHERE cv_facts_json IS NOT NULL
GROUP BY cv_language
ORDER BY row_count DESC;

-- ── Most recently populated rows ─────────────────────────────────────────────
\echo '=== 5 most recent rows with cv_facts_json populated ==='
SELECT
    application_id,
    created_at,
    cv_facts_json ->> 'extractor_version'   AS extractor_version,
    cv_facts_json ->> 'language'            AS cv_language,
    cv_facts_json ->> 'total_char_count'    AS char_count,
    jsonb_array_length(
        COALESCE(cv_facts_json -> 'skills', '[]'::jsonb)
    )                                       AS skill_count,
    cv_facts_json ->> '_schema'             AS schema_version
FROM application_scores
WHERE cv_facts_json IS NOT NULL
ORDER BY created_at DESC
LIMIT 5;
