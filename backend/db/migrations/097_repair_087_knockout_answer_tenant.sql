-- 097_repair_087_knockout_answer_tenant.sql
--
-- Replaces 087_autosave_deterministic_answers.sql. The original is superseded and is never executed.
--
-- Why: 087 inserts tenant_id into application_knockout_answers, but no migration creates that column. The table is created
-- by 044 without it, and migrations 078, 081 and 082 do not add it. The knockout service writes tenant_id on every answer,
-- so knockout answer persistence cannot work on a database built from the files.
--
-- What this file does, in order:
--   1. Adds tenant_id UUID referencing tenants, or validates the existing column.
--   2. Back-fills tenant_id from applications, and fails if any row has no application or a conflicting tenant.
--   3. Sets tenant_id NOT NULL only after the validation above passes.
--   4. Adds evidence_text and confidence, or validates them.
--   5. Promotes high-confidence deterministic suggestions to final answers, as 087 intended.
--
-- Step 5 differs from 087 in one respect: it has no ON CONFLICT clause. The anti-join in its WHERE clause already excludes
-- every existing answer, so a conflicting row can only appear through a concurrent write, and that write should fail loudly.

SET search_path = cv_analyzer;

-- 0. Preconditions.
DO $$
BEGIN
    IF to_regclass('cv_analyzer.application_knockout_answers') IS NULL
       OR to_regclass('cv_analyzer.application_knockout_answer_suggestions') IS NULL
       OR to_regclass('cv_analyzer.applications') IS NULL
       OR to_regclass('cv_analyzer.tenants') IS NULL THEN
        RAISE EXCEPTION '097: knockout answer tables, applications and tenants must exist before this migration';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM (VALUES ('application_id'), ('question_id'), ('answer_value'), ('is_disqualifying'),
                     ('answer_source'), ('answer_method'), ('updated_at'), ('updated_by')) AS e(col)
        LEFT JOIN pg_attribute a
               ON a.attrelid = 'cv_analyzer.application_knockout_answers'::regclass
              AND a.attname = e.col AND a.attnum > 0 AND NOT a.attisdropped
        WHERE a.attnum IS NULL
    ) THEN
        RAISE EXCEPTION '097: application_knockout_answers lacks a column the promotion step needs';
    END IF;
END $$;

-- 1. tenant_id: add it, or validate the existing column type.
DO $$
DECLARE
    typ text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO typ
    FROM pg_attribute a
    WHERE a.attrelid = 'cv_analyzer.application_knockout_answers'::regclass
      AND a.attname = 'tenant_id' AND a.attnum > 0 AND NOT a.attisdropped;

    IF typ IS NULL THEN
        ALTER TABLE cv_analyzer.application_knockout_answers
            ADD COLUMN tenant_id UUID REFERENCES cv_analyzer.tenants (tenant_id);
    ELSIF typ <> 'uuid' THEN
        RAISE EXCEPTION '097: application_knockout_answers.tenant_id has type %, expected uuid', typ;
    END IF;
END $$;

-- 2. Back-fill from the owning application.
UPDATE cv_analyzer.application_knockout_answers a
SET tenant_id = app.tenant_id
FROM cv_analyzer.applications app
WHERE app.application_id = a.application_id
  AND a.tenant_id IS NULL;

-- 2a. Validation. Every answer must have an application, and any tenant_id already present must agree with it.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM cv_analyzer.application_knockout_answers a
        LEFT JOIN cv_analyzer.applications app ON app.application_id = a.application_id
        WHERE app.application_id IS NULL
    ) THEN
        RAISE EXCEPTION '097: knockout answers reference an application that does not exist';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM cv_analyzer.application_knockout_answers a
        JOIN cv_analyzer.applications app ON app.application_id = a.application_id
        WHERE a.tenant_id IS DISTINCT FROM app.tenant_id
    ) THEN
        RAISE EXCEPTION '097: knockout answer tenant_id disagrees with its application';
    END IF;
END $$;

-- 3. NOT NULL, applied only after validation.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cv_analyzer.application_knockout_answers WHERE tenant_id IS NULL) THEN
        RAISE EXCEPTION '097: knockout answers without tenant_id remain; refusing to set NOT NULL';
    END IF;
END $$;

ALTER TABLE cv_analyzer.application_knockout_answers ALTER COLUMN tenant_id SET NOT NULL;

-- 4. Provenance columns from 087, validated or added.
DO $$
DECLARE
    typ text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO typ
    FROM pg_attribute a
    WHERE a.attrelid = 'cv_analyzer.application_knockout_answers'::regclass
      AND a.attname = 'evidence_text' AND a.attnum > 0 AND NOT a.attisdropped;
    IF typ IS NULL THEN
        ALTER TABLE cv_analyzer.application_knockout_answers ADD COLUMN evidence_text TEXT;
    ELSIF typ <> 'text' THEN
        RAISE EXCEPTION '097: application_knockout_answers.evidence_text has type %, expected text', typ;
    END IF;

    SELECT format_type(a.atttypid, a.atttypmod) INTO typ
    FROM pg_attribute a
    WHERE a.attrelid = 'cv_analyzer.application_knockout_answers'::regclass
      AND a.attname = 'confidence' AND a.attnum > 0 AND NOT a.attisdropped;
    IF typ IS NULL THEN
        ALTER TABLE cv_analyzer.application_knockout_answers ADD COLUMN confidence NUMERIC(4,3);
    ELSIF typ <> 'numeric(4,3)' THEN
        RAISE EXCEPTION '097: application_knockout_answers.confidence has type %, expected numeric(4,3)', typ;
    END IF;
END $$;

-- 5. Promote deterministic candidate-email answers that meet the 087 criteria and have no final answer yet.
INSERT INTO cv_analyzer.application_knockout_answers (
    application_id,
    tenant_id,
    question_id,
    answer_value,
    is_disqualifying,
    answer_source,
    answer_method,
    evidence_text,
    confidence,
    updated_at,
    updated_by
)
SELECT
    s.application_id,
    app.tenant_id,
    s.question_id,
    s.suggested_answer,
    FALSE,
    'candidate_email',
    'direct_statement',
    s.evidence_text,
    s.confidence,
    now(),
    NULL
FROM cv_analyzer.application_knockout_answer_suggestions s
JOIN cv_analyzer.applications app ON app.application_id = s.application_id
LEFT JOIN cv_analyzer.application_knockout_answers existing
       ON existing.application_id = s.application_id
      AND existing.question_id = s.question_id
WHERE s.suggested_source = 'candidate_email'
  AND s.answer_method = 'direct_statement'
  AND s.ai_model = 'deterministic_extractor'
  AND s.verification_status = 'verified'
  AND s.confidence >= 0.95
  AND s.status = 'pending'
  AND s.suggested_answer IS NOT NULL
  AND existing.answer_id IS NULL;

-- 6. Mark the promoted suggestions accepted, as 087 did.
UPDATE cv_analyzer.application_knockout_answer_suggestions s
SET status = 'accepted',
    accepted_at = now()
FROM cv_analyzer.application_knockout_answers a
WHERE s.application_id = a.application_id
  AND s.question_id = a.question_id
  AND s.suggested_source = 'candidate_email'
  AND s.answer_method = 'direct_statement'
  AND s.ai_model = 'deterministic_extractor'
  AND s.verification_status = 'verified'
  AND s.confidence >= 0.95
  AND s.status = 'pending'
  AND a.answer_source = 'candidate_email';

-- 7. Postcondition.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'cv_analyzer'
          AND table_name = 'application_knockout_answers'
          AND column_name = 'tenant_id'
          AND is_nullable = 'YES'
    ) THEN
        RAISE EXCEPTION '097: application_knockout_answers.tenant_id is still nullable';
    END IF;
END $$;
