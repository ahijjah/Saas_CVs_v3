import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { JobDetails } from '../../pages/JobDetails';
import { LanguageProvider } from '../../context/LanguageContext';
import { PageTitleProvider } from '../../context/PageTitleContext';
import { apiService } from '../../services/api';
import { getRequirements, RequirementsApiError } from '../../services/requirementsApi';
import { WEBHOOK_CONFIG } from '../../config';

vi.mock('../../services/api', () => ({ apiService: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));
vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(),
}));

const getMock = vi.mocked(apiService.get);
const requirementsMock = vi.mocked(getRequirements);
const DETAILS_URL = WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL;

// The sample job that the page used to show in place of a real one. None of it may appear, whatever the error.
const SAMPLE_TEXT = ['Senior Frontend Engineer', 'JB-772', '142', 'Global Finance Solutions', 'London (Hybrid)'];

function jobPayload(status: string) {
  return {
    details: {
      job_id: 'job-1', job_code: 'JOB-2026-0001', job_title: 'IT Support Engineer', job_client: '', job_status: 'Active',
      job_type: 'Full-time', location: 'Ramallah', posted_date: '2026-10-01', closing_date: '', ingestion_note: '',
      applications_total: 0, applications_qualified: 0, applications_partial: 0, applications_rejected: 0,
      applications_above_threshold: 0, applications_below_threshold: 0, applications_recommended: 0,
      description: 'Senior IT Support Engineer. Python or PowerShell.',
      criteria_extraction_status: status, criteria_extraction_error: null, criteria_retry_allowed: false,
      requirements_schema_version: 2,
    },
    analysis: null, original_analysis: null,
  };
}

function renderPage() {
  return render(
    <LanguageProvider>
      <PageTitleProvider>
        <JobDetails jobId="job-1" auth={{ token: 'token', user: { role: 'admin' } as any }} onBack={() => {}}
          onViewApplications={() => {}} onOpenApplication={() => {}} addToast={() => {}} />
      </PageTitleProvider>
    </LanguageProvider>,
  );
}

function httpError(status: number) {
  return Object.assign(new Error(`API Error: ${status}`), { status });
}

function detailsCalls() {
  return getMock.mock.calls.filter(([url]) => url === DETAILS_URL).length;
}

// One poll interval per act(): each poll re-renders and schedules the next timer from a passive effect, which React flushes
// only when an act boundary closes. Advancing many intervals inside one act would stop after the first.
async function advancePolls(intervals: number) {
  for (let i = 0; i < intervals; i += 1) {
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  }
}

beforeEach(() => {
  getMock.mockReset();
  requirementsMock.mockReset();
  requirementsMock.mockRejectedValue(new RequirementsApiError(404, 'not_found', 'Requirements not found.', null));
});

afterEach(() => {
  vi.useRealTimers();
});

// What a loaded job puts on the page: its own title and code, the application counts and the intake actions.
const LOADED_JOB_TEXT = ['IT Support Engineer', 'JOB-2026-0001', 'Ramallah'];
const JOB_ACTION_NAMES = [/upload cvs/i, /score uploaded cvs/i, /copy/i, /edit/i, /save/i];

function expectNoJobContent(container: HTMLElement) {
  const text = container.textContent ?? '';
  for (const sample of [...SAMPLE_TEXT, ...LOADED_JOB_TEXT]) expect(text).not.toContain(sample);
  for (const label of ['Total Received', 'AI Scored', 'Qualified', 'Evaluation Logic', 'Edit Weights', 'Original Job Description']) {
    expect(text).not.toContain(label);
  }
  expect(screen.queryByTestId('requirements-panel')).toBeNull();
  const buttons = screen.getAllByRole('button').map((b) => b.textContent ?? '');
  expect(buttons).toEqual(['Return to Home']);                                  // the only action is the way back
  for (const name of JOB_ACTION_NAMES) expect(screen.queryByRole('button', { name })).toBeNull();
}

describe('a refused or missing job shows one safe state and no job content', () => {
  it.each([404, 403])('renders the same unavailable state for %i and none of the sample data', async (status) => {
    getMock.mockRejectedValue(httpError(status));
    const { container } = renderPage();
    expect(await screen.findByTestId('job-unavailable')).toBeInTheDocument();
    expectNoJobContent(container);
  });

  it('says nothing that tells a missing job from another tenant\'s job', async () => {
    getMock.mockRejectedValue(httpError(404));
    const missing = renderPage();
    await screen.findByTestId('job-unavailable');
    const missingHtml = missing.container.innerHTML;
    missing.unmount();

    getMock.mockReset();
    getMock.mockRejectedValue(httpError(403));
    const refused = renderPage();
    await screen.findByTestId('job-unavailable');
    expect(refused.container.innerHTML).toBe(missingHtml);
    expect(refused.container.textContent ?? '').not.toMatch(/forbidden|unauthori[sz]ed|another tenant|permission|found/i);
  });

  it.each([404, 403])('clears a job that was already loaded when a later poll is refused with %i', async (status) => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    let refuse = false;
    getMock.mockImplementation(async (url: string) => {
      if (url !== DETAILS_URL) return [];
      if (refuse) throw httpError(status);
      return jobPayload('pending');
    });
    const { container } = renderPage();
    await vi.waitFor(() => expect(detailsCalls()).toBe(1));
    await vi.waitFor(() => expect(container.textContent ?? '').toContain('JOB-2026-0001'));   // the job really was on the page
    expect(container.textContent ?? '').toContain('IT Support Engineer');

    refuse = true;
    await advancePolls(1);
    await vi.waitFor(() => expect(screen.queryByTestId('job-unavailable')).not.toBeNull());
    expectNoJobContent(container);

    const after = detailsCalls();
    await advancePolls(10);
    expect(detailsCalls()).toBe(after);                                         // polling ended with the data
  });

  it('keeps a loaded job on screen when a poll fails for a transient reason', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    let fail = false;
    getMock.mockImplementation(async (url: string) => {
      if (url !== DETAILS_URL) return [];
      if (fail) throw httpError(500);
      return jobPayload('pending');
    });
    const { container } = renderPage();
    await vi.waitFor(() => expect(container.textContent ?? '').toContain('JOB-2026-0001'));
    fail = true;
    await advancePolls(2);
    expect(container.textContent ?? '').toContain('JOB-2026-0001');
    expect(screen.queryByTestId('job-unavailable')).toBeNull();
  });

  it('renders a job that loads normally, with no unavailable state', async () => {
    getMock.mockImplementation(async (url: string) => (url === DETAILS_URL ? jobPayload('completed') : []));
    const { container } = renderPage();
    await vi.waitFor(() => expect(container.textContent ?? '').toContain('JOB-2026-0001'));
    expect(container.textContent ?? '').toContain('IT Support Engineer');
    expect(screen.queryByTestId('job-unavailable')).toBeNull();
    expect(screen.queryByText('Sync Error')).toBeNull();
  });

  it('shows one full-width authoritative analysis for requirements-v2 jobs', async () => {
    getMock.mockImplementation(async (url: string) => (url === DETAILS_URL ? jobPayload('completed') : []));
    renderPage();

    await waitFor(() => expect(screen.getByTestId('requirements-panel')).toBeInTheDocument());

    expect(screen.queryByText('Skills Analysis')).toBeNull();
    expect(screen.getByTestId('job-details-analysis-layout').className).not.toContain('lg:grid-cols-3');
  });

  it('shows the error with a failure state, not sample data, for any other failure', async () => {
    getMock.mockRejectedValue(httpError(500));
    const { container } = renderPage();
    await screen.findByText('API Error: 500');
    const text = container.textContent ?? '';
    for (const sample of SAMPLE_TEXT) expect(text).not.toContain(sample);
    expect(screen.queryByTestId('job-unavailable')).toBeNull();
  });
});

describe('extraction polling is bounded and stops', () => {
  it('polls while pending, stops at the limit with a timeout state, and resumes only on request', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    getMock.mockImplementation(async (url: string) => (url === DETAILS_URL ? jobPayload('pending') : []));
    renderPage();
    await vi.waitFor(() => expect(detailsCalls()).toBe(1));

    await advancePolls(130);
    const polls = detailsCalls();
    expect(polls).toBeGreaterThan(100);
    expect(polls).toBeLessThanOrEqual(121);
    expect(screen.getByTestId('requirements-polling-timeout')).toBeInTheDocument();

    await advancePolls(50);
    expect(detailsCalls()).toBe(polls);
  });

  it('stops polling once the extraction completes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    let status = 'pending';
    getMock.mockImplementation(async (url: string) => (url === DETAILS_URL ? jobPayload(status) : []));
    renderPage();
    await vi.waitFor(() => expect(detailsCalls()).toBe(1));
    await advancePolls(2);
    expect(detailsCalls()).toBeGreaterThan(1);   // polling really ran while pending
    status = 'completed';
    await advancePolls(2);
    const after = detailsCalls();
    await advancePolls(10);
    expect(detailsCalls()).toBe(after);
  });

  it('stops polling when the page unmounts', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    getMock.mockImplementation(async (url: string) => (url === DETAILS_URL ? jobPayload('pending') : []));
    const { unmount } = renderPage();
    await vi.waitFor(() => expect(detailsCalls()).toBe(1));
    unmount();
    const before = detailsCalls();
    await advancePolls(10);
    expect(detailsCalls()).toBe(before);
  });
});
