-- 024_plan_features_seed.sql
-- Fix migration 023 which seeded 0 rows because plan_code='trial' did not
-- exist in production (only 'starter', 'professional', 'enterprise').
--
-- Changes:
--   1. Create the Trial plan row if it is missing
--   2. Seed all 19 plan_features for trial / starter / professional
--   3. Enterprise is intentionally excluded from the feature matrix
--   4. Fully idempotent — safe to rerun (ON CONFLICT DO NOTHING)

BEGIN;

SET search_path = cv_analyzer;

-- ════════════════════════════════════════════════════════════════════
-- 1. Ensure Trial plan exists
-- ════════════════════════════════════════════════════════════════════
INSERT INTO subscription_plans (
    plan_code, plan_name, description,
    monthly_price, yearly_price, currency,
    trial_days,
    max_campaigns, max_processed_cvs_per_month, max_users,
    status, display_order
)
SELECT
    'trial', 'Trial', 'Free trial — explore the platform for 14 days',
    0, 0, 'USD',
    14,
    2, 20, 1,
    'active',
    COALESCE(
        (SELECT MIN(display_order) - 1 FROM subscription_plans WHERE plan_code IN ('starter', 'professional')),
        0
    )
WHERE NOT EXISTS (
    SELECT 1 FROM subscription_plans WHERE plan_code = 'trial'
);

-- ════════════════════════════════════════════════════════════════════
-- 2. Seed plan_features for trial / starter / professional
-- ════════════════════════════════════════════════════════════════════
DO $$
DECLARE
    trial_id   UUID;
    starter_id UUID;
    pro_id     UUID;
BEGIN
    SELECT plan_id INTO trial_id   FROM subscription_plans WHERE plan_code = 'trial';
    SELECT plan_id INTO starter_id FROM subscription_plans WHERE plan_code = 'starter';
    SELECT plan_id INTO pro_id     FROM subscription_plans WHERE plan_code = 'professional';

    -- Abort only if starter or professional are genuinely missing (trial was just created above)
    IF starter_id IS NULL OR pro_id IS NULL THEN
        RAISE EXCEPTION
            'Cannot seed plan_features: starter or professional plan not found. '
            'Ensure these plans exist in subscription_plans before running this migration.';
    END IF;

    IF trial_id IS NULL THEN
        RAISE EXCEPTION 'Trial plan insert failed unexpectedly — plan_id is still NULL.';
    END IF;

    -- ── Numeric features ────────────────────────────────────────────
    INSERT INTO plan_features
        (plan_id, feature_key, feature_name, description, value_type, value_number, display_order)
    VALUES
        (trial_id,   'monthly_cv_processing', 'Monthly CV Processing', 'Number of CVs processed per month',          'number', 20,   1),
        (starter_id, 'monthly_cv_processing', 'Monthly CV Processing', 'Number of CVs processed per month',          'number', 500,  1),
        (pro_id,     'monthly_cv_processing', 'Monthly CV Processing', 'Number of CVs processed per month',          'number', 5000, 1),
        (trial_id,   'active_job_campaigns',  'Active Job Campaigns',  'Maximum active job postings at once',         'number', 2,    2),
        (starter_id, 'active_job_campaigns',  'Active Job Campaigns',  'Maximum active job postings at once',         'number', 5,    2),
        (pro_id,     'active_job_campaigns',  'Active Job Campaigns',  'Maximum active job postings at once',         'number', 25,   2),
        (trial_id,   'number_of_users',       'Number of Users',       'Maximum team members on the platform',        'number', 1,    3),
        (starter_id, 'number_of_users',       'Number of Users',       'Maximum team members on the platform',        'number', 3,    3),
        (pro_id,     'number_of_users',       'Number of Users',       'Maximum team members on the platform',        'number', 15,   3)
    ON CONFLICT (plan_id, feature_key) DO NOTHING;

    -- ── Boolean features: Yes / Yes / Yes ───────────────────────────
    INSERT INTO plan_features
        (plan_id, feature_key, feature_name, description, value_type, value_boolean, display_order)
    VALUES
        (trial_id,   'ai_cv_analysis',         'AI CV Analysis',         'Automated AI-powered CV scoring and analysis',             'boolean', true, 4),
        (starter_id, 'ai_cv_analysis',         'AI CV Analysis',         'Automated AI-powered CV scoring and analysis',             'boolean', true, 4),
        (pro_id,     'ai_cv_analysis',         'AI CV Analysis',         'Automated AI-powered CV scoring and analysis',             'boolean', true, 4),
        (trial_id,   'email_cv_intake',        'Email CV Intake',        'Receive CVs via email forwarding or platform alias',       'boolean', true, 5),
        (starter_id, 'email_cv_intake',        'Email CV Intake',        'Receive CVs via email forwarding or platform alias',       'boolean', true, 5),
        (pro_id,     'email_cv_intake',        'Email CV Intake',        'Receive CVs via email forwarding or platform alias',       'boolean', true, 5),
        (trial_id,   'job_email_alias',        'Job Email Alias',        'Dedicated email alias per job posting',                    'boolean', true, 6),
        (starter_id, 'job_email_alias',        'Job Email Alias',        'Dedicated email alias per job posting',                    'boolean', true, 6),
        (pro_id,     'job_email_alias',        'Job Email Alias',        'Dedicated email alias per job posting',                    'boolean', true, 6),
        (trial_id,   'public_apply_page',      'Public Apply Page',      'Shareable public job application landing page',            'boolean', true, 7),
        (starter_id, 'public_apply_page',      'Public Apply Page',      'Shareable public job application landing page',            'boolean', true, 7),
        (pro_id,     'public_apply_page',      'Public Apply Page',      'Shareable public job application landing page',            'boolean', true, 7),
        (trial_id,   'duplicate_detection',    'Duplicate Detection',    'Automatic detection and flagging of duplicate CVs',        'boolean', true, 8),
        (starter_id, 'duplicate_detection',    'Duplicate Detection',    'Automatic detection and flagging of duplicate CVs',        'boolean', true, 8),
        (pro_id,     'duplicate_detection',    'Duplicate Detection',    'Automatic detection and flagging of duplicate CVs',        'boolean', true, 8),
        (trial_id,   'multi_language',         'Multi-language',         'Support for Arabic and English CVs',                      'boolean', true, 9),
        (starter_id, 'multi_language',         'Multi-language',         'Support for Arabic and English CVs',                      'boolean', true, 9),
        (pro_id,     'multi_language',         'Multi-language',         'Support for Arabic and English CVs',                      'boolean', true, 9),
        (trial_id,   'ai_criteria_generation', 'AI Criteria Generation', 'AI-generated evaluation criteria from job descriptions',   'boolean', true, 10),
        (starter_id, 'ai_criteria_generation', 'AI Criteria Generation', 'AI-generated evaluation criteria from job descriptions',   'boolean', true, 10),
        (pro_id,     'ai_criteria_generation', 'AI Criteria Generation', 'AI-generated evaluation criteria from job descriptions',   'boolean', true, 10),
        (trial_id,   'intake_notifications',   'Intake Notifications',   'Automated email notifications for CV intake events',       'boolean', true, 11),
        (starter_id, 'intake_notifications',   'Intake Notifications',   'Automated email notifications for CV intake events',       'boolean', true, 11),
        (pro_id,     'intake_notifications',   'Intake Notifications',   'Automated email notifications for CV intake events',       'boolean', true, 11),
        (trial_id,   'basic_dashboard',        'Basic Dashboard',        'Overview dashboard with key recruitment metrics',          'boolean', true, 12),
        (starter_id, 'basic_dashboard',        'Basic Dashboard',        'Overview dashboard with key recruitment metrics',          'boolean', true, 12),
        (pro_id,     'basic_dashboard',        'Basic Dashboard',        'Overview dashboard with key recruitment metrics',          'boolean', true, 12)
    ON CONFLICT (plan_id, feature_key) DO NOTHING;

    -- ── Boolean features: No / Yes / Yes ────────────────────────────
    INSERT INTO plan_features
        (plan_id, feature_key, feature_name, description, value_type, value_boolean, display_order)
    VALUES
        (trial_id,   'rbac',                       'RBAC',                       'Role-based access control for team members',      'boolean', false, 13),
        (starter_id, 'rbac',                       'RBAC',                       'Role-based access control for team members',      'boolean', true,  13),
        (pro_id,     'rbac',                       'RBAC',                       'Role-based access control for team members',      'boolean', true,  13),
        (trial_id,   'custom_evaluation_criteria', 'Custom Evaluation Criteria', 'Define custom scoring weights and criteria',      'boolean', false, 14),
        (starter_id, 'custom_evaluation_criteria', 'Custom Evaluation Criteria', 'Define custom scoring weights and criteria',      'boolean', true,  14),
        (pro_id,     'custom_evaluation_criteria', 'Custom Evaluation Criteria', 'Define custom scoring weights and criteria',      'boolean', true,  14),
        (trial_id,   'bulk_cv_upload',             'Bulk CV Upload',             'Upload multiple CVs at once',                    'boolean', false, 15),
        (starter_id, 'bulk_cv_upload',             'Bulk CV Upload',             'Upload multiple CVs at once',                    'boolean', true,  15),
        (pro_id,     'bulk_cv_upload',             'Bulk CV Upload',             'Upload multiple CVs at once',                    'boolean', true,  15),
        (trial_id,   'audit_logs',                 'Audit Logs',                 'Complete audit trail of all platform actions',   'boolean', false, 16),
        (starter_id, 'audit_logs',                 'Audit Logs',                 'Complete audit trail of all platform actions',   'boolean', true,  16),
        (pro_id,     'audit_logs',                 'Audit Logs',                 'Complete audit trail of all platform actions',   'boolean', true,  16),
        (trial_id,   'data_export',                'Data Export',                'Export applicant data and reports as CSV/Excel', 'boolean', false, 17),
        (starter_id, 'data_export',                'Data Export',                'Export applicant data and reports as CSV/Excel', 'boolean', true,  17),
        (pro_id,     'data_export',                'Data Export',                'Export applicant data and reports as CSV/Excel', 'boolean', true,  17)
    ON CONFLICT (plan_id, feature_key) DO NOTHING;

    -- ── Text features ────────────────────────────────────────────────
    INSERT INTO plan_features
        (plan_id, feature_key, feature_name, description, value_type, value_text, display_order)
    VALUES
        (trial_id,   'cv_retention_period', 'CV Retention Period', 'How long CV files and data are retained', 'text', '14 Days',   18),
        (starter_id, 'cv_retention_period', 'CV Retention Period', 'How long CV files are retained',          'text', '30 Days',   18),
        (pro_id,     'cv_retention_period', 'CV Retention Period', 'How long CV files are retained',          'text', '12 Months', 18),
        (trial_id,   'custom_branding',     'Custom Branding',     'White-label and custom branding options',  'text', 'No',        19),
        (starter_id, 'custom_branding',     'Custom Branding',     'White-label and custom branding options',  'text', 'No',        19),
        (pro_id,     'custom_branding',     'Custom Branding',     'White-label and custom branding options',  'text', 'Basic',     19)
    ON CONFLICT (plan_id, feature_key) DO NOTHING;

    RAISE NOTICE 'plan_features seeded: trial=%, starter=%, professional=%',
        trial_id, starter_id, pro_id;
END $$;

COMMIT;
