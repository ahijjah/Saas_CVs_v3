
export interface User {
  user_id?: string;
  sub?: string;
  tenant_id?: string;
  email: string;
  role: string;
  tenant_name?: string;
  tenant_type?: string;
  cv_ingestion_mode?: 'platform_email' | 'forwarding';
  subscription_status?: SubscriptionStatus;
  must_change_password?: boolean;
  job_application_controls_enabled?: boolean;
}

export type SubscriptionStatus =
  | 'pending_plan_selection'
  | 'pending_payment'
  | 'pending_sales_contact'
  | 'trial'
  | 'active'
  | 'grace'
  | 'trial_expired'
  | 'suspended'
  | 'cancelled'
  | 'cancelled_pending_expiry'
  | 'expired';

export interface UserProfile {
  user_id: string;
  tenant_id: string;
  tenant_name: string;
  admin_name: string;
  email: string;
  role: string;
  intake_method: 'IMAP' | 'FORWARD';
  forwarding_email: string | null;
  email_domain?: string;
  plan?: string | null;
  pending_plan?: string | null;
  tenant_type?: string;
  max_users?: number;
  max_jobs?: number;
  tenant_status?: string;
  subscription_status?: SubscriptionStatus;
  trial_end_at?: string | null;
  tenant_created_at?: string | null;
  active_users_count?: number;
}

export interface TenantUser {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  status: 'active' | 'disabled' | 'pending_email_verification';
  created_at: string | null;
  last_login_at: string | null;
}

export interface AuthState {
  token: string | null;
  user: User | null;
}

// Super Admin Interfaces
export interface AdminOverview {
  total_tenants: number;
  active_tenants: number;
  total_users: number;
  total_jobs: number;
  total_applications: number;
  applications_today: number;
  failed_ingest_24h: number;
}

export interface Tenant {
  tenant_id: string;
  tenant_name: string;
  tenant_code: string;
  tenant_type?: TenantType;
  status: 'active' | 'suspended';
  created_at: string;
  users_count: number;
  jobs_count: number;
  applications_count: number;
}

export interface AdminUser {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  status: 'active' | 'disabled';
  created_at: string;
  last_login_at: string | null;
  tenant_id: string;
  tenant_name: string;
  tenant_code: string;
}

export type TenantType = 'organization' | 'agency' | 'individual_recruiter';

export interface ClientOrganization {
  client_organization_id: string;
  tenant_id: string;
  organization_name: string;
  industry?: string | null;
  contact_person?: string | null;
  email?: string | null;
  phone?: string | null;
  logo_url?: string | null;
  notes?: string | null;
  status: 'active' | 'inactive';
  created_at?: string;
  updated_at?: string;
  active_jobs_count?: number;
  assigned_users_count?: number;
}

export interface AgencyUserClient {
  assignment_id: string;
  user_id: string;
  email: string;
  full_name: string;
  tenant_role: string;
  user_status: string;
  assigned_at?: string;
}

export interface Job {
  job_id: string;
  job_code: string;
  job_title: string;
  job_client: string;
  job_status: 'Active' | 'Closed' | 'Draft';
  criteria_extraction_status?: string;
  tenant_name?: string;
  job_type?: string;
  location?: string;
  duration?: string;
  posted_date?: string;
  closing_date?: string;
  salary_range?: string;
  ingestion_note?: string;
  platform_email?: string;
  receive_cv_via_forwarding_email?: boolean;
  receive_cv_via_platform_email?: boolean;
  restrict_forwarding_sender_to_tenant_email?: boolean;
  client_organization_id?: string | null;
  client_org_name?: string | null;
  vacancies_count?: number | null;
  applications_total: number;
  applications_qualified: number;
  applications_partial: number;
  applications_rejected: number;
  applications_evaluated?: number;
  applications_pending?: number;
  applications_above_threshold?: number;
  applications_below_threshold?: number;
  applications_recommended?: number;
  applications_in_progress?: number;
}

export interface AnalysisJson {
  skills: {
    required: string[];
    preferred: string[];
  };
  experience: {
    minimum_years: number;
    relevant_roles: string[];
    key_responsibilities: string[];
  };
  education: {
    minimum_level: string;
    fields_of_study: string[];
  };
  certifications: string[];
  domain_knowledge: string[];
  other_requirements: string[];
  scoring_weights: {
    skills?: number;
    experience?: number;
    education?: number;
    certifications?: number;
    soft_skills?: number;
    domain_knowledge?: number;
    other_requirements?: number;
  };
}

export interface JobDetails extends Job {
  description: string;
  analysis_json: AnalysisJson | null;
  forwarding_email?: string;
  // Ingestion booleans (replaces forwarding_enabled/alias_enabled)
  receive_cv_via_forwarding_email?: boolean;
  receive_cv_via_platform_email?: boolean;
  restrict_forwarding_sender_to_tenant_email?: boolean;
  // Confirmation email toggles (source-aware)
  send_confirmation_to_cv_email_for_upload?: boolean;
  send_confirmation_to_cv_email_for_forwarding?: boolean;
  send_confirmation_to_sender_for_forwarding?: boolean;
  send_confirmation_to_cv_email_for_platform_email?: boolean;
  // AI comparison toggle
  enable_ai_comparison?: boolean;
  criteria_extraction_status?: 'pending' | 'processing' | 'completed' | 'insufficient' | 'blocked' | 'failed';
  criteria_extraction_error?: string | null;
  // Retry control
  criteria_extraction_retry_count?: number;
  criteria_extraction_max_retries?: number;
  criteria_retry_allowed?: boolean;
  criteria_retry_blocked_reason?: 'max_retries_reached' | 'description_unchanged' | null;
  knockout_questions?: KnockoutQuestion[];
}

// decision='low_match' is a frontend-only display alias for evaluation_stage=1 + gatekeeper_passed=false + decision='rejected'
export type ApplicationDecision = 'qualified' | 'partial' | 'rejected' | 'low_match';

export interface Application {
  id: string;
  application_id: string;
  candidate_name: string;
  score: number | null;
  status: ApplicationDecision | null;
  processing_status?: string;
  duplicate_status?: 'not_duplicate' | 'possible_duplicate';
  duplicate_reason?: string | null;
  duplicate_reference_application_id?: string | null;
  evaluation_exit_reason?: string | null;
  applied_date: string;
  summary: string;
}

export interface ScoreDimension {
  achieved: number;
  max: number;
  weight?: number;
  reasoning?: string;
}

export interface ScoreDetail {
  positive: string[];
  negative: string[];
  additional_strengths?: string[];
  summary: string;
}

export interface AIComparison {
  provider: string;
  model: string;
  final_score: number;
  score_skills?: number;
  score_experience?: number;
  score_education?: number;
  score_certifications?: number;
  score_soft_skills?: number;
  score_domain_knowledge?: number;
  score_other?: number;
  score_details?: Record<string, ScoreDetail>;
  evaluation_notes?: string;
  strengths?: string[];
  gaps_identified?: string[];
  scoring_prompt_code?: string;
  scoring_prompt_version?: number;
  created_at?: string;
}

export interface ApplicationDetailedAnalysis {
  application_id: string;
  candidate_name: string;
  candidate_email?: string;
  candidate_email_from_cv?: string;
  candidate_phone_from_cv?: string;
  email_sender_address?: string;
  submitted_by_user_id?: string | null;
  submitted_by_name?: string | null;
  submitted_by_email?: string | null;
  applied_at?: string | null;
  original_filename?: string | null;
  decision: ApplicationDecision;
  overall_score: number;
  submission_source?: 'manual_upload' | 'email_forwarding' | 'platform_email';
  processing_status?: string;
  evaluation_stage?: 1 | 2 | 3 | null;
  evaluation_exit_reason?: string | null;
  scores: {
    skills?: ScoreDimension;
    experience?: ScoreDimension;
    education?: ScoreDimension;
    certifications?: ScoreDimension;
    soft_skills?: ScoreDimension;
    domain_knowledge?: ScoreDimension;
    other_requirements?: ScoreDimension;
  };
  score_details?: Record<string, ScoreDetail>;
  analysis: {
    summary: string;
    cv_skills_matched?: string;
    cv_experience_summary?: string;
    cv_education_summary?: string;
    cv_certifications_found?: string;
    gaps_identified?: string[];
    interview_focus_points?: string[];
    interview_suggested_questions?: string[];
    evaluation_notes?: string;
    strengths?: string[];
    risks?: string[];
  };
  cv_language?: string;
  local_similarity_score?: number;
  skill_match_ratio?: number;
  gatekeeper_passed?: boolean;
  matched_skills?: string[];
  missing_skills?: string[];
  red_flags?: string[];
  reasoning?: Record<string, string>;
  raw_ai_response?: any;
  ai_model?: string;
  scoring_provider?: string;
  scoring_prompt_code?: string;
  scoring_prompt_version?: number;
  level2_prompt_code?: string;
  level2_prompt_version?: number;
  ai_comparisons?: AIComparison[];
  duplicate_status?: 'not_duplicate' | 'possible_duplicate';
  duplicate_reference_application_id?: string | null;
  duplicate_similarity_score?: number | null;
  duplicate_reason?: string | null;
  duplicate_checked_at?: string | null;
  duplicate_reference?: {
    application_id: string;
    candidate_name: string;
    applied_at: string | null;
  } | null;
  security_check_status?: 'passed' | 'warning' | 'blocked' | null;
  security_risk_level?: 'low' | 'medium' | 'high' | null;
  security_risk_score?: number | null;
  security_reason_codes?: string[];
  security_detected_patterns?: string[];
  security_checked_at?: string | null;
}

export type ApplicationFilter = 'qualified' | 'partial' | 'rejected' | 'low_match' | 'all' | 'possible_duplicate';

export interface UploadedCV {
  application_id: string;
  candidate_name: string;
  processing_status: 'pending' | 'queued' | 'processing' | 'scored' | 'failed';
  decision: ApplicationDecision | null;
  evaluation_stage: 1 | 2 | 3 | null;
  evaluation_stage_label: string | null;
  evaluation_exit_reason: string | null;
  score: number | null;
  uploaded_at: string | null;
  original_filename: string | null;
}

export interface UploadQueueStatus {
  total: number;
  pending: number;
  queued: number;
  processing: number;
  completed: number;
  failed: number;
  is_processing: boolean;
  has_stuck: boolean;
  percentage: number;
}

// ─── Platform Control ─────────────────────────────────────────────────────────

export interface PassingCriteria {
  // yes_no and single_choice
  passing_answers?: string[];
  // number
  operator?: '>=' | '>' | '=' | '<=' | '<';
  value?: number;
}

/** Full question shape — authenticated APIs only. Includes passing_criteria. */
export interface KnockoutQuestion {
  question_id: string;
  question_text: string;
  question_type: 'yes_no' | 'single_choice' | 'number';
  is_required: boolean;
  passing_criteria?: PassingCriteria | null;
  options?: string[] | null;
  display_order: number;
}

/** Public question shape — candidate-facing API. passing_criteria omitted. */
export interface PublicKnockoutQuestion {
  question_id: string;
  question_text: string;
  question_type: 'yes_no' | 'single_choice' | 'number';
  is_required: boolean;
  options?: string[] | null;
  display_order: number;
}

export interface KnockoutAnswer {
  question_id: string;
  answer_value: string;
}

export interface PlatformConfig {
  key: string;
  value: string;
  type: 'string' | 'number' | 'boolean' | 'json';
  category: 'scoring' | 'ai' | 'email' | 'queue' | 'subscription' | 'security' | 'general';
  description: string | null;
  editable: boolean;
  updated_at: string | null;
  updated_by: string | null;
  updated_by_email: string | null;
}

export interface PlanFeature {
  feature_id: string;
  feature_key: string;
  feature_name: string;
  description: string | null;
  value_type: 'boolean' | 'number' | 'text';
  value_boolean: boolean | null;
  value_number: number | null;
  value_text: string | null;
  display_order: number;
}

export interface SubscriptionPlan {
  plan_id: string;
  plan_code: string;
  plan_name: string;
  description: string | null;
  monthly_price: number;
  yearly_price: number;
  currency: string;
  trial_days: number;
  max_campaigns: number;
  max_processed_cvs_per_month: number;
  max_users: number;
  status: 'active' | 'inactive';
  display_order: number;
  created_at: string | null;
  updated_at: string | null;
  updated_by_email: string | null;
  features: PlanFeature[];
}

export interface TenantSubscriptionRow {
  tenant_id: string;
  tenant_name: string;
  email_domain?: string;
  plan: string | null;
  pending_plan?: string | null;
  tenant_type?: TenantType;
  subscription_status: SubscriptionStatus;
  trial_end_at: string | null;
  subscription_started_at: string | null;
  subscription_ends_at: string | null;
  status: 'active' | 'suspended';
  created_at: string;
  user_count?: number;
  job_application_controls_enabled?: boolean;
}

export interface TenantUsage {
  tenant_id: string;
  tenant_name: string;
  plan: string;
  subscription_status: string;
  trial_end_at: string | null;
  subscription_started_at: string | null;
  subscription_ends_at: string | null;
  limits: {
    max_campaigns: number;
    max_users: number;
    max_processed_cvs_per_month: number;
  };
  usage: {
    active_campaigns: number;
    active_users: number;
    processed_cvs_this_month: number;
  };
  percentage_used: {
    campaigns: number;
    users: number;
    cvs: number;
  };
  plan_features: {
    api_access: boolean;
    advanced_analytics: boolean;
    priority_support: boolean;
    custom_ai_prompts: boolean;
  };
}

// ─── Platform Secrets ─────────────────────────────────────────────────────────

export interface PlatformSecret {
  key: string;
  masked_value: string;
  description: string;
  category: string;
  is_critical: boolean;
  has_value: boolean;
  updated_at: string | null;
  updated_by_email: string;
  source: 'env' | 'db';
  restart_required: boolean;
  min_length: number;
  critical_warning?: string | null;
}

// ─── AI Prompts ───────────────────────────────────────────────────────────────

export type PromptCategory = 'criteria' | 'scoring' | 'screening' | 'summary' | 'interview';

export interface AIPrompt {
  prompt_id: string;
  prompt_code: string;
  prompt_name: string;
  prompt_category: PromptCategory;
  system_prompt: string;
  user_prompt_template: string;
  model: string;
  temperature: number;
  max_tokens: number;
  output_language: string;
  is_active: boolean;
  version: number;
  notes: string;
  created_at: string | null;
  updated_at: string | null;
  updated_by_email: string;
}

// ─── Audit Logs ───────────────────────────────────────────────────────────────

export interface AuditLog {
  log_id: string;
  tenant_id: string | null;
  tenant_name: string;
  user_id: string | null;
  user_email: string;
  action: string;
  resource_type: string;
  resource_id: string;
  details: Record<string, any> | null;
  ip_address: string;
  created_at: string | null;
}
