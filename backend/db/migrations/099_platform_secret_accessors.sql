-- 099_platform_secret_accessors.sql
--
-- Secret values leave platform_secrets only through two functions:
--   * cv_analyzer.get_platform_secret(key)  -- read; executable by cv_secrets only (granted by the operator grant script)
--   * cv_analyzer.set_platform_secret(...)  -- write; executable by cv_admin only (granted by the operator grant script)
--
-- Both run as the owner of this schema (the migrator role) with a fixed search_path, every object schema-qualified, and EXECUTE
-- revoked from PUBLIC here. A key outside the allowlist is refused with an explicit error. The functions do not catch errors:
-- a refused call aborts only the statement and transaction of its caller, which must roll back. Neither function ever returns
-- a value to a log or an error message.
--
-- DB_PASSWORD and JWT_SECRET are never readable or writable through these functions. They belong to the environment or the
-- secret manager. Rows for them may still exist from earlier seeds; the operator runbook covers clearing them.

BEGIN;

CREATE TABLE IF NOT EXISTS cv_analyzer.platform_secret_readable (
    key    VARCHAR(100) PRIMARY KEY REFERENCES cv_analyzer.platform_secrets (key) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (length(reason) > 0)
);

COMMENT ON TABLE cv_analyzer.platform_secret_readable IS
    'Allowlist of platform_secrets keys that get_platform_secret may return. Changed only by migrations.';

-- Keys the runtime reads. Rows that earlier seeds did not create are added here, so the allowlist can reference them. Existing rows are
-- left unchanged.
INSERT INTO cv_analyzer.platform_secrets (key, description, category, is_critical) VALUES
    ('OPENAI_API_KEY',        'OpenAI API key for the AI pipeline stages', 'ai', FALSE),
    ('SECONDARY_LLM_API_KEY', 'API key for the secondary/comparison LLM', 'ai', FALSE),
    ('SMTP_HOST',             'SMTP server host for outbound mail', 'email', FALSE),
    ('SMTP_PORT',             'SMTP server port for outbound mail', 'email', FALSE),
    ('SMTP_USER',             'SMTP username for outbound mail', 'email', FALSE),
    ('SMTP_PASSWORD',         'SMTP password for outbound mail', 'email', FALSE),
    ('SMTP_USE_TLS',          'Whether outbound mail uses TLS', 'email', FALSE),
    ('IMAP_HOST',             'IMAP host of the CV intake mailbox', 'email', FALSE),
    ('IMAP_PORT',             'IMAP port of the CV intake mailbox', 'email', FALSE),
    ('IMAP_USER',             'IMAP username of the CV intake mailbox', 'email', FALSE),
    ('IMAP_PASSWORD',         'IMAP password of the CV intake mailbox', 'email', FALSE),
    ('IMAP_USE_SSL',          'Whether the CV intake mailbox uses SSL', 'email', FALSE)
ON CONFLICT (key) DO NOTHING;

-- Keys the runtime reads today. Provider keys named *_API_KEY in category ai are also readable (see the function body).
INSERT INTO cv_analyzer.platform_secret_readable (key, reason) VALUES
    ('OPENAI_API_KEY',          'AI pipeline stages (default provider)'),
    ('SECONDARY_LLM_API_KEY',   'Secondary/comparison LLM provider'),
    ('SMTP_HOST',               'Outbound mail host'),
    ('SMTP_PORT',               'Outbound mail port'),
    ('SMTP_USER',               'Outbound mail username'),
    ('SMTP_PASSWORD',           'Outbound mail password'),
    ('SMTP_USE_TLS',            'Outbound mail TLS flag'),
    ('IMAP_HOST',               'CV intake mailbox host'),
    ('IMAP_PORT',               'CV intake mailbox port'),
    ('IMAP_USER',               'CV intake mailbox username'),
    ('IMAP_PASSWORD',           'CV intake mailbox password'),
    ('IMAP_USE_SSL',            'CV intake mailbox SSL flag')
ON CONFLICT (key) DO NOTHING;

CREATE OR REPLACE FUNCTION cv_analyzer.get_platform_secret(p_key text)
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, cv_analyzer
AS $$
DECLARE
    v_value text;
BEGIN
    IF p_key IS NULL OR p_key IN ('DB_PASSWORD', 'JWT_SECRET') THEN
        RAISE EXCEPTION 'secret key is not readable through the accessor' USING ERRCODE = '42501';
    END IF;

    IF NOT (
        EXISTS (SELECT 1 FROM cv_analyzer.platform_secret_readable r WHERE r.key = p_key)
        OR EXISTS (
            SELECT 1 FROM cv_analyzer.platform_secrets s
            WHERE s.key = p_key AND s.category = 'ai' AND s.key LIKE '%\_API\_KEY' ESCAPE '\'
        )
    ) THEN
        RAISE EXCEPTION 'secret key is not readable through the accessor' USING ERRCODE = '42501';
    END IF;

    SELECT s.value INTO v_value FROM cv_analyzer.platform_secrets s WHERE s.key = p_key;
    RETURN v_value;
END;
$$;

CREATE OR REPLACE FUNCTION cv_analyzer.set_platform_secret(
    p_key        text,
    p_value      text,
    p_masked     text,
    p_user_id    text,
    p_user_email text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, cv_analyzer
AS $$
BEGIN
    IF p_key IS NULL OR p_key IN ('DB_PASSWORD', 'JWT_SECRET') THEN
        RAISE EXCEPTION 'secret key cannot be set through the accessor' USING ERRCODE = '42501';
    END IF;
    IF p_value IS NULL OR length(p_value) = 0 THEN
        RAISE EXCEPTION 'secret value must not be empty' USING ERRCODE = '22023';
    END IF;

    UPDATE cv_analyzer.platform_secrets s
    SET value            = p_value,
        masked_value     = p_masked,
        has_value        = TRUE,
        updated_at       = now(),
        updated_by       = p_user_id,
        updated_by_email = p_user_email
    WHERE s.key = p_key;

    RETURN FOUND;
END;
$$;

-- No PUBLIC execution. The grant script decides which runtime role may execute each function.
REVOKE ALL ON FUNCTION cv_analyzer.get_platform_secret(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION cv_analyzer.set_platform_secret(text, text, text, text, text) FROM PUBLIC;
REVOKE ALL ON cv_analyzer.platform_secret_readable FROM PUBLIC;

COMMIT;
