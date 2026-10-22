-- CV Analyzer SaaS — Seed Data
-- Run AFTER schema.sql
-- Sets system-wide defaults and creates the initial super_admin tenant + user.

SET search_path = cv_analyzer;

-------------------------------------------------------------------------------
-- System defaults
-------------------------------------------------------------------------------
INSERT INTO system_config (key, value, description) VALUES
    ('default_qualified_threshold', '85',       'Minimum score (0-100) for "qualified" decision'),
    ('default_partial_threshold',   '65',       'Minimum score (0-100) for "partial" decision; below = rejected'),
    ('ai_model',                    'gpt-4o-mini', 'OpenAI model used for CV scoring and criteria extraction'),
    ('imap_poll_interval_seconds',  '120',      'How often Celery polls the IMAP inbox'),
    ('max_file_size_mb',            '10',       'Maximum CV file upload size in MB'),
    ('allowed_file_types',          'pdf,docx', 'Comma-separated allowed file extensions'),
    ('jwt_expire_hours',            '24',       'JWT access token lifetime in hours'),
    ('password_min_length',         '9',        'Minimum password length'),
    ('app_base_url',                'https://app.ai970.cloud', 'Frontend base URL (used in email links)'),
    ('api_base_url',                'https://api.ai970.cloud', 'Backend API base URL'),
    ('email_from_address',          'noreply@ai970.cloud',     'From address for transactional emails'),
    ('email_from_name',             'CV Analyzer',             'From name for transactional emails')
ON CONFLICT (key) DO NOTHING;

-------------------------------------------------------------------------------
-- Super-admin tenant (platform operator)
-- Replace password_hash with a real bcrypt hash before go-live.
-- Generate: python3 -c "from passlib.hash import bcrypt; print(bcrypt.using(rounds=10).hash('CHANGE_ME'))"
-------------------------------------------------------------------------------
INSERT INTO tenants (
    tenant_id,
    name,
    email_domain,
    plan,
    max_users,
    max_jobs,
    cv_ingestion_mode,
    status
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Platform Admin',
    'ai970.cloud',
    'enterprise',
    100,
    9999,
    'platform_email',
    'active'
) ON CONFLICT (tenant_id) DO NOTHING;

-- Super-admin user  (password: CHANGE_ME — bcrypt cost 10)
-- Replace password_hash before deploying.
INSERT INTO users (
    user_id,
    tenant_id,
    email,
    password_hash,
    full_name,
    role,
    status
) VALUES (
    '00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000001',
    'admin@ai970.cloud',
    crypt('CHANGE_ME_BEFORE_DEPLOY', gen_salt('bf', 10)),
    'Platform Administrator',
    'super_admin',
    'active'
) ON CONFLICT (user_id) DO NOTHING;
