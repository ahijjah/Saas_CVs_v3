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

describe('no sample job is shown for an error', () => {
  it('renders Job Not Found for 404 and shows none of the sample data', async () => {
    getMock.mockRejectedValue(httpError(404));
    const { container } = renderPage();
    expect(await screen.findByTestId('job-not-found')).toBeInTheDocument();
    const text = container.textContent ?? '';
    for (const sample of SAMPLE_TEXT) expect(text).not.toContain(sample);
  });

  it('renders Unauthorized for 403 and shows none of the sample data', async () => {
    getMock.mockRejectedValue(httpError(403));
    const { container } = renderPage();
    expect(await screen.findByTestId('job-unauthorized')).toBeInTheDocument();
    const text = container.textContent ?? '';
    for (const sample of SAMPLE_TEXT) expect(text).not.toContain(sample);
  });

  it('shows the error with a failure state, not sample data, for any other failure', async () => {
    getMock.mockRejectedValue(httpError(500));
    const { container } = renderPage();
    await screen.findByText('API Error: 500');
    const text = container.textContent ?? '';
    for (const sample of SAMPLE_TEXT) expect(text).not.toContain(sample);
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
