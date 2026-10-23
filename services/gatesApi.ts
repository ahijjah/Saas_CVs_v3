// Gate results of an application. Routes: GET /applications/{id}/gates (every tenant role), and for administrators and HR managers
// POST .../gates/{key}/override, DELETE .../gates/{key}/override, POST .../gates/{key}/result, POST .../gates/evaluate.
// The server refuses every write for any other role; nothing here is the protection.

import { WEBHOOK_CONFIG } from '../config';
import type { EligibilityStatus, GateResult } from '../utils/gates';

export interface GateHistoryEvent {
  event_type: 'evaluated' | 'recorded' | 'overridden' | 'override_cleared';
  original_result: GateResult | null;
  new_result: GateResult;
  reason: string | null;
  at: string | null;
  by: string | null;
}

export interface GateItem {
  gate_key: string;
  source: 'requirement' | 'question';
  label: string;
  verification_method: 'cv_evidence' | 'candidate_question' | 'assessment' | 'manual';
  /** A technical failure of the check, not a result about the candidate: checking again retries it. */
  processing_error: boolean;
  requirement_item_id: string | null;
  question_id: string | null;
  result: GateResult;
  computed_result: Exclude<GateResult, 'overridden'>;
  evidence_source: 'cv' | 'candidate_answer' | 'assessment' | 'manual' | 'none';
  evidence_summary: string | null;
  confidence: number | null;
  evaluated_at: string | null;
  override_reason: string | null;
  overridden_at: string | null;
  history: GateHistoryEvent[];
}

export interface GateSummaryData {
  application_id: string;
  eligibility_status: EligibilityStatus;
  effective_decision: string | null;
  evaluated_at: string | null;
  gates: GateItem[];
}

export class GatesApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'GatesApiError';
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

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${WEBHOOK_CONFIG.APPLICATION_GATES_BASE_URL}${path}`, { ...init, headers: headers() });
  let body: any = null;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const detail = body?.detail;
    const code = typeof detail === 'object' && detail?.code ? String(detail.code) : 'request_failed';
    const message = typeof detail === 'object' && detail?.message ? String(detail.message)
      : typeof detail === 'string' ? detail : `Request failed (${response.status}).`;
    throw new GatesApiError(response.status, code, message);
  }
  return body as T;
}

const enc = encodeURIComponent;

export const getGates = (applicationId: string) => send<GateSummaryData>(`/${enc(applicationId)}/gates`, { method: 'GET' });
export const overrideGate = (applicationId: string, gateKey: string, reason: string) =>
  send<GateSummaryData>(`/${enc(applicationId)}/gates/${enc(gateKey)}/override`, { method: 'POST', body: JSON.stringify({ reason }) });
export const clearGateOverride = (applicationId: string, gateKey: string) =>
  send<GateSummaryData>(`/${enc(applicationId)}/gates/${enc(gateKey)}/override`, { method: 'DELETE' });
export const recordGateResult = (applicationId: string, gateKey: string, result: 'passed' | 'failed' | 'needs_verification', summary: string | null) =>
  send<GateSummaryData>(`/${enc(applicationId)}/gates/${enc(gateKey)}/result`, { method: 'POST', body: JSON.stringify({ result, summary }) });
export const evaluateGates = (applicationId: string) =>
  send<GateSummaryData>(`/${enc(applicationId)}/gates/evaluate`, { method: 'POST' });
