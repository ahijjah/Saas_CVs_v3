import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiService } from '@/services/api';
import { WEBHOOK_CONFIG } from '@/config';

// Regression: CandidatesWorkspace called these knockout methods on an APIService instance, which
// does not define them, so the actions failed at runtime. The workspace now calls apiService
// with the token. These tests pin the endpoints and the request shape it relies on.
describe('knockout suggestion API', () => {
  const base = WEBHOOK_CONFIG.KNOCKOUT_ANALYSIS_BASE_URL;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('triggers screening validation with the bearer token', async () => {
    await apiService.triggerScreeningValidation('app-1', 'tok-1');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${base}/app-1/screening-validation`);
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer tok-1');
  });

  it('accepts a suggestion with the override answer, defaulting to null', async () => {
    await apiService.acceptKnockoutSuggestion('app-1', 'q-1', null, 'tok-1');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${base}/app-1/knockout-suggestions/accept`);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({ question_id: 'q-1', override_answer: null });
  });

  it('ignores a suggestion with an empty answer value', async () => {
    await apiService.ignoreKnockoutSuggestion('app-1', 'q-1', 'tok-1');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${base}/app-1/knockout-suggestions/ignore`);
    expect(JSON.parse(init.body as string)).toEqual({ question_id: 'q-1', answer_value: '' });
  });
});
