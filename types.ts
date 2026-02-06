
export interface User {
  email: string;
  role: string;
  tenant_name: string;
  cv_ingestion_mode?: 'platform_email' | 'forwarding';
}

export interface AuthState {
  token: string | null;
  user: User | null;
}

export interface Job {
  job_code: string;
  job_id: string;
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

// Added Application interface to satisfy imports in ApplicationsList.tsx
export interface Application {
  id: string;
  application_id: string; // Required for details lookup
  candidate_name: string;
  score: number;
  status: 'qualified' | 'partial' | 'rejected';
  applied_date: string;
  summary: string;
}

export type ApplicationFilter = 'qualified' | 'partial' | 'rejected' | 'all';
