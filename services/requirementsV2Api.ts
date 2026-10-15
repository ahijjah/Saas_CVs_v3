import { WEBHOOK_CONFIG } from '../config';
import { apiService } from './api';
import type { ServerDocument } from '../utils/requirementsV2';

// Client for the requirements-v2 editing/review API (backend: routers/job_requirements.py). The server owns the
// revision, acknowledgments, confirmations and provenance; this client only sends what the recruiter decided.

/** One open issue from the server-side checks (derived by the backend on every call; the UI never recomputes it). */
export interface PipelineIssue {
  gate: 'validation' | 'injection' | 'split_or' | 'classification' | 'conflict' | 'structure' | 'confirmation' | 'no_items' | 'extraction';
  id: string; kind: string; message: string; item_ids: string[]; category: string | null;
  blocks_when_ack_required: boolean; blocks_when_ack_not_required: boolean; resolution_options: string[];
  details?: {
    instruction_text?: string | null; source_text?: string | null; item_text?: string | null; clause?: string | null; category?: string;
    proposed_weight?: number | null; contaminated_applied_weight?: number | null;
    options?: string[]; shared_evidence?: string | null; form?: string; item_texts?: Record<string, string>;
    model_warnings?: string[]; jd_statements?: { class: string; text: string }[]; flagged_importance?: string;
  } | null;
}

export interface ModelConflict {
  id: string; code: string; item_id: string; category: string; message: string; flagged_importance: string;
  state: 'unresolved' | 'acknowledged' | 'inactive' | 'resolved';
  model_warnings: string[]; jd_statements: { class: string; text: string }[];
  acknowledgment: { user_id: string; acknowledged_at: string } | null;
}

export interface PipelineInfo {
  status: 'ok' | 'unavailable' | 'invalid_record' | 'not_extracted'; available: boolean; errors: string[]; message?: string;
  contract_version?: string; record_version?: string; job_description_sha256?: string;
  component_versions?: { pipeline?: string; injection_guard?: string; split_or_guard?: string; warning_adapter?: string; extraction_prompt?: { version?: string; sha256?: string } | null };
  provenance?: { extraction_prompt?: { version?: string; sha256?: string } | null; model?: string | null };
  raw_response?: { sha256: string | null; finish_reason: string | null; bytes: number | null; text?: string | null };
  raw_ai_output?: unknown;
}

export type GateName = 'classification' | 'conflict';

export interface RequirementsView {
  job_id: string;
  revision: number;
  requirements: ServerDocument;
  original: ServerDocument | null;
  edited_categories: Record<string, boolean> | null;
  readiness: {
    state: string; scoring_mode: string | null; can_proceed: boolean;
    reasons: { code: string; message: string; category: string | null; item_id: string | null; field?: string | null }[];
    open_warning_ids: string[]; unresolved_warning_ids: string[]; structure_review_item_ids: string[];
    /** false/absent: the additional (pipeline) checks were NOT applied to this readiness. */
    guarded?: boolean; basis?: 'pipeline' | 'pipeline_inputs' | 'frozen_only' | 'pipeline_record_invalid';
  };
  /** Which job fields the analysis was made from changed, or cannot be verified (null without a stored analysis; absent on older servers). */
  inputs?: { state: 'bound' | 'inputs_changed' | 'inputs_unverified'; changed: string[]; unverified: boolean } | null;
  /** Open AI-directed text found in a job field: the field and the exact text (null without a usable record; absent on older servers). */
  context_findings?: { id: string; field: string; snippet: string; rule_codes: string[] }[] | null;
  /** Additional checks (all optional: absent on older servers; null without a usable pipeline record). */
  pipeline?: PipelineInfo;
  unresolved_issues?: PipelineIssue[] | null;
  gates?: Record<string, { open: number; blocks_when_ack_required: boolean; blocks_when_ack_not_required: boolean }> | null;
  model_conflicts?: ModelConflict[] | null;
  normalized_warnings?: { index: number; text: string; kind: string; linked_item_ids: string[]; informational: boolean }[] | null;
  informational?: { generic_model_notes: { index: number; text: string; kind: string }[]; parser_review: { code: string; message: string }[] } | null;
  coverage_warnings?: { code: string; heading: string; candidate_lines: number }[] | null;   // non-blocking: duties may be missing (total omission only)
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
  /** Re-analysis of a stale or unverified analysis (absent on older servers). */
  recovery?: RecoveryView | null;
  /** Present for requirements-v2 jobs (schema marker 2). While the extraction has no document the view is a read-only skeleton. */
  extraction?: ExtractionStatus | null;
  can_edit: boolean;
}

/** One analysis version of a job: the draft in use, or a candidate (kept by the server in an append-only history). */
export interface RecoveryVersion {
  version_id: string; version_no: number; kind: 'candidate';
  status: 'requested' | 'completed' | 'rejected' | 'applied' | 'discarded' | 'superseded';
  requested_at: string | null; completed_at: string | null; request_key: string | null; input_sha256: string | null;
  job_revision_at_request: number | null;
  rejection_code?: string | null; applied_choice?: 'take' | 'merge' | null;
}

/** Re-analysis state. Absent on older servers; `enabled: false` while the platform switch is off (the default), which shows no controls. */
export interface RecoveryView {
  enabled: boolean;
  can_request: boolean;
  reason: 'disabled' | 'no_analysis' | 'bound' | 'candidate_open' | null;
  blockers: string[];
  active_version: { version_id: string; version_no: number };
  open_candidate: RecoveryVersion | null;
  versions: RecoveryVersion[];
}

/** The decisions a merge needs. Each key is answered with one of the choices listed for its group. */
export interface MergePreview {
  version_id: string; revision: number;
  conflicts: { key: string; category: string; text: string | null; fields: string[]; draft: Record<string, unknown>; candidate: Record<string, unknown> }[];
  draft_only: { key: string; category: string; text: string | null; origin: string | null }[];
  candidate_only: { key: string; category: string; text: string | null; origin: string | null }[];
  weights: { key: string; category: string; draft: number | null; candidate: number | null }[];
}

export const MERGE_CHOICES = {
  conflicts: ['draft', 'candidate'], draft_only: ['keep', 'drop'], candidate_only: ['add', 'skip'], weights: ['draft', 'candidate'],
} as const;

export interface ExtractionStatus {
  status: 'pending' | 'processing' | 'completed' | 'failed' | null;
  error: string | null;
  /** Server-decided: true only for an editor (admin / HR manager) on a failed attempt of a job that has no document yet. */
  retry_available: boolean;
}

/** The calls the panel makes; tests and previews can supply a mock. */
export interface RequirementsApi {
  get(jobId: string): Promise<RequirementsView>;
  save(jobId: string, expectedRevision: number, requirements: unknown): Promise<RequirementsView>;
  acknowledge(jobId: string, expectedRevision: number, warningId: string, gate?: GateName): Promise<RequirementsView>;
  confirmStructure(jobId: string, expectedRevision: number, itemId: string): Promise<RequirementsView>;
  confirmNoScore(jobId: string, expectedRevision: number): Promise<RequirementsView>;
  /** Asks the server for a new extraction attempt (only for a failed attempt with no document yet). */
  retryExtraction(jobId: string): Promise<{ job_id: string; extraction: { status: string } }>;
  /** Starts a new analysis beside the draft. `requestKey` makes a repeated request (e.g. a double click) return the same version. */
  requestRecovery(jobId: string, expectedRevision: number, requestKey: string): Promise<{ version: RecoveryVersion; idempotent: boolean }>;
  mergePreview(jobId: string, versionId: string): Promise<MergePreview>;
  /** 'take' replaces the draft with the candidate; 'merge' needs one resolution per key of the preview. The caller reloads the view. */
  applyRecovery(jobId: string, versionId: string, expectedRevision: number, choice: 'take' | 'merge', resolutions?: Record<string, string>): Promise<unknown>;
  keepRecovery(jobId: string, versionId: string): Promise<{ kept: boolean; version_id: string }>;
  discardRecovery(jobId: string, versionId: string): Promise<{ discarded: boolean; version_id: string; idempotent: boolean }>;
}

/** Whether the requirements-v2 switch is on. The job-creation screen offers the v2 choice only then (read-only, boolean only). */
export const getCreationAvailability = (token: string): Promise<{ enabled: boolean }> =>
  apiService.get(`${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/requirements-v2/availability`, {}, token);

const base = (jobId: string) => `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${jobId}/requirements`;

export const createRequirementsApi = (token: string): RequirementsApi => ({
  get: (jobId) => apiService.get(base(jobId), {}, token),
  save: (jobId, expected_revision, requirements) => apiService.put(base(jobId), { expected_revision, requirements }, token),
  acknowledge: (jobId, expected_revision, warning_id, gate = 'classification') =>
    apiService.post(`${base(jobId)}/classification-warnings/acknowledge`, { expected_revision, warning_id, gate }, token),
  confirmStructure: (jobId, expected_revision, item_id) =>
    apiService.post(`${base(jobId)}/structure-review/confirm`, { expected_revision, item_id }, token),
  confirmNoScore: (jobId, expected_revision) =>
    apiService.post(`${base(jobId)}/confirm-no-numeric-score`, { expected_revision }, token),
  retryExtraction: (jobId) => apiService.post(`${base(jobId)}/extraction/retry`, {}, token),
  requestRecovery: (jobId, expected_revision, request_key) =>
    apiService.post(`${base(jobId)}/recovery/requests`, { expected_revision, request_key }, token),
  mergePreview: (jobId, versionId) => apiService.get(`${base(jobId)}/recovery/versions/${versionId}/merge`, {}, token),
  applyRecovery: (jobId, versionId, expected_revision, choice, resolutions) =>
    apiService.post(`${base(jobId)}/recovery/versions/${versionId}/apply`, { expected_revision, choice, resolutions: resolutions ?? null }, token),
  keepRecovery: (jobId, versionId) => apiService.post(`${base(jobId)}/recovery/versions/${versionId}/keep`, {}, token),
  discardRecovery: (jobId, versionId) => apiService.post(`${base(jobId)}/recovery/versions/${versionId}/discard`, {}, token),
});
