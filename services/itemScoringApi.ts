// Item-level scoring of an application. Routes: GET /applications/{id}/item-scoring (every tenant role), and for administrators and HR
// managers POST .../item-scoring/items/{item_id}/resolve and POST .../item-scoring/rescore. The server refuses every write for any other role.

import { WEBHOOK_CONFIG } from '../config';
import type { ItemScoringResponse } from '../utils/itemScoring';

export class ItemScoringApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ItemScoringApiError';
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
    throw new ItemScoringApiError(response.status, code, message);
  }
  return body as T;
}

const enc = encodeURIComponent;

export const getItemScoring = (applicationId: string) =>
  send<ItemScoringResponse>(`/${enc(applicationId)}/item-scoring`, { method: 'GET' });
export const resolveItem = (applicationId: string, itemId: string, result: 'met' | 'partially_met' | 'not_met', reason: string) =>
  send<ItemScoringResponse>(`/${enc(applicationId)}/item-scoring/items/${enc(itemId)}/resolve`, { method: 'POST', body: JSON.stringify({ result, reason }) });
export const rescoreApplication = (applicationId: string) =>
  send<{ queued: boolean }>(`/${enc(applicationId)}/item-scoring/rescore`, { method: 'POST' });
