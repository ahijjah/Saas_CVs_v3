-- Snapshot of the live settings and prompt/model selection that this release must leave as they are. READ-ONLY.
-- Run before the deployment and after it, save both outputs, and compare them. The only expected difference is the new row
-- requirements_v2.recovery_enabled (created 'false' by migration 109). Any other difference is a stop condition.
-- Run:  psql -U cv_app -d cv_analyzer_prod -f deploy/settings_snapshot.sql > /root/backups/settings_<stage>_<TS>.txt
SET search_path = cv_analyzer;
SELECT key, value, type FROM system_config WHERE key LIKE 'requirements_v2.%' OR key LIKE 'job_analysis.%' ORDER BY key;
SELECT prompt_code, version, is_active, temperature, max_tokens, md5(system_prompt) AS system_prompt_md5
  FROM ai_prompts WHERE prompt_code = 'criteria_extraction_v2' ORDER BY version;
SELECT stage, primary_model_id FROM ai_stage_defaults WHERE stage = 'requirements_v2_extraction';
