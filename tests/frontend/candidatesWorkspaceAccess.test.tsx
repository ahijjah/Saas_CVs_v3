import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { CandidatesWorkspace } from '../../pages/CandidatesWorkspace';
import { LanguageProvider } from '../../context/LanguageContext';
import { PageTitleProvider } from '../../context/PageTitleContext';
import { apiService } from '../../services/api';
import { WEBHOOK_CONFIG } from '../../config';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  class QuietAPIService extends actual.APIService {
    async listTags() { return []; }
  }
  return {
    ...actual,
    APIService: QuietAPIService,
    apiService: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), downloadFile: vi.fn() },
  };
});

const CANDIDATE = {
  application_id: 'app-1', candidate_name: 'Sam Candidate', job_title: 'IT Support', job_code: 'JOB-1', job_id: 'job-1',
  processing_status: 'ai_scored', workflow_status: 'awaiting_review', score: 72, ai_result: 'recommended', applied_at: '2026-10-01T10:00:00Z',
  tags: [], is_talent_pool: false,
} as any;

beforeEach(() => {
  vi.mocked(apiService.get).mockReset();
  vi.mocked(apiService.get).mockImplementation(async (url: string) => {
    if (url === WEBHOOK_CONFIG.CANDIDATES_SEARCH_URL) return { candidates: [CANDIDATE], pagination: { total: 1, page: 1, page_size: 25, total_pages: 1 } } as any;
    return [] as any;
  });
});

async function renderWorkspace(role: string) {
  render(
    <MemoryRouter><LanguageProvider><PageTitleProvider>
      <CandidatesWorkspace auth={{ token: 't', user: { role, user_id: 'u', tenant_type: 'organization' } as any }} addToast={() => {}} />
    </PageTitleProvider></LanguageProvider></MemoryRouter>,
  );
  await screen.findByText('Sam Candidate');
}

describe('the candidates workspace', () => {
  it.each([['admin', true], ['hr_manager', true], ['recruiter', true], ['viewer', false]] as const)(
    'offers Export to %s: %s', async (role, visible) => {
      await renderWorkspace(role);
      expect(screen.queryByTestId('export-menu') !== null).toBe(visible);
    });

  it.each([['admin', true], ['hr_manager', true], ['recruiter', false], ['viewer', false]] as const)(
    'offers bulk selection to %s: %s', async (role, visible) => {
      await renderWorkspace(role);
      await waitFor(() => expect(screen.getByText('Sam Candidate')).toBeInTheDocument());
      expect(screen.queryByTitle('Select all eligible candidates') !== null).toBe(visible);
    });
});

describe('the candidate drawer', () => {
  it.each([['admin', true], ['hr_manager', true], ['recruiter', false], ['viewer', false]] as const)(
    'offers %s the editing controls: %s', async (role, editable) => {
      await renderWorkspace(role);
      await userEvent.click(screen.getByText('Sam Candidate'));
      await screen.findByText('Talent Pool');
      expect(screen.queryByRole('button', { name: '+ Add' }) !== null).toBe(editable);                 // tags
      expect(screen.queryByRole('button', { name: 'Add to Pool' }) !== null).toBe(editable);            // talent pool
      expect(screen.queryByTestId('assign-control') !== null).toBe(editable);                           // assignment
      expect(screen.queryByTestId('workflow-actions') !== null).toBe(editable);                         // workflow moves
      const notes = screen.getByPlaceholderText(editable ? 'Add recruiter notes...' : '', { exact: true });
      expect(notes.hasAttribute('readonly')).toBe(!editable);                                           // recruiter notes
    });
});
