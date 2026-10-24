// Final Candidate Matrix. Routes (all under /candidate-matrix): GET /jobs/{id}/columns, GET /jobs/{id}/config, POST /jobs/{id}/rows,
// PUT|DELETE /jobs/{id}/config/job|personal, GET|PUT|DELETE /tenant-config, POST /jobs/{id}/export.
// The server decides who may save a shared layout, save a personal one and export; every number and status on screen comes from it.

import { WEBHOOK_CONFIG } from '../config';

export type MandatoryStatus = 'met' | 'partially_met' | 'not_met' | 'unclear' | 'not_assessed';
export type PreferredStatus = 'yes' | 'no' | 'unclear' | 'not_assessed';
export type GateStatus = 'passed' | 'failed' | 'review_required' | 'not_assessed';
export type Eligibility = 'eligible' | 'not_eligible' | 'review_required' | 'not_assessed';
export type FlagKind = 'unclear_criteria' | 'conflicting_evidence' | 'evidence_not_found' | 'low_confidence' | 'processing_error' | 'unresolved_review';
export type Severity = 'high' | 'medium' | 'low';
export type Scope = 'job' | 'personal' | 'tenant';
export type ColumnGroup = 'candidate' | 'decision' | 'mandatory' | 'preferred' | 'gates' | 'workflow';

export interface MatrixColumn {
  id: string;
  group: ColumnGroup;
  label: { en: string; ar: string };
  sortable: boolean;
  available: boolean;
  unavailable_reason: string | null;
  kind: 'static' | 'mandatory' | 'preferred' | 'gate';
  ref: string | null;
  default: boolean;
  required: boolean;
}

export interface CriteriaFilter { results: string[]; item_id?: string; gate_key?: string }
export interface MatrixFilters {
  search?: string | null;
  eligibility?: Eligibility[] | null;
  scoring_status?: string[] | null;
  score_min?: number | null;
  score_max?: number | null;
  workflow_status?: string[] | null;
  source?: string[] | null;
  flags?: FlagKind[] | null;
  applied_after?: string | null;
  applied_before?: string | null;
  assigned_to?: string | null;
  mandatory?: { item_id: string; results: MandatoryStatus[] }[];
  preferred?: { item_id: string; results: PreferredStatus[] }[];
  gates?: { gate_key: string; results: GateStatus[] }[];
}
export interface MatrixSort { column: string; direction: 'asc' | 'desc' }
export interface MatrixLayout {
  columns: string[];
  frozen_candidate: boolean;
  sort: MatrixSort | null;
  filters: MatrixFilters;
  page_size: number;
}

export interface LayerInfo { version: number; updated_at: string | null; updated_by: string | null; readable: boolean }
export interface MatrixConfig {
  job_id: string;
  source: 'personal' | 'job' | 'tenant' | 'system';
  effective: MatrixLayout;
  unavailable_columns: string[];
  layers: { personal: LayerInfo | null; job: LayerInfo | null; tenant: LayerInfo | null };
  system_default: MatrixLayout;
  permissions: { edit_shared: boolean; edit_personal: boolean; export: boolean };
}
export interface MatrixColumns {
  job_id: string;
  groups: ColumnGroup[];
  columns: MatrixColumn[];
  vocabulary: { mandatory: string[]; preferred: string[]; gates: string[]; eligibility: string[]; flags: string[] };
}

export interface FlagEntry { kind: FlagKind; severity: Severity; count: number; targets: { type: 'item' | 'gate' | 'application'; id: string }[] }
export interface MatrixCell {
  status: string;
  confidence?: number | null;
  resolved?: boolean;
  review_reason?: string | null;
  flagged?: boolean;
  overridden?: boolean;
}
export interface MatrixRow {
  application_id: string;
  candidate_name: string;
  applied_at: string | null;
  source: string | null;
  workflow_status: string | null;
  assigned_to: { id: string; name: string } | null;
  tags: { id: string; name: string }[];
  eligibility: Eligibility;
  score: number | null;
  scoring: { state: string; method: 'item_level_v2' | 'legacy_category' | 'none'; revision_used: number | null; stale: boolean };
  mandatory: { assessed: boolean; met: number | null; partially_met: number | null; not_met: number | null; unclear: number | null; total: number | null };
  preferred: { assessed: boolean; yes: number | null; no: number | null; unclear: number | null; total: number | null };
  gates: { assessed: boolean; passed: number | null; failed: number | null; review_required: number | null; not_assessed: number | null; total: number };
  flags: { total: number; items: FlagEntry[] };
  cells: Record<string, MatrixCell>;
}
export interface MatrixPage {
  job_id: string;
  rows: MatrixRow[];
  total: number;
  page: number;
  page_size: number;
  columns: string[];
  sort: MatrixSort | null;
}

export class MatrixApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'MatrixApiError';
    this.status = status;
    this.code = code;
  }
}

function headers(): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('token');
  if (token) h['Authorization'] = `Bearer ${token}`;
  return h;
}

async function failure(response: Response): Promise<MatrixApiError> {
  let body: any = null;
  try { body = await response.json(); } catch { body = null; }
  const detail = body?.detail;
  const code = typeof detail === 'object' && detail?.code ? String(detail.code) : 'request_failed';
  const message = typeof detail === 'object' && detail?.message ? String(detail.message)
    : typeof detail === 'string' ? detail : `Request failed (${response.status}).`;
  return new MatrixApiError(response.status, code, message);
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${WEBHOOK_CONFIG.CANDIDATE_MATRIX_BASE_URL}${path}`, { ...init, headers: headers() });
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

const enc = encodeURIComponent;
const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

export const getMatrixColumns = (jobId: string) => send<MatrixColumns>(`/jobs/${enc(jobId)}/columns`, { method: 'GET' });
export const getMatrixConfig = (jobId: string) => send<MatrixConfig>(`/jobs/${enc(jobId)}/config`, { method: 'GET' });
export const getMatrixRows = (jobId: string, body: { columns: string[]; filters: MatrixFilters; sort: MatrixSort | null; page: number; page_size: number }) =>
  send<MatrixPage>(`/jobs/${enc(jobId)}/rows`, post({ ...body, sort: body.sort ?? undefined }));
export const saveMatrixLayout = (jobId: string, scope: 'job' | 'personal', layout: MatrixLayout, expectedVersion: number | null) =>
  send<{ scope: string; version: number }>(`/jobs/${enc(jobId)}/config/${scope}`, { method: 'PUT', body: JSON.stringify({ layout, expected_version: expectedVersion }) });
export const resetMatrixLayout = (jobId: string, scope: 'job' | 'personal') =>
  send<{ scope: string; removed: boolean }>(`/jobs/${enc(jobId)}/config/${scope}`, { method: 'DELETE' });

export interface ExportRequest { columns: string[]; filters: MatrixFilters; sort: MatrixSort | null; lang: 'en' | 'ar'; include_metadata?: boolean }
/** The CSV as a Blob, with the file name the server chose. */
export async function exportMatrix(jobId: string, body: ExportRequest): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(`${WEBHOOK_CONFIG.CANDIDATE_MATRIX_BASE_URL}/jobs/${enc(jobId)}/export`, {
    ...post({ ...body, sort: body.sort ?? undefined }), headers: headers() });
  if (!response.ok) throw await failure(response);
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await response.blob(), filename: match ? match[1] : 'candidate-matrix.csv' };
}
