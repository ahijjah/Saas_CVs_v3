
export interface User {
  user_id?: string;
  sub?: string;
  tenant_id?: string;
  email: string;
  role: string;
  tenant_name?: string;
  cv_ingestion_mode?: 'platform_email' | 'forwarding';
}

export interface UserProfile {
  user_id: string;
  tenant_id: string;
  tenant_name: string;
  admin_name: string;
  email: string;
  role: string;
  intake_method: 'IMAP' | 'FORWARD';
  forwarding_email: string | null;
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

export interface Job {
  job_id: string;   // The UUID (e.g., JOB-2026-00074) - MUST be used for all API calls
  job_code: string; // The human-readable code (e.g., IT-2026-002) - Used for UI display only
  job_title: string;
  job_client: string;
  job_status: 'Active' | 'Closed' | 'Draft';
  job_type?: string;
  location?: string;
  posted_date?: string;
  closing_date?: string;
  salary_range?: string;
  ingestion_note?: string; 
  ingestion_mode?: 'forwarding' | 'platform_email';
  ingestion_email?: string | null;
  applications_total: number;
  applications_qualified: number;
  applications_partial: number;
  applications_rejected: number;
  // KPI fields
  applications_evaluated?: number;
  applications_pending?: number;
  applications_above_threshold?: number;
  applications_below_threshold?: number;
  applications_recommended?: number;
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
  analysis_json: AnalysisJson;
}

export interface Application {
  id: string;
  application_id: string; // Required for details lookup
  candidate_name: string;
  score: number;
  status: 'qualified' | 'partial' | 'rejected' | 'low_match';
  applied_date: string;
  summary: string;
}

export interface ScoreDimension {
  achieved: number;
  max: number;
  reasoning?: string;
}

export interface ApplicationDetailedAnalysis {
  application_id: string;
  candidate_name: string;
  decision: 'qualified' | 'partial' | 'rejected' | 'low_match';
  overall_score: number;
  scores: {
    skills?: ScoreDimension;
    experience?: ScoreDimension;
    education?: ScoreDimension;
    certifications?: ScoreDimension;
    soft_skills?: ScoreDimension;
    domain_knowledge?: ScoreDimension;
    other_requirements?: ScoreDimension;
  };
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
  // Intelligence pipeline fields
  cv_language?: string;
  local_similarity_score?: number;
  skill_match_ratio?: number;
  gatekeeper_passed?: boolean;
  matched_skills?: string[];
  missing_skills?: string[];
  red_flags?: string[];
  reasoning?: Record<string, string>;
  raw_ai_response?: any;
}

export type ApplicationFilter = 'qualified' | 'partial' | 'rejected' | 'low_match' | 'all';
