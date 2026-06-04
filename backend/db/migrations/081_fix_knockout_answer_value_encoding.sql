-- 081_fix_knockout_answer_value_encoding.sql
--
-- ROOT CAUSE: save_knockout_answers() in knockout_questions_service.py used
-- json.dumps(val) + CAST(:val AS jsonb) when inserting into the answer_value
-- TEXT column.  This caused two problems:
--
--   1. Values are stored JSON-encoded (e.g.  "yes" → '"yes"' with surrounding
--      double-quotes).  Display and comparison both see the quotes.
--
--   2. asyncpg's prepared-statement layer infers the column type as jsonb
--      (because the INSERT used CAST AS jsonb).  Subsequent SELECT queries that
--      compare answer_value against a text literal (e.g. answer_value <> '')
--      cause asyncpg to try to validate '' as JSONB, raising:
--        InvalidTextRepresentationError: invalid input syntax for type json
--
-- FIX:
--   Step 1 – Ensure answer_value is unambiguously TEXT (no-op if already TEXT;
--             forces a type reset if asyncpg/planner has cached it differently).
--   Step 2 – Strip the JSON-string quoting from simple string values (e.g.
--             '"yes"' → 'yes', '"no"' → 'no', '"3"' → '3').
--   Step 3 – Delete rows whose answer was an empty JSON string ('""' or ''),
--             which represent "no answer saved" and must not block analysis.
--
-- Idempotent: safe to re-run; each step only touches affected rows.

BEGIN;
SET search_path = cv_analyzer;

-- ── Step 1: Assert / re-declare column as TEXT ────────────────────────────────
-- If the column is TEXT this is a no-op type change (PostgreSQL allows it).
-- If it somehow became jsonb, this converts it back using its text representation.
ALTER TABLE application_knockout_answers
    ALTER COLUMN answer_value TYPE TEXT
    USING answer_value::text;

-- ── Step 2: Strip outer double-quotes from JSON-encoded simple string values ──
-- Matches values that look like a JSON string: starts with " and ends with ",
-- no embedded backslashes or quotes (handles simple yes/no/option/numeric answers).
-- e.g.  '"yes"'  → 'yes'
--        '"3.5"'  → '3.5'
--        '"Option A"' → 'Option A'
UPDATE application_knockout_answers
SET answer_value = substring(answer_value FROM 2 FOR char_length(answer_value) - 2)
WHERE answer_value ~ '^"[^"\\]*"$'           -- simple JSON string (no escape sequences)
  AND char_length(answer_value) > 2;          -- not just '""'

-- ── Step 3: Delete rows with effectively-empty answers ───────────────────────
-- '""' = JSON empty string left by old code path.
-- ''   = plain empty string (should not exist given NOT NULL + old code).
-- Both represent "candidate did not answer" and should not block AI analysis.
DELETE FROM application_knockout_answers
WHERE trim(answer_value) = ''
   OR answer_value = '""';

-- ── Diagnostic: show any remaining unusual answer_value patterns ──────────────
-- (Comment-out in production if verbose output is not desired)
-- SELECT answer_id, application_id, question_id, answer_value
-- FROM application_knockout_answers
-- WHERE answer_value ~ '^"'   -- still starts with a quote after cleanup
-- LIMIT 20;

COMMIT;
