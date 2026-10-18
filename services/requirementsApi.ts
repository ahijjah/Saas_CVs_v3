// Requirements v2 API client. It uses the approved routes only: GET /jobs/{id}/requirements and POST /jobs/{id}/requirements/edits.
// Errors keep the HTTP status, the stable detail code and, for a revision conflict, the current revision, so the UI can react to
// each one. Nothing here stores or logs the document.

const TOKEN_KEY = 'token';

export type ImportanceLevel = 'required' | 'preferred';

export interface RequirementsItem {
  id: string;
  text: string;
  importance: ImportanceLevel;
  weight: number | null;
  origin: 'stated' | 'from_responsibilities' | 'recruiter_added';
  source_text: string | null;
  alternatives: string[] | null;
  experience: { subject: string | null; min_years: number | null } | null;
}

export interface RequirementsCategory {
  weight: number;
  items: RequirementsItem[];
}

export interface ReadinessReason {
  code: string;
  message: string;
  category: string | null;
  item_id: string | null;
}

export interface RequirementsView {
  job_id: string;
  requirements_revision: number;
  schema_version: number;
  document: { schema_version: number; categories: Record<string, RequirementsCategory> };
  review: {
    readiness: { state: string; can_proceed: boolean; scoring_mode: string | null; reasons: ReadinessReason[] };
    confirmation: { status: string; confirmed_at: string | null };
    classification: {
      acknowledgment_required: boolean;
      open_warnings: { id: string; code: string; item_id: string; category: string; acknowledged: boolean }[];
    };
    structure: { pending_item_ids: string[] };
    structural: {
      status: string;
      open_findings: { id: string; code: string; item_ids: string[]; source_text: string; message: string }[];
    };
  };
}

export type RequirementsOperation =
  | { op: 'set_text'; item_id: string; text: string }
  | { op: 'set_importance'; item_id: string; importance: ImportanceLevel }
  | { op: 'set_item_weight'; item_id: string; weight: number | null }
  | { op: 'set_alternatives'; item_id: string; alternatives: string[] | null }
  | { op: 'set_experience'; item_id: string; experience: { subject: string | null; min_years: number | null } | null }
  | { op: 'set_category_weight'; category: string; weight: number }
  | { op: 'add_item'; category: string; text: string; importance: ImportanceLevel; weight: number | null;
      alternatives: string[] | null; experience: { subject: string | null; min_years: number | null } | null }
  | { op: 'remove_item'; item_id: string }
  | { op: 'confirm_no_numeric_score' }
  | { op: 'acknowledge_classification_warning'; warning_id: string }
  | { op: 'confirm_structure'; item_id: string }
  | { op: 'resolve_structural_finding'; finding_id: string; decision: 'fixed' | 'accepted_as_is' | 'dismissed_false_positive';
      note: string | null; evidence_item_ids: string[] | null }
  | { op: 'reorder_items'; category: string; item_ids: string[] };

export class RequirementsApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly currentRevision: number | null;

  constructor(status: number, code: string, message: string, currentRevision: number | null) {
    super(message);
    this.name = 'RequirementsApiError';
    this.status = status;
    this.code = code;
    this.currentRevision = currentRevision;
  }
}

function authHeaders(): HeadersInit {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: authHeaders() });
  let body: any = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail = body?.detail;
    const code = typeof detail === 'object' && detail?.code ? String(detail.code) : 'request_failed';
    const message = typeof detail === 'object' && detail?.message ? String(detail.message)
      : typeof detail === 'string' ? detail : `Request failed (${response.status}).`;
    const current = typeof detail === 'object' && typeof detail?.current_revision === 'number' ? detail.current_revision : null;
    throw new RequirementsApiError(response.status, code, message, current);
  }
  return body as T;
}

export function getRequirements(jobId: string): Promise<RequirementsView> {
  return send<RequirementsView>(`/jobs/${encodeURIComponent(jobId)}/requirements`, { method: 'GET' });
}

export function saveRequirementEdits(
  jobId: string,
  clientRevision: number,
  operations: RequirementsOperation[],
): Promise<{ requirements_revision: number }> {
  return send<{ requirements_revision: number }>(`/jobs/${encodeURIComponent(jobId)}/requirements/edits`, {
    method: 'POST',
    body: JSON.stringify({ client_revision: clientRevision, operations }),
  });
}

// The roles that may edit requirements. Every other role reads the view only; the server refuses edits for them as well.
export const REQUIREMENTS_EDIT_ROLES: readonly string[] = ['admin', 'recruiter'];

export function canEditRequirements(role: string | null | undefined): boolean {
  return !!role && REQUIREMENTS_EDIT_ROLES.includes(role);
}
