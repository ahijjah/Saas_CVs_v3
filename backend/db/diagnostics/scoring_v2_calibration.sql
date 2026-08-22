-- scoring_v2_calibration.sql
--
-- Diagnostic: calibration assessment for Scoring V2 Phase 2A.
-- Compares LLM dimension scores against rule-based algorithmic_scores
-- stored in match_results_json, and estimates overall alignment.
--
-- Run as:
--   docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod \
--       -f /path/to/scoring_v2_calibration.sql
-- Or:
--   psql -h localhost -U cv_app cv_analyzer_prod \
--       -c "SET search_path = cv_analyzer" \
--       < scoring_v2_calibration.sql
--
-- Output sections:
--   1. Population summary
--   2. Per-dimension delta statistics (LLM − algorithmic)
--   3. Weighted algorithmic estimate vs final_score
--   4. Outlier rows per dimension (largest absolute delta)
--   5. blocking_gap_count vs final_score buckets
--   6. Match method distribution
--   7. Calibration signal summary (directional bias per dimension)

SET search_path TO cv_analyzer;

-- ── Base CTE: extract algorithmic scores for all populated rows ───────────────
-- Dimensions in algorithmic_scores: skills, experience, education,
-- certifications, soft_skills, domain_knowledge, other
-- LLM columns: score_skills, score_experience, score_education,
--              score_certifications, score_soft_skills,
--              score_domain_knowledge, score_other, final_score

\echo ''
\echo '================================================================='
\echo ' Scoring V2 Phase 2A — Calibration Assessment'
\echo '================================================================='

-- ── 1. Population summary ─────────────────────────────────────────────────────
\echo ''
\echo '=== 1. POPULATION SUMMARY ==='

SELECT
    COUNT(*)                                                AS total_rows,
    COUNT(*) FILTER (WHERE match_results_json IS NOT NULL) AS with_v2_data,
    COUNT(*) FILTER (WHERE match_results_json IS NULL)     AS legacy_only,
    ROUND(
        COUNT(*) FILTER (WHERE match_results_json IS NOT NULL)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                       AS v2_coverage_pct,
    MIN(created_at) FILTER (WHERE match_results_json IS NOT NULL) AS earliest_v2,
    MAX(created_at) FILTER (WHERE match_results_json IS NOT NULL) AS latest_v2
FROM application_scores;

-- ── 2. Per-dimension delta statistics ─────────────────────────────────────────
-- Delta = LLM score − algorithmic score  (positive → LLM is more generous)
\echo ''
\echo '=== 2. PER-DIMENSION DELTA STATISTICS (LLM − algorithmic) ==='
\echo '    Positive delta = LLM scores HIGHER than rule-based engine'
\echo '    Negative delta = LLM scores LOWER  than rule-based engine'

WITH algo AS (
    SELECT
        application_id,
        score_skills,
        score_experience,
        score_education,
        score_certifications,
        score_soft_skills,
        score_domain_knowledge,
        score_other,
        final_score,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'skills'),        '')::FLOAT AS a_skills,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'experience'),    '')::FLOAT AS a_experience,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'education'),     '')::FLOAT AS a_education,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'certifications'),'')::FLOAT AS a_certifications,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'soft_skills'),   '')::FLOAT AS a_soft_skills,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'domain_knowledge'),'')::FLOAT AS a_domain_knowledge,
        NULLIF((match_results_json -> 'algorithmic_scores' ->> 'other'),         '')::FLOAT AS a_other
    FROM application_scores
    WHERE match_results_json IS NOT NULL
),
deltas AS (
    SELECT
        'skills'          AS dimension,
        AVG(score_skills - a_skills)               AS avg_delta,
        STDDEV(score_skills - a_skills)            AS stddev_delta,
        MIN(score_skills - a_skills)               AS min_delta,
        MAX(score_skills - a_skills)               AS max_delta,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_skills - a_skills) AS p50,
        COUNT(*)                                   AS n
    FROM algo WHERE a_skills IS NOT NULL
    UNION ALL
    SELECT
        'experience',
        AVG(score_experience - a_experience),
        STDDEV(score_experience - a_experience),
        MIN(score_experience - a_experience),
        MAX(score_experience - a_experience),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_experience - a_experience),
        COUNT(*)
    FROM algo WHERE a_experience IS NOT NULL
    UNION ALL
    SELECT
        'education',
        AVG(score_education - a_education),
        STDDEV(score_education - a_education),
        MIN(score_education - a_education),
        MAX(score_education - a_education),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_education - a_education),
        COUNT(*)
    FROM algo WHERE a_education IS NOT NULL
    UNION ALL
    SELECT
        'certifications',
        AVG(score_certifications - a_certifications),
        STDDEV(score_certifications - a_certifications),
        MIN(score_certifications - a_certifications),
        MAX(score_certifications - a_certifications),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_certifications - a_certifications),
        COUNT(*)
    FROM algo WHERE a_certifications IS NOT NULL
    UNION ALL
    SELECT
        'soft_skills',
        AVG(score_soft_skills - a_soft_skills),
        STDDEV(score_soft_skills - a_soft_skills),
        MIN(score_soft_skills - a_soft_skills),
        MAX(score_soft_skills - a_soft_skills),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_soft_skills - a_soft_skills),
        COUNT(*)
    FROM algo WHERE a_soft_skills IS NOT NULL
    UNION ALL
    SELECT
        'domain_knowledge',
        AVG(score_domain_knowledge - a_domain_knowledge),
        STDDEV(score_domain_knowledge - a_domain_knowledge),
        MIN(score_domain_knowledge - a_domain_knowledge),
        MAX(score_domain_knowledge - a_domain_knowledge),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_domain_knowledge - a_domain_knowledge),
        COUNT(*)
    FROM algo WHERE a_domain_knowledge IS NOT NULL
    UNION ALL
    SELECT
        'other',
        AVG(score_other - a_other),
        STDDEV(score_other - a_other),
        MIN(score_other - a_other),
        MAX(score_other - a_other),
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY score_other - a_other),
        COUNT(*)
    FROM algo WHERE a_other IS NOT NULL
)
SELECT
    dimension,
    n,
    ROUND(avg_delta::NUMERIC,   1) AS avg_delta,
    ROUND(stddev_delta::NUMERIC, 1) AS stddev,
    ROUND(p50::NUMERIC,          1) AS median_delta,
    ROUND(min_delta::NUMERIC,    1) AS min_delta,
    ROUND(max_delta::NUMERIC,    1) AS max_delta,
    CASE
        WHEN avg_delta >  15 THEN 'LLM much more generous'
        WHEN avg_delta >   5 THEN 'LLM slightly more generous'
        WHEN avg_delta < -15 THEN 'LLM much stricter'
        WHEN avg_delta <  -5 THEN 'LLM slightly stricter'
        ELSE 'broadly aligned'
    END                             AS signal
FROM deltas
ORDER BY ABS(avg_delta) DESC;

-- ── 3. Weighted algorithmic estimate vs final_score ───────────────────────────
\echo ''
\echo '=== 3. WEIGHTED ALGORITHMIC ESTIMATE vs FINAL_SCORE ==='
\echo '    Uses weights_snapshot (defaults: skills=0.30 exp=0.25 edu=0.15'
\echo '    cert=0.10 soft=0.10 domain=0.10)'

WITH weighted AS (
    SELECT
        application_id,
        final_score,
        (
            COALESCE((weights_snapshot ->> 'skills')::FLOAT,         0.30) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'skills'),         '')::FLOAT, 0)
          + COALESCE((weights_snapshot ->> 'experience')::FLOAT,     0.25) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'experience'),     '')::FLOAT, 0)
          + COALESCE((weights_snapshot ->> 'education')::FLOAT,      0.15) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'education'),      '')::FLOAT, 0)
          + COALESCE((weights_snapshot ->> 'certifications')::FLOAT, 0.10) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'certifications'), '')::FLOAT, 0)
          + COALESCE((weights_snapshot ->> 'soft_skills')::FLOAT,    0.10) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'soft_skills'),    '')::FLOAT, 0)
          + COALESCE((weights_snapshot ->> 'domain_knowledge')::FLOAT, 0.10) *
                COALESCE(NULLIF((match_results_json -> 'algorithmic_scores' ->> 'domain_knowledge'),'')::FLOAT, 0)
        ) AS algo_weighted
    FROM application_scores
    WHERE match_results_json IS NOT NULL
      AND weights_snapshot IS NOT NULL
)
SELECT
    COUNT(*)                                    AS n,
    ROUND(AVG(final_score)::NUMERIC,     1)     AS avg_llm_final,
    ROUND(AVG(algo_weighted)::NUMERIC,   1)     AS avg_algo_weighted,
    ROUND(AVG(final_score - algo_weighted)::NUMERIC, 1) AS avg_delta,
    ROUND(STDDEV(final_score - algo_weighted)::NUMERIC, 1) AS stddev_delta,
    ROUND(CORR(final_score, algo_weighted)::NUMERIC, 3)    AS pearson_r,
    ROUND(MIN(final_score - algo_weighted)::NUMERIC, 1)    AS min_delta,
    ROUND(MAX(final_score - algo_weighted)::NUMERIC, 1)    AS max_delta
FROM weighted;

-- ── 4. Outlier rows per dimension ─────────────────────────────────────────────
\echo ''
\echo '=== 4. TOP 10 OUTLIERS — abs(LLM skills − algo skills) ==='

SELECT
    application_id,
    score_skills                                                          AS llm_skills,
    ROUND((match_results_json -> 'algorithmic_scores' ->> 'skills')::NUMERIC, 0) AS algo_skills,
    score_skills -
        ROUND((match_results_json -> 'algorithmic_scores' ->> 'skills')::NUMERIC, 0) AS delta,
    (match_results_json ->> 'blocking_gap_count')::INT                    AS blocking_gaps,
    final_score,
    created_at::DATE                                                      AS scored_date
FROM application_scores
WHERE match_results_json IS NOT NULL
  AND (match_results_json -> 'algorithmic_scores' ->> 'skills') IS NOT NULL
ORDER BY ABS(
    score_skills -
    (match_results_json -> 'algorithmic_scores' ->> 'skills')::FLOAT
) DESC
LIMIT 10;

\echo ''
\echo '=== 4b. TOP 10 OUTLIERS — abs(LLM experience − algo experience) ==='

SELECT
    application_id,
    score_experience                                                               AS llm_exp,
    ROUND((match_results_json -> 'algorithmic_scores' ->> 'experience')::NUMERIC, 0) AS algo_exp,
    score_experience -
        ROUND((match_results_json -> 'algorithmic_scores' ->> 'experience')::NUMERIC, 0) AS delta,
    final_score,
    created_at::DATE                                                               AS scored_date
FROM application_scores
WHERE match_results_json IS NOT NULL
  AND (match_results_json -> 'algorithmic_scores' ->> 'experience') IS NOT NULL
ORDER BY ABS(
    score_experience -
    (match_results_json -> 'algorithmic_scores' ->> 'experience')::FLOAT
) DESC
LIMIT 10;

-- ── 5. blocking_gap_count vs final_score ──────────────────────────────────────
\echo ''
\echo '=== 5. BLOCKING GAP COUNT vs FINAL_SCORE ==='

SELECT
    (match_results_json ->> 'blocking_gap_count')::INT                          AS blocking_gaps,
    COUNT(*)                                                                     AS n,
    ROUND(AVG(final_score)::NUMERIC, 1)                                         AS avg_final_score,
    ROUND(STDDEV(final_score)::NUMERIC, 1)                                      AS stddev_final,
    ROUND(MIN(final_score)::NUMERIC, 1)                                         AS min_final,
    ROUND(MAX(final_score)::NUMERIC, 1)                                         AS max_final,
    ROUND(AVG(
        COALESCE((match_results_json ->> 'required_match_pct')::FLOAT, 0)
    )::NUMERIC, 1)                                                               AS avg_required_match_pct
FROM application_scores
WHERE match_results_json IS NOT NULL
GROUP BY (match_results_json ->> 'blocking_gap_count')::INT
ORDER BY blocking_gaps;

-- ── 6. Match method distribution ──────────────────────────────────────────────
\echo ''
\echo '=== 6. MATCH METHOD DISTRIBUTION (across all matches) ==='

WITH method_counts AS (
    SELECT
        key   AS method,
        SUM(value::INT) AS total_matches
    FROM application_scores,
         LATERAL jsonb_each_text(match_results_json -> 'matching_method_summary') AS kv(key, value)
    WHERE match_results_json IS NOT NULL
      AND match_results_json -> 'matching_method_summary' IS NOT NULL
    GROUP BY key
)
SELECT
    method,
    total_matches,
    ROUND(total_matches::NUMERIC / NULLIF(SUM(total_matches) OVER (), 0) * 100, 1) AS pct
FROM method_counts
ORDER BY total_matches DESC;

-- ── 7. Calibration signal summary ─────────────────────────────────────────────
\echo ''
\echo '=== 7. CALIBRATION SIGNAL SUMMARY ==='
\echo '    Quadrant counts per dimension:'
\echo '    BOTH_HIGH / BOTH_LOW / LLM_HIGH / LLM_LOW'
\echo '    (threshold: 60 = rough "pass" boundary)'

WITH algo AS (
    SELECT
        score_skills    AS l_sk,   (match_results_json -> 'algorithmic_scores' ->> 'skills')::FLOAT    AS a_sk,
        score_experience AS l_ex,  (match_results_json -> 'algorithmic_scores' ->> 'experience')::FLOAT AS a_ex,
        score_education  AS l_ed,  (match_results_json -> 'algorithmic_scores' ->> 'education')::FLOAT  AS a_ed,
        score_soft_skills AS l_ss, (match_results_json -> 'algorithmic_scores' ->> 'soft_skills')::FLOAT AS a_ss,
        score_domain_knowledge AS l_dk, (match_results_json -> 'algorithmic_scores' ->> 'domain_knowledge')::FLOAT AS a_dk
    FROM application_scores
    WHERE match_results_json IS NOT NULL
),
quadrants AS (
    SELECT
        'skills'   AS dim,
        COUNT(*) FILTER (WHERE l_sk >= 60 AND a_sk >= 60) AS both_high,
        COUNT(*) FILTER (WHERE l_sk <  60 AND a_sk <  60) AS both_low,
        COUNT(*) FILTER (WHERE l_sk >= 60 AND a_sk <  60) AS llm_high_algo_low,
        COUNT(*) FILTER (WHERE l_sk <  60 AND a_sk >= 60) AS llm_low_algo_high
    FROM algo WHERE a_sk IS NOT NULL
    UNION ALL
    SELECT 'experience',
        COUNT(*) FILTER (WHERE l_ex >= 60 AND a_ex >= 60),
        COUNT(*) FILTER (WHERE l_ex <  60 AND a_ex <  60),
        COUNT(*) FILTER (WHERE l_ex >= 60 AND a_ex <  60),
        COUNT(*) FILTER (WHERE l_ex <  60 AND a_ex >= 60)
    FROM algo WHERE a_ex IS NOT NULL
    UNION ALL
    SELECT 'education',
        COUNT(*) FILTER (WHERE l_ed >= 60 AND a_ed >= 60),
        COUNT(*) FILTER (WHERE l_ed <  60 AND a_ed <  60),
        COUNT(*) FILTER (WHERE l_ed >= 60 AND a_ed <  60),
        COUNT(*) FILTER (WHERE l_ed <  60 AND a_ed >= 60)
    FROM algo WHERE a_ed IS NOT NULL
    UNION ALL
    SELECT 'soft_skills',
        COUNT(*) FILTER (WHERE l_ss >= 60 AND a_ss >= 60),
        COUNT(*) FILTER (WHERE l_ss <  60 AND a_ss <  60),
        COUNT(*) FILTER (WHERE l_ss >= 60 AND a_ss <  60),
        COUNT(*) FILTER (WHERE l_ss <  60 AND a_ss >= 60)
    FROM algo WHERE a_ss IS NOT NULL
    UNION ALL
    SELECT 'domain_knowledge',
        COUNT(*) FILTER (WHERE l_dk >= 60 AND a_dk >= 60),
        COUNT(*) FILTER (WHERE l_dk <  60 AND a_dk <  60),
        COUNT(*) FILTER (WHERE l_dk >= 60 AND a_dk <  60),
        COUNT(*) FILTER (WHERE l_dk <  60 AND a_dk >= 60)
    FROM algo WHERE a_dk IS NOT NULL
)
SELECT
    dim                 AS dimension,
    both_high           AS "agree_high",
    both_low            AS "agree_low",
    llm_high_algo_low   AS "llm_high/algo_low",
    llm_low_algo_high   AS "llm_low/algo_high",
    ROUND(
        (both_high + both_low)::NUMERIC
        / NULLIF(both_high + both_low + llm_high_algo_low + llm_low_algo_high, 0)
        * 100, 1
    )                   AS "agreement_pct"
FROM quadrants
ORDER BY "agreement_pct";

-- ── 8. D-01 LLM Criteria Mapping diagnostics ─────────────────────────────────
\echo ''
\echo '=== 8. D-01 LLM CRITERIA MAPPING POPULATION ==='
\echo '    Counts rows with llm_match_results_json populated.'
\echo '    SCORING_V2_LLM_MAPPING must be enabled for new rows to appear here.'

SELECT
    COUNT(*)                                                             AS total_scored,
    COUNT(*) FILTER (WHERE llm_match_results_json IS NOT NULL)          AS with_llm_mapping,
    COUNT(*) FILTER (WHERE llm_match_results_json IS NULL)              AS without_llm_mapping,
    ROUND(
        COUNT(*) FILTER (WHERE llm_match_results_json IS NOT NULL)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                                    AS llm_mapping_coverage_pct,
    MIN(created_at) FILTER (WHERE llm_match_results_json IS NOT NULL)   AS earliest_mapped,
    MAX(created_at) FILTER (WHERE llm_match_results_json IS NOT NULL)   AS latest_mapped
FROM application_scores;

\echo ''
\echo '=== 8b. LLM MAPPING — AGGREGATE COUNTS PER ROW ==='
\echo '    matched_count / partial_count / absent_count stored in llm_match_results_json.'

SELECT
    ROUND(AVG((llm_match_results_json ->> 'total_criteria')::INT),   1) AS avg_total_criteria,
    ROUND(AVG((llm_match_results_json ->> 'matched_count')::INT),    1) AS avg_matched,
    ROUND(AVG((llm_match_results_json ->> 'partial_count')::INT),    1) AS avg_partial,
    ROUND(AVG((llm_match_results_json ->> 'absent_count')::INT),     1) AS avg_absent,
    ROUND(AVG((llm_match_results_json ->> 'high_confidence_count')::INT), 1) AS avg_high_conf,
    ROUND(AVG((llm_match_results_json ->> 'low_confidence_count')::INT),  1) AS avg_low_conf,
    ROUND(AVG((llm_match_results_json ->> 'processing_ms')::INT),    0) AS avg_processing_ms,
    COUNT(*)                                                             AS n
FROM application_scores
WHERE llm_match_results_json IS NOT NULL;

\echo ''
\echo '=== 8c. LLM MAPPING — STATUS DISTRIBUTION (across all assessments) ==='

WITH assessments AS (
    SELECT
        jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
    FROM application_scores
    WHERE llm_match_results_json IS NOT NULL
      AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
)
SELECT
    a ->> 'status'          AS status,
    COUNT(*)                AS n,
    ROUND(COUNT(*)::NUMERIC / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 1) AS pct
FROM assessments
GROUP BY 1
ORDER BY n DESC;

\echo ''
\echo '=== 8d. LLM MAPPING — MATCH TYPE DISTRIBUTION ==='

WITH assessments AS (
    SELECT
        jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
    FROM application_scores
    WHERE llm_match_results_json IS NOT NULL
      AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
)
SELECT
    a ->> 'match_type'      AS match_type,
    COUNT(*)                AS n,
    ROUND(COUNT(*)::NUMERIC / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 1) AS pct
FROM assessments
GROUP BY 1
ORDER BY n DESC;

\echo ''
\echo '=== 8e. LLM MAPPING — CRITERION CLASS DISTRIBUTION ==='

WITH assessments AS (
    SELECT
        jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
    FROM application_scores
    WHERE llm_match_results_json IS NOT NULL
      AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
)
SELECT
    a ->> 'criterion_class' AS criterion_class,
    COUNT(*)                AS n,
    ROUND(COUNT(*)::NUMERIC / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 1) AS pct
FROM assessments
GROUP BY 1
ORDER BY n DESC;

\echo ''
\echo '=== 8f. LLM MAPPING — LOW CONFIDENCE ASSESSMENTS (confidence < 0.40) ==='
\echo '    High count here signals ambiguous evidence or criteria needing clarification.'

WITH assessments AS (
    SELECT
        application_id,
        jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
    FROM application_scores
    WHERE llm_match_results_json IS NOT NULL
      AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
)
SELECT
    a ->> 'dimension'       AS dimension,
    a ->> 'criterion_class' AS criterion_class,
    COUNT(*)                AS low_conf_count,
    ROUND(AVG((a ->> 'confidence')::FLOAT)::NUMERIC, 3) AS avg_confidence
FROM assessments
WHERE (a ->> 'confidence')::FLOAT < 0.40
GROUP BY 1, 2
ORDER BY low_conf_count DESC
LIMIT 20;

\echo ''
\echo '=== 8g. LLM vs RULE-BASED — STATUS COMPARISON (where both exist) ==='
\echo '    Diagnostic only. Agreement rate is NOT the success metric.'
\echo '    Real benchmark will be human-reviewed sample accuracy.'

WITH
rule_matches AS (
    SELECT
        application_id,
        jsonb_array_elements(match_results_json -> 'matches') AS rm
    FROM application_scores
    WHERE match_results_json IS NOT NULL
      AND llm_match_results_json IS NOT NULL
      AND jsonb_typeof(match_results_json -> 'matches') = 'array'
),
llm_assessments AS (
    SELECT
        application_id,
        jsonb_array_elements(llm_match_results_json -> 'assessments') AS la
    FROM application_scores
    WHERE match_results_json IS NOT NULL
      AND llm_match_results_json IS NOT NULL
      AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
),
paired AS (
    SELECT
        r.application_id,
        r.rm ->> 'status'         AS rule_status,
        l.la ->> 'status'         AS llm_status,
        r.rm ->> 'criterion_text' AS criterion_text
    FROM rule_matches r
    JOIN llm_assessments l
      ON r.application_id = l.application_id
     AND lower(trim(r.rm ->> 'criterion_text')) = lower(trim(l.la ->> 'criterion_text'))
)
SELECT
    rule_status,
    llm_status,
    COUNT(*)                                                         AS n,
    ROUND(COUNT(*)::NUMERIC / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 1) AS pct
FROM paired
GROUP BY rule_status, llm_status
ORDER BY rule_status, llm_status;

\echo ''
-- ── Section 9: D-01.7 Evidence Quality Metrics ───────────────────────────────
-- Diagnostics for evidence retrieval quality after D-01.7 improvements.
-- Shows average evidence density, zero-evidence rates, and dimension breakdown.

\echo ''
\echo '=== 9. D-01.7 EVIDENCE QUALITY METRICS ==='

-- 9a. Average supporting_evidence count per assessment
\echo ''
\echo '--- 9a. Average evidence items per assessment (by status) ---'
SELECT
    a ->> 'status'                             AS status,
    COUNT(*)                                   AS n_assessments,
    ROUND(AVG(jsonb_array_length(a -> 'supporting_evidence')), 2) AS avg_evidence_items,
    COUNT(*) FILTER (WHERE jsonb_array_length(a -> 'supporting_evidence') = 0) AS zero_evidence_count,
    ROUND(
        COUNT(*) FILTER (WHERE jsonb_array_length(a -> 'supporting_evidence') = 0)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                          AS zero_evidence_pct
FROM application_scores,
     jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
WHERE llm_match_results_json IS NOT NULL
  AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
GROUP BY a ->> 'status'
ORDER BY a ->> 'status';

-- 9b. Integrity check: MATCHED/PARTIAL with zero evidence (should be 0 after D-01.6 C4)
\echo ''
\echo '--- 9b. Integrity check: MATCHED or PARTIAL with zero supporting_evidence ---'
\echo '    Expected: 0 rows after D-01.6 C4 validation.'
SELECT
    application_id,
    a ->> 'status'         AS status,
    a ->> 'criterion_text' AS criterion_text,
    a ->> 'dimension'      AS dimension,
    a -> 'risk_flags'      AS risk_flags
FROM application_scores,
     jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
WHERE llm_match_results_json IS NOT NULL
  AND a ->> 'status' IN ('MATCHED', 'PARTIAL')
  AND jsonb_array_length(a -> 'supporting_evidence') = 0
ORDER BY application_id, a ->> 'status'
LIMIT 30;

-- 9c. Evidence density by dimension (soft_skills focus)
\echo ''
\echo '--- 9c. Evidence density by dimension ---'
SELECT
    a ->> 'dimension'                          AS dimension,
    COUNT(*)                                   AS n_assessments,
    ROUND(AVG(jsonb_array_length(a -> 'supporting_evidence')), 2) AS avg_evidence_items,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'MATCHED') AS matched,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'PARTIAL') AS partial,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'ABSENT')  AS absent,
    ROUND(
        COUNT(*) FILTER (WHERE a ->> 'status' = 'ABSENT')::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                          AS absent_pct
FROM application_scores,
     jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
WHERE llm_match_results_json IS NOT NULL
  AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
GROUP BY a ->> 'dimension'
ORDER BY absent_pct DESC;

-- 9d. Soft skill criterion class focus
\echo ''
\echo '--- 9d. soft_skill class distribution and evidence density ---'
SELECT
    a ->> 'criterion_class'                    AS criterion_class,
    COUNT(*)                                   AS n,
    ROUND(AVG(jsonb_array_length(a -> 'supporting_evidence')), 2) AS avg_evidence,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'MATCHED') AS matched,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'PARTIAL') AS partial,
    COUNT(*) FILTER (WHERE a ->> 'status' = 'ABSENT')  AS absent
FROM application_scores,
     jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
WHERE llm_match_results_json IS NOT NULL
  AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array'
  AND a ->> 'criterion_class' IN ('soft_skill', 'flexible', 'strict')
GROUP BY a ->> 'criterion_class'
ORDER BY a ->> 'criterion_class';

-- 9e. Missing-evidence risk flag audit (tracks C4 downgrade rate)
\echo ''
\echo '--- 9e. missing_supporting_evidence risk flag rate ---'
SELECT
    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(a -> 'risk_flags') rf
            WHERE rf = 'missing_supporting_evidence'
        )
    )                                          AS c4_downgrade_count,
    COUNT(*)                                   AS total_assessments,
    ROUND(
        COUNT(*) FILTER (
            WHERE EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(a -> 'risk_flags') rf
                WHERE rf = 'missing_supporting_evidence'
            )
        )::NUMERIC / NULLIF(COUNT(*), 0) * 100, 1
    )                                          AS c4_downgrade_pct
FROM application_scores,
     jsonb_array_elements(llm_match_results_json -> 'assessments') AS a
WHERE llm_match_results_json IS NOT NULL
  AND jsonb_typeof(llm_match_results_json -> 'assessments') = 'array';

\echo ''
\echo '=== END OF CALIBRATION REPORT ==='
\echo ''
\echo 'Interpretation guide (Sections 1–7):'
\echo '  avg_delta > +15  → algorithmic engine is too strict; needs score floor or weight boost'
\echo '  avg_delta > +5   → algorithmic engine under-detects; consider synonym expansion'
\echo '  avg_delta < -15  → algorithmic engine is too generous; fuzzy threshold too loose'
\echo '  avg_delta < -5   → LLM may be penalising criteria the rule engine cannot see'
\echo '  pearson_r > 0.7  → strong alignment; Phase 3 bounding safe to activate'
\echo '  pearson_r < 0.4  → weak alignment; Phase 3 bounding should not be activated yet'
\echo '  blocking_gaps=0, low final → LLM diverging downward from rule engine (unexpected)'
\echo '  blocking_gaps>0, high final → LLM diverging upward from rule engine (over-generous)'
\echo ''
\echo 'Interpretation guide (Section 8 — D-01 LLM Mapping):'
\echo '  Section 8 will be empty until scoring_v2.llm_criteria_mapping_enabled=true.'
\echo '  8c status: healthy mix of MATCHED/PARTIAL/ABSENT shows the mapper is discriminating.'
\echo '  8d match_type: heavy "inferred" share → criteria may be underspecified.'
\echo '  8e criterion_class: distribution should reflect job type (e.g. tech roles → more strict).'
\echo '  8f low-confidence: investigate dimensions with high counts — may need prompt revision.'
\echo '  8g rule-vs-LLM: agreement is a diagnostic signal, NOT the success metric.'
\echo '     Real accuracy benchmark requires human-reviewed ground truth.'
\echo ''
\echo 'Interpretation guide (Section 9 — D-01.7 Evidence Quality):'
\echo '  9a avg_evidence_items=0 for MATCHED/PARTIAL → C4 validation not applied (check version).'
\echo '  9b should return 0 rows after D-01.6 C4 is deployed.'
\echo '  9c soft_skills absent_pct < 50% is target after D-01.7 (was 65%+ before).'
\echo '  9d soft_skill class count >> 1 confirms D-01.6 C2/C3 are working.'
\echo '  9e c4_downgrade_pct > 10% → evidence window still too narrow; consider further expansion.'

-- ── Section 10: F-01 Deterministic Scoring Engine Calibration ────────────────
-- Compares det_final_score (deterministic) against final_score (LLM) and
-- reports population coverage, delta statistics, decision agreement, and
-- per-dimension breakdown.
-- All queries are safe on pre-F-01 rows (det_final_score IS NULL → excluded).

\echo ''
\echo '================================================================='
\echo '=== 10. F-01 DETERMINISTIC SCORING ENGINE CALIBRATION ==='
\echo '================================================================='

-- 10a. Coverage: how many rows have det_final_score populated?
\echo ''
\echo '--- 10a. Deterministic score coverage ---'
SELECT
    COUNT(*)                                                           AS total_scored,
    COUNT(*) FILTER (WHERE det_final_score IS NOT NULL)               AS with_det_score,
    COUNT(*) FILTER (WHERE det_final_score IS NULL)                   AS without_det_score,
    ROUND(
        COUNT(*) FILTER (WHERE det_final_score IS NOT NULL)::NUMERIC
        / NULLIF(COUNT(*), 0) * 100, 1
    )                                                                  AS det_coverage_pct,
    MIN(created_at) FILTER (WHERE det_final_score IS NOT NULL)        AS earliest_det,
    MAX(created_at) FILTER (WHERE det_final_score IS NOT NULL)        AS latest_det
FROM application_scores;

-- 10b. LLM vs deterministic — aggregate statistics
\echo ''
\echo '--- 10b. LLM final_score vs det_final_score (aggregate) ---'
\echo '    avg_delta = avg(final_score - det_final_score)'
\echo '    Pearson r approximated via corr() which requires both series non-null.'
SELECT
    COUNT(*)                                                           AS n,
    ROUND(AVG(final_score), 1)                                        AS avg_llm_score,
    ROUND(AVG(det_final_score), 1)                                    AS avg_det_score,
    ROUND(AVG(final_score - det_final_score), 1)                      AS avg_delta,
    ROUND(STDDEV(final_score - det_final_score)::NUMERIC, 1)         AS stddev_delta,
    ROUND(MIN(final_score - det_final_score)::NUMERIC, 0)            AS min_delta,
    ROUND(MAX(final_score - det_final_score)::NUMERIC, 0)            AS max_delta,
    ROUND(corr(final_score::FLOAT, det_final_score::FLOAT)::NUMERIC, 3) AS pearson_r
FROM application_scores
WHERE det_final_score IS NOT NULL;

-- 10c. Decision agreement simulation
-- Uses production thresholds from the applications table (qualified_threshold_used,
-- partial_threshold_used).  Simulates what det_final_score would decide.
\echo ''
\echo '--- 10c. Decision agreement: LLM decision vs simulated det decision ---'
\echo '    det_decision uses the same thresholds stored at scoring time.'
WITH scored AS (
    SELECT
        s.application_id,
        s.final_score,
        s.det_final_score,
        a.decision                              AS llm_decision,
        a.qualified_threshold_used              AS q_thresh,
        a.partial_threshold_used                AS p_thresh,
        CASE
            WHEN s.det_final_score >= a.qualified_threshold_used THEN 'qualified'
            WHEN s.det_final_score >= a.partial_threshold_used   THEN 'partially_qualified'
            ELSE 'rejected'
        END                                     AS det_decision
    FROM application_scores s
    JOIN applications a USING (application_id)
    WHERE s.det_final_score IS NOT NULL
      AND a.qualified_threshold_used IS NOT NULL
      AND a.partial_threshold_used   IS NOT NULL
),
agreement AS (
    SELECT
        llm_decision,
        det_decision,
        COUNT(*) AS n
    FROM scored
    GROUP BY llm_decision, det_decision
)
SELECT
    llm_decision,
    det_decision,
    n,
    ROUND(n::NUMERIC / NULLIF(SUM(n) OVER (PARTITION BY llm_decision), 0) * 100, 1) AS row_pct,
    CASE WHEN llm_decision = det_decision THEN 'AGREE' ELSE 'DISAGREE' END           AS verdict
FROM agreement
ORDER BY llm_decision, det_decision;

-- 10d. Top 20 largest absolute deltas (outliers for investigation)
\echo ''
\echo '--- 10d. Top 20 largest absolute delta rows (|LLM - det| DESC) ---'
SELECT
    s.application_id,
    s.final_score                              AS llm_score,
    s.det_final_score                          AS det_score,
    (s.final_score - s.det_final_score)        AS delta,
    ABS(s.final_score - s.det_final_score)     AS abs_delta,
    a.decision                                 AS llm_decision,
    s.created_at                               AS scored_at
FROM application_scores s
JOIN applications a USING (application_id)
WHERE s.det_final_score IS NOT NULL
ORDER BY abs_delta DESC
LIMIT 20;

-- 10e. Per-dimension comparison (LLM raw scores vs det dimension scores)
-- LLM raw dimension scores are 0–100 integers; det dimension scores are
-- 0.0–1.0 floats.  Multiply det by 100 for comparison.
\echo ''
\echo '--- 10e. Dimension comparison: LLM dim score vs det dim score (×100) ---'
SELECT
    dim.key                                         AS dimension,
    ROUND(AVG(
        CASE dim.key
            WHEN 'skills'           THEN s.score_skills
            WHEN 'experience'       THEN s.score_experience
            WHEN 'education'        THEN s.score_education
            WHEN 'certifications'   THEN s.score_certifications
            WHEN 'soft_skills'      THEN s.score_soft_skills
            WHEN 'domain_knowledge' THEN s.score_domain_knowledge
            WHEN 'other'            THEN s.score_other
        END
    ), 1)                                           AS avg_llm_dim_score,
    ROUND(AVG(
        ((s.det_score_json -> 'dimensions' -> dim.key) ->> 'dimension_score')::FLOAT * 100
    )::NUMERIC, 1)                                  AS avg_det_dim_score_x100,
    ROUND(AVG(
        CASE dim.key
            WHEN 'skills'           THEN s.score_skills
            WHEN 'experience'       THEN s.score_experience
            WHEN 'education'        THEN s.score_education
            WHEN 'certifications'   THEN s.score_certifications
            WHEN 'soft_skills'      THEN s.score_soft_skills
            WHEN 'domain_knowledge' THEN s.score_domain_knowledge
            WHEN 'other'            THEN s.score_other
        END
        - ((s.det_score_json -> 'dimensions' -> dim.key) ->> 'dimension_score')::FLOAT * 100
    )::NUMERIC, 1)                                  AS avg_delta,
    COUNT(*)                                        AS n
FROM application_scores s
CROSS JOIN (
    SELECT unnest(ARRAY['skills','experience','education','certifications',
                        'soft_skills','domain_knowledge','other']) AS key
) AS dim
WHERE s.det_final_score IS NOT NULL
  AND s.det_score_json IS NOT NULL
GROUP BY dim.key
ORDER BY ABS(AVG(
    CASE dim.key
        WHEN 'skills'           THEN s.score_skills
        WHEN 'experience'       THEN s.score_experience
        WHEN 'education'        THEN s.score_education
        WHEN 'certifications'   THEN s.score_certifications
        WHEN 'soft_skills'      THEN s.score_soft_skills
        WHEN 'domain_knowledge' THEN s.score_domain_knowledge
        WHEN 'other'            THEN s.score_other
    END
    - ((s.det_score_json -> 'dimensions' -> dim.key) ->> 'dimension_score')::FLOAT * 100
)) DESC;

-- 10f. det_final_score distribution histogram (10-point buckets)
\echo ''
\echo '--- 10f. det_final_score histogram (10-point buckets) ---'
SELECT
    FLOOR(det_final_score / 10) * 10    AS bucket_start,
    FLOOR(det_final_score / 10) * 10 + 9 AS bucket_end,
    COUNT(*)                             AS n,
    ROUND(COUNT(*)::NUMERIC / NULLIF(SUM(COUNT(*)) OVER (), 0) * 100, 1) AS pct
FROM application_scores
WHERE det_final_score IS NOT NULL
GROUP BY 1, 2
ORDER BY 1;

\echo ''
\echo 'Interpretation guide (Section 10 — F-01 Deterministic Engine):'
\echo '  10a: Coverage should reach 100% of rows where llm_match_results_json is populated.'
\echo '  10b: avg_delta measures LLM generosity vs det engine. Positive = LLM scores higher.'
\echo '       pearson_r > 0.70 → engines broadly agree; safe to use det for bounding (Phase 2).'
\echo '       pearson_r < 0.50 → significant divergence; investigate 10d outliers before bounding.'
\echo '  10c: DISAGREE rows identify candidates where det and LLM would bucket differently.'
\echo '       qualified→rejected disagrees warrant human review before activating det bounding.'
\echo '  10d: Largest delta rows are the highest-value calibration targets for human review.'
\echo '  10e: Dimension deltas identify which scoring dimensions drive LLM/det divergence.'
\echo '       Large positive delta → LLM awards more credit for that dimension than det.'
\echo '       Large negative delta → det awards more credit (rare with conservative defaults).'
\echo '  10f: Histogram shape should mirror final_score histogram (section 3b if present).'
\echo '       Systematic shift (all det scores 10+ lower) may indicate weight recalibration needed.'
