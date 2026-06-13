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

\echo ''
\echo '=== END OF CALIBRATION REPORT ==='
\echo ''
\echo 'Interpretation guide:'
\echo '  avg_delta > +15  → algorithmic engine is too strict; needs score floor or weight boost'
\echo '  avg_delta > +5   → algorithmic engine under-detects; consider synonym expansion'
\echo '  avg_delta < -15  → algorithmic engine is too generous; fuzzy threshold too loose'
\echo '  avg_delta < -5   → LLM may be penalising criteria the rule engine cannot see'
\echo '  pearson_r > 0.7  → strong alignment; Phase 3 bounding safe to activate'
\echo '  pearson_r < 0.4  → weak alignment; Phase 3 bounding should not be activated yet'
\echo '  blocking_gaps=0, low final → LLM diverging downward from rule engine (unexpected)'
\echo '  blocking_gaps>0, high final → LLM diverging upward from rule engine (over-generous)'
