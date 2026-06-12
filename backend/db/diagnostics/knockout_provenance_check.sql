-- Inspect knockout answer provenance for a specific application.
-- Replace :application_id with the target UUID.
--
-- Usage: psql -v application_id="'<uuid>'" -f knockout_provenance_check.sql

SELECT
    a.question_id,
    kq.question_text,
    a.answer_value,
    a.answer_source,
    a.answer_method,
    a.is_verified,
    a.evidence,
    a.updated_at       AS answer_updated_at,

    s.suggestion_id,
    s.suggested_value,
    s.suggested_source,
    s.answer_method    AS suggestion_method,
    s.status           AS suggestion_status,
    s.evidence         AS suggestion_evidence,
    s.created_at       AS suggestion_created_at

FROM application_knockout_answers a
JOIN knockout_questions kq
    ON kq.question_id = a.question_id
LEFT JOIN application_knockout_answer_suggestions s
    ON  s.application_id = a.application_id
    AND s.question_id    = a.question_id

WHERE a.application_id = :application_id

ORDER BY kq.display_order, a.question_id, s.created_at;
