import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { CandidateMatrix } from '../../pages/CandidateMatrix';
import { LanguageProvider } from '../../context/LanguageContext';
import {
  exportMatrix, getMatrixRows, MatrixApiError, MatrixColumn, MatrixConfig, MatrixLayout, MatrixPage, MatrixRow, resetMatrixLayout, saveMatrixLayout,
} from '../../services/matrixApi';
import { getItemScoring } from '../../services/itemScoringApi';
import { getGates } from '../../services/gatesApi';
import { dropColumn, moveColumn, NAME_COLUMN, summaryText, toggleColumn } from '../../utils/matrix';

vi.mock('../../services/matrixApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/matrixApi')>()),
  getMatrixColumns: vi.fn(), getMatrixConfig: vi.fn(), getMatrixRows: vi.fn(), saveMatrixLayout: vi.fn(), resetMatrixLayout: vi.fn(), exportMatrix: vi.fn(),
}));
vi.mock('../../services/itemScoringApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../services/itemScoringApi')>()), getItemScoring: vi.fn() }));
vi.mock('../../services/gatesApi', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../services/gatesApi')>()), getGates: vi.fn() }));

const col = (id: string, group: MatrixColumn['group'], en: string, over: Partial<MatrixColumn> = {}): MatrixColumn => ({
  id, group, label: { en, ar: `${en} (ar)` }, sortable: true, available: true, unavailable_reason: null, kind: 'static', ref: null, default: false, required: id === 'candidate.name', ...over });

const REGISTRY: MatrixColumn[] = [
  col('candidate.name', 'candidate', 'Candidate', { label: { en: 'Candidate', ar: 'المرشح' } }),
  col('candidate.location', 'candidate', 'Location', { available: false, sortable: false, unavailable_reason: 'not stored' }),
  col('candidate.applied_at', 'candidate', 'Application date'),
  col('decision.eligibility', 'decision', 'Eligibility', { label: { en: 'Eligibility', ar: 'الأهلية' } }),
  col('decision.score', 'decision', 'Overall score'),
  col('decision.scoring', 'decision', 'Scoring status'),
  col('decision.flags', 'decision', 'AI / review flags'),
  col('mandatory.summary', 'mandatory', 'Mandatory criteria'),
  col('preferred.summary', 'preferred', 'Preferred criteria'),
  col('gates.summary', 'gates', 'Gates'),
  col('workflow.status', 'workflow', 'Workflow status'),
  col('mandatory:req_sql', 'mandatory', 'SQL troubleshooting', { kind: 'mandatory', ref: 'req_sql' }),
  col('preferred:req_k8', 'preferred', 'Kubernetes', { kind: 'preferred', ref: 'req_k8' }),
  col('gate:req:req_sql', 'gates', 'SQL troubleshooting', { kind: 'gate', ref: 'req:req_sql' }),
];

const LAYOUT: MatrixLayout = {
  columns: ['candidate.name', 'decision.eligibility', 'decision.score', 'mandatory.summary', 'preferred.summary', 'gates.summary', 'decision.flags'],
  frozen_candidate: true, sort: { column: 'decision.score', direction: 'desc' }, filters: { mandatory: [], preferred: [], gates: [] }, page_size: 25 };

function config(role: 'admin' | 'hr_manager' | 'recruiter' | 'viewer', over: Partial<MatrixConfig> = {}): MatrixConfig {
  return {
    job_id: 'job-1', source: 'system', effective: LAYOUT, unavailable_columns: [], layers: { personal: null, job: null, tenant: null }, system_default: LAYOUT,
    permissions: { edit_shared: role === 'admin' || role === 'hr_manager', edit_personal: role !== 'viewer', export: role !== 'viewer' }, ...over };
}

const base = (over: Partial<MatrixRow>): MatrixRow => ({
  application_id: 'a', candidate_name: 'Name', applied_at: '2026-10-20T10:00:00+00:00', source: 'manual_upload', workflow_status: 'under_review', assigned_to: null, tags: [],
  eligibility: 'eligible', score: 100, scoring: { state: 'scored', method: 'item_level_v2', revision_used: 3, stale: false },
  mandatory: { assessed: true, met: 7, partially_met: 0, not_met: 0, unclear: 0, total: 7 }, preferred: { assessed: true, yes: 1, no: 0, unclear: 0, total: 1 },
  gates: { assessed: true, passed: 1, failed: 0, review_required: 0, not_assessed: 0, total: 1 }, flags: { total: 0, items: [] }, cells: {}, ...over });

const FAILED_GATE = base({ application_id: 'gate', candidate_name: 'Failed Gate', eligibility: 'not_eligible', score: 91,
  gates: { assessed: true, passed: 0, failed: 1, review_required: 0, not_assessed: 0, total: 1 },
  cells: { 'mandatory:req_sql': { status: 'not_met', confidence: 0.9, resolved: false, flagged: false }, 'gate:req:req_sql': { status: 'failed', overridden: false },
           'preferred:req_k8': { status: 'yes', confidence: 0.9 } } });
const UNCLEAR = base({ application_id: 'unclear', candidate_name: 'Unclear One', eligibility: 'review_required', score: null,
  scoring: { state: 'review_required', method: 'item_level_v2', revision_used: 3, stale: true },
  mandatory: { assessed: true, met: 4, partially_met: 1, not_met: 1, unclear: 1, total: 7 }, preferred: { assessed: true, yes: 0, no: 0, unclear: 1, total: 1 },
  flags: { total: 1, items: [{ kind: 'unclear_criteria', severity: 'medium', count: 1, targets: [{ type: 'item', id: 'req_sql' }] }] },
  cells: { 'mandatory:req_sql': { status: 'unclear', confidence: 0.5, resolved: false, flagged: true }, 'gate:req:req_sql': { status: 'review_required' }, 'preferred:req_k8': { status: 'unclear' } } });
const LEGACY = base({ application_id: 'legacy', candidate_name: 'Legacy One', eligibility: 'not_assessed', score: 72,
  scoring: { state: 'legacy', method: 'legacy_category', revision_used: 1, stale: false },
  mandatory: { assessed: false, met: null, partially_met: null, not_met: null, unclear: null, total: null }, preferred: { assessed: false, yes: null, no: null, unclear: null, total: null },
  gates: { assessed: false, passed: null, failed: null, review_required: null, not_assessed: null, total: 0 },
  cells: { 'mandatory:req_sql': { status: 'not_assessed' }, 'gate:req:req_sql': { status: 'not_assessed' }, 'preferred:req_k8': { status: 'not_assessed' } } });

const page = (rows: MatrixRow[], over: Partial<MatrixPage> = {}): MatrixPage => ({ job_id: 'job-1', rows, total: rows.length, page: 1, page_size: 25, columns: LAYOUT.columns, sort: LAYOUT.sort, ...over });

function mount(role: 'admin' | 'hr_manager' | 'recruiter' | 'viewer' = 'admin', over: { config?: Partial<MatrixConfig>; rows?: MatrixRow[]; lang?: 'en' | 'ar'; total?: number } = {}) {
  localStorage.setItem('app_lang', over.lang ?? 'en');
  const open = vi.fn();
  const back = vi.fn();
  const cfg = config(role, over.config);
  const view = render(
    <LanguageProvider>
      <CandidateMatrix jobId="job-1" auth={{ user: { role } }} onBack={back} onOpenApplication={open}
        initial={{ columns: REGISTRY, config: cfg, page: page(over.rows ?? [FAILED_GATE, UNCLEAR, LEGACY], over.total ? { total: over.total } : {}) }} />
    </LanguageProvider>);
  return { ...view, open, back };
}

beforeEach(() => {
  vi.mocked(getMatrixRows).mockReset().mockResolvedValue(page([FAILED_GATE, UNCLEAR, LEGACY]));
  vi.mocked(saveMatrixLayout).mockReset().mockResolvedValue({ scope: 'personal', version: 1 });
  vi.mocked(resetMatrixLayout).mockReset().mockResolvedValue({ scope: 'personal', removed: true });
  vi.mocked(exportMatrix).mockReset().mockResolvedValue({ blob: new Blob(['x']), filename: 'candidate-matrix-J.csv' });
  vi.mocked(getItemScoring).mockReset();
  vi.mocked(getGates).mockReset();
});

const lastRequest = () => vi.mocked(getMatrixRows).mock.calls.at(-1)![1];

describe('what the matrix shows', () => {
  it('shows the decision elements side by side and says the score is one element, never an automatic ranking', () => {
    mount();
    expect(screen.getByTestId('matrix-principle').textContent).toContain('one decision-support element');
    expect(screen.getByTestId('matrix-principle').textContent).toContain('never ranked automatically');
    for (const id of ['decision.eligibility', 'decision.score', 'mandatory.summary', 'preferred.summary', 'gates.summary', 'decision.flags']) expect(screen.getByTestId(`header-${id}`)).toBeTruthy();
  });
  it('shows a high-score candidate with a failed gate as Not Eligible, with the score still visible', () => {
    mount();
    expect(screen.getByTestId('eligibility-gate').textContent).toBe('Not Eligible');
    expect(screen.getByTestId('score-gate').textContent).toBe('91');
    expect(screen.getByTestId('matrix-row-gate').getAttribute('data-eligibility')).toBe('not_eligible');
    expect(screen.getByTestId('gates-summary-gate').textContent).toBe('Passed 0 · Failed 1 · Review Required 0 · Not Assessed 0 · Total 1');
  });
  it('shows an unclear mandatory item as Review Required with no score and a visible flag', () => {
    mount();
    expect(screen.getByTestId('eligibility-unclear').textContent).toBe('Review Required');
    expect(screen.getByTestId('score-unclear').textContent).toContain('—');
    expect(screen.getByTestId('mandatory-summary-unclear').textContent).toContain('Unclear 1');
    expect(within(screen.getByTestId('mandatory-summary-unclear')).getByLabelText('Unclear')).toBeTruthy();
    expect(screen.getByTestId('flags-unclear').getAttribute('data-severity')).toBe('medium');
  });
  it('marks a stale score without hiding it', () => {
    mount();
    expect(screen.getByTestId('score-unclear').textContent).toContain('Out of date');
  });
  it('shows summaries as counts of words, and Preferred only as Yes, No and Unclear', () => {
    mount();
    expect(screen.getByTestId('mandatory-summary-unclear').textContent).toContain('Met 4 · Partially Met 1 · Not Met 1 · Unclear 1 · Total 7');
    const preferred = screen.getByTestId('preferred-summary-unclear').textContent!;
    expect(preferred).toContain('Yes 0 · No 0 · Unclear 1 · Total 1');
    expect(preferred).not.toMatch(/partial/i);
  });
  it('shows Not Assessed for a legacy application instead of failing', () => {
    mount();
    expect(screen.getByTestId('mandatory-summary-legacy').textContent).toBe('Not Assessed');
    expect(screen.getByTestId('preferred-summary-legacy').textContent).toBe('Not Assessed');
    expect(screen.getByTestId('gates-summary-legacy').textContent).toBe('Not Assessed');
    expect(screen.getByTestId('eligibility-legacy').textContent).toBe('Not Assessed');
    expect(screen.getByTestId('score-legacy').textContent).toBe('72');
  });
  it('shows an empty state and keeps the filters reachable', () => {
    mount('admin', { rows: [] });
    expect(screen.getByTestId('matrix-empty').textContent).toContain('No candidates match');
  });
  it('tells the person when saved columns are no longer available', () => {
    mount('admin', { config: { unavailable_columns: ['mandatory:req_gone'] } });
    expect(screen.getByTestId('matrix-unavailable').textContent).toContain('1 saved column is no longer available');
  });
  it('shows which layout is in effect', () => {
    mount('admin', { config: { source: 'job' } });
    expect(screen.getByTestId('matrix-source').textContent).toBe('Job configuration');
  });
});

describe('columns', () => {
  it('keeps the candidate column in view when frozen, and releases it when not', () => {
    mount();
    const style = screen.getByTestId('cell-candidate.name-gate').style;
    expect(style.position).toBe('sticky');
    expect(['0', '0px']).toContain(style.insetInlineStart);
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    fireEvent.click(screen.getByTestId('matrix-freeze'));
    expect(screen.getByTestId('cell-candidate.name-gate').style.position).toBe('');
  });
  it('adds a mandatory, a preferred and a gate column from the chooser and requests them', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    for (const id of ['mandatory:req_sql', 'preferred:req_k8', 'gate:req:req_sql']) fireEvent.click(screen.getByTestId(`toggle-${id}`));
    await waitFor(() => expect(lastRequest().columns).toEqual([...LAYOUT.columns, 'mandatory:req_sql', 'preferred:req_k8', 'gate:req:req_sql']));
    expect(screen.getByTestId('header-mandatory:req_sql').textContent).toContain('SQL troubleshooting');
  });
  it('shows each dynamic result with its own word, the unclear one flagged', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    for (const id of ['mandatory:req_sql', 'preferred:req_k8', 'gate:req:req_sql']) fireEvent.click(screen.getByTestId(`toggle-${id}`));
    expect(screen.getByTestId('result-mandatory:req_sql-gate').getAttribute('data-status')).toBe('not_met');
    expect(screen.getByTestId('result-mandatory:req_sql-unclear').textContent).toContain('Unclear');
    expect(screen.getByTestId('result-mandatory:req_sql-unclear').textContent).toContain('⚑');
    expect(screen.getByTestId('result-gate:req:req_sql-gate').getAttribute('data-status')).toBe('failed');
    expect(screen.getByTestId('result-preferred:req_k8-unclear').getAttribute('data-status')).toBe('unclear');
    expect(screen.getByTestId('cell-mandatory:req_sql-legacy').textContent).toBe('Not Assessed');
  });
  it('hides a column and reorders with accessible buttons, never moving the candidate column', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    fireEvent.click(screen.getByLabelText('Move up: Overall score'));
    await waitFor(() => expect(lastRequest().columns.slice(0, 3)).toEqual(['candidate.name', 'decision.score', 'decision.eligibility']));
    fireEvent.click(screen.getByLabelText('Hide: Gates'));
    await waitFor(() => expect(lastRequest().columns).not.toContain('gates.summary'));
    expect(within(screen.getByTestId('visible-candidate.name')).queryByLabelText(/Move|Hide/)).toBeNull();
    expect((screen.getByTestId('toggle-candidate.name') as HTMLInputElement).disabled).toBe(true);
  });
  it('lists the columns in the six groups and disables the ones that are not available', () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    for (const g of ['candidate', 'decision', 'mandatory', 'preferred', 'gates', 'workflow']) expect(screen.getByTestId(`group-${g}`)).toBeTruthy();
    expect((screen.getByTestId('toggle-candidate.location') as HTMLInputElement).disabled).toBe(true);
  });
  it('shows no age column anywhere', () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    expect(screen.queryByText(/\bage\b/i)).toBeNull();
  });
});

describe('filters and sorting', () => {
  it('sends an eligibility filter and returns to the first page', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-filters-button'));
    fireEvent.click(screen.getByTestId('filter-eligibility-not_eligible'));
    await waitFor(() => expect(lastRequest().filters.eligibility).toEqual(['not_eligible']));
    expect(lastRequest().page).toBe(1);
    expect(screen.getByTestId('matrix-clear-filters')).toBeTruthy();
  });
  it('sends score range and review-flag filters', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-filters-button'));
    fireEvent.change(screen.getByTestId('filter-score-min'), { target: { value: '80' } });
    fireEvent.click(screen.getByTestId('filter-flag-low_confidence'));
    await waitFor(() => expect(lastRequest().filters).toMatchObject({ score_min: 80, flags: ['low_confidence'] }));
  });
  it('adds a criteria filter whose choices never include partial compliance for a Preferred item', async () => {
    mount();
    fireEvent.click(screen.getByTestId('matrix-filters-button'));
    fireEvent.change(screen.getByTestId('criteria-target'), { target: { value: 'preferred:req_k8' } });
    expect(screen.queryByRole('button', { name: 'Partially Met' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Unclear' }));
    fireEvent.click(screen.getByTestId('criteria-add'));
    await waitFor(() => expect(lastRequest().filters.preferred).toEqual([{ item_id: 'req_k8', results: ['unclear'] }]));
    fireEvent.change(screen.getByTestId('criteria-target'), { target: { value: 'mandatory:req_sql' } });
    expect(screen.getByRole('button', { name: 'Partially Met' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Not Met' }));
    fireEvent.click(screen.getByTestId('criteria-add'));
    await waitFor(() => expect(lastRequest().filters.mandatory).toEqual([{ item_id: 'req_sql', results: ['not_met'] }]));
    fireEvent.click(screen.getByLabelText('Remove filter: Kubernetes'));
    await waitFor(() => expect(lastRequest().filters.preferred).toEqual([]));
  });
  it('searches by name after typing stops', async () => {
    vi.useFakeTimers();
    try {
      mount();
      fireEvent.change(screen.getByTestId('matrix-search'), { target: { value: 'ali' } });
      await act(async () => { vi.advanceTimersByTime(400); });
      expect(lastRequest().filters.search).toBe('ali');
    } finally { vi.useRealTimers(); }
  });
  it('sorts by the clicked header, toggles the direction, and exposes it to assistive technology', async () => {
    mount();
    expect(screen.getByTestId('header-decision.score').getAttribute('aria-sort')).toBe('descending');
    fireEvent.click(within(screen.getByTestId('header-decision.eligibility')).getByRole('button'));
    await waitFor(() => expect(lastRequest().sort).toEqual({ column: 'decision.eligibility', direction: 'asc' }));
    expect(screen.getByTestId('header-decision.eligibility').getAttribute('aria-sort')).toBe('ascending');
    fireEvent.click(within(screen.getByTestId('header-decision.eligibility')).getByRole('button'));
    await waitFor(() => expect(lastRequest().sort?.direction).toBe('desc'));
    fireEvent.change(screen.getByTestId('matrix-sort-select'), { target: { value: 'candidate.applied_at' } });
    await waitFor(() => expect(lastRequest().sort?.column).toBe('candidate.applied_at'));
  });
  it('pages through the results', async () => {
    vi.mocked(getMatrixRows).mockResolvedValue(page([FAILED_GATE], { total: 60 }));
    mount('admin', { rows: [FAILED_GATE], total: 60 });
    expect(screen.getByTestId('matrix-page-label').textContent).toBe('Page 1 of 3');
    fireEvent.click(screen.getByTestId('matrix-next'));
    await waitFor(() => expect(lastRequest().page).toBe(2));
  });
});

describe('saved layouts and roles', () => {
  it('lets a recruiter save a personal view but not the shared one, and never exposes the shared reset', () => {
    mount('recruiter');
    expect(screen.getByTestId('matrix-save-personal')).toBeTruthy();
    expect(screen.queryByTestId('matrix-save-shared')).toBeNull();
    expect(screen.queryByTestId('matrix-reset-shared')).toBeNull();
    expect(screen.getByTestId('matrix-export')).toBeTruthy();
  });
  it('gives a viewer a read-only matrix: no save, no reset, no export, and says so', () => {
    mount('viewer');
    for (const id of ['matrix-save-personal', 'matrix-save-shared', 'matrix-reset-personal', 'matrix-reset-shared', 'matrix-export']) expect(screen.queryByTestId(id)).toBeNull();
    expect(screen.getByText(/Read-only/)).toBeTruthy();
    expect(screen.getByTestId('matrix-columns-button')).toBeTruthy();                      // arranging for the session is still possible
  });
  it('lets an admin and an HR manager save the shared job layout', () => {
    for (const role of ['admin', 'hr_manager'] as const) {
      const { unmount } = mount(role);
      expect(screen.getByTestId('matrix-save-shared')).toBeTruthy();
      unmount();
    }
  });
  it('saves the personal view with the current layout and the stored version, then reloads', async () => {
    const { rerender } = mount('recruiter', { config: { layers: { personal: { version: 4, updated_at: null, updated_by: null, readable: true }, job: null, tenant: null }, source: 'personal' } });
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    fireEvent.click(screen.getByLabelText('Hide: Gates'));
    expect(screen.getByTestId('matrix-unsaved')).toBeTruthy();
    const { getMatrixConfig } = await import('../../services/matrixApi');
    vi.mocked(getMatrixConfig).mockResolvedValue(config('recruiter', { source: 'personal' }));
    fireEvent.click(screen.getByTestId('matrix-save-personal'));
    await waitFor(() => expect(saveMatrixLayout).toHaveBeenCalled());
    const [job, scope, layout, version] = vi.mocked(saveMatrixLayout).mock.calls[0];
    expect([job, scope, version]).toEqual(['job-1', 'personal', 4]);
    expect(layout.columns).not.toContain('gates.summary');
    await waitFor(() => expect(screen.getByTestId('matrix-notice').textContent).toBe('Saved.'));
    expect(rerender).toBeTruthy();
  });
  it('saves the shared job layout through the job scope', async () => {
    mount('admin');
    const { getMatrixConfig } = await import('../../services/matrixApi');
    vi.mocked(getMatrixConfig).mockResolvedValue(config('admin', { source: 'job' }));
    fireEvent.click(screen.getByTestId('matrix-save-shared'));
    await waitFor(() => expect(saveMatrixLayout).toHaveBeenCalledWith('job-1', 'job', expect.anything(), null));
  });
  it('explains a version conflict instead of overwriting', async () => {
    vi.mocked(saveMatrixLayout).mockRejectedValue(new MatrixApiError(409, 'version_conflict', 'x'));
    mount('admin');
    fireEvent.click(screen.getByTestId('matrix-save-shared'));
    await waitFor(() => expect(screen.getByTestId('matrix-notice').textContent).toContain('changed by someone else'));
  });
  it('resets the personal view', async () => {
    mount('recruiter', { config: { layers: { personal: { version: 1, updated_at: null, updated_by: null, readable: true }, job: null, tenant: null }, source: 'personal' } });
    const { getMatrixConfig } = await import('../../services/matrixApi');
    vi.mocked(getMatrixConfig).mockResolvedValue(config('recruiter'));
    fireEvent.click(screen.getByTestId('matrix-reset-personal'));
    await waitFor(() => expect(resetMatrixLayout).toHaveBeenCalledWith('job-1', 'personal'));
  });
});

describe('export', () => {
  it('exports exactly the visible columns, filters and sort, in the page language', async () => {
    const created = vi.fn(() => 'blob:x');
    Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    mount();
    fireEvent.click(screen.getByTestId('matrix-export'));
    await waitFor(() => expect(exportMatrix).toHaveBeenCalled());
    expect(vi.mocked(exportMatrix).mock.calls[0]).toEqual(['job-1', { columns: LAYOUT.columns, filters: LAYOUT.filters, sort: LAYOUT.sort, lang: 'en' }]);
    await waitFor(() => expect(created).toHaveBeenCalled());
  });
  it('shows the failure when the export is refused', async () => {
    vi.mocked(exportMatrix).mockRejectedValue(new MatrixApiError(403, 'forbidden', 'Insufficient permissions'));
    mount();
    fireEvent.click(screen.getByTestId('matrix-export'));
    await waitFor(() => expect(screen.getByTestId('matrix-notice').textContent).toBe('Insufficient permissions'));
  });
});

describe('evidence and navigation', () => {
  const item = (over: Record<string, unknown>) => ({ item_id: 'req_sql', text: 'SQL troubleshooting', weight: 20, result: 'unclear', ai_result: 'unclear', earned: null, evidence: [], explanation: 'The CV does not say.',
    confidence: '0.500', source: 'ai', evaluated_at: null, resolution: null, history: [], ...over });
  const scoring = (items: unknown[]) => ({ application_id: 'unclear', scoring_method: 'item_level_v2', status: 'review_required', requirements_revision_used: 3, current_revision: 3, stale: false,
    final_score: null, final_score_raw: null, range: { min: '50', max: '100' }, unclear_count: 1, decision: null, eligibility_status: 'review_required', effective_decision: 'review_required',
    qualified_threshold: 85, partial_threshold: 65, categories: [{ category: 'skills', weight: 100, score: null, score_min: '50', score_max: '100', contribution: null, contribution_min: '50', contribution_max: '100', items }],
    preferred: { summary: { yes: 0, no: 0, not_clear: 1 }, items: [] }, narrative: {}, model: 'm', scored_at: null });

  it('opens the evidence for a clicked result with the quotation, the confidence and the human resolution', async () => {
    vi.mocked(getItemScoring).mockResolvedValue(scoring([item({ result: 'met', evidence: ['Five years of SQL troubleshooting'], confidence: '0.900',
      resolution: { by: 'Hala', at: '2026-10-22T10:00:00Z', reason: 'Verified in interview', previous_result: 'unclear' } })]) as never);
    vi.mocked(getGates).mockResolvedValue(null as never);
    mount();
    fireEvent.click(screen.getByTestId('matrix-columns-button'));
    fireEvent.click(screen.getByTestId('toggle-mandatory:req_sql'));
    fireEvent.click(screen.getByTestId('result-mandatory:req_sql-unclear'));
    const drawer = await screen.findByTestId('matrix-drawer');
    await waitFor(() => expect(within(drawer).getByTestId('drawer-item-req_sql')).toBeTruthy());
    expect(drawer.textContent).toContain('Five years of SQL troubleshooting');
    expect(drawer.textContent).toContain('90%');
    expect(screen.getByTestId('drawer-resolution-req_sql').textContent).toContain('Verified in interview');
    expect(getItemScoring).toHaveBeenCalledWith('unclear');
    fireEvent.click(screen.getByTestId('drawer-close'));
    expect(screen.queryByTestId('matrix-drawer')).toBeNull();
  });
  it('opens the flagged targets from a flag and closes with Escape', async () => {
    vi.mocked(getItemScoring).mockResolvedValue(scoring([item({}), item({ item_id: 'req_other', text: 'Other' })]) as never);
    vi.mocked(getGates).mockResolvedValue({ application_id: 'unclear', eligibility_status: 'review_required', effective_decision: null, evaluated_at: null, gates: [] } as never);
    mount();
    fireEvent.click(screen.getByTestId('flags-unclear'));
    await screen.findByTestId('drawer-item-req_sql');
    expect(screen.queryByTestId('drawer-item-req_other')).toBeNull();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByTestId('matrix-drawer')).toBeNull();
  });
  it('opens the existing candidate details from the name and from the drawer', async () => {
    vi.mocked(getItemScoring).mockResolvedValue(scoring([item({})]) as never);
    vi.mocked(getGates).mockResolvedValue(null as never);
    const { open } = mount();
    fireEvent.click(screen.getByTestId('open-candidate-gate'));
    expect(open).toHaveBeenCalledWith('job-1', 'gate');
    fireEvent.click(screen.getByTestId('mandatory-summary-unclear'));
    fireEvent.click(await screen.findByTestId('drawer-open-candidate'));
    expect(open).toHaveBeenLastCalledWith('job-1', 'unclear');
  });
  it('shows a load failure inside the drawer', async () => {
    vi.mocked(getItemScoring).mockRejectedValue(new Error('Could not load'));
    vi.mocked(getGates).mockResolvedValue(null as never);
    mount();
    fireEvent.click(screen.getByTestId('mandatory-summary-unclear'));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Could not load'));
  });
});

describe('Arabic and right-to-left', () => {
  it('speaks Arabic, sets the document direction, and uses logical sticky positioning', async () => {
    mount('admin', { lang: 'ar' });
    expect(document.documentElement.dir).toBe('rtl');
    expect(screen.getByTestId('eligibility-gate').textContent).toBe('غير مؤهل');
    expect(screen.getByTestId('eligibility-unclear').textContent).toBe('مطلوب مراجعة');
    expect(screen.getByTestId('matrix-principle').textContent).toContain('لا يتم ترتيب المرشحين تلقائيًا');
    expect(screen.getByTestId('mandatory-summary-unclear').textContent).toContain('غير واضح 1');
    expect(screen.getByTestId('preferred-summary-unclear').textContent).toContain('نعم 0 · لا 0 · غير واضح 1');
    expect(['0', '0px']).toContain(screen.getByTestId('cell-candidate.name-gate').style.insetInlineStart);
    expect(screen.getByTestId('header-candidate.name').textContent).toContain('المرشح');
    expect(screen.getByTestId('matrix-source').textContent).toBe('الإعداد الافتراضي للنظام');
    document.documentElement.dir = 'ltr';
  });
  it('has an Arabic string for every English string', async () => {
    const { matrixCopy } = await import('../../utils/matrix');
    const en = matrixCopy('en'); const ar = matrixCopy('ar');
    expect(Object.keys(ar).sort()).toEqual(Object.keys(en).sort());
    for (const group of ['groups', 'status', 'scoringState', 'summary', 'flag', 'severity', 'viewSource'] as const) expect(Object.keys((ar as never)[group]).sort()).toEqual(Object.keys((en as never)[group]).sort());
  });
});

describe('layout helpers', () => {
  const cols = ['candidate.name', 'a', 'b', 'c'];
  it('never moves or hides the candidate column', () => {
    expect(moveColumn(cols, NAME_COLUMN, 1)).toEqual(cols);
    expect(moveColumn(cols, 'a', -1)).toEqual(cols);
    expect(toggleColumn(cols, NAME_COLUMN)).toEqual(cols);
    expect(dropColumn(cols, 'c', NAME_COLUMN)).toEqual(cols);
    expect(dropColumn(cols, NAME_COLUMN, 'c')).toEqual(cols);
  });
  it('moves, toggles and drops', () => {
    expect(moveColumn(cols, 'a', 1)).toEqual(['candidate.name', 'b', 'a', 'c']);
    expect(toggleColumn(cols, 'b')).toEqual(['candidate.name', 'a', 'c']);
    expect(toggleColumn(cols, 'z')).toEqual([...cols, 'z']);
    expect(dropColumn(cols, 'c', 'a')).toEqual(['candidate.name', 'c', 'a', 'b']);
    expect(dropColumn(cols, 'a', 'c')).toEqual(['candidate.name', 'b', 'c', 'a']);
  });
  it('writes summaries as words and Not Assessed when nothing was assessed', () => {
    expect(summaryText([['met', 2], ['unclear', 1]], 3, true, 'en')).toBe('Met 2 · Unclear 1 · Total 3');
    expect(summaryText([['met', null]], null, false, 'ar')).toBe('لم يُقيَّم');
  });
});
