import { WEBHOOK_CONFIG } from '../config';
import { apiService } from './api';
import type { ServerDocument } from '../utils/requirementsV2';

// Client for the requirements-v2 editing/review API (backend: routers/job_requirements.py). The server owns the
// revision, acknowledgments, confirmations and provenance; this client only sends what the recruiter decided.

export interface RequirementsView {
  job_id: string;
  revision: number;
  requirements: ServerDocument;
  original: ServerDocument | null;
  edited_categories: Record<string, boolean> | null;
  readiness: {
    state: string; scoring_mode: string | null; can_proceed: boolean;
    reasons: { code: string; message: string; category: string | null; item_id: string | null }[];
    open_warning_ids: string[]; unresolved_warning_ids: string[]; structure_review_item_ids: string[];
  };
  classification_warnings: {
    id: string; code: string; item_id: string; category: string; message: string;
    evidence: { code: string; cue: string | null; source_text: string | null };
    state: 'acknowledged' | 'unresolved' | 'inactive' | 'resolved';
    acknowledgment: { user_id: string; acknowledged_at: string } | null;
  }[];
  classification_policy: { key: string; require_acknowledgment: boolean };
  preferred_only_confirmation: { confirmed: boolean; user_id?: string; confirmed_at?: string };
  structure_review: {
    needs_review_item_ids: string[];
    items: { item_id: string; category: string; state: 'original' | 'confirmed' | 'corrected' | 'entered' | 'needs_review';
             record: { kind: string; user_id: string; recorded_at: string } | null }[];
  };
  similarity_warnings: {
    id: string; kind: 'possible_duplicate' | 'similar_requirement';
    items: { item_id: string; category: string; importance: string }[];
    same_category: boolean; differences: string[];
  }[];
  similarity_method: string;
  discarded_client_fields?: string[];
  changed?: boolean;
  can_edit: boolean;
}

/** The calls the panel makes; tests and previews can supply a mock. */
export interface RequirementsApi {
  get(jobId: string): Promise<RequirementsView>;
  save(jobId: string, expectedRevision: number, requirements: unknown): Promise<RequirementsView>;
  acknowledge(jobId: string, expectedRevision: number, warningId: string): Promise<RequirementsView>;
  confirmStructure(jobId: string, expectedRevision: number, itemId: string): Promise<RequirementsView>;
  confirmNoScore(jobId: string, expectedRevision: number): Promise<RequirementsView>;
}

const base = (jobId: string) => `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${jobId}/requirements`;

export const createRequirementsApi = (token: string): RequirementsApi => ({
  get: (jobId) => apiService.get(base(jobId), {}, token),
  save: (jobId, expected_revision, requirements) => apiService.put(base(jobId), { expected_revision, requirements }, token),
  acknowledge: (jobId, expected_revision, warning_id) =>
    apiService.post(`${base(jobId)}/classification-warnings/acknowledge`, { expected_revision, warning_id }, token),
  confirmStructure: (jobId, expected_revision, item_id) =>
    apiService.post(`${base(jobId)}/structure-review/confirm`, { expected_revision, item_id }, token),
  confirmNoScore: (jobId, expected_revision) =>
    apiService.post(`${base(jobId)}/confirm-no-numeric-score`, { expected_revision }, token),
});
