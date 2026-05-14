-- 015_runtime_email_config_from_db.sql
-- Seed IMAP_PORT, SMTP_PORT, SMTP_USE_TLS into platform_secrets so the
-- runtime_config service can read them from the DB without requiring env vars.
SET search_path = cv_analyzer;

INSERT INTO platform_secrets (key, value, masked_value, description, category, is_critical, has_value)
VALUES
    ('IMAP_PORT', '993', '••••••••• 993', 'IMAP SSL port used by the CV-ingestion worker', 'email', FALSE, TRUE),
    ('SMTP_PORT', '587', '•••••••••587', 'SMTP port used for outbound transactional email', 'email', FALSE, TRUE),
    ('SMTP_USE_TLS', 'true', '••••••••true', 'Whether to use TLS for SMTP (true/false)', 'email', FALSE, TRUE)
ON CONFLICT (key) DO NOTHING;
