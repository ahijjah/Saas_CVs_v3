-- Restored production schema (schema only, no data), pg_dump of cv_analyzer from the restored backup, PostgreSQL 16.13.
-- Committed as a test fixture for db/legacy: tests build it on a disposable cluster and compare it with db/legacy/reference.json.
-- Changes made to the dump: the pg_dump \restrict / \unrestrict lines and `CREATE SCHEMA cv_analyzer;` were removed.
-- The loader (tests/legacy_production_helper.py: restored_production()) creates schema cv_analyzer, pgcrypto and uuid-ossp in it, and
-- public.set_updated_at(), which trigger trg_ail_updated_at calls and the schema-only dump does not contain.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: cv_analyzer; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: set_jobs_updated_at(); Type: FUNCTION; Schema: cv_analyzer; Owner: -
--

CREATE FUNCTION cv_analyzer.set_jobs_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: cv_analyzer; Owner: -
--

CREATE FUNCTION cv_analyzer.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: tenant_features_set_updated_at(); Type: FUNCTION; Schema: cv_analyzer; Owner: -
--

CREATE FUNCTION cv_analyzer.tenant_features_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_ai_prompts_updated_at(); Type: FUNCTION; Schema: cv_analyzer; Owner: -
--

CREATE FUNCTION cv_analyzer.update_ai_prompts_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agency_user_clients; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.agency_user_clients (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    client_organization_id uuid NOT NULL,
    role_for_client character varying(30) DEFAULT 'recruiter'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agency_user_clients_role_check CHECK (((role_for_client)::text = ANY (ARRAY[('account_manager'::character varying)::text, ('recruiter'::character varying)::text, ('viewer'::character varying)::text])))
);


--
-- Name: TABLE agency_user_clients; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.agency_user_clients IS 'Maps agency/recruiter users to specific client organisations with a per-client role. Agency admins see all clients; other roles see only their assigned clients.';


--
-- Name: COLUMN agency_user_clients.role_for_client; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.agency_user_clients.role_for_client IS 'account_manager | recruiter | viewer — access level for this specific client.';


--
-- Name: ai_model_pricing; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.ai_model_pricing (
    pricing_id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    model text NOT NULL,
    input_price_per_1m_tokens numeric(12,6) DEFAULT 0 NOT NULL,
    output_price_per_1m_tokens numeric(12,6) DEFAULT 0 NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_model_registry; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.ai_model_registry (
    model_id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    model_name text NOT NULL,
    display_name text NOT NULL,
    provider_secret_key text,
    base_url text,
    supported_stages text[] DEFAULT '{}'::text[] NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    max_context_tokens integer,
    input_price_per_1m_tokens numeric(12,6),
    output_price_per_1m_tokens numeric(12,6),
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_prompts; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.ai_prompts (
    prompt_id uuid DEFAULT gen_random_uuid() NOT NULL,
    prompt_code character varying(100) NOT NULL,
    prompt_name character varying(255) NOT NULL,
    prompt_category character varying(50) NOT NULL,
    system_prompt text NOT NULL,
    user_prompt_template text,
    model character varying(100) DEFAULT 'gpt-4o-mini'::character varying NOT NULL,
    temperature numeric(3,2) DEFAULT 0.20 NOT NULL,
    max_tokens integer DEFAULT 2000 NOT NULL,
    output_language character varying(10) DEFAULT 'ar'::character varying NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    updated_by character varying(255),
    updated_by_email character varying(255),
    CONSTRAINT ai_prompts_prompt_category_check CHECK (((prompt_category)::text = ANY (ARRAY[('criteria'::character varying)::text, ('scoring'::character varying)::text, ('screening'::character varying)::text, ('summary'::character varying)::text, ('interview'::character varying)::text, ('knockout'::character varying)::text, ('mapping'::character varying)::text]))),
    CONSTRAINT ai_prompts_temperature_check CHECK (((temperature >= (0)::numeric) AND (temperature <= (2)::numeric)))
);


--
-- Name: ai_stage_defaults; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.ai_stage_defaults (
    stage text NOT NULL,
    primary_model_id uuid,
    fallback_model_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_usage_log; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.ai_usage_log (
    usage_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    job_id uuid,
    application_id uuid,
    stage text NOT NULL,
    provider text DEFAULT 'openai'::text NOT NULL,
    model text NOT NULL,
    prompt_key text,
    prompt_version_id uuid,
    prompt_tokens integer DEFAULT 0 NOT NULL,
    completion_tokens integer DEFAULT 0 NOT NULL,
    total_tokens integer DEFAULT 0 NOT NULL,
    input_price_per_1m_tokens numeric(12,6),
    output_price_per_1m_tokens numeric(12,6),
    estimated_cost_usd numeric(14,8),
    request_status text DEFAULT 'success'::text NOT NULL,
    latency_ms integer,
    retry_count integer DEFAULT 0 NOT NULL,
    error_type text,
    error_message text,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ai_usage_log_request_status_check CHECK ((request_status = ANY (ARRAY['success'::text, 'failed'::text, 'timeout'::text, 'cancelled'::text])))
);


--
-- Name: application_candidate_tags; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_candidate_tags (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tag_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: application_files; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_files (
    file_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    original_name character varying(255) NOT NULL,
    mime_type character varying(100) NOT NULL,
    file_path text NOT NULL,
    file_size_bytes bigint,
    extracted_text text,
    extraction_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    normalized_text_hash character varying(64),
    canonical_text_fingerprint character varying(64),
    CONSTRAINT application_files_extraction_status_check CHECK (((extraction_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('done'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: application_intake_log; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_intake_log (
    intake_log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    job_id uuid,
    application_id uuid,
    intake_method character varying(30) NOT NULL,
    status character varying(50) NOT NULL,
    candidate_email character varying(255),
    candidate_name character varying(255),
    source_identifier text,
    source_message_id text,
    sender_email character varying(255),
    recipient_email character varying(255),
    subject text,
    original_filename text,
    file_hash character varying(64),
    file_size_bytes integer,
    mime_type character varying(100),
    error_message text,
    duplicate_reference_application_id uuid,
    duplicate_log_id uuid,
    received_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    processed_at timestamp with time zone,
    processing_duration_ms integer,
    scoring_enqueued_at timestamp with time zone,
    notification_queued_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    email_body_plain text
);


--
-- Name: TABLE application_intake_log; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.application_intake_log IS 'Unified intake audit log covering all CV submission paths';


--
-- Name: COLUMN application_intake_log.intake_method; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_intake_log.intake_method IS 'email | public_apply | manual_upload | api_upload';


--
-- Name: COLUMN application_intake_log.status; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_intake_log.status IS 'IntakeStatus enum value';


--
-- Name: COLUMN application_intake_log.file_hash; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_intake_log.file_hash IS 'SHA-256 hex digest of raw file bytes';


--
-- Name: application_knockout_answer_suggestions; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_knockout_answer_suggestions (
    suggestion_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    question_id uuid NOT NULL,
    suggested_answer text,
    suggested_source character varying(30) DEFAULT 'not_found'::character varying NOT NULL,
    confidence numeric(3,2) DEFAULT 0.00 NOT NULL,
    evidence_text text,
    verification_status character varying(30) DEFAULT 'not_found'::character varying NOT NULL,
    ai_model character varying(100),
    prompt_code character varying(100),
    prompt_version integer,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    accepted_by uuid,
    accepted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    answer_method character varying(30) DEFAULT 'ai_inference'::character varying NOT NULL
);


--
-- Name: application_knockout_answer_validations; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_knockout_answer_validations (
    validation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    question_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    final_answer_value text,
    final_answer_source character varying(30),
    suggested_answer text,
    validation_status character varying(30) DEFAULT 'cannot_validate'::character varying NOT NULL,
    validation_source character varying(30),
    confidence numeric(4,3),
    evidence_text text,
    reasoning_summary text,
    ai_model character varying(100),
    prompt_code character varying(100),
    prompt_version integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone
);


--
-- Name: application_knockout_answers; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_knockout_answers (
    answer_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    application_id uuid NOT NULL,
    question_id uuid NOT NULL,
    answer_value text NOT NULL,
    is_disqualifying boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    answer_source character varying(30) DEFAULT 'candidate_provided'::character varying NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone,
    answer_method character varying(30) DEFAULT 'direct_statement'::character varying NOT NULL,
    evidence_text text,
    confidence numeric(4,3)
);


--
-- Name: application_knockout_validation_runs; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_knockout_validation_runs (
    run_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    validation_status character varying(20) DEFAULT 'completed'::character varying NOT NULL,
    questions_count integer DEFAULT 0 NOT NULL,
    validated_count integer DEFAULT 0 NOT NULL,
    suggested_count integer DEFAULT 0 NOT NULL,
    cannot_count integer DEFAULT 0 NOT NULL,
    not_supported_count integer DEFAULT 0 NOT NULL,
    prompt_version integer,
    model character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: application_score_comparisons; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_score_comparisons (
    comparison_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    provider character varying(50) NOT NULL,
    model character varying(100) NOT NULL,
    final_score smallint NOT NULL,
    score_skills smallint,
    score_experience smallint,
    score_education smallint,
    score_certifications smallint,
    score_soft_skills smallint,
    score_domain_knowledge smallint,
    score_other smallint,
    score_details jsonb,
    weights_snapshot jsonb NOT NULL,
    evaluation_notes text,
    strengths text[] DEFAULT '{}'::text[] NOT NULL,
    gaps_identified text[] DEFAULT '{}'::text[] NOT NULL,
    scoring_prompt_code character varying(100),
    scoring_prompt_version integer,
    raw_response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: application_scores; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_scores (
    score_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    score_skills smallint NOT NULL,
    score_experience smallint NOT NULL,
    score_education smallint NOT NULL,
    score_certifications smallint NOT NULL,
    score_soft_skills smallint NOT NULL,
    score_domain_knowledge smallint NOT NULL,
    score_other smallint NOT NULL,
    final_score smallint NOT NULL,
    weights_snapshot jsonb NOT NULL,
    ai_model character varying(100) NOT NULL,
    strengths text[] DEFAULT '{}'::text[] NOT NULL,
    gaps_identified text[] DEFAULT '{}'::text[] NOT NULL,
    evaluation_notes text,
    interview_questions text[] DEFAULT '{}'::text[] NOT NULL,
    raw_ai_response jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    local_similarity_score numeric(5,2),
    skill_match_ratio numeric(5,2),
    matched_skills text[] DEFAULT '{}'::text[] NOT NULL,
    missing_skills text[] DEFAULT '{}'::text[] NOT NULL,
    cv_language character varying(10),
    gatekeeper_passed boolean DEFAULT true NOT NULL,
    red_flags text[] DEFAULT '{}'::text[] NOT NULL,
    reasoning jsonb,
    analysis_metadata jsonb,
    score_details jsonb,
    criteria_prompt_code character varying(100),
    criteria_prompt_version integer,
    level2_prompt_code character varying(100),
    level2_prompt_version integer,
    scoring_prompt_code character varying(100),
    scoring_prompt_version integer,
    scoring_provider character varying(50) DEFAULT 'openai'::character varying NOT NULL,
    cv_facts_json jsonb,
    match_results_json jsonb,
    llm_match_results_json jsonb,
    det_final_score integer,
    det_score_json jsonb,
    scoring_method character varying(50),
    CONSTRAINT application_scores_score_certifications_check CHECK (((score_certifications >= 0) AND (score_certifications <= 100))),
    CONSTRAINT application_scores_score_domain_knowledge_check CHECK (((score_domain_knowledge >= 0) AND (score_domain_knowledge <= 100))),
    CONSTRAINT application_scores_score_education_check CHECK (((score_education >= 0) AND (score_education <= 100))),
    CONSTRAINT application_scores_score_experience_check CHECK (((score_experience >= 0) AND (score_experience <= 100))),
    CONSTRAINT application_scores_score_other_check CHECK (((score_other >= 0) AND (score_other <= 100))),
    CONSTRAINT application_scores_score_skills_check CHECK (((score_skills >= 0) AND (score_skills <= 100))),
    CONSTRAINT application_scores_score_soft_skills_check CHECK (((score_soft_skills >= 0) AND (score_soft_skills <= 100))),
    CONSTRAINT application_scores_scoring_method_check CHECK (((scoring_method IS NULL) OR ((scoring_method)::text ~ '^[a-z][a-z0-9_]*_v[0-9]+$'::text)))
);


--
-- Name: COLUMN application_scores.cv_facts_json; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.cv_facts_json IS 'Scoring V2 Phase 2A — Layer 1 CVFacts. Structured CV evidence (skills, experience, education, certifications, soft-skill signals, domain signals) extracted before the LLM call. Schema version stored in _schema key. NULL for pre-Phase-2A rows.';


--
-- Name: COLUMN application_scores.match_results_json; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.match_results_json IS 'Scoring V2 Phase 2A — Layer 2 MatchResult. Per-criterion match results, gap candidates, and algorithmic score baseline computed from CVFacts and job criteria. Used in Phase 3 to bound LLM scoring deltas. Schema version stored in _schema key. NULL for pre-Phase-2A rows.';


--
-- Name: COLUMN application_scores.llm_match_results_json; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.llm_match_results_json IS 'Scoring V2 D-01 — Layer 3 LLMMatchResult. Per-criterion LLM assessments (MATCHED/PARTIAL/ABSENT, confidence, evidence, match_type, criterion_class, risk_flags) from LLMCriteriaMapper. One entry per job criterion per application. Schema version in _schema key (llm_match_result_v1). NULL when SCORING_V2_LLM_MAPPING env flag is off or pre-D-01 rows.';


--
-- Name: COLUMN application_scores.det_final_score; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.det_final_score IS 'F-01 deterministic score (0–100). Silent-mode calibration only — does NOT affect final_score or decision. NULL when det engine disabled or not yet run.';


--
-- Name: COLUMN application_scores.det_score_json; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.det_score_json IS 'F-01 deterministic scoring explainability payload (_schema: det_score_v1). Contains per-dimension breakdown, per-criterion credits, and overqualification flags. Silent-mode calibration only.';


--
-- Name: COLUMN application_scores.scoring_method; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.application_scores.scoring_method IS 'P0-01: methodology that produced this score. deterministic_v1 | legacy_llm_v1 | gatekeeper_local_v1 | legacy_llm_det_backfill_v1 | NULL (unclassifiable history).';


--
-- Name: application_workflow_history; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.application_workflow_history (
    history_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    changed_by uuid,
    changed_by_name character varying(255),
    from_status character varying(50),
    to_status character varying(50) NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    is_advanced_move boolean DEFAULT false NOT NULL
);


--
-- Name: applications; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.applications (
    application_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    candidate_name character varying(255) NOT NULL,
    candidate_email character varying(255),
    submission_source character varying(30) DEFAULT 'email'::character varying NOT NULL,
    processing_status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    decision character varying(20),
    qualified_threshold_used smallint,
    partial_threshold_used smallint,
    applied_at timestamp with time zone DEFAULT now() NOT NULL,
    scored_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    gatekeeper_passed boolean,
    candidate_phone character varying(50),
    linkedin_url text,
    cover_letter text,
    source_channel character varying(30) DEFAULT 'manual_upload'::character varying NOT NULL,
    intake_status character varying(20) DEFAULT 'received'::character varying NOT NULL,
    original_email_subject text,
    original_email_body text,
    source_email character varying(255),
    cv_file_path text,
    error_reason text,
    scoring_batch_id uuid,
    queued_at timestamp with time zone,
    evaluation_stage smallint,
    evaluation_exit_reason text,
    candidate_email_from_cv character varying(255),
    candidate_phone_from_cv character varying(50),
    email_sender_address character varying(255),
    confirmation_email_recipient character varying(255),
    semantic_similarity_score numeric(5,2),
    semantic_threshold numeric(5,2),
    matched_skills jsonb,
    missing_skills jsonb,
    matched_keywords jsonb,
    missing_keywords jsonb,
    gatekeeper_reason_json jsonb,
    duplicate_status character varying(30) DEFAULT 'not_duplicate'::character varying NOT NULL,
    duplicate_reference_application_id uuid,
    duplicate_similarity_score numeric(5,2),
    duplicate_reason text,
    duplicate_checked_at timestamp with time zone,
    submitted_by_user_id uuid,
    submitted_by_name text,
    submitted_by_email text,
    security_check_status text,
    security_risk_level text,
    security_risk_score integer,
    security_reason_codes text[],
    security_detected_patterns text[],
    security_checked_at timestamp with time zone,
    security_detected_snippets text[],
    stopped_reason character varying(50),
    workflow_status character varying(50) DEFAULT 'new'::character varying,
    recruiter_notes text,
    assigned_user_id uuid,
    assigned_at timestamp with time zone,
    assigned_by uuid,
    is_talent_pool boolean DEFAULT false NOT NULL,
    preferred_contact_email character varying(255),
    preferred_contact_source character varying(30),
    preferred_contact_confidence numeric(3,2),
    preferred_contact_locked boolean DEFAULT false NOT NULL,
    gender_value character varying(10) DEFAULT 'unknown'::character varying,
    gender_confidence numeric(3,2) DEFAULT 0.00,
    gender_basis character varying(30) DEFAULT 'unknown'::character varying,
    gender_source character varying(20) DEFAULT 'cv_text'::character varying,
    gender_used_for_scoring boolean DEFAULT false,
    CONSTRAINT applications_decision_check CHECK (((decision)::text = ANY (ARRAY[('qualified'::character varying)::text, ('partial'::character varying)::text, ('rejected'::character varying)::text, ('needs_verification'::character varying)::text]))),
    CONSTRAINT applications_duplicate_status_check CHECK (((duplicate_status)::text = ANY (ARRAY[('not_duplicate'::character varying)::text, ('possible_duplicate'::character varying)::text, ('exact_duplicate'::character varying)::text]))),
    CONSTRAINT applications_intake_status_check CHECK (((intake_status)::text = ANY (ARRAY[('received'::character varying)::text, ('queued'::character varying)::text, ('processing'::character varying)::text, ('scored'::character varying)::text, ('failed'::character varying)::text, ('unassigned'::character varying)::text]))),
    CONSTRAINT applications_processing_status_check CHECK (((processing_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('queued'::character varying)::text, ('processing'::character varying)::text, ('scored'::character varying)::text, ('ai_scored'::character varying)::text, ('failed'::character varying)::text]))),
    CONSTRAINT applications_security_check_status_check CHECK ((security_check_status = ANY (ARRAY['passed'::text, 'warning'::text, 'blocked'::text]))),
    CONSTRAINT applications_security_risk_level_check CHECK ((security_risk_level = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT applications_source_channel_check CHECK (((source_channel)::text = ANY (ARRAY[('public_page'::character varying)::text, ('dedicated_alias'::character varying)::text, ('central_forward'::character varying)::text, ('manual_upload'::character varying)::text, ('api'::character varying)::text]))),
    CONSTRAINT applications_stopped_reason_check CHECK (((stopped_reason)::text = ANY (ARRAY[('security_blocked'::character varying)::text, ('extraction_failed'::character varying)::text, ('processing_error'::character varying)::text, ('duplicate_blocked'::character varying)::text, ('scoring_failed'::character varying)::text, ('evaluation_unsupported'::character varying)::text, ('other'::character varying)::text]))),
    CONSTRAINT applications_submission_source_check CHECK (((submission_source)::text = ANY (ARRAY[('manual_upload'::character varying)::text, ('email_forwarding'::character varying)::text, ('platform_email'::character varying)::text, ('public_apply'::character varying)::text, ('bulk_excel_upload'::character varying)::text])))
);


--
-- Name: COLUMN applications.candidate_phone; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.candidate_phone IS 'Phone number provided by candidate on public apply form.';


--
-- Name: COLUMN applications.cover_letter; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.cover_letter IS 'Cover letter text provided by candidate on public apply form.';


--
-- Name: COLUMN applications.semantic_similarity_score; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.semantic_similarity_score IS 'Level 1: cosine similarity % between CV and criteria text';


--
-- Name: COLUMN applications.semantic_threshold; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.semantic_threshold IS 'Level 1: threshold % used when this application was scored';


--
-- Name: COLUMN applications.matched_skills; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.matched_skills IS 'Level 1: skills/certifications matched in CV';


--
-- Name: COLUMN applications.missing_skills; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.missing_skills IS 'Level 1: skills/certifications not found in CV';


--
-- Name: COLUMN applications.matched_keywords; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.matched_keywords IS 'Level 1: broader criteria keywords matched in CV';


--
-- Name: COLUMN applications.missing_keywords; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.missing_keywords IS 'Level 1: broader criteria keywords not found in CV';


--
-- Name: COLUMN applications.gatekeeper_reason_json; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gatekeeper_reason_json IS 'Level 1: full diagnostic object (scores, counts, override reason)';


--
-- Name: COLUMN applications.submitted_by_user_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.submitted_by_user_id IS 'User who manually uploaded this CV. NULL for email/API intake.';


--
-- Name: COLUMN applications.submitted_by_name; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.submitted_by_name IS 'Display name of the uploading user (denormalised for display without JOIN).';


--
-- Name: COLUMN applications.submitted_by_email; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.submitted_by_email IS 'Email of the uploading user (denormalised for display).';


--
-- Name: COLUMN applications.gender_value; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gender_value IS 'Inferred gender: male | female | unknown. Metadata only — never influences scoring.';


--
-- Name: COLUMN applications.gender_confidence; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gender_confidence IS '0.0–1.0 confidence in the inference. 0.0 = no signal found.';


--
-- Name: COLUMN applications.gender_basis; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gender_basis IS 'Signal used: title | pronoun | explicit_cv_text | unknown.';


--
-- Name: COLUMN applications.gender_source; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gender_source IS 'Always cv_text — images and external sources are forbidden.';


--
-- Name: COLUMN applications.gender_used_for_scoring; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.applications.gender_used_for_scoring IS 'Always FALSE. Enforced in application code and documented here.';


--
-- Name: CONSTRAINT applications_submission_source_check ON applications; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON CONSTRAINT applications_submission_source_check ON cv_analyzer.applications IS 'Allowed intake sources. bulk_excel_upload added in migration 099 for E-01.';


--
-- Name: audit_logs; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.audit_logs (
    log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    user_id uuid,
    user_email character varying(255),
    action character varying(100) NOT NULL,
    resource_type character varying(100),
    resource_id text,
    details jsonb,
    ip_address character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: bulk_upload_batches; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.bulk_upload_batches (
    batch_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    created_by uuid,
    batch_status character varying(20) DEFAULT 'created'::character varying NOT NULL,
    original_filename character varying(500),
    source_file_path character varying(1000),
    zip_file_path character varying(1000),
    row_count integer DEFAULT 0 NOT NULL,
    valid_row_count integer DEFAULT 0 NOT NULL,
    warning_row_count integer DEFAULT 0 NOT NULL,
    error_row_count integer DEFAULT 0 NOT NULL,
    imported_row_count integer DEFAULT 0 NOT NULL,
    skipped_row_count integer DEFAULT 0 NOT NULL,
    failed_row_count integer DEFAULT 0 NOT NULL,
    completed_row_count integer DEFAULT 0 NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    validated_at timestamp with time zone,
    imported_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    processing_completed_at timestamp with time zone,
    zip_file_metadata jsonb,
    excel_column_mapping jsonb,
    unmatched_cv_count integer DEFAULT 0 NOT NULL,
    queued_row_count integer DEFAULT 0 NOT NULL,
    processing_row_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT bulk_upload_batches_status_check CHECK (((batch_status)::text = ANY (ARRAY[('created'::character varying)::text, ('uploaded'::character varying)::text, ('validating'::character varying)::text, ('validated'::character varying)::text, ('importing'::character varying)::text, ('imported'::character varying)::text, ('processing'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: TABLE bulk_upload_batches; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.bulk_upload_batches IS 'One record per bulk upload session (Excel sheet + optional CV ZIP).';


--
-- Name: COLUMN bulk_upload_batches.batch_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.batch_id IS 'Primary key — shared with the caller as a handle for subsequent operations.';


--
-- Name: COLUMN bulk_upload_batches.batch_status; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.batch_status IS 'Lifecycle: created→uploaded→validating→validated→importing→imported→processing→completed|failed.';


--
-- Name: COLUMN bulk_upload_batches.row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.row_count IS 'Total rows parsed from the Excel sheet. 0 until parsing completes.';


--
-- Name: COLUMN bulk_upload_batches.valid_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.valid_row_count IS 'Rows with validation_status=ready. Maintained by update_bulk_upload_batch_summary.';


--
-- Name: COLUMN bulk_upload_batches.warning_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.warning_row_count IS 'Rows with validation_status=warning. Can still be imported.';


--
-- Name: COLUMN bulk_upload_batches.error_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.error_row_count IS 'Rows with validation_status=error. Blocked from import.';


--
-- Name: COLUMN bulk_upload_batches.completed_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.completed_row_count IS 'Rows whose applications have reached processing_status=completed.';


--
-- Name: COLUMN bulk_upload_batches.zip_file_metadata; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.zip_file_metadata IS 'JSONB array of file records from the uploaded ZIP: [{filename, normalized_name, stem, extension, is_supported, size_bytes, zip_path}]. Set when ZIP is uploaded.';


--
-- Name: COLUMN bulk_upload_batches.excel_column_mapping; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.excel_column_mapping IS 'JSONB: detected Excel header-to-field mapping {cv_filename_col, name_col, email_col, phone_col, extra_cols}. Set when Excel is validated.';


--
-- Name: COLUMN bulk_upload_batches.unmatched_cv_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.unmatched_cv_count IS 'Number of CV files present in the ZIP but not referenced by any Excel row. Set after validation.';


--
-- Name: COLUMN bulk_upload_batches.queued_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.queued_row_count IS 'Rows whose Celery scoring task has been enqueued (processing_status=queued).';


--
-- Name: COLUMN bulk_upload_batches.processing_row_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_batches.processing_row_count IS 'Rows actively being scored by a Celery worker (processing_status=processing).';


--
-- Name: bulk_upload_rows; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.bulk_upload_rows (
    row_id uuid DEFAULT gen_random_uuid() NOT NULL,
    batch_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    row_number integer NOT NULL,
    validation_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    import_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    processing_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    candidate_data jsonb,
    knockout_answers jsonb,
    matched_cv_filename character varying(500),
    application_id uuid,
    validation_warnings jsonb,
    validation_errors jsonb,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    validated_at timestamp with time zone,
    imported_at timestamp with time zone,
    processing_started_at timestamp with time zone,
    processing_completed_at timestamp with time zone,
    processing_failure_reason text,
    CONSTRAINT bulk_upload_rows_import_status_check CHECK (((import_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('imported'::character varying)::text, ('skipped'::character varying)::text, ('failed'::character varying)::text]))),
    CONSTRAINT bulk_upload_rows_processing_status_check CHECK (((processing_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('queued'::character varying)::text, ('processing'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text]))),
    CONSTRAINT bulk_upload_rows_row_number_positive CHECK ((row_number >= 1)),
    CONSTRAINT bulk_upload_rows_validation_status_check CHECK (((validation_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('ready'::character varying)::text, ('warning'::character varying)::text, ('error'::character varying)::text])))
);


--
-- Name: TABLE bulk_upload_rows; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.bulk_upload_rows IS 'One record per data row in a bulk upload Excel sheet.';


--
-- Name: COLUMN bulk_upload_rows.row_number; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.row_number IS '1-indexed position of this row in the Excel data sheet (row 1 = first data row after the header).';


--
-- Name: COLUMN bulk_upload_rows.candidate_data; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.candidate_data IS 'JSONB: candidate fields parsed from the Excel row (name, email, phone, etc.).';


--
-- Name: COLUMN bulk_upload_rows.knockout_answers; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.knockout_answers IS 'JSONB: {question_id → answer_value} map extracted from the Excel answers columns.';


--
-- Name: COLUMN bulk_upload_rows.matched_cv_filename; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.matched_cv_filename IS 'File name inside the companion ZIP that was matched to this Excel row.';


--
-- Name: COLUMN bulk_upload_rows.application_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.application_id IS 'Set after successful import. NULL until the row is imported.';


--
-- Name: COLUMN bulk_upload_rows.validation_warnings; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.validation_warnings IS 'JSONB array of human-readable warnings. Row can still be imported.';


--
-- Name: COLUMN bulk_upload_rows.validation_errors; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.validation_errors IS 'JSONB array of human-readable errors. Row is blocked from import.';


--
-- Name: COLUMN bulk_upload_rows.processing_failure_reason; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.bulk_upload_rows.processing_failure_reason IS 'Failure reason from the scoring pipeline, populated from applications.stopped_reason (e.g. extraction_failed, duplicate_rejected, ai_processing_failed, scoring_failed, unexpected_error). NULL until processing fails.';


--
-- Name: candidate_approvals; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_approvals (
    approval_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    approval_stage character varying(100) NOT NULL,
    stage_order smallint DEFAULT 0 NOT NULL,
    approver_id uuid,
    decision character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    comment text,
    decided_at timestamp with time zone,
    requested_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_approvals_decision_check CHECK (((decision)::text = ANY (ARRAY[('pending'::character varying)::text, ('approved'::character varying)::text, ('rejected'::character varying)::text, ('needs_revision'::character varying)::text])))
);


--
-- Name: candidate_comments; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_comments (
    comment_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    comment_text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_comments_comment_text_check CHECK (((char_length(comment_text) >= 1) AND (char_length(comment_text) <= 2000)))
);


--
-- Name: candidate_communications; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_communications (
    communication_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    application_id uuid NOT NULL,
    candidate_email text,
    candidate_name text,
    channel text DEFAULT 'email'::text NOT NULL,
    direction text DEFAULT 'outbound'::text NOT NULL,
    subject text,
    body text,
    status text DEFAULT 'draft'::text NOT NULL,
    template_id uuid,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    error_message text,
    event_type character varying(50),
    automation_rule_id uuid,
    is_automated boolean DEFAULT false NOT NULL
);


--
-- Name: candidate_interview_feedback; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_interview_feedback (
    feedback_id uuid DEFAULT gen_random_uuid() NOT NULL,
    interview_id uuid NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    reviewer_id uuid NOT NULL,
    overall_rating smallint,
    recommendation character varying(20),
    scorecard jsonb DEFAULT '{}'::jsonb NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_interview_feedback_overall_rating_check CHECK (((overall_rating >= 1) AND (overall_rating <= 5))),
    CONSTRAINT candidate_interview_feedback_recommendation_check CHECK (((recommendation)::text = ANY (ARRAY[('strong_yes'::character varying)::text, ('yes'::character varying)::text, ('neutral'::character varying)::text, ('no'::character varying)::text, ('strong_no'::character varying)::text])))
);


--
-- Name: candidate_interviews; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_interviews (
    interview_id uuid DEFAULT gen_random_uuid() NOT NULL,
    application_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    interview_type character varying(30) NOT NULL,
    scheduled_at timestamp with time zone,
    duration_min integer,
    location text,
    interviewers text[] DEFAULT '{}'::text[],
    status character varying(20) DEFAULT 'scheduled'::character varying NOT NULL,
    notes text,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_interviews_interview_type_check CHECK (((interview_type)::text = ANY (ARRAY[('phone_screen'::character varying)::text, ('technical'::character varying)::text, ('hr'::character varying)::text, ('panel'::character varying)::text, ('final'::character varying)::text, ('other'::character varying)::text]))),
    CONSTRAINT candidate_interviews_status_check CHECK (((status)::text = ANY (ARRAY[('scheduled'::character varying)::text, ('completed'::character varying)::text, ('cancelled'::character varying)::text, ('no_show'::character varying)::text])))
);


--
-- Name: candidate_message_templates; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_message_templates (
    template_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    name text NOT NULL,
    category text DEFAULT 'general'::text NOT NULL,
    subject text NOT NULL,
    body text NOT NULL,
    language text DEFAULT 'en'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: candidate_saved_views; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_saved_views (
    saved_view_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    filters_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    visibility_type character varying(20) DEFAULT 'private'::character varying NOT NULL,
    created_by uuid,
    CONSTRAINT candidate_saved_views_name_check CHECK (((char_length(name) >= 1) AND (char_length(name) <= 100))),
    CONSTRAINT candidate_saved_views_visibility_type_check CHECK (((visibility_type)::text = ANY (ARRAY[('private'::character varying)::text, ('tenant_shared'::character varying)::text])))
);


--
-- Name: candidate_tags; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.candidate_tags (
    tag_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    tag_name character varying(100) NOT NULL,
    color character varying(10),
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT candidate_tags_tag_name_check CHECK ((((tag_name)::text ~ '^\S+.*\S+$'::text) OR ((tag_name)::text ~ '^\S+$'::text)))
);


--
-- Name: client_organizations; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.client_organizations (
    client_organization_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    organization_name character varying(255) NOT NULL,
    industry character varying(100),
    contact_person character varying(255),
    email character varying(255),
    phone character varying(50),
    logo_url text,
    notes text,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT client_organizations_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('inactive'::character varying)::text])))
);


--
-- Name: TABLE client_organizations; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.client_organizations IS 'Client organisations managed by agency/individual_recruiter tenants. Direct organisation tenants do not use this table.';


--
-- Name: communication_automation_rules; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.communication_automation_rules (
    rule_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    event_type character varying(50) NOT NULL,
    category character varying(30) DEFAULT 'workflow'::character varying NOT NULL,
    template_id uuid,
    mode character varying(20) DEFAULT 'draft_only'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    delay_minutes integer DEFAULT 0 NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_automation_mode CHECK (((mode)::text = ANY (ARRAY[('disabled'::character varying)::text, ('draft_only'::character varying)::text, ('auto_send'::character varying)::text])))
);


--
-- Name: duplicate_application_logs; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.duplicate_application_logs (
    log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    duplicate_email text,
    duplicate_name text,
    attachment_hash text,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    original_application_id uuid,
    email_message_id text,
    raw_filename text,
    notes text,
    duplicate_file_path text,
    duplicate_original_filename text,
    duplicate_content_type text,
    duplicate_file_size_bytes integer,
    source text,
    submitted_by_user_id uuid,
    submitted_by_name text,
    submitted_by_email text,
    duplicate_reason text,
    duplicate_similarity_score numeric(5,2),
    duplicate_application_id uuid
);


--
-- Name: email_ingest_log; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.email_ingest_log (
    log_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid,
    job_id uuid,
    application_id uuid,
    message_id text,
    sender_email character varying(255),
    recipient_email character varying(255),
    subject text,
    ingestion_mode character varying(30),
    status character varying(30) DEFAULT 'received'::character varying NOT NULL,
    error_message text,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    attachment_hash character varying(64),
    attachment_name character varying(255),
    job_code_source character varying(50),
    processing_started_at timestamp with time zone,
    scoring_enqueued_at timestamp with time zone,
    CONSTRAINT email_ingest_log_ingestion_mode_check CHECK ((((ingestion_mode)::text = ANY (ARRAY[('platform_email'::character varying)::text, ('forwarding'::character varying)::text, ('unknown'::character varying)::text])) OR (ingestion_mode IS NULL))),
    CONSTRAINT email_ingest_log_status_check CHECK (((status)::text = ANY (ARRAY[('received'::character varying)::text, ('processing'::character varying)::text, ('scored'::character varying)::text, ('duplicate'::character varying)::text, ('failed'::character varying)::text, ('skipped'::character varying)::text, ('unassigned'::character varying)::text])))
);


--
-- Name: job_campaigns; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.job_campaigns (
    campaign_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    client_organization_id uuid,
    name character varying(255) NOT NULL,
    description text,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    start_date date,
    end_date date,
    target_hire_count integer,
    campaign_owner_id uuid,
    notes text,
    public_title character varying(255),
    is_publicly_listed boolean DEFAULT false NOT NULL,
    CONSTRAINT job_campaigns_status_check CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('active'::character varying)::text, ('on_hold'::character varying)::text, ('closed'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT job_campaigns_target_hire_check CHECK (((target_hire_count IS NULL) OR (target_hire_count > 0)))
);


--
-- Name: TABLE job_campaigns; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.job_campaigns IS 'Optional grouping layer over jobs (filtering/reporting only — never a permission boundary). A campaign belongs to one tenant and at most one client organisation (NULL client_organization_id = public/shared campaign).';


--
-- Name: COLUMN job_campaigns.client_organization_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.client_organization_id IS 'The single client this campaign belongs to. NULL = public/shared campaign (may contain only jobs that are themselves public, i.e. jobs.client_organization_id IS NULL).';


--
-- Name: COLUMN job_campaigns.status; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.status IS 'Business lifecycle: draft (setup), active (running), on_hold (paused), closed (completed normally), cancelled (stopped before completion). Status NEVER cascades to linked jobs — jobs remain independently operable.';


--
-- Name: COLUMN job_campaigns.start_date; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.start_date IS 'Campaign window start — informational, system never auto-activates on this date.';


--
-- Name: COLUMN job_campaigns.end_date; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.end_date IS 'Campaign window end — informational, system never auto-closes on this date.';


--
-- Name: COLUMN job_campaigns.target_hire_count; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.target_hire_count IS 'Total headcount goal across all campaign jobs. Drives fill-rate reporting. System never auto-closes campaign when this target is reached.';


--
-- Name: COLUMN job_campaigns.campaign_owner_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.campaign_owner_id IS 'The recruiter / manager responsible for this campaign. May differ from created_by.';


--
-- Name: COLUMN job_campaigns.public_title; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.public_title IS 'External-facing title for future public campaign listing page. NULL = use internal name field.';


--
-- Name: COLUMN job_campaigns.is_publicly_listed; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_campaigns.is_publicly_listed IS 'Architecture placeholder for future public campaign listing feature. Has no effect on current access control.';


--
-- Name: job_criteria; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.job_criteria (
    criteria_id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id uuid NOT NULL,
    skills text[] DEFAULT '{}'::text[] NOT NULL,
    experience text[] DEFAULT '{}'::text[] NOT NULL,
    education text[] DEFAULT '{}'::text[] NOT NULL,
    certifications text[] DEFAULT '{}'::text[] NOT NULL,
    soft_skills text[] DEFAULT '{}'::text[] NOT NULL,
    domain_knowledge text[] DEFAULT '{}'::text[] NOT NULL,
    other_requirements text[] DEFAULT '{}'::text[] NOT NULL,
    weight_skills smallint DEFAULT 30 NOT NULL,
    weight_experience smallint DEFAULT 25 NOT NULL,
    weight_education smallint DEFAULT 15 NOT NULL,
    weight_certifications smallint DEFAULT 10 NOT NULL,
    weight_soft_skills smallint DEFAULT 10 NOT NULL,
    weight_domain_knowledge smallint DEFAULT 5 NOT NULL,
    weight_other smallint DEFAULT 5 NOT NULL,
    ai_model character varying(100),
    ai_generated_at timestamp with time zone,
    last_edited_by uuid,
    last_edited_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    criteria_extraction_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    criteria_extraction_error text,
    criteria_extracted_at timestamp with time zone,
    analysis_json jsonb,
    original_analysis_json jsonb,
    criteria_extraction_retry_count integer DEFAULT 0 NOT NULL,
    criteria_last_failed_description_hash text,
    criteria_last_failed_at timestamp with time zone,
    requirements_schema_version smallint,
    requirements_revision integer DEFAULT 0 NOT NULL,
    requirements_retired_item_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    requirements_extraction_token uuid,
    requirements_extraction_started_at timestamp with time zone,
    CONSTRAINT job_criteria_criteria_extraction_status_check CHECK (((criteria_extraction_status)::text = ANY (ARRAY[('pending'::character varying)::text, ('processing'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text, ('insufficient'::character varying)::text, ('blocked'::character varying)::text]))),
    CONSTRAINT job_criteria_requirements_retired_item_ids_check CHECK ((jsonb_typeof(requirements_retired_item_ids) = 'array'::text)),
    CONSTRAINT job_criteria_requirements_revision_check CHECK ((requirements_revision >= 0)),
    CONSTRAINT job_criteria_requirements_schema_version_check CHECK (((requirements_schema_version IS NULL) OR (requirements_schema_version = 2))),
    CONSTRAINT weights_sum_100 CHECK (((((((((weight_skills + weight_experience) + weight_education) + weight_certifications) + weight_soft_skills) + weight_domain_knowledge) + weight_other) = 100) OR ((requirements_schema_version IS NOT NULL) AND (((((((weight_skills + weight_experience) + weight_education) + weight_certifications) + weight_soft_skills) + weight_domain_knowledge) + weight_other) = 0))))
);


--
-- Name: COLUMN job_criteria.requirements_schema_version; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_criteria.requirements_schema_version IS 'NULL = legacy job analysis. 2 = requirements-v2 analysis. Set when the job is created; never used to convert an existing job.';


--
-- Name: COLUMN job_criteria.requirements_revision; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_criteria.requirements_revision IS 'Optimistic-concurrency token of the requirements-v2 document; incremented by every write through the editing API.';


--
-- Name: COLUMN job_criteria.requirements_retired_item_ids; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.job_criteria.requirements_retired_item_ids IS 'Ids of requirements-v2 items removed by a recruiter save. Append-only; ids are never reused.';


--
-- Name: job_knockout_questions; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.job_knockout_questions (
    question_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    job_id uuid NOT NULL,
    question_text text NOT NULL,
    question_type character varying(50) NOT NULL,
    options jsonb,
    is_required boolean DEFAULT true,
    disqualifying_answer text,
    display_order integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    passing_criteria jsonb
);


--
-- Name: jobs; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.jobs (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    created_by uuid NOT NULL,
    title character varying(255) NOT NULL,
    department character varying(255),
    description text NOT NULL,
    platform_email character varying(255),
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    qualified_threshold smallint,
    partial_threshold smallint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    job_code character varying(50),
    forwarding_enabled boolean DEFAULT true NOT NULL,
    alias_enabled boolean DEFAULT true NOT NULL,
    location character varying(255),
    job_type character varying(100),
    duration character varying(100),
    alias_status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    receive_cv_via_forwarding_email boolean DEFAULT true NOT NULL,
    receive_cv_via_platform_email boolean DEFAULT true NOT NULL,
    restrict_forwarding_sender_to_tenant_email boolean DEFAULT false NOT NULL,
    enable_ai_comparison boolean DEFAULT false NOT NULL,
    send_confirmation_to_cv_email_for_upload boolean DEFAULT false NOT NULL,
    send_confirmation_to_cv_email_for_forwarding boolean DEFAULT false NOT NULL,
    send_confirmation_to_sender_for_forwarding boolean DEFAULT true NOT NULL,
    send_confirmation_to_cv_email_for_platform_email boolean DEFAULT true NOT NULL,
    send_intake_notifications boolean DEFAULT true NOT NULL,
    notify_recruiter_on_inactive boolean DEFAULT false NOT NULL,
    recruiter_alert_email character varying(255),
    experience_level character varying(50),
    work_mode character varying(50),
    application_deadline date,
    vacancies_count integer DEFAULT 1,
    updated_by uuid,
    max_applications integer,
    auto_close_when_limit_reached boolean DEFAULT true NOT NULL,
    client_organization_id uuid,
    campaign_id uuid,
    allow_advanced_workflow_move boolean DEFAULT false NOT NULL,
    CONSTRAINT jobs_alias_status_check CHECK (((alias_status)::text = ANY (ARRAY[('active'::character varying)::text, ('paused'::character varying)::text, ('expired'::character varying)::text]))),
    CONSTRAINT jobs_partial_threshold_check CHECK (((partial_threshold >= 0) AND (partial_threshold <= 100))),
    CONSTRAINT jobs_qualified_threshold_check CHECK (((qualified_threshold >= 0) AND (qualified_threshold <= 100))),
    CONSTRAINT jobs_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('closed'::character varying)::text, ('draft'::character varying)::text])))
);


--
-- Name: COLUMN jobs.max_applications; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.jobs.max_applications IS 'Maximum number of valid (non-duplicate, non-failed) applications accepted for this job. NULL = unlimited. Enforced across all intake paths: manual upload, email forwarding, platform email alias, public apply form. Added in migration 027; universal enforcement added in migration 034.';


--
-- Name: COLUMN jobs.auto_close_when_limit_reached; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.jobs.auto_close_when_limit_reached IS 'When max_applications is reached, automatically set job status to closed.';


--
-- Name: COLUMN jobs.client_organization_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.jobs.client_organization_id IS 'Mandatory for agency/individual_recruiter tenants; NULL for direct organisation tenants.';


--
-- Name: COLUMN jobs.campaign_id; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.jobs.campaign_id IS 'Optional campaign grouping. NULL = standalone job. ON DELETE SET NULL: removing a campaign detaches its jobs, never deletes them. Must align with the campaign client (enforced in the application layer).';


--
-- Name: notification_log; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.notification_log (
    notification_id uuid DEFAULT gen_random_uuid() NOT NULL,
    notification_type character varying(60) NOT NULL,
    intake_status character varying(60) NOT NULL,
    recipient_email character varying(255) NOT NULL,
    recipient_type character varying(20) DEFAULT 'candidate'::character varying NOT NULL,
    tenant_id uuid,
    job_id uuid,
    application_id uuid,
    intake_log_id uuid,
    duplicate_log_id uuid,
    details jsonb,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    error_message text,
    sent_at timestamp with time zone,
    retry_count smallint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT notification_log_recipient_type_check CHECK (((recipient_type)::text = ANY (ARRAY[('candidate'::character varying)::text, ('recruiter'::character varying)::text]))),
    CONSTRAINT notification_log_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('sent'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: plan_features; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.plan_features (
    feature_id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_id uuid NOT NULL,
    feature_key character varying(100) NOT NULL,
    feature_name character varying(255) NOT NULL,
    description text,
    value_type character varying(20) NOT NULL,
    value_boolean boolean,
    value_number numeric,
    value_text character varying(500),
    display_order integer DEFAULT 0 NOT NULL,
    CONSTRAINT plan_features_value_type_check CHECK (((value_type)::text = ANY (ARRAY[('boolean'::character varying)::text, ('number'::character varying)::text, ('text'::character varying)::text])))
);


--
-- Name: platform_secrets; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.platform_secrets (
    key character varying(100) NOT NULL,
    value text,
    masked_value character varying(60),
    description text,
    category character varying(50) DEFAULT 'general'::character varying NOT NULL,
    is_critical boolean DEFAULT false NOT NULL,
    has_value boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    updated_by character varying(255),
    updated_by_email character varying(255)
);


--
-- Name: subscription_plans; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.subscription_plans (
    plan_id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_code character varying(50) NOT NULL,
    plan_name character varying(100) NOT NULL,
    description text,
    monthly_price numeric(10,2) DEFAULT 0 NOT NULL,
    yearly_price numeric(10,2) DEFAULT 0 NOT NULL,
    currency character varying(10) DEFAULT 'USD'::character varying NOT NULL,
    trial_days integer DEFAULT 14 NOT NULL,
    max_campaigns integer DEFAULT 5 NOT NULL,
    max_processed_cvs_per_month integer DEFAULT 500 NOT NULL,
    max_users integer DEFAULT 3 NOT NULL,
    api_access boolean DEFAULT false NOT NULL,
    advanced_analytics boolean DEFAULT false NOT NULL,
    priority_support boolean DEFAULT false NOT NULL,
    custom_ai_prompts boolean DEFAULT false NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    display_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT subscription_plans_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('inactive'::character varying)::text])))
);


--
-- Name: system_config; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.system_config (
    key character varying(100) NOT NULL,
    value text NOT NULL,
    description text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    type character varying(20) DEFAULT 'string'::character varying NOT NULL,
    category character varying(50) DEFAULT 'general'::character varying NOT NULL,
    editable boolean DEFAULT true NOT NULL,
    updated_by uuid,
    CONSTRAINT system_config_category_check CHECK (((category)::text = ANY (ARRAY[('scoring'::character varying)::text, ('ai'::character varying)::text, ('email'::character varying)::text, ('queue'::character varying)::text, ('subscription'::character varying)::text, ('security'::character varying)::text, ('general'::character varying)::text]))),
    CONSTRAINT system_config_type_check CHECK (((type)::text = ANY (ARRAY[('string'::character varying)::text, ('number'::character varying)::text, ('boolean'::character varying)::text, ('json'::character varying)::text])))
);


--
-- Name: tenant_feature_audit; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.tenant_feature_audit (
    audit_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    feature_code character varying(64) NOT NULL,
    old_value boolean,
    new_value boolean NOT NULL,
    changed_by uuid NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tenant_features; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.tenant_features (
    tenant_feature_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    feature_code character varying(64) NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tenant_usage_monthly; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.tenant_usage_monthly (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    usage_month date NOT NULL,
    cvs_processed integer DEFAULT 0 NOT NULL,
    ai_reports_generated integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE tenant_usage_monthly; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON TABLE cv_analyzer.tenant_usage_monthly IS 'Calendar-month CV processing and AI report usage per tenant. Used for Fair Usage Policy (soft/hard limit) enforcement and admin reporting. usage_month is always the first day of the month (DATE_TRUNC(''month'', current_date)).';


--
-- Name: tenant_workflow_policies; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.tenant_workflow_policies (
    policy_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    policies jsonb DEFAULT '{"allow_bulk_reject": true, "require_rejection_reason": false, "required_approval_stages": [], "require_interview_feedback": false, "require_approval_before_hire": false, "require_interview_before_offer": false}'::jsonb NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: tenants; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.tenants (
    tenant_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    email_domain character varying(253) NOT NULL,
    plan character varying(50),
    max_users integer DEFAULT 3 NOT NULL,
    max_jobs integer DEFAULT 10 NOT NULL,
    cv_ingestion_mode character varying(20) DEFAULT 'platform_email'::character varying NOT NULL,
    forwarding_email character varying(255),
    qualified_threshold smallint,
    partial_threshold smallint,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    subscription_status character varying(50) DEFAULT 'trial'::character varying,
    trial_start_at timestamp with time zone,
    trial_end_at timestamp with time zone,
    subscription_started_at timestamp with time zone,
    subscription_ends_at timestamp with time zone,
    receive_cv_via_forwarding_email boolean DEFAULT false NOT NULL,
    receive_cv_via_platform_email boolean DEFAULT true NOT NULL,
    restrict_forwarding_sender_to_tenant_email boolean DEFAULT false NOT NULL,
    tenant_type character varying(30) DEFAULT 'organization'::character varying NOT NULL,
    monthly_cv_processing_soft_limit integer,
    monthly_cv_processing_hard_limit integer,
    max_clients integer,
    api_access_enabled boolean DEFAULT false NOT NULL,
    branding_level character varying(20) DEFAULT 'none'::character varying NOT NULL,
    billing_cycle character varying(20),
    pending_plan character varying(30),
    job_application_controls_enabled boolean DEFAULT false NOT NULL,
    allow_advanced_workflow_move boolean DEFAULT false NOT NULL,
    CONSTRAINT tenants_billing_cycle_check CHECK (((billing_cycle)::text = ANY (ARRAY[('monthly'::character varying)::text, ('yearly'::character varying)::text]))),
    CONSTRAINT tenants_branding_level_check CHECK (((branding_level)::text = ANY (ARRAY[('none'::character varying)::text, ('basic'::character varying)::text, ('white_label'::character varying)::text]))),
    CONSTRAINT tenants_cv_ingestion_mode_check CHECK (((cv_ingestion_mode)::text = ANY (ARRAY[('platform_email'::character varying)::text, ('forwarding'::character varying)::text]))),
    CONSTRAINT tenants_partial_threshold_check CHECK (((partial_threshold >= 0) AND (partial_threshold <= 100))),
    CONSTRAINT tenants_pending_plan_check CHECK (((pending_plan)::text = ANY (ARRAY[('trial'::character varying)::text, ('starter'::character varying)::text, ('professional'::character varying)::text, ('enterprise'::character varying)::text]))),
    CONSTRAINT tenants_qualified_threshold_check CHECK (((qualified_threshold >= 0) AND (qualified_threshold <= 100))),
    CONSTRAINT tenants_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('suspended'::character varying)::text]))),
    CONSTRAINT tenants_subscription_status_check CHECK (((subscription_status)::text = ANY (ARRAY[('pending_plan_selection'::character varying)::text, ('pending_payment'::character varying)::text, ('pending_sales_contact'::character varying)::text, ('trial'::character varying)::text, ('active'::character varying)::text, ('grace'::character varying)::text, ('trial_expired'::character varying)::text, ('suspended'::character varying)::text, ('cancelled'::character varying)::text, ('cancelled_pending_expiry'::character varying)::text, ('expired'::character varying)::text]))),
    CONSTRAINT tenants_tenant_type_check CHECK (((tenant_type)::text = ANY (ARRAY[('organization'::character varying)::text, ('agency'::character varying)::text, ('individual_recruiter'::character varying)::text])))
);


--
-- Name: COLUMN tenants.tenant_type; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.tenants.tenant_type IS 'organization = direct employer; agency = recruitment agency; individual_recruiter = solo recruiter working for multiple clients.';


--
-- Name: COLUMN tenants.monthly_cv_processing_soft_limit; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.tenants.monthly_cv_processing_soft_limit IS 'Fair Usage Policy: admin monitoring/alert threshold (CVs per calendar month). Does not block processing. NULL = no soft cap.';


--
-- Name: COLUMN tenants.monthly_cv_processing_hard_limit; Type: COMMENT; Schema: cv_analyzer; Owner: -
--

COMMENT ON COLUMN cv_analyzer.tenants.monthly_cv_processing_hard_limit IS 'Fair Usage Policy: hard block threshold (CVs per calendar month). AI processing is paused when reached. NULL = no hard cap.';


--
-- Name: user_invites; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.user_invites (
    invite_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    invited_by uuid NOT NULL,
    email character varying(255) NOT NULL,
    role character varying(30) DEFAULT 'recruiter'::character varying NOT NULL,
    token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_invites_role_check CHECK (((role)::text = ANY (ARRAY[('admin'::character varying)::text, ('recruiter'::character varying)::text, ('viewer'::character varying)::text])))
);


--
-- Name: users; Type: TABLE; Schema: cv_analyzer; Owner: -
--

CREATE TABLE cv_analyzer.users (
    user_id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid NOT NULL,
    email character varying(255) NOT NULL,
    password_hash text NOT NULL,
    full_name character varying(255) NOT NULL,
    role character varying(30) DEFAULT 'admin'::character varying NOT NULL,
    status character varying(50) DEFAULT 'active'::character varying NOT NULL,
    reset_token_hash text,
    reset_token_expires timestamp with time zone,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    email_verified_at timestamp with time zone,
    email_verification_token_hash text,
    email_verification_expires_at timestamp with time zone,
    must_change_password boolean DEFAULT false NOT NULL,
    CONSTRAINT users_role_check CHECK (((role)::text = ANY (ARRAY[('super_admin'::character varying)::text, ('admin'::character varying)::text, ('hr_manager'::character varying)::text, ('recruiter'::character varying)::text, ('viewer'::character varying)::text]))),
    CONSTRAINT users_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('disabled'::character varying)::text, ('pending_email_verification'::character varying)::text])))
);


--
-- Name: agency_user_clients agency_user_clients_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.agency_user_clients
    ADD CONSTRAINT agency_user_clients_pkey PRIMARY KEY (id);


--
-- Name: agency_user_clients agency_user_clients_unique; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.agency_user_clients
    ADD CONSTRAINT agency_user_clients_unique UNIQUE (user_id, client_organization_id);


--
-- Name: ai_model_pricing ai_model_pricing_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_model_pricing
    ADD CONSTRAINT ai_model_pricing_pkey PRIMARY KEY (pricing_id);


--
-- Name: ai_model_registry ai_model_registry_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_model_registry
    ADD CONSTRAINT ai_model_registry_pkey PRIMARY KEY (model_id);


--
-- Name: ai_model_registry ai_model_registry_provider_model_name_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_model_registry
    ADD CONSTRAINT ai_model_registry_provider_model_name_key UNIQUE (provider, model_name);


--
-- Name: ai_prompts ai_prompts_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_prompts
    ADD CONSTRAINT ai_prompts_pkey PRIMARY KEY (prompt_id);


--
-- Name: ai_prompts ai_prompts_prompt_code_version_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_prompts
    ADD CONSTRAINT ai_prompts_prompt_code_version_key UNIQUE (prompt_code, version);


--
-- Name: ai_stage_defaults ai_stage_defaults_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_stage_defaults
    ADD CONSTRAINT ai_stage_defaults_pkey PRIMARY KEY (stage);


--
-- Name: ai_usage_log ai_usage_log_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_usage_log
    ADD CONSTRAINT ai_usage_log_pkey PRIMARY KEY (usage_id);


--
-- Name: application_candidate_tags application_candidate_tags_application_id_tag_id_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_candidate_tags
    ADD CONSTRAINT application_candidate_tags_application_id_tag_id_key UNIQUE (application_id, tag_id);


--
-- Name: application_candidate_tags application_candidate_tags_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_candidate_tags
    ADD CONSTRAINT application_candidate_tags_pkey PRIMARY KEY (id);


--
-- Name: application_files application_files_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_files
    ADD CONSTRAINT application_files_pkey PRIMARY KEY (file_id);


--
-- Name: application_intake_log application_intake_log_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_intake_log
    ADD CONSTRAINT application_intake_log_pkey PRIMARY KEY (intake_log_id);


--
-- Name: application_knockout_answer_suggestions application_knockout_answer_suggestions_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_suggestions
    ADD CONSTRAINT application_knockout_answer_suggestions_pkey PRIMARY KEY (suggestion_id);


--
-- Name: application_knockout_answer_validations application_knockout_answer_validations_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_validations
    ADD CONSTRAINT application_knockout_answer_validations_pkey PRIMARY KEY (validation_id);


--
-- Name: application_knockout_answers application_knockout_answers_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answers
    ADD CONSTRAINT application_knockout_answers_pkey PRIMARY KEY (answer_id);


--
-- Name: application_knockout_validation_runs application_knockout_validation_runs_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_validation_runs
    ADD CONSTRAINT application_knockout_validation_runs_pkey PRIMARY KEY (run_id);


--
-- Name: application_score_comparisons application_score_comparisons_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_score_comparisons
    ADD CONSTRAINT application_score_comparisons_pkey PRIMARY KEY (comparison_id);


--
-- Name: application_scores application_scores_application_id_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_scores
    ADD CONSTRAINT application_scores_application_id_key UNIQUE (application_id);


--
-- Name: application_scores application_scores_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_scores
    ADD CONSTRAINT application_scores_pkey PRIMARY KEY (score_id);


--
-- Name: application_workflow_history application_workflow_history_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_workflow_history
    ADD CONSTRAINT application_workflow_history_pkey PRIMARY KEY (history_id);


--
-- Name: applications applications_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_pkey PRIMARY KEY (application_id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (log_id);


--
-- Name: bulk_upload_batches bulk_upload_batches_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_batches
    ADD CONSTRAINT bulk_upload_batches_pkey PRIMARY KEY (batch_id);


--
-- Name: bulk_upload_rows bulk_upload_rows_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_rows
    ADD CONSTRAINT bulk_upload_rows_pkey PRIMARY KEY (row_id);


--
-- Name: candidate_approvals candidate_approvals_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_approvals
    ADD CONSTRAINT candidate_approvals_pkey PRIMARY KEY (approval_id);


--
-- Name: candidate_comments candidate_comments_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_comments
    ADD CONSTRAINT candidate_comments_pkey PRIMARY KEY (comment_id);


--
-- Name: candidate_communications candidate_communications_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_pkey PRIMARY KEY (communication_id);


--
-- Name: candidate_interview_feedback candidate_interview_feedback_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interview_feedback
    ADD CONSTRAINT candidate_interview_feedback_pkey PRIMARY KEY (feedback_id);


--
-- Name: candidate_interviews candidate_interviews_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interviews
    ADD CONSTRAINT candidate_interviews_pkey PRIMARY KEY (interview_id);


--
-- Name: candidate_message_templates candidate_message_templates_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_message_templates
    ADD CONSTRAINT candidate_message_templates_pkey PRIMARY KEY (template_id);


--
-- Name: candidate_saved_views candidate_saved_views_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_saved_views
    ADD CONSTRAINT candidate_saved_views_pkey PRIMARY KEY (saved_view_id);


--
-- Name: candidate_tags candidate_tags_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_tags
    ADD CONSTRAINT candidate_tags_pkey PRIMARY KEY (tag_id);


--
-- Name: candidate_tags candidate_tags_tenant_id_tag_name_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_tags
    ADD CONSTRAINT candidate_tags_tenant_id_tag_name_key UNIQUE (tenant_id, tag_name);


--
-- Name: client_organizations client_organizations_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.client_organizations
    ADD CONSTRAINT client_organizations_pkey PRIMARY KEY (client_organization_id);


--
-- Name: communication_automation_rules communication_automation_rules_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.communication_automation_rules
    ADD CONSTRAINT communication_automation_rules_pkey PRIMARY KEY (rule_id);


--
-- Name: duplicate_application_logs duplicate_application_logs_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.duplicate_application_logs
    ADD CONSTRAINT duplicate_application_logs_pkey PRIMARY KEY (log_id);


--
-- Name: email_ingest_log email_ingest_log_message_id_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.email_ingest_log
    ADD CONSTRAINT email_ingest_log_message_id_key UNIQUE (message_id);


--
-- Name: email_ingest_log email_ingest_log_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.email_ingest_log
    ADD CONSTRAINT email_ingest_log_pkey PRIMARY KEY (log_id);


--
-- Name: job_campaigns job_campaigns_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_campaigns
    ADD CONSTRAINT job_campaigns_pkey PRIMARY KEY (campaign_id);


--
-- Name: job_criteria job_criteria_job_id_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_criteria
    ADD CONSTRAINT job_criteria_job_id_key UNIQUE (job_id);


--
-- Name: job_criteria job_criteria_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_criteria
    ADD CONSTRAINT job_criteria_pkey PRIMARY KEY (criteria_id);


--
-- Name: job_knockout_questions job_knockout_questions_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_knockout_questions
    ADD CONSTRAINT job_knockout_questions_pkey PRIMARY KEY (question_id);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (job_id);


--
-- Name: notification_log notification_log_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_pkey PRIMARY KEY (notification_id);


--
-- Name: plan_features plan_features_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.plan_features
    ADD CONSTRAINT plan_features_pkey PRIMARY KEY (feature_id);


--
-- Name: plan_features plan_features_plan_id_feature_key_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.plan_features
    ADD CONSTRAINT plan_features_plan_id_feature_key_key UNIQUE (plan_id, feature_key);


--
-- Name: platform_secrets platform_secrets_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.platform_secrets
    ADD CONSTRAINT platform_secrets_pkey PRIMARY KEY (key);


--
-- Name: subscription_plans subscription_plans_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.subscription_plans
    ADD CONSTRAINT subscription_plans_pkey PRIMARY KEY (plan_id);


--
-- Name: system_config system_config_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.system_config
    ADD CONSTRAINT system_config_pkey PRIMARY KEY (key);


--
-- Name: tenant_feature_audit tenant_feature_audit_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_feature_audit
    ADD CONSTRAINT tenant_feature_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: tenant_features tenant_features_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_features
    ADD CONSTRAINT tenant_features_pkey PRIMARY KEY (tenant_feature_id);


--
-- Name: tenant_usage_monthly tenant_usage_monthly_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_usage_monthly
    ADD CONSTRAINT tenant_usage_monthly_pkey PRIMARY KEY (id);


--
-- Name: tenant_usage_monthly tenant_usage_monthly_unique; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_usage_monthly
    ADD CONSTRAINT tenant_usage_monthly_unique UNIQUE (tenant_id, usage_month);


--
-- Name: tenant_workflow_policies tenant_workflow_policies_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_workflow_policies
    ADD CONSTRAINT tenant_workflow_policies_pkey PRIMARY KEY (policy_id);


--
-- Name: tenant_workflow_policies tenant_workflow_policies_tenant_id_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_workflow_policies
    ADD CONSTRAINT tenant_workflow_policies_tenant_id_key UNIQUE (tenant_id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (tenant_id);


--
-- Name: communication_automation_rules uq_automation_event_per_tenant; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.communication_automation_rules
    ADD CONSTRAINT uq_automation_event_per_tenant UNIQUE (tenant_id, event_type);


--
-- Name: application_knockout_answer_suggestions uq_ko_suggestion; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_suggestions
    ADD CONSTRAINT uq_ko_suggestion UNIQUE (application_id, question_id);


--
-- Name: application_knockout_answer_validations uq_ko_validation; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_validations
    ADD CONSTRAINT uq_ko_validation UNIQUE (application_id, question_id);


--
-- Name: subscription_plans uq_subscription_plan_code; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.subscription_plans
    ADD CONSTRAINT uq_subscription_plan_code UNIQUE (plan_code);


--
-- Name: tenant_features uq_tenant_features_tenant_feature; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_features
    ADD CONSTRAINT uq_tenant_features_tenant_feature UNIQUE (tenant_id, feature_code);


--
-- Name: user_invites user_invites_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.user_invites
    ADD CONSTRAINT user_invites_pkey PRIMARY KEY (invite_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: idx_agency_user_clients_client; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_agency_user_clients_client ON cv_analyzer.agency_user_clients USING btree (client_organization_id);


--
-- Name: idx_agency_user_clients_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_agency_user_clients_tenant ON cv_analyzer.agency_user_clients USING btree (tenant_id);


--
-- Name: idx_agency_user_clients_user; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_agency_user_clients_user ON cv_analyzer.agency_user_clients USING btree (user_id);


--
-- Name: idx_ai_prompts_active; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_prompts_active ON cv_analyzer.ai_prompts USING btree (prompt_code, is_active) WHERE (is_active = true);


--
-- Name: idx_ai_prompts_code; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_prompts_code ON cv_analyzer.ai_prompts USING btree (prompt_code);


--
-- Name: idx_ai_usage_log_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_application ON cv_analyzer.ai_usage_log USING btree (application_id);


--
-- Name: idx_ai_usage_log_created; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_created ON cv_analyzer.ai_usage_log USING btree (created_at DESC);


--
-- Name: idx_ai_usage_log_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_job ON cv_analyzer.ai_usage_log USING btree (job_id, created_at DESC);


--
-- Name: idx_ai_usage_log_model; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_model ON cv_analyzer.ai_usage_log USING btree (model, created_at DESC);


--
-- Name: idx_ai_usage_log_stage; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_stage ON cv_analyzer.ai_usage_log USING btree (stage, created_at DESC);


--
-- Name: idx_ai_usage_log_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ai_usage_log_tenant ON cv_analyzer.ai_usage_log USING btree (tenant_id, created_at DESC);


--
-- Name: idx_ail_application_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_application_id ON cv_analyzer.application_intake_log USING btree (application_id) WHERE (application_id IS NOT NULL);


--
-- Name: idx_ail_created_at; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_created_at ON cv_analyzer.application_intake_log USING btree (created_at DESC);


--
-- Name: idx_ail_file_hash; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_file_hash ON cv_analyzer.application_intake_log USING btree (file_hash) WHERE (file_hash IS NOT NULL);


--
-- Name: idx_ail_intake_method; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_intake_method ON cv_analyzer.application_intake_log USING btree (intake_method);


--
-- Name: idx_ail_job_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_job_id ON cv_analyzer.application_intake_log USING btree (job_id) WHERE (job_id IS NOT NULL);


--
-- Name: idx_ail_message_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_message_id ON cv_analyzer.application_intake_log USING btree (source_message_id) WHERE (source_message_id IS NOT NULL);


--
-- Name: idx_ail_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_status ON cv_analyzer.application_intake_log USING btree (status);


--
-- Name: idx_ail_stuck; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_stuck ON cv_analyzer.application_intake_log USING btree (processing_started_at) WHERE ((processed_at IS NULL) AND (processing_started_at IS NOT NULL));


--
-- Name: idx_ail_tenant_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ail_tenant_id ON cv_analyzer.application_intake_log USING btree (tenant_id);


--
-- Name: idx_app_candidate_tags_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_candidate_tags_app ON cv_analyzer.application_candidate_tags USING btree (application_id);


--
-- Name: idx_app_candidate_tags_tag; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_candidate_tags_tag ON cv_analyzer.application_candidate_tags USING btree (tag_id);


--
-- Name: idx_app_files_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_files_application ON cv_analyzer.application_files USING btree (application_id);


--
-- Name: idx_app_files_canonical_fp; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_files_canonical_fp ON cv_analyzer.application_files USING btree (canonical_text_fingerprint) WHERE (canonical_text_fingerprint IS NOT NULL);


--
-- Name: idx_app_files_norm_hash; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_files_norm_hash ON cv_analyzer.application_files USING btree (normalized_text_hash) WHERE (normalized_text_hash IS NOT NULL);


--
-- Name: idx_app_workflow_history_advanced; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_workflow_history_advanced ON cv_analyzer.application_workflow_history USING btree (application_id) WHERE (is_advanced_move = true);


--
-- Name: idx_app_workflow_history_app_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_app_workflow_history_app_id ON cv_analyzer.application_workflow_history USING btree (application_id, created_at DESC);


--
-- Name: idx_application_knockout_answers_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_application_knockout_answers_app ON cv_analyzer.application_knockout_answers USING btree (application_id);


--
-- Name: idx_applications_assigned_user; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_assigned_user ON cv_analyzer.applications USING btree (tenant_id, assigned_user_id) WHERE (assigned_user_id IS NOT NULL);


--
-- Name: idx_applications_decision; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_decision ON cv_analyzer.applications USING btree (decision);


--
-- Name: idx_applications_dup_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_dup_status ON cv_analyzer.applications USING btree (tenant_id, job_id, duplicate_status) WHERE ((duplicate_status)::text = 'possible_duplicate'::text);


--
-- Name: idx_applications_duplicate_blocked; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_duplicate_blocked ON cv_analyzer.applications USING btree (tenant_id, job_id) WHERE ((stopped_reason)::text = 'duplicate_blocked'::text);


--
-- Name: idx_applications_exact_dup; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_exact_dup ON cv_analyzer.applications USING btree (tenant_id, job_id, duplicate_status) WHERE ((duplicate_status)::text = 'exact_duplicate'::text);


--
-- Name: idx_applications_gender_value; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_gender_value ON cv_analyzer.applications USING btree (tenant_id, gender_value) WHERE (gender_value IS NOT NULL);


--
-- Name: idx_applications_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_job ON cv_analyzer.applications USING btree (job_id);


--
-- Name: idx_applications_preferred_contact_email; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_preferred_contact_email ON cv_analyzer.applications USING btree (preferred_contact_email) WHERE (preferred_contact_email IS NOT NULL);


--
-- Name: idx_applications_scoring_batch; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_scoring_batch ON cv_analyzer.applications USING btree (scoring_batch_id) WHERE (scoring_batch_id IS NOT NULL);


--
-- Name: idx_applications_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_status ON cv_analyzer.applications USING btree (processing_status);


--
-- Name: idx_applications_talent_pool; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_talent_pool ON cv_analyzer.applications USING btree (job_id, is_talent_pool) WHERE (is_talent_pool = true);


--
-- Name: idx_applications_talent_pool_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_talent_pool_tenant ON cv_analyzer.applications USING btree (is_talent_pool) WHERE (is_talent_pool = true);


--
-- Name: idx_applications_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_tenant ON cv_analyzer.applications USING btree (tenant_id);


--
-- Name: idx_applications_workflow_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_applications_workflow_status ON cv_analyzer.applications USING btree (job_id, workflow_status) WHERE ((workflow_status)::text <> 'new'::text);


--
-- Name: idx_approvals_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_approvals_application ON cv_analyzer.candidate_approvals USING btree (application_id, stage_order);


--
-- Name: idx_approvals_approver; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_approvals_approver ON cv_analyzer.candidate_approvals USING btree (tenant_id, approver_id) WHERE (approver_id IS NOT NULL);


--
-- Name: idx_approvals_pending; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_approvals_pending ON cv_analyzer.candidate_approvals USING btree (tenant_id, approver_id, decision) WHERE ((decision)::text = 'pending'::text);


--
-- Name: idx_audit_logs_action; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_audit_logs_action ON cv_analyzer.audit_logs USING btree (action);


--
-- Name: idx_audit_logs_created; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_audit_logs_created ON cv_analyzer.audit_logs USING btree (created_at DESC);


--
-- Name: idx_audit_logs_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_audit_logs_tenant ON cv_analyzer.audit_logs USING btree (tenant_id);


--
-- Name: idx_audit_logs_user; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_audit_logs_user ON cv_analyzer.audit_logs USING btree (user_id);


--
-- Name: idx_automation_rules_tenant_active; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_automation_rules_tenant_active ON cv_analyzer.communication_automation_rules USING btree (tenant_id, is_active);


--
-- Name: idx_bulk_batches_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_batches_job ON cv_analyzer.bulk_upload_batches USING btree (job_id, created_at DESC);


--
-- Name: idx_bulk_batches_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_batches_status ON cv_analyzer.bulk_upload_batches USING btree (tenant_id, batch_status) WHERE ((batch_status)::text <> ALL (ARRAY[('completed'::character varying)::text, ('failed'::character varying)::text]));


--
-- Name: idx_bulk_batches_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_batches_tenant ON cv_analyzer.bulk_upload_batches USING btree (tenant_id, created_at DESC);


--
-- Name: idx_bulk_rows_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_application ON cv_analyzer.bulk_upload_rows USING btree (application_id) WHERE (application_id IS NOT NULL);


--
-- Name: idx_bulk_rows_batch; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_batch ON cv_analyzer.bulk_upload_rows USING btree (batch_id, row_number);


--
-- Name: idx_bulk_rows_import_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_import_status ON cv_analyzer.bulk_upload_rows USING btree (batch_id, import_status);


--
-- Name: idx_bulk_rows_processing_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_processing_status ON cv_analyzer.bulk_upload_rows USING btree (batch_id, processing_status) WHERE ((processing_status)::text <> ALL (ARRAY[('completed'::character varying)::text, ('failed'::character varying)::text]));


--
-- Name: idx_bulk_rows_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_tenant ON cv_analyzer.bulk_upload_rows USING btree (tenant_id);


--
-- Name: idx_bulk_rows_validation_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_bulk_rows_validation_status ON cv_analyzer.bulk_upload_rows USING btree (batch_id, validation_status);


--
-- Name: idx_candidate_comments_app_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_candidate_comments_app_id ON cv_analyzer.candidate_comments USING btree (application_id, created_at);


--
-- Name: idx_candidate_comments_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_candidate_comments_tenant ON cv_analyzer.candidate_comments USING btree (tenant_id);


--
-- Name: idx_candidate_saved_views_user; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_candidate_saved_views_user ON cv_analyzer.candidate_saved_views USING btree (user_id, tenant_id);


--
-- Name: idx_candidate_tags_name; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_candidate_tags_name ON cv_analyzer.candidate_tags USING btree (tenant_id, tag_name);


--
-- Name: idx_candidate_tags_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_candidate_tags_tenant ON cv_analyzer.candidate_tags USING btree (tenant_id);


--
-- Name: idx_client_organizations_tenant_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_client_organizations_tenant_id ON cv_analyzer.client_organizations USING btree (tenant_id);


--
-- Name: idx_client_organizations_tenant_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_client_organizations_tenant_status ON cv_analyzer.client_organizations USING btree (tenant_id, status);


--
-- Name: idx_comm_automated_event; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_comm_automated_event ON cv_analyzer.candidate_communications USING btree (application_id, event_type) WHERE ((event_type IS NOT NULL) AND (is_automated = true));


--
-- Name: idx_communication_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_communication_application ON cv_analyzer.candidate_communications USING btree (application_id);


--
-- Name: idx_communication_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_communication_status ON cv_analyzer.candidate_communications USING btree (status);


--
-- Name: idx_communication_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_communication_tenant ON cv_analyzer.candidate_communications USING btree (tenant_id);


--
-- Name: idx_communication_tenant_created; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_communication_tenant_created ON cv_analyzer.candidate_communications USING btree (tenant_id, created_at DESC);


--
-- Name: idx_dup_logs_email; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_dup_logs_email ON cv_analyzer.duplicate_application_logs USING btree (duplicate_email);


--
-- Name: idx_dup_logs_hash; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_dup_logs_hash ON cv_analyzer.duplicate_application_logs USING btree (attachment_hash);


--
-- Name: idx_dup_logs_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_dup_logs_job ON cv_analyzer.duplicate_application_logs USING btree (job_id);


--
-- Name: idx_dup_logs_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_dup_logs_tenant ON cv_analyzer.duplicate_application_logs USING btree (tenant_id);


--
-- Name: idx_ingest_log_hash; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ingest_log_hash ON cv_analyzer.email_ingest_log USING btree (attachment_hash) WHERE (attachment_hash IS NOT NULL);


--
-- Name: idx_ingest_log_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ingest_log_job ON cv_analyzer.email_ingest_log USING btree (job_id);


--
-- Name: idx_ingest_log_msg_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ingest_log_msg_id ON cv_analyzer.email_ingest_log USING btree (message_id);


--
-- Name: idx_ingest_log_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ingest_log_tenant ON cv_analyzer.email_ingest_log USING btree (tenant_id);


--
-- Name: idx_interview_feedback_interview; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_interview_feedback_interview ON cv_analyzer.candidate_interview_feedback USING btree (interview_id);


--
-- Name: idx_interview_feedback_unique; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX idx_interview_feedback_unique ON cv_analyzer.candidate_interview_feedback USING btree (interview_id, reviewer_id);


--
-- Name: idx_interviews_application; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_interviews_application ON cv_analyzer.candidate_interviews USING btree (application_id, scheduled_at);


--
-- Name: idx_interviews_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_interviews_tenant ON cv_analyzer.candidate_interviews USING btree (tenant_id, scheduled_at);


--
-- Name: idx_invites_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_invites_tenant ON cv_analyzer.user_invites USING btree (tenant_id);


--
-- Name: idx_invites_token; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_invites_token ON cv_analyzer.user_invites USING btree (token_hash);


--
-- Name: idx_job_campaigns_owner; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_job_campaigns_owner ON cv_analyzer.job_campaigns USING btree (campaign_owner_id) WHERE (campaign_owner_id IS NOT NULL);


--
-- Name: idx_job_campaigns_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_job_campaigns_tenant ON cv_analyzer.job_campaigns USING btree (tenant_id);


--
-- Name: idx_job_campaigns_tenant_client; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_job_campaigns_tenant_client ON cv_analyzer.job_campaigns USING btree (tenant_id, client_organization_id);


--
-- Name: idx_job_knockout_questions_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_job_knockout_questions_job ON cv_analyzer.job_knockout_questions USING btree (job_id);


--
-- Name: idx_jobs_campaign_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_jobs_campaign_id ON cv_analyzer.jobs USING btree (campaign_id) WHERE (campaign_id IS NOT NULL);


--
-- Name: idx_jobs_client_organization_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_jobs_client_organization_id ON cv_analyzer.jobs USING btree (client_organization_id) WHERE (client_organization_id IS NOT NULL);


--
-- Name: idx_jobs_job_code; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX idx_jobs_job_code ON cv_analyzer.jobs USING btree (job_code);


--
-- Name: idx_jobs_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_jobs_status ON cv_analyzer.jobs USING btree (status);


--
-- Name: idx_jobs_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_jobs_tenant ON cv_analyzer.jobs USING btree (tenant_id);


--
-- Name: idx_knockout_answers_app_question_unique; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX idx_knockout_answers_app_question_unique ON cv_analyzer.application_knockout_answers USING btree (application_id, question_id);


--
-- Name: idx_ko_suggestions_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_suggestions_app ON cv_analyzer.application_knockout_answer_suggestions USING btree (application_id);


--
-- Name: idx_ko_suggestions_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_suggestions_tenant ON cv_analyzer.application_knockout_answer_suggestions USING btree (tenant_id);


--
-- Name: idx_ko_val_runs_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_val_runs_app ON cv_analyzer.application_knockout_validation_runs USING btree (application_id, created_at DESC);


--
-- Name: idx_ko_val_runs_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_val_runs_tenant ON cv_analyzer.application_knockout_validation_runs USING btree (tenant_id);


--
-- Name: idx_ko_validations_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_validations_app ON cv_analyzer.application_knockout_answer_validations USING btree (application_id);


--
-- Name: idx_ko_validations_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_ko_validations_tenant ON cv_analyzer.application_knockout_answer_validations USING btree (tenant_id);


--
-- Name: idx_notification_log_created; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_notification_log_created ON cv_analyzer.notification_log USING btree (created_at DESC);


--
-- Name: idx_notification_log_job; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_notification_log_job ON cv_analyzer.notification_log USING btree (job_id);


--
-- Name: idx_notification_log_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_notification_log_status ON cv_analyzer.notification_log USING btree (status);


--
-- Name: idx_notification_log_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_notification_log_tenant ON cv_analyzer.notification_log USING btree (tenant_id);


--
-- Name: idx_notification_log_type; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_notification_log_type ON cv_analyzer.notification_log USING btree (notification_type);


--
-- Name: idx_saved_views_tenant_shared; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_saved_views_tenant_shared ON cv_analyzer.candidate_saved_views USING btree (tenant_id, visibility_type) WHERE ((visibility_type)::text = 'tenant_shared'::text);


--
-- Name: idx_score_comparisons_app; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_score_comparisons_app ON cv_analyzer.application_score_comparisons USING btree (application_id);


--
-- Name: idx_template_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_template_tenant ON cv_analyzer.candidate_message_templates USING btree (tenant_id);


--
-- Name: idx_template_tenant_category; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_template_tenant_category ON cv_analyzer.candidate_message_templates USING btree (tenant_id, category);


--
-- Name: idx_tenant_feature_audit_changed_at; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_feature_audit_changed_at ON cv_analyzer.tenant_feature_audit USING btree (changed_at DESC);


--
-- Name: idx_tenant_feature_audit_changed_by; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_feature_audit_changed_by ON cv_analyzer.tenant_feature_audit USING btree (changed_by);


--
-- Name: idx_tenant_feature_audit_feature; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_feature_audit_feature ON cv_analyzer.tenant_feature_audit USING btree (tenant_id, feature_code);


--
-- Name: idx_tenant_feature_audit_tenant_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_feature_audit_tenant_id ON cv_analyzer.tenant_feature_audit USING btree (tenant_id);


--
-- Name: idx_tenant_features_feature_code; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_features_feature_code ON cv_analyzer.tenant_features USING btree (feature_code);


--
-- Name: idx_tenant_features_lookup; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_features_lookup ON cv_analyzer.tenant_features USING btree (tenant_id, feature_code) INCLUDE (is_enabled);


--
-- Name: idx_tenant_features_tenant_id; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_features_tenant_id ON cv_analyzer.tenant_features USING btree (tenant_id);


--
-- Name: idx_tenant_usage_monthly_month; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_usage_monthly_month ON cv_analyzer.tenant_usage_monthly USING btree (usage_month DESC);


--
-- Name: idx_tenant_usage_monthly_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenant_usage_monthly_tenant ON cv_analyzer.tenant_usage_monthly USING btree (tenant_id);


--
-- Name: idx_tenants_allow_advanced_move; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenants_allow_advanced_move ON cv_analyzer.tenants USING btree (allow_advanced_workflow_move);


--
-- Name: idx_tenants_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenants_status ON cv_analyzer.tenants USING btree (status);


--
-- Name: idx_tenants_subscription_status; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_tenants_subscription_status ON cv_analyzer.tenants USING btree (subscription_status);


--
-- Name: idx_users_email; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_users_email ON cv_analyzer.users USING btree (email);


--
-- Name: idx_users_email_verification_token; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_users_email_verification_token ON cv_analyzer.users USING btree (email_verification_token_hash) WHERE (email_verification_token_hash IS NOT NULL);


--
-- Name: idx_users_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_users_tenant ON cv_analyzer.users USING btree (tenant_id);


--
-- Name: idx_workflow_policies_tenant; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE INDEX idx_workflow_policies_tenant ON cv_analyzer.tenant_workflow_policies USING btree (tenant_id);


--
-- Name: uix_ai_model_pricing_active; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX uix_ai_model_pricing_active ON cv_analyzer.ai_model_pricing USING btree (provider, model) WHERE (active = true);


--
-- Name: uix_template_tenant_name; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX uix_template_tenant_name ON cv_analyzer.candidate_message_templates USING btree (tenant_id, lower(name));


--
-- Name: uq_bulk_rows_batch_row_number; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX uq_bulk_rows_batch_row_number ON cv_analyzer.bulk_upload_rows USING btree (batch_id, row_number);


--
-- Name: uq_notification_intake_candidate; Type: INDEX; Schema: cv_analyzer; Owner: -
--

CREATE UNIQUE INDEX uq_notification_intake_candidate ON cv_analyzer.notification_log USING btree (intake_log_id, intake_status, recipient_email) WHERE ((intake_log_id IS NOT NULL) AND ((recipient_type)::text = 'candidate'::text));


--
-- Name: ai_prompts trg_ai_prompts_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_ai_prompts_updated_at BEFORE UPDATE ON cv_analyzer.ai_prompts FOR EACH ROW EXECUTE FUNCTION cv_analyzer.update_ai_prompts_updated_at();


--
-- Name: application_intake_log trg_ail_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_ail_updated_at BEFORE UPDATE ON cv_analyzer.application_intake_log FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: applications trg_apps_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_apps_updated_at BEFORE UPDATE ON cv_analyzer.applications FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();


--
-- Name: job_criteria trg_criteria_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_criteria_updated_at BEFORE UPDATE ON cv_analyzer.job_criteria FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();


--
-- Name: jobs trg_jobs_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_jobs_updated_at BEFORE UPDATE ON cv_analyzer.jobs FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_jobs_updated_at();


--
-- Name: subscription_plans trg_subscription_plans_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_subscription_plans_updated_at BEFORE UPDATE ON cv_analyzer.subscription_plans FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();


--
-- Name: tenant_features trg_tenant_features_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_tenant_features_updated_at BEFORE UPDATE ON cv_analyzer.tenant_features FOR EACH ROW EXECUTE FUNCTION cv_analyzer.tenant_features_set_updated_at();


--
-- Name: tenants trg_tenants_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_tenants_updated_at BEFORE UPDATE ON cv_analyzer.tenants FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();


--
-- Name: users trg_users_updated_at; Type: TRIGGER; Schema: cv_analyzer; Owner: -
--

CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON cv_analyzer.users FOR EACH ROW EXECUTE FUNCTION cv_analyzer.set_updated_at();


--
-- Name: agency_user_clients agency_user_clients_client_organization_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.agency_user_clients
    ADD CONSTRAINT agency_user_clients_client_organization_id_fkey FOREIGN KEY (client_organization_id) REFERENCES cv_analyzer.client_organizations(client_organization_id) ON DELETE CASCADE;


--
-- Name: agency_user_clients agency_user_clients_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.agency_user_clients
    ADD CONSTRAINT agency_user_clients_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: agency_user_clients agency_user_clients_user_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.agency_user_clients
    ADD CONSTRAINT agency_user_clients_user_id_fkey FOREIGN KEY (user_id) REFERENCES cv_analyzer.users(user_id) ON DELETE CASCADE;


--
-- Name: ai_stage_defaults ai_stage_defaults_fallback_model_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_stage_defaults
    ADD CONSTRAINT ai_stage_defaults_fallback_model_id_fkey FOREIGN KEY (fallback_model_id) REFERENCES cv_analyzer.ai_model_registry(model_id) ON DELETE SET NULL;


--
-- Name: ai_stage_defaults ai_stage_defaults_primary_model_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.ai_stage_defaults
    ADD CONSTRAINT ai_stage_defaults_primary_model_id_fkey FOREIGN KEY (primary_model_id) REFERENCES cv_analyzer.ai_model_registry(model_id) ON DELETE SET NULL;


--
-- Name: application_candidate_tags application_candidate_tags_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_candidate_tags
    ADD CONSTRAINT application_candidate_tags_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_candidate_tags application_candidate_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_candidate_tags
    ADD CONSTRAINT application_candidate_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES cv_analyzer.candidate_tags(tag_id) ON DELETE CASCADE;


--
-- Name: application_files application_files_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_files
    ADD CONSTRAINT application_files_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_files application_files_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_files
    ADD CONSTRAINT application_files_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id);


--
-- Name: application_knockout_answer_suggestions application_knockout_answer_suggestions_accepted_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_suggestions
    ADD CONSTRAINT application_knockout_answer_suggestions_accepted_by_fkey FOREIGN KEY (accepted_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: application_knockout_answer_suggestions application_knockout_answer_suggestions_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_suggestions
    ADD CONSTRAINT application_knockout_answer_suggestions_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_knockout_answer_suggestions application_knockout_answer_suggestions_question_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_suggestions
    ADD CONSTRAINT application_knockout_answer_suggestions_question_id_fkey FOREIGN KEY (question_id) REFERENCES cv_analyzer.job_knockout_questions(question_id) ON DELETE CASCADE;


--
-- Name: application_knockout_answer_validations application_knockout_answer_validations_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_validations
    ADD CONSTRAINT application_knockout_answer_validations_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_knockout_answer_validations application_knockout_answer_validations_question_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answer_validations
    ADD CONSTRAINT application_knockout_answer_validations_question_id_fkey FOREIGN KEY (question_id) REFERENCES cv_analyzer.job_knockout_questions(question_id) ON DELETE CASCADE;


--
-- Name: application_knockout_answers application_knockout_answers_updated_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_answers
    ADD CONSTRAINT application_knockout_answers_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: application_knockout_validation_runs application_knockout_validation_runs_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_knockout_validation_runs
    ADD CONSTRAINT application_knockout_validation_runs_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_score_comparisons application_score_comparisons_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_score_comparisons
    ADD CONSTRAINT application_score_comparisons_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_scores application_scores_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_scores
    ADD CONSTRAINT application_scores_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_workflow_history application_workflow_history_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_workflow_history
    ADD CONSTRAINT application_workflow_history_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: application_workflow_history application_workflow_history_changed_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.application_workflow_history
    ADD CONSTRAINT application_workflow_history_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: applications applications_assigned_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: applications applications_assigned_user_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_assigned_user_id_fkey FOREIGN KEY (assigned_user_id) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: applications applications_duplicate_reference_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_duplicate_reference_application_id_fkey FOREIGN KEY (duplicate_reference_application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE SET NULL;


--
-- Name: applications applications_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE;


--
-- Name: applications applications_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.applications
    ADD CONSTRAINT applications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: audit_logs audit_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.audit_logs
    ADD CONSTRAINT audit_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE SET NULL;


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: bulk_upload_batches bulk_upload_batches_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_batches
    ADD CONSTRAINT bulk_upload_batches_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: bulk_upload_batches bulk_upload_batches_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_batches
    ADD CONSTRAINT bulk_upload_batches_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE;


--
-- Name: bulk_upload_rows bulk_upload_rows_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_rows
    ADD CONSTRAINT bulk_upload_rows_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE SET NULL;


--
-- Name: bulk_upload_rows bulk_upload_rows_batch_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.bulk_upload_rows
    ADD CONSTRAINT bulk_upload_rows_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES cv_analyzer.bulk_upload_batches(batch_id) ON DELETE CASCADE;


--
-- Name: candidate_approvals candidate_approvals_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_approvals
    ADD CONSTRAINT candidate_approvals_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: candidate_approvals candidate_approvals_approver_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_approvals
    ADD CONSTRAINT candidate_approvals_approver_id_fkey FOREIGN KEY (approver_id) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_approvals candidate_approvals_requested_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_approvals
    ADD CONSTRAINT candidate_approvals_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_comments candidate_comments_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_comments
    ADD CONSTRAINT candidate_comments_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: candidate_comments candidate_comments_user_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_comments
    ADD CONSTRAINT candidate_comments_user_id_fkey FOREIGN KEY (user_id) REFERENCES cv_analyzer.users(user_id) ON DELETE CASCADE;


--
-- Name: candidate_communications candidate_communications_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: candidate_communications candidate_communications_automation_rule_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_automation_rule_id_fkey FOREIGN KEY (automation_rule_id) REFERENCES cv_analyzer.communication_automation_rules(rule_id) ON DELETE SET NULL;


--
-- Name: candidate_communications candidate_communications_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_communications candidate_communications_template_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_template_id_fkey FOREIGN KEY (template_id) REFERENCES cv_analyzer.candidate_message_templates(template_id) ON DELETE SET NULL;


--
-- Name: candidate_communications candidate_communications_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_communications
    ADD CONSTRAINT candidate_communications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: candidate_interview_feedback candidate_interview_feedback_interview_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interview_feedback
    ADD CONSTRAINT candidate_interview_feedback_interview_id_fkey FOREIGN KEY (interview_id) REFERENCES cv_analyzer.candidate_interviews(interview_id) ON DELETE CASCADE;


--
-- Name: candidate_interview_feedback candidate_interview_feedback_reviewer_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interview_feedback
    ADD CONSTRAINT candidate_interview_feedback_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES cv_analyzer.users(user_id) ON DELETE CASCADE;


--
-- Name: candidate_interviews candidate_interviews_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interviews
    ADD CONSTRAINT candidate_interviews_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE CASCADE;


--
-- Name: candidate_interviews candidate_interviews_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_interviews
    ADD CONSTRAINT candidate_interviews_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_message_templates candidate_message_templates_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_message_templates
    ADD CONSTRAINT candidate_message_templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_message_templates candidate_message_templates_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_message_templates
    ADD CONSTRAINT candidate_message_templates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: candidate_saved_views candidate_saved_views_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_saved_views
    ADD CONSTRAINT candidate_saved_views_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_saved_views candidate_saved_views_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_saved_views
    ADD CONSTRAINT candidate_saved_views_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: candidate_saved_views candidate_saved_views_user_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_saved_views
    ADD CONSTRAINT candidate_saved_views_user_id_fkey FOREIGN KEY (user_id) REFERENCES cv_analyzer.users(user_id) ON DELETE CASCADE;


--
-- Name: candidate_tags candidate_tags_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_tags
    ADD CONSTRAINT candidate_tags_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: candidate_tags candidate_tags_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.candidate_tags
    ADD CONSTRAINT candidate_tags_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: client_organizations client_organizations_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.client_organizations
    ADD CONSTRAINT client_organizations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: communication_automation_rules communication_automation_rules_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.communication_automation_rules
    ADD CONSTRAINT communication_automation_rules_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: communication_automation_rules communication_automation_rules_template_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.communication_automation_rules
    ADD CONSTRAINT communication_automation_rules_template_id_fkey FOREIGN KEY (template_id) REFERENCES cv_analyzer.candidate_message_templates(template_id) ON DELETE SET NULL;


--
-- Name: communication_automation_rules communication_automation_rules_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.communication_automation_rules
    ADD CONSTRAINT communication_automation_rules_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: duplicate_application_logs duplicate_application_logs_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.duplicate_application_logs
    ADD CONSTRAINT duplicate_application_logs_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE;


--
-- Name: duplicate_application_logs duplicate_application_logs_original_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.duplicate_application_logs
    ADD CONSTRAINT duplicate_application_logs_original_application_id_fkey FOREIGN KEY (original_application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE SET NULL;


--
-- Name: duplicate_application_logs duplicate_application_logs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.duplicate_application_logs
    ADD CONSTRAINT duplicate_application_logs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: email_ingest_log email_ingest_log_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.email_ingest_log
    ADD CONSTRAINT email_ingest_log_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id);


--
-- Name: email_ingest_log email_ingest_log_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.email_ingest_log
    ADD CONSTRAINT email_ingest_log_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id);


--
-- Name: email_ingest_log email_ingest_log_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.email_ingest_log
    ADD CONSTRAINT email_ingest_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id);


--
-- Name: job_campaigns job_campaigns_campaign_owner_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_campaigns
    ADD CONSTRAINT job_campaigns_campaign_owner_id_fkey FOREIGN KEY (campaign_owner_id) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: job_campaigns job_campaigns_client_organization_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_campaigns
    ADD CONSTRAINT job_campaigns_client_organization_id_fkey FOREIGN KEY (client_organization_id) REFERENCES cv_analyzer.client_organizations(client_organization_id) ON DELETE SET NULL;


--
-- Name: job_campaigns job_campaigns_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_campaigns
    ADD CONSTRAINT job_campaigns_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: job_campaigns job_campaigns_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_campaigns
    ADD CONSTRAINT job_campaigns_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: job_criteria job_criteria_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_criteria
    ADD CONSTRAINT job_criteria_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id) ON DELETE CASCADE;


--
-- Name: job_criteria job_criteria_last_edited_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.job_criteria
    ADD CONSTRAINT job_criteria_last_edited_by_fkey FOREIGN KEY (last_edited_by) REFERENCES cv_analyzer.users(user_id);


--
-- Name: jobs jobs_campaign_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES cv_analyzer.job_campaigns(campaign_id) ON DELETE SET NULL;


--
-- Name: jobs jobs_client_organization_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_client_organization_id_fkey FOREIGN KEY (client_organization_id) REFERENCES cv_analyzer.client_organizations(client_organization_id) ON DELETE SET NULL;


--
-- Name: jobs jobs_created_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_created_by_fkey FOREIGN KEY (created_by) REFERENCES cv_analyzer.users(user_id);


--
-- Name: jobs jobs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: jobs jobs_updated_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.jobs
    ADD CONSTRAINT jobs_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: notification_log notification_log_application_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_application_id_fkey FOREIGN KEY (application_id) REFERENCES cv_analyzer.applications(application_id) ON DELETE SET NULL;


--
-- Name: notification_log notification_log_duplicate_log_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_duplicate_log_id_fkey FOREIGN KEY (duplicate_log_id) REFERENCES cv_analyzer.duplicate_application_logs(log_id) ON DELETE SET NULL;


--
-- Name: notification_log notification_log_intake_log_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_intake_log_id_fkey FOREIGN KEY (intake_log_id) REFERENCES cv_analyzer.email_ingest_log(log_id) ON DELETE SET NULL;


--
-- Name: notification_log notification_log_job_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_job_id_fkey FOREIGN KEY (job_id) REFERENCES cv_analyzer.jobs(job_id) ON DELETE SET NULL;


--
-- Name: notification_log notification_log_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.notification_log
    ADD CONSTRAINT notification_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE SET NULL;


--
-- Name: plan_features plan_features_plan_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.plan_features
    ADD CONSTRAINT plan_features_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES cv_analyzer.subscription_plans(plan_id) ON DELETE CASCADE;


--
-- Name: subscription_plans subscription_plans_updated_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.subscription_plans
    ADD CONSTRAINT subscription_plans_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: system_config system_config_updated_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.system_config
    ADD CONSTRAINT system_config_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: tenant_usage_monthly tenant_usage_monthly_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_usage_monthly
    ADD CONSTRAINT tenant_usage_monthly_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: tenant_workflow_policies tenant_workflow_policies_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_workflow_policies
    ADD CONSTRAINT tenant_workflow_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: tenant_workflow_policies tenant_workflow_policies_updated_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.tenant_workflow_policies
    ADD CONSTRAINT tenant_workflow_policies_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES cv_analyzer.users(user_id) ON DELETE SET NULL;


--
-- Name: user_invites user_invites_invited_by_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.user_invites
    ADD CONSTRAINT user_invites_invited_by_fkey FOREIGN KEY (invited_by) REFERENCES cv_analyzer.users(user_id);


--
-- Name: user_invites user_invites_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.user_invites
    ADD CONSTRAINT user_invites_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: users users_tenant_id_fkey; Type: FK CONSTRAINT; Schema: cv_analyzer; Owner: -
--

ALTER TABLE ONLY cv_analyzer.users
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES cv_analyzer.tenants(tenant_id) ON DELETE CASCADE;


--
-- Name: agency_user_clients; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.agency_user_clients ENABLE ROW LEVEL SECURITY;

--
-- Name: application_intake_log ail_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY ail_tenant_isolation ON cv_analyzer.application_intake_log USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: application_files; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_files ENABLE ROW LEVEL SECURITY;

--
-- Name: application_intake_log; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_intake_log ENABLE ROW LEVEL SECURITY;

--
-- Name: application_knockout_answer_suggestions; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_knockout_answer_suggestions ENABLE ROW LEVEL SECURITY;

--
-- Name: application_knockout_answer_validations; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_knockout_answer_validations ENABLE ROW LEVEL SECURITY;

--
-- Name: application_knockout_validation_runs; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_knockout_validation_runs ENABLE ROW LEVEL SECURITY;

--
-- Name: application_score_comparisons; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_score_comparisons ENABLE ROW LEVEL SECURITY;

--
-- Name: application_scores; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_scores ENABLE ROW LEVEL SECURITY;

--
-- Name: application_workflow_history; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.application_workflow_history ENABLE ROW LEVEL SECURITY;

--
-- Name: applications; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.applications ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_logs; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.audit_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_logs audit_logs_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY audit_logs_isolation ON cv_analyzer.audit_logs USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: bulk_upload_batches bulk_batches_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY bulk_batches_tenant_isolation ON cv_analyzer.bulk_upload_batches USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: bulk_upload_rows bulk_rows_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY bulk_rows_tenant_isolation ON cv_analyzer.bulk_upload_rows USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: bulk_upload_batches; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.bulk_upload_batches ENABLE ROW LEVEL SECURITY;

--
-- Name: bulk_upload_rows; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.bulk_upload_rows ENABLE ROW LEVEL SECURITY;

--
-- Name: client_organizations; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.client_organizations ENABLE ROW LEVEL SECURITY;

--
-- Name: duplicate_application_logs dup_logs_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY dup_logs_tenant_isolation ON cv_analyzer.duplicate_application_logs USING (((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)));


--
-- Name: duplicate_application_logs; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.duplicate_application_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: email_ingest_log; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.email_ingest_log ENABLE ROW LEVEL SECURITY;

--
-- Name: job_campaigns; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.job_campaigns ENABLE ROW LEVEL SECURITY;

--
-- Name: job_criteria; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.job_criteria ENABLE ROW LEVEL SECURITY;

--
-- Name: jobs; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.jobs ENABLE ROW LEVEL SECURITY;

--
-- Name: application_knockout_answer_suggestions ko_suggestions_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY ko_suggestions_tenant_isolation ON cv_analyzer.application_knockout_answer_suggestions USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: application_knockout_validation_runs ko_val_runs_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY ko_val_runs_tenant_isolation ON cv_analyzer.application_knockout_validation_runs USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: application_knockout_answer_validations ko_validations_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY ko_validations_tenant_isolation ON cv_analyzer.application_knockout_answer_validations USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: notification_log; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.notification_log ENABLE ROW LEVEL SECURITY;

--
-- Name: notification_log notification_log_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY notification_log_isolation ON cv_analyzer.notification_log USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id IS NOT NULL) AND ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true)))));


--
-- Name: plan_features; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.plan_features ENABLE ROW LEVEL SECURITY;

--
-- Name: plan_features plan_features_super_admin; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY plan_features_super_admin ON cv_analyzer.plan_features USING ((current_setting('app.role'::text, true) = 'super_admin'::text));


--
-- Name: plan_features plan_features_tenant_read; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY plan_features_tenant_read ON cv_analyzer.plan_features FOR SELECT USING ((current_setting('app.role'::text, true) = ANY (ARRAY['tenant_admin'::text, 'recruiter'::text, 'viewer'::text])));


--
-- Name: application_score_comparisons score_comparisons_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY score_comparisons_isolation ON cv_analyzer.application_score_comparisons USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR (application_id IN ( SELECT applications.application_id
   FROM cv_analyzer.applications
  WHERE ((applications.tenant_id)::text = current_setting('app.current_tenant_id'::text, true))))));


--
-- Name: tenant_features; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.tenant_features ENABLE ROW LEVEL SECURITY;

--
-- Name: tenant_features tenant_features_admin_bypass; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_features_admin_bypass ON cv_analyzer.tenant_features USING ((current_setting('app.current_role'::text, true) = ANY (ARRAY['platform_admin'::text, 'super_admin'::text])));


--
-- Name: tenant_features tenant_features_tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_features_tenant_isolation ON cv_analyzer.tenant_features USING ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid)) WITH CHECK ((tenant_id = (current_setting('app.current_tenant_id'::text, true))::uuid));


--
-- Name: application_workflow_history tenant_isolation; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation ON cv_analyzer.application_workflow_history USING ((tenant_id = (current_setting('app.tenant_id'::text, true))::uuid));


--
-- Name: agency_user_clients tenant_isolation_agency_user_clients; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_agency_user_clients ON cv_analyzer.agency_user_clients USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: application_files tenant_isolation_app_files; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_app_files ON cv_analyzer.application_files USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: application_scores tenant_isolation_app_scores; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_app_scores ON cv_analyzer.application_scores USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR (application_id IN ( SELECT applications.application_id
   FROM cv_analyzer.applications
  WHERE ((applications.tenant_id)::text = current_setting('app.current_tenant_id'::text, true))))));


--
-- Name: applications tenant_isolation_applications; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_applications ON cv_analyzer.applications USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: client_organizations tenant_isolation_client_organizations; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_client_organizations ON cv_analyzer.client_organizations USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: email_ingest_log tenant_isolation_ingest_log; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_ingest_log ON cv_analyzer.email_ingest_log USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: user_invites tenant_isolation_invites; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_invites ON cv_analyzer.user_invites USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: job_campaigns tenant_isolation_job_campaigns; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_job_campaigns ON cv_analyzer.job_campaigns USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: job_criteria tenant_isolation_job_criteria; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_job_criteria ON cv_analyzer.job_criteria USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR (job_id IN ( SELECT jobs.job_id
   FROM cv_analyzer.jobs
  WHERE ((jobs.tenant_id)::text = current_setting('app.current_tenant_id'::text, true))))));


--
-- Name: jobs tenant_isolation_jobs; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_jobs ON cv_analyzer.jobs USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: tenant_usage_monthly tenant_isolation_tenant_usage_monthly; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_tenant_usage_monthly ON cv_analyzer.tenant_usage_monthly USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: tenants tenant_isolation_tenants; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_tenants ON cv_analyzer.tenants USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: users tenant_isolation_users; Type: POLICY; Schema: cv_analyzer; Owner: -
--

CREATE POLICY tenant_isolation_users ON cv_analyzer.users USING (((current_setting('app.current_role'::text, true) = 'super_admin'::text) OR ((tenant_id)::text = current_setting('app.current_tenant_id'::text, true))));


--
-- Name: tenant_usage_monthly; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.tenant_usage_monthly ENABLE ROW LEVEL SECURITY;

--
-- Name: tenants; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.tenants ENABLE ROW LEVEL SECURITY;

--
-- Name: user_invites; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.user_invites ENABLE ROW LEVEL SECURITY;

--
-- Name: users; Type: ROW SECURITY; Schema: cv_analyzer; Owner: -
--

ALTER TABLE cv_analyzer.users ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--


