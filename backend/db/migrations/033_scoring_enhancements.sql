-- Migration 033: Scoring pipeline enhancements
--
-- Adds system_config keys for:
--   1. Extraction quality gate (min_extracted_text_chars)
--   2. Configurable bilingual gatekeeper auto-reject policy
--   3. System-wide AI comparison default
--
-- All inserts use ON CONFLICT (key) DO NOTHING for idempotency.
-- Existing rows are never overwritten — safe to re-run.

INSERT INTO system_config (key, value, type, category, description, editable)
VALUES

    -- Enhancement 1: Extraction quality gate
    (
        'min_extracted_text_chars',
        '300',
        'number',
        'Scoring',
        'Minimum number of characters required from PDF/DOCX text extraction. '
        'CVs producing fewer characters are marked failed without running the Gatekeeper or AI.',
        true
    ),

    -- Enhancement 2: Gatekeeper auto-reject master switch
    (
        'gatekeeper_auto_reject_enabled',
        'true',
        'boolean',
        'Scoring',
        'Master switch for Level 1 local auto-rejection. '
        'When false, the Gatekeeper still runs (metrics are recorded) but no CV is rejected locally — '
        'all CVs proceed to AI scoring. Individual threshold keys below are only active when this is true.',
        true
    ),

    -- Enhancement 2: English CV dual-threshold rejection thresholds
    (
        'gatekeeper_english_similarity_reject_below',
        '0.25',
        'number',
        'Scoring',
        'English CVs only: reject locally only when BOTH this semantic similarity threshold (0.0–1.0 scale) '
        'AND gatekeeper_english_skill_ratio_reject_below are breached simultaneously. '
        'Single-condition breach sends the CV to AI scoring instead.',
        true
    ),
    (
        'gatekeeper_english_skill_ratio_reject_below',
        '10',
        'number',
        'Scoring',
        'English CVs only: reject locally only when BOTH gatekeeper_english_similarity_reject_below '
        'AND this skill match ratio threshold (0–100 %) are breached simultaneously.',
        true
    ),

    -- Enhancement 2: Non-English/mixed CV dual-threshold rejection thresholds
    (
        'gatekeeper_non_english_similarity_reject_below',
        '0.15',
        'number',
        'Scoring',
        'Non-English and mixed-language CVs: reject locally only when BOTH this semantic similarity '
        'threshold (0.0–1.0 scale) AND gatekeeper_non_english_skill_ratio_reject_below are breached. '
        'Lower default than English threshold to account for embedding model variance across languages.',
        true
    ),
    (
        'gatekeeper_non_english_skill_ratio_reject_below',
        '5',
        'number',
        'Scoring',
        'Non-English and mixed-language CVs: reject locally only when BOTH '
        'gatekeeper_non_english_similarity_reject_below AND this skill match ratio threshold (0–100 %) '
        'are breached simultaneously.',
        true
    ),

    -- Enhancement 4: System-wide AI comparison default
    (
        'enable_ai_comparison_default',
        'false',
        'boolean',
        'Scoring',
        'System-wide default for the optional secondary AI comparison scoring run. '
        'The job-level jobs.enable_ai_comparison flag takes priority when explicitly set on a job. '
        'When that flag is NULL/unset the system default here is used.',
        true
    )

ON CONFLICT (key) DO NOTHING;
