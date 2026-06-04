
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
  allow_advanced_workflow_move?: boolean;
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

export type CampaignStatus = 'draft' | 'active' | 'on_hold' | 'closed' | 'cancelled';

export interface Campaign {
  campaign_id: string;
  tenant_id: string;
  client_organization_id?: string | null;
  client_org_name?: string | null;
  name: string;
  description?: string | null;
  status: CampaignStatus;
  start_date?: string | null;
  end_date?: string | null;
  target_hire_count?: number | null;
  campaign_owner_id?: string | null;
  campaign_owner_name?: string | null;
  notes?: string | null;
  public_title?: string | null;
  is_publicly_listed?: boolean;
  created_by?: string | null;
  created_at?: string;
  updated_at?: string;
  jobs_total?: number;
  jobs_active?: number;
  // detail-level aggregate stats (only in GET /campaigns/:id response)
  applications_total?: number;
  applications_qualified?: number;
  applications_partial?: number;
  applications_rejected?: number;
  applications_scored?: number;
}

export interface CampaignJobRef {
  job_id: string;
  job_code: string;
  job_title: string;
  job_status: string;
  client_organization_id?: string | null;
  created_at?: string;
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
  campaign_id?: string | null;
  campaign_name?: string | null;
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
  applications_scored?: number;
  applications_security_blocked?: number;
  applications_duplicate_blocked?: number;
  applications_possible_duplicate?: number;
  applications_failed_needs_review?: number;
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

export type WorkflowStatus =
  | 'awaiting_review'
  | 'under_review'
  | 'shortlisted'
  | 'interviewing'
  | 'offer_made'
  | 'hired'
  | 'rejected'
  | 'withdrawn'
  | 'on_hold';

export interface WorkflowHistoryEntry {
  history_id: string;
  from_status: WorkflowStatus | null;
  to_status: WorkflowStatus;
  note: string | null;
  changed_by_name: string | null;
  created_at: string | null;
  is_advanced_move?: boolean;
}

export interface CandidateComment {
  comment_id: string;
  application_id: string;
  user_id: string;
  author_name: string;
  author_email: string;
  comment_text: string;
  mentions: string[];
  created_at: string | null;
  updated_at: string | null;
  is_own: boolean;
}

export interface AssignableUser {
  user_id: string;
  email: string;
  full_name: string;
  role: string;
}

export interface Application {
  id: string;
  application_id: string;
  candidate_name: string;
  score: number | null;
  status: ApplicationDecision | null;
  processing_status?: string;
  stopped_reason?: 'security_blocked' | 'extraction_failed' | 'processing_error' | 'duplicate_blocked' | 'other' | null;
  duplicate_status?: 'not_duplicate' | 'possible_duplicate' | 'exact_duplicate';
  duplicate_reason?: string | null;
  duplicate_reference_application_id?: string | null;
  evaluation_exit_reason?: string | null;
  security_check_status?: 'passed' | 'warning' | 'blocked' | null;
  applied_date: string;
  summary: string;
  workflow_status?: WorkflowStatus;
  recruiter_notes?: string | null;
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
  preferred_contact_email?: string | null;
  preferred_contact_source?: string | null;
  preferred_contact_confidence?: number | null;
  submitted_by_user_id?: string | null;
  submitted_by_name?: string | null;
  submitted_by_email?: string | null;
  applied_at?: string | null;
  original_filename?: string | null;
  decision: ApplicationDecision;
  overall_score: number;
  submission_source?: 'manual_upload' | 'email_forwarding' | 'platform_email';
  processing_status?: string;
  stopped_reason?: 'security_blocked' | 'extraction_failed' | 'processing_error' | 'duplicate_blocked' | 'other' | null;
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
  duplicate_status?: 'not_duplicate' | 'possible_duplicate' | 'exact_duplicate';
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
  security_detected_snippets?: string[];
  security_checked_at?: string | null;
  knockout_answers?: KnockoutAnswerRecord[];
  knockout_suggestions?: KnockoutSuggestionRecord[];
  workflow_status?: WorkflowStatus;
  recruiter_notes?: string | null;
  workflow_history?: WorkflowHistoryEntry[];
}

export type ApplicationFilter = 'qualified' | 'partial' | 'rejected' | 'low_match' | 'all' | 'possible_duplicate' | 'ai_scored' | 'security_blocked' | 'duplicate_blocked' | 'failed_needs_review' | 'blocked' | 'workflow_awaiting_review' | 'workflow_under_review' | 'workflow_shortlisted' | 'workflow_interviewing' | 'workflow_offer' | 'workflow_hired' | 'workflow_rejected' | 'workflow_withdrawn' | 'workflow_on_hold';

export interface UploadedCV {
  application_id: string;
  candidate_name: string;
  processing_status: 'pending' | 'queued' | 'processing' | 'ai_scored' | 'failed';
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

/** Knockout answer with question metadata — returned in application detail API. */
export interface KnockoutAnswerRecord {
  answer_id?: string | null;
  question_id: string;
  answer_value?: string | null;
  is_disqualifying?: boolean | null;
  answer_source?: string | null;
  answer_method?: string | null;
  updated_at?: string | null;
  updated_by_name?: string | null;
  question_text: string;
  question_type: 'yes_no' | 'single_choice' | 'number';
  is_required: boolean;
  options?: string[] | null;
  passing_criteria?: PassingCriteria | null;
  display_order: number;
}

/** AI-generated suggestion for an unanswered knockout question. */
export interface KnockoutSuggestionRecord {
  suggestion_id: string;
  question_id: string;
  suggested_answer?: string | null;
  suggested_source: 'candidate_email' | 'ai_cv' | 'ai_email' | 'ai_cv_email' | 'not_found';
  answer_method?: string | null;
  confidence: number;
  evidence_text?: string | null;
  verification_status: 'verified' | 'inferred' | 'no_evidence' | 'contradiction' | 'not_found';
  ai_model?: string | null;
  status: 'pending' | 'accepted' | 'ignored';
  accepted_at?: string | null;
  accepted_by_name?: string | null;
  created_at?: string | null;
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

export type PromptCategory = 'criteria' | 'scoring' | 'screening' | 'summary' | 'interview' | 'knockout';

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

// ─── Group B — Interviews ─────────────────────────────────────────────────────

export type InterviewType = 'phone_screen' | 'technical' | 'hr' | 'panel' | 'final' | 'other';
export type InterviewStatus = 'scheduled' | 'completed' | 'cancelled' | 'no_show';
export type InterviewRecommendation = 'strong_yes' | 'yes' | 'neutral' | 'no' | 'strong_no';

export interface CandidateInterview {
  interview_id: string;
  application_id: string;
  interview_type: InterviewType;
  scheduled_at: string | null;
  duration_min: number | null;
  location: string | null;
  interviewers: string[];
  status: InterviewStatus;
  notes: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  feedback_count: number;
}

export interface InterviewFeedback {
  feedback_id: string;
  interview_id: string;
  reviewer_id: string;
  reviewer_name: string | null;
  overall_rating: number | null;
  recommendation: InterviewRecommendation | null;
  scorecard: Record<string, any>;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  is_own: boolean;
}

// ─── Group B — Approvals ──────────────────────────────────────────────────────

export type ApprovalDecision = 'pending' | 'approved' | 'rejected' | 'needs_revision';

export interface CandidateApproval {
  approval_id: string;
  application_id: string;
  approval_stage: string;
  stage_order: number;
  approver_id: string | null;
  approver_name: string | null;
  decision: ApprovalDecision;
  comment: string | null;
  decided_at: string | null;
  requested_by: string | null;
  requested_by_name: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// ─── Group B — Workflow Policies ──────────────────────────────────────────────

export interface WorkflowPolicies {
  require_interview_before_offer: boolean;
  require_approval_before_hire: boolean;
  require_rejection_reason: boolean;
  allow_bulk_reject: boolean;
  require_interview_feedback: boolean;
  required_approval_stages: string[];
}

export interface WorkflowPoliciesResponse {
  policies: WorkflowPolicies;
  updated_at: string | null;
  updated_by_name: string | null;
}

// ─── Group C — Analytics & SLA Monitoring ─────────────────────────────────

export interface FunnelStageMetric {
  workflow_status: WorkflowStatus;
  stage_count: number;
  conversion_from_previous: number | null;
  percentage_of_pipeline: number;
  avg_days_in_stage: number | null;
  median_days_in_stage: number | null;
}

export interface FunnelMetricsResponse {
  stages: FunnelStageMetric[];
  total_applications: number;
  date_range: {
    from: string;
    to: string;
  };
  filters: Record<string, any>;
}

export interface RecruiterProductivityMetric {
  user_id: string;
  recruiter_name: string;
  total_applications_assigned: number;
  applications_in_review: number;
  workflow_moves_made: number;
  interviews_completed: number;
  feedback_provided: number;
  approvals_decided: number;
  avg_days_assigned: number | null;
}

export interface RecruiterProductivityResponse {
  recruiters: RecruiterProductivityMetric[];
  date_range: {
    from: string;
    to: string;
  };
}

export interface AgingMetric {
  application_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  workflow_status: WorkflowStatus;
  assigned_user_id: string | null;
  assigned_user_name: string | null;
  days_in_status: number;
  sla_status: 'green' | 'amber' | 'red';
  pending_approvals: number;
}

export interface AgingMetricsResponse {
  metrics: AgingMetric[];
  total_count: number;
  red_count: number;
  amber_count: number;
  green_count: number;
  filters: Record<string, any>;
}

export interface SLAThresholds {
  review_days: number;
  interview_feedback_days: number;
  approval_days: number;
  offer_response_days: number;
}

export interface RecruitmentInsight {
  insight_id: string;
  type: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  description: string;
  metric_value: number | null;
  threshold: number | null;
  suggested_action: string;
  related_entity_type: string | null;
  related_entity_id: string | null;
}

export interface InsightsResponse {
  insights: RecruitmentInsight[];
  generated_at: string;
}

export interface CandidateTag {
  tag_id: string;
  tag_name: string;
  color: string | null;
}

export interface TalentPoolCandidate {
  application_id: string;
  candidate_name: string;
  candidate_email: string;
  workflow_status: WorkflowStatus;
  assigned_user_id: string | null;
  assigned_user_name: string | null;
  created_at: string;
  job_title: string;
  job_id: string;
}

export interface TalentPoolResponse {
  candidates: TalentPoolCandidate[];
  total: number;
  skip: number;
  limit: number;
}

export interface MessageTemplate {
  template_id: string;
  name: string;
  category: string;
  subject: string;
  body: string;
  language: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  created_by_name: string | null;
}

export interface CandidateCommunication {
  communication_id: string;
  channel: string;
  direction: string;
  subject: string | null;
  body: string | null;
  status: string;
  error_message?: string | null;
  candidate_email?: string | null;
  created_at: string;
  template_name: string | null;
  template_category: string | null;
  created_by_name: string | null;
  is_automated?: boolean;
  event_type?: string | null;
}

export interface AutomationRule {
  rule_id: string;
  event_type: string;
  category: string;
  mode: 'disabled' | 'draft_only' | 'auto_send';
  is_active: boolean;
  delay_minutes: number;
  template_id: string | null;
  template_name: string | null;
  created_at: string;
  updated_at: string;
}
