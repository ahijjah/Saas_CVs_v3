import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AuditLogs from '../../pages/AuditLogs';
import { JobsDashboard } from '../../pages/JobsDashboard';
import { JobDetails } from '../../pages/JobDetails';
import { Layout } from '../../components/Layout';
import { LanguageProvider } from '../../context/LanguageContext';
import { PageTitleProvider } from '../../context/PageTitleContext';
import { apiService } from '../../services/api';
import { getRequirements } from '../../services/requirementsApi';
import { WEBHOOK_CONFIG } from '../../config';

vi.mock('../../services/api', () => ({ apiService: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));
vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(),
}));

const ROLES = ['admin', 'hr_manager', 'recruiter', 'viewer'] as const;
const getMock = vi.mocked(apiService.get);

const providers = (node: React.ReactNode) => (
  <MemoryRouter><LanguageProvider><PageTitleProvider>{node}</PageTitleProvider></LanguageProvider></MemoryRouter>
);

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({});
  vi.mocked(getRequirements).mockRejectedValue(new Error('not used'));
  localStorage.clear();
});
afterEach(() => { vi.unstubAllGlobals(); });

describe('navigation', () => {
  const user = (role: string) => ({ user_id: 'u', email: 'u@example.test', full_name: 'U', role, tenant_id: 't', tenant_name: 'T', tenant_type: 'organization' }) as any;

  it.each([['admin', true], ['hr_manager', false], ['recruiter', false], ['viewer', false]] as const)(
    'shows the tenant audit log link to %s: %s', (role, visible) => {
      render(providers(<Layout user={user(role)} onLogout={() => {}}><div /></Layout>));
      expect(screen.queryAllByText('Audit Logs').length > 0).toBe(visible);
    });

  it('keeps the audit log link in the platform menu for a super admin', () => {
    render(providers(<Layout user={user('super_admin')} onLogout={() => {}}><div /></Layout>));
    expect(screen.queryAllByText('Audit Logs').length).toBeGreaterThan(0);
  });
});

describe('audit log page', () => {
  async function renderLogs(scope: 'tenant' | 'platform') {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({
      success: true, total: 1, pages: 1,
      logs: [{ log_id: 'l1', tenant_id: 't', tenant_name: 'Acme', user_email: 'a@example.test', action: 'update_job', resource_type: 'job', resource_id: 'j', created_at: null, ip_address: '', details: null }],
    }) })));
    render(providers(<AuditLogs scope={scope} />));
    await screen.findByText('a@example.test');
  }

  it('shows a tenant admin no tenant column and never asks for another tenant', async () => {
    await renderLogs('tenant');
    expect(screen.queryByRole('columnheader', { name: 'Tenant' })).toBeNull();
    expect(screen.queryByText('Acme')).toBeNull();
    for (const [url] of (fetch as any).mock.calls) expect(String(url)).not.toContain('tenant_id');
  });

  it('shows a super admin the tenant column', async () => {
    await renderLogs('platform');
    expect(screen.getByRole('columnheader', { name: 'Tenant' })).toBeInTheDocument();
    expect(screen.getByText('Acme')).toBeInTheDocument();
  });
});

describe('add job', () => {
  it.each([['admin', true], ['hr_manager', true], ['recruiter', false], ['viewer', false]] as const)(
    'offers Add New Job to %s: %s', async (role, visible) => {
      render(providers(<JobsDashboard auth={{ token: 't', user: { role } as any }} onViewDetails={() => {}} onViewApplications={() => {}}
        onAddJob={() => {}} addToast={() => {}} />));
      await waitFor(() => expect(screen.getByText('Jobs')).toBeInTheDocument());
      expect(screen.queryByTestId('add-job') !== null).toBe(visible);
    });
});

describe('job intake', () => {
  const payload = {
    details: {
      job_id: 'job-1', job_code: 'JOB-1', job_title: 'IT Support', job_client: '', job_status: 'Active', job_type: 'Full-time',
      location: 'Ramallah', posted_date: '2026-10-01', closing_date: '', ingestion_note: '', applications_total: 0, applications_qualified: 0,
      applications_partial: 0, applications_rejected: 0, applications_above_threshold: 0, applications_below_threshold: 0,
      applications_recommended: 0, description: 'Python', criteria_extraction_status: 'completed', criteria_extraction_error: null,
      criteria_retry_allowed: false, requirements_schema_version: 2,
    },
    analysis: null, original_analysis: null,
  };

  it.each([['admin', true], ['hr_manager', true], ['recruiter', false], ['viewer', false]] as const)(
    'offers manual CV upload to %s: %s', async (role, visible) => {
      getMock.mockImplementation(async (url: string) => (url === WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL ? payload : {}) as any);
      render(providers(<JobDetails jobId="job-1" auth={{ token: 't', user: { role } as any }} onBack={() => {}}
        onViewApplications={() => {}} onOpenApplication={() => {}} addToast={() => {}} />));
      await screen.findByText('IT Support');
      expect(screen.queryByRole('button', { name: /^manual upload/i }) !== null).toBe(visible);
    });
});

void ROLES;
