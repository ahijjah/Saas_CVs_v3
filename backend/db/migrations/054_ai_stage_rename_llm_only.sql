BEGIN;
SET search_path = cv_analyzer;

-- Rename criteria_extraction → cv_analyzer in model registry supported_stages arrays
UPDATE ai_model_registry
SET supported_stages = array_replace(supported_stages, 'criteria_extraction', 'cv_analyzer'),
    updated_at = now()
WHERE 'criteria_extraction' = ANY(supported_stages);

-- Remove non-LLM stages from supported_stages (level2_screening, translation_explanation,
-- security_detection are local-code stages and must not appear in the registry)
UPDATE ai_model_registry
SET supported_stages = (
    SELECT array_agg(s)
    FROM unnest(supported_stages) AS s
    WHERE s NOT IN ('level2_screening', 'translation_explanation', 'security_detection')
),
    updated_at = now()
WHERE supported_stages && ARRAY['level2_screening', 'translation_explanation', 'security_detection'];

-- Migrate stage_defaults: rename criteria_extraction → cv_analyzer
INSERT INTO ai_stage_defaults (stage, primary_model_id, fallback_model_id, updated_at)
SELECT 'cv_analyzer', primary_model_id, fallback_model_id, now()
FROM ai_stage_defaults
WHERE stage = 'criteria_extraction'
ON CONFLICT (stage) DO UPDATE SET
    primary_model_id  = EXCLUDED.primary_model_id,
    fallback_model_id = EXCLUDED.fallback_model_id,
    updated_at        = now();

-- Remove the old criteria_extraction row
DELETE FROM ai_stage_defaults WHERE stage = 'criteria_extraction';

-- Remove non-LLM stage rows entirely
DELETE FROM ai_stage_defaults
WHERE stage IN ('level2_screening', 'translation_explanation', 'security_detection');

-- Ensure the three canonical LLM stages exist (upsert with gpt-4o-mini default)
INSERT INTO ai_stage_defaults (stage, primary_model_id, fallback_model_id, updated_at)
SELECT s, m.model_id, m.model_id, now()
FROM (VALUES ('cv_analyzer'), ('cv_scoring'), ('fallback')) AS stages(s)
CROSS JOIN (
    SELECT model_id FROM ai_model_registry
    WHERE provider = 'openai' AND model_name = 'gpt-4o-mini' LIMIT 1
) m
ON CONFLICT (stage) DO NOTHING;

COMMIT;
