-- knockout_email_body_trace.sql
-- Root-cause diagnostic for application 1c568b11-43c0-47d7-8058-cee7d7d9512a
-- Run as: docker compose exec -T postgres psql -U cv_app -d cv_analyzer_prod -f /path/to/this.sql
-- Or paste each block separately.
--
-- Questions answered:
--   Q1. Is email_body_plain stored, and how long is it?
--   Q2. Does the HEAD of the body contain candidate answers?
--   Q3. Does the TAIL of the body contain candidate answers or job description?
--   Q4. What does the CV extracted text contain?
--   Q5. What suggestions did the AI return?

SET search_path TO cv_analyzer;

-- ── Q1 + Q2 + Q3: Stored email body ─────────────────────────────────────────
\echo '=== Q1/Q2/Q3: email_body_plain stored for application ==='
SELECT
    intake_method,
    subject,
    sender_email,
    LENGTH(email_body_plain)              AS body_length,
    LEFT(email_body_plain, 2000)          AS body_HEAD_2000,
    SUBSTRING(email_body_plain FROM 2001 FOR 1000) AS body_MID_2000_3000,
    RIGHT(email_body_plain, 1000)         AS body_TAIL_1000
FROM application_intake_log
WHERE application_id = '1c568b11-43c0-47d7-8058-cee7d7d9512a'
ORDER BY created_at DESC
LIMIT 1;

-- ── Q4: CV extracted text ────────────────────────────────────────────────────
\echo '=== Q4: CV extracted text ==='
SELECT
    extraction_status,
    LENGTH(extracted_text) AS cv_length,
    LEFT(extracted_text, 2000) AS cv_HEAD_2000
FROM application_files
WHERE application_id = '1c568b11-43c0-47d7-8058-cee7d7d9512a'
  AND extraction_status = 'done'
ORDER BY created_at DESC
LIMIT 1;

-- ── Q5: AI suggestions ───────────────────────────────────────────────────────
\echo '=== Q5: Current knockout suggestions ==='
SELECT
    s.question_id,
    q.question_text,
    q.question_type,
    s.suggested_answer,
    s.suggested_source,
    s.answer_method,
    s.confidence,
    s.verification_status,
    LEFT(s.evidence_text, 200) AS evidence_text,
    s.status
FROM application_knockout_answer_suggestions s
JOIN job_knockout_questions q ON q.question_id = s.question_id
WHERE s.application_id = '1c568b11-43c0-47d7-8058-cee7d7d9512a'
ORDER BY q.display_order;

-- ── Q6: Compare 618adbd7 (known case) vs 1c568b11 (new case) ────────────────
\echo '=== Q6: Side-by-side body stats for both applications ==='
SELECT
    application_id::text,
    intake_method,
    LENGTH(email_body_plain)     AS body_length,
    CASE WHEN email_body_plain IS NULL THEN 'NULL' ELSE 'present' END AS body_status
FROM application_intake_log
WHERE application_id IN (
    '618adbd7-c575-4efd-97a8-6bcba5c455e7',
    '1c568b11-43c0-47d7-8058-cee7d7d9512a'
)
ORDER BY application_id;
