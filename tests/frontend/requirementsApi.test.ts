import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WEBHOOK_CONFIG } from '../../config';
import { canEditRequirements, getRequirements, RequirementsApiError, saveRequirementEdits } from '../../services/requirementsApi';

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

describe('requirements API client', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'tok-123');
  });
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('sends the bearer token and the revision with the operations', async () => {
    const fetchMock = vi.fn(() => respond(200, { requirements_revision: 4 }));
    vi.stubGlobal('fetch', fetchMock);
    await saveRequirementEdits('job-1', 3, [{ op: 'confirm_no_numeric_score' }]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/job-1/requirements/edits`);
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
    expect(JSON.parse(init.body as string)).toEqual({ client_revision: 3, operations: [{ op: 'confirm_no_numeric_score' }] });
    vi.unstubAllGlobals();
  });

  it('keeps the status, code and current revision of a revision conflict', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(409, { detail: { code: 'revision_conflict', message: 'changed', current_revision: 7 } })));
    const error = await saveRequirementEdits('job-1', 3, [{ op: 'confirm_no_numeric_score' }]).catch((e) => e);
    expect(error).toBeInstanceOf(RequirementsApiError);
    expect(error).toMatchObject({ status: 409, code: 'revision_conflict', currentRevision: 7 });
    vi.unstubAllGlobals();
  });

  it('keeps the code of a validation error', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(422, { detail: { code: 'unknown_warning', message: 'no such warning' } })));
    const error = await saveRequirementEdits('job-1', 3, []).catch((e) => e);
    expect(error).toMatchObject({ status: 422, code: 'unknown_warning', currentRevision: null });
    vi.unstubAllGlobals();
  });

  it('reads the document with GET', async () => {
    const fetchMock = vi.fn(() => respond(200, { job_id: 'job-1' }));
    vi.stubGlobal('fetch', fetchMock);
    await getRequirements('job-1');
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe(`${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/job-1/requirements`);
    vi.unstubAllGlobals();
  });
});

describe('edit permission on the client', () => {
  it('allows only administrators and recruiters', () => {
    expect(canEditRequirements('admin')).toBe(true);
    expect(canEditRequirements('recruiter')).toBe(true);
    for (const role of ['viewer', 'hr_manager', 'super_admin', 'unknown', '', null, undefined]) {
      expect(canEditRequirements(role as string | null | undefined)).toBe(false);
    }
  });
});
