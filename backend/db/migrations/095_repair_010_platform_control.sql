-- 095_repair_010_platform_control.sql
--
-- Replaces 010_platform_control.sql. The original is superseded and is never executed.
--
-- Why: on a clean chain 010 failed at CREATE TRIGGER trg_subscription_plans_updated_at, which schema.sql already creates.
-- The whole file rolled back, so system_config metadata, the trial/starter/professional/enterprise plan rows, and the
-- tenants subscription columns were never created. Migration 024 needs the starter and professional plans.
--
-- What this file does: restates every structural and data effect of 010 except its GRANT statements. GRANT effects belong
-- to DB1 and are not applied here, so this file grants nothing to any role.
--
-- How it is safe to re-run: every block inspects existing objects first. Missing objects are created. Objects that exist
-- must match the expected type, nullability or definition exactly; any other state raises and the file is rolled back.
-- Nothing is skipped silently and no ON CONFLICT clause hides an incompatible existing row.

SET search_path = cv_analyzer;

-- 1. Preconditions: the tables this file extends must exist. schema.sql creates them.
DO $$
BEGIN
    IF to_regclass('cv_analyzer.system_config') IS NULL
       OR to_regclass('cv_analyzer.subscription_plans') IS NULL
       OR to_regclass('cv_analyzer.tenants') IS NULL
       OR to_regclass('cv_analyzer.users') IS NULL THEN
        RAISE EXCEPTION '095: system_config, subscription_plans, tenants and users must exist before this migration';
    END IF;
END $$;

-- 2. system_config metadata columns. 010 added these four together, so they are all present or all absent.
DO $$
DECLARE
    present integer;
BEGIN
    SELECT count(*) INTO present
    FROM pg_attribute
    WHERE attrelid = 'cv_analyzer.system_config'::regclass
      AND attnum > 0 AND NOT attisdropped
      AND attname IN ('type', 'category', 'editable', 'updated_by');

    IF present = 0 THEN
        ALTER TABLE cv_analyzer.system_config
            ADD COLUMN type       VARCHAR(20) NOT NULL DEFAULT 'string'
                CHECK (type IN ('string', 'number', 'boolean', 'json')),
            ADD COLUMN category   VARCHAR(50) NOT NULL DEFAULT 'general'
                CHECK (category IN ('scoring', 'ai', 'email', 'queue', 'subscription', 'security', 'general')),
            ADD COLUMN editable   BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN updated_by UUID REFERENCES cv_analyzer.users (user_id) ON DELETE SET NULL;

        -- Back-fill for rows seeded before this metadata existed. Runs only when the columns are created here.
        UPDATE cv_analyzer.system_config SET type = 'number', category = 'scoring'
        WHERE key IN ('qualified_threshold', 'partial_threshold');
        UPDATE cv_analyzer.system_config SET type = 'number', category = 'ai'
        WHERE key IN ('gatekeeper_semantic_threshold', 'gatekeeper_skill_fuzzy_threshold');
    ELSIF present <> 4 THEN
        RAISE EXCEPTION '095: system_config has % of the 4 metadata columns; partial state is not repaired automatically',
            present;
    ELSE
        IF EXISTS (
            SELECT 1
            FROM (VALUES ('type', 'character varying(20)', true),
                         ('category', 'character varying(50)', true),
                         ('editable', 'boolean', true),
                         ('updated_by', 'uuid', false)) AS e(col, typ, not_null)
            LEFT JOIN pg_attribute a
                   ON a.attrelid = 'cv_analyzer.system_config'::regclass
                  AND a.attname = e.col AND a.attnum > 0 AND NOT a.attisdropped
            WHERE a.attnum IS NULL
               OR format_type(a.atttypid, a.atttypmod) <> e.typ
               OR (e.not_null AND NOT a.attnotnull)
        ) THEN
            RAISE EXCEPTION '095: system_config metadata columns do not match the definition added by 010';
        END IF;
    END IF;
END $$;

-- 3. system_config seed rows. A key that is missing is inserted. A key that already exists keeps its current row, as 010's
--    ON CONFLICT (key) DO NOTHING did: earlier migrations seed some of these keys with their own type and category.
CREATE TEMP TABLE _095_system_config_seed (
    key         varchar(100) PRIMARY KEY,
    value       text NOT NULL,
    type        varchar(20) NOT NULL,
    category    varchar(50) NOT NULL,
    editable    boolean NOT NULL,
    description text NOT NULL
) ON COMMIT DROP;

INSERT INTO _095_system_config_seed (key, value, type, category, editable, description) VALUES
    ('default_ai_model',        'gpt-4o-mini',         'string', 'ai',           true,
        'OpenAI model used for CV scoring and criteria extraction'),
    ('output_language',         'ar',                  'string', 'ai',           true,
        'Default language for AI-generated text outputs: ar or en'),
    ('max_file_size_mb',        '10',                  'number', 'general',      true,
        'Maximum CV file size allowed for upload, in megabytes'),
    ('imap_poll_interval',      '120',                 'number', 'email',        true,
        'Seconds between IMAP inbox polling cycles for email CV ingestion'),
    ('queue_stuck_timeout_min', '10',                  'number', 'queue',        true,
        'Minutes before a queued scoring job is considered stuck and eligible for reset'),
    ('default_trial_days',      '14',                  'number', 'subscription', true,
        'Default free trial period in days assigned to newly created tenants'),
    ('default_tenant_status',   'trial',               'string', 'subscription', true,
        'Default subscription_status assigned to newly created tenants (trial/active)'),
    ('email_sender_name',       'CV Analyzer',         'string', 'email',        true,
        'Display name used as the email sender for all outbound messages'),
    ('email_sender_address',    'noreply@ai970.cloud', 'string', 'email',        true,
        'From/reply-to email address for all outbound system messages');

INSERT INTO cv_analyzer.system_config (key, value, type, category, editable, description)
SELECT s.key, s.value, s.type, s.category, s.editable, s.description
FROM _095_system_config_seed s
WHERE NOT EXISTS (SELECT 1 FROM cv_analyzer.system_config c WHERE c.key = s.key);

-- 4. Platform plans. schema.sql defines subscription_plans with UNIQUE (plan_code).
CREATE TEMP TABLE _095_plan_seed (
    plan_code                   varchar(50) PRIMARY KEY,
    plan_name                   varchar(100) NOT NULL,
    description                 text NOT NULL,
    monthly_price               numeric(10,2) NOT NULL,
    yearly_price                numeric(10,2) NOT NULL,
    currency                    varchar(10) NOT NULL,
    trial_days                  int NOT NULL,
    max_campaigns               int NOT NULL,
    max_processed_cvs_per_month int NOT NULL,
    max_users                   int NOT NULL,
    api_access                  boolean NOT NULL,
    advanced_analytics          boolean NOT NULL,
    priority_support            boolean NOT NULL,
    custom_ai_prompts           boolean NOT NULL,
    status                      varchar(20) NOT NULL,
    display_order               int NOT NULL
) ON COMMIT DROP;

INSERT INTO _095_plan_seed VALUES
    ('starter', 'Starter',
     'Perfect for small teams starting their recruitment automation journey',
     49, 490, 'USD', 14, 5, 500, 3, false, false, false, false, 'active', 1),
    ('professional', 'Professional',
     'Advanced features for growing teams and high-volume recruitment operations',
     149, 1490, 'USD', 14, 25, 5000, 15, false, true, false, false, 'active', 2),
    ('enterprise', 'Enterprise',
     'Full-featured platform built for enterprise-scale recruitment with API access',
     499, 4990, 'USD', 14, 500, 100000, 100, true, true, true, true, 'active', 3);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM _095_plan_seed s
        JOIN cv_analyzer.subscription_plans p ON p.plan_code = s.plan_code
        WHERE p.plan_name <> s.plan_name OR p.monthly_price <> s.monthly_price
           OR p.yearly_price <> s.yearly_price OR p.status <> s.status
           OR p.max_processed_cvs_per_month <> s.max_processed_cvs_per_month
    ) THEN
        RAISE EXCEPTION '095: an existing platform plan differs from the seeded definition';
    END IF;
END $$;

INSERT INTO cv_analyzer.subscription_plans (
    plan_code, plan_name, description, monthly_price, yearly_price, currency, trial_days,
    max_campaigns, max_processed_cvs_per_month, max_users,
    api_access, advanced_analytics, priority_support, custom_ai_prompts, status, display_order
)
SELECT s.plan_code, s.plan_name, s.description, s.monthly_price, s.yearly_price, s.currency, s.trial_days,
       s.max_campaigns, s.max_processed_cvs_per_month, s.max_users,
       s.api_access, s.advanced_analytics, s.priority_support, s.custom_ai_prompts, s.status, s.display_order
FROM _095_plan_seed s
WHERE NOT EXISTS (SELECT 1 FROM cv_analyzer.subscription_plans p WHERE p.plan_code = s.plan_code);

-- 5. The updated_at trigger. schema.sql creates it; this file only makes sure it exists with the expected function and
--    timing. A trigger with the same name but a different definition raises.
DO $$
DECLARE
    trg record;
BEGIN
    SELECT t.tgfoid, t.tgtype::int AS tgtype, t.tgisinternal INTO trg
    FROM pg_trigger t
    WHERE t.tgrelid = 'cv_analyzer.subscription_plans'::regclass
      AND t.tgname = 'trg_subscription_plans_updated_at';

    IF NOT FOUND THEN
        CREATE TRIGGER trg_subscription_plans_updated_at
            BEFORE UPDATE ON cv_analyzer.subscription_plans
            FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();
    ELSIF trg.tgisinternal
          OR trg.tgfoid <> to_regprocedure('cv_analyzer.set_updated_at()')::oid
          OR (trg.tgtype & 19) <> 19 THEN   -- 1 = ROW, 2 = BEFORE, 16 = UPDATE
        RAISE EXCEPTION '095: trg_subscription_plans_updated_at exists with a different definition';
    END IF;
END $$;

-- 6. tenants subscription columns. 010 added these five together, so they are all present or all absent.
DO $$
DECLARE
    present integer;
BEGIN
    SELECT count(*) INTO present
    FROM pg_attribute
    WHERE attrelid = 'cv_analyzer.tenants'::regclass
      AND attnum > 0 AND NOT attisdropped
      AND attname IN ('subscription_status', 'trial_start_at', 'trial_end_at',
                      'subscription_started_at', 'subscription_ends_at');

    IF present = 0 THEN
        ALTER TABLE cv_analyzer.tenants
            ADD COLUMN subscription_status      VARCHAR(20) DEFAULT 'trial'
                CHECK (subscription_status IN ('trial', 'active', 'suspended', 'expired')),
            ADD COLUMN trial_start_at           TIMESTAMPTZ,
            ADD COLUMN trial_end_at             TIMESTAMPTZ,
            ADD COLUMN subscription_started_at  TIMESTAMPTZ,
            ADD COLUMN subscription_ends_at     TIMESTAMPTZ;

        -- Back-fill that 010 intended: tenants that predate the trial system are active. Runs only when created here.
        UPDATE cv_analyzer.tenants
        SET subscription_status = 'active',
            subscription_started_at = created_at
        WHERE subscription_status IS NULL OR subscription_status = 'trial';
    ELSIF present <> 5 THEN
        RAISE EXCEPTION '095: tenants has % of the 5 subscription columns; partial state is not repaired automatically',
            present;
    ELSE
        IF EXISTS (
            SELECT 1
            FROM (VALUES ('subscription_status', 'character varying(20)'),
                         ('trial_start_at', 'timestamp with time zone'),
                         ('trial_end_at', 'timestamp with time zone'),
                         ('subscription_started_at', 'timestamp with time zone'),
                         ('subscription_ends_at', 'timestamp with time zone')) AS e(col, typ)
            LEFT JOIN pg_attribute a
                   ON a.attrelid = 'cv_analyzer.tenants'::regclass
                  AND a.attname = e.col AND a.attnum > 0 AND NOT a.attisdropped
            WHERE a.attnum IS NULL OR format_type(a.atttypid, a.atttypmod) <> e.typ
        ) THEN
            RAISE EXCEPTION '095: tenants subscription columns do not match the definition added by 010';
        END IF;
    END IF;
END $$;

-- 7. Index on tenants.subscription_status.
DO $$
BEGIN
    IF to_regclass('cv_analyzer.idx_tenants_subscription_status') IS NULL THEN
        CREATE INDEX idx_tenants_subscription_status ON cv_analyzer.tenants (subscription_status);
    ELSIF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        WHERE i.indexrelid = 'cv_analyzer.idx_tenants_subscription_status'::regclass
          AND i.indrelid = 'cv_analyzer.tenants'::regclass
          AND NOT i.indisunique
          AND i.indnatts = 1
          AND i.indkey[0] = (SELECT attnum FROM pg_attribute
                             WHERE attrelid = 'cv_analyzer.tenants'::regclass AND attname = 'subscription_status')
    ) THEN
        RAISE EXCEPTION '095: idx_tenants_subscription_status exists with a different definition';
    END IF;
END $$;

-- 8. Postcondition: the platform plans this file is responsible for exist.
DO $$
BEGIN
    IF (SELECT count(*) FROM cv_analyzer.subscription_plans
        WHERE plan_code IN ('starter', 'professional', 'enterprise')) <> 3 THEN
        RAISE EXCEPTION '095: starter, professional and enterprise plans are not all present';
    END IF;
END $$;
