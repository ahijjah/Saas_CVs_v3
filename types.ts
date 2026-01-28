
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
  job_title: string;
  job_client: string;
  job_status: 'Active' | 'Closed' | 'Draft';
  applications_total: number;
  applications_qualified: number;
  applications_partial: number;
  applications_rejected: number;
}

export interface Application {
  id: string;
  candidate_name: string;
  score: number;
  status: 'qualified' | 'partial' | 'rejected';
  applied_date: string;
  summary: string;
}

export interface JobDetails extends Job {
  description: string;
  requirements: string[];
  location: string;
  salary_range: string;
  skills_required?: string[];
  experience_min_years?: number;
  education_requirement?: string;
  scoring_weights?: {
    technical?: number;
    experience?: number;
    education?: number;
    culture?: number;
  };
}

export type ApplicationFilter = 'qualified' | 'partial' | 'rejected' | 'all';
