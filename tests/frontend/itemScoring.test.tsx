import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ItemScoring from '../../components/ItemScoring';
import { rescoreApplication, resolveItem } from '../../services/itemScoringApi';
import type { CategoryView, ItemScoringView, ItemView } from '../../utils/itemScoring';
import { itemScoringCopy, outcomeLabel, outcomeTone, rangeText, resultTone, scoreText } from '../../utils/itemScoring';

vi.mock('../../services/itemScoringApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/itemScoringApi')>()),
  getItemScoring: vi.fn(), resolveItem: vi.fn(), rescoreApplication: vi.fn(),
}));

const item = (id: string, text: string, result: ItemView['result'], weight: number | null, earned: string | null, over: Partial<ItemView> = {}): ItemView => ({
  item_id: id, text, weight, result, ai_result: result, earned, evidence: result === 'unclear' ? [] : [`quote for ${id}`], explanation: `why ${id}`,
  confidence: '0.90', source: 'ai', evaluated_at: null, resolution: null, history: [], ...over });

const skills = (arabic: ItemView, range = true): CategoryView => ({
  category: 'skills', weight: 53, score: range ? null : '70', score_min: '50', score_max: range ? '70' : '70',
  contribution: range ? null : '37.1', contribution_min: '26.5', contribution_max: '37.1',
  items: [item('sql', 'SQL troubleshooting', 'met', 20, '20'), item('inc', 'Incident management', 'met', 20, '20'),
    item('py', 'Python / PowerShell', 'partially_met', 20, '10'), arabic, item('en', 'English fluency', 'not_met', 20, '0')] });

const rest: CategoryView[] = [
  { category: 'experience', weight: 27, score: '100', score_min: '100', score_max: '100', contribution: '27', contribution_min: '27', contribution_max: '27',
    items: [item('exp', '3 years in support', 'met', 100, '100')] },
  { category: 'education', weight: 20, score: '100', score_min: '100', score_max: '100', contribution: '20', contribution_min: '20', contribution_max: '20',
    items: [item('edu', 'Bachelor degree', 'met', 100, '100')] },
];

function reviewView(over: Partial<ItemScoringView> = {}): ItemScoringView {
  return {
    application_id: 'app-1', scoring_method: 'item_level_v2', status: 'review_required', requirements_revision_used: 3, current_revision: 3, stale: false,
    final_score: null, final_score_raw: null, range: { min: '73.5', max: '84.1' }, unclear_count: 1, decision: null, eligibility_status: 'not_applicable',
    effective_decision: 'review_required', qualified_threshold: 85, partial_threshold: 65,
    categories: [skills(item('ar', 'Arabic fluency', 'unclear', 20, null)), ...rest],
    preferred: { summary: { yes: 3, no: 1, not_clear: 2 }, items: [item('p1', 'Cloud certificate', 'yes', null, null), item('p2', 'ITIL', 'not_clear', null, null)] },
    narrative: {}, model: 'm', scored_at: null, ...over };
}

beforeEach(() => { vi.mocked(resolveItem).mockReset(); vi.mocked(rescoreApplication).mockReset(); });

describe('the worked example, Review Required', () => {
  it('shows the provisional range, no final score and no stored decision', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    expect(screen.getByTestId('provisional-range').textContent).toBe('73.5 – 84.1');
    expect(screen.queryByTestId('final-score')).toBeNull();
    expect(screen.getByTestId('item-scoring-outcome').textContent).toBe('Review Required');
    expect(screen.getByTestId('item-scoring').getAttribute('data-status')).toBe('review_required');
  });
  it('shows weight, score range and contribution range for the open category and exact values for the others', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    expect(screen.getByTestId('score-skills').textContent).toContain('50 – 70');
    expect(screen.getByTestId('contribution-skills').textContent).toContain('26.5 – 37.1');
    expect(screen.getByTestId('contribution-experience').textContent).toContain('27');
    expect(screen.getByTestId('contribution-education').textContent).toContain('20');
  });
  it('shows each Required row: result, earned points and evidence; an unclear row asks for a reviewer instead of points', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    expect(screen.getByTestId('result-sql').textContent).toBe('Met');
    expect(screen.getByTestId('earned-sql').textContent).toContain('20');
    expect(screen.getByTestId('earned-py').textContent).toContain('10');
    expect(screen.getByTestId('result-py').textContent).toBe('Partially Met');
    expect(screen.getByTestId('result-en').textContent).toBe('Not Met');
    expect(screen.getByTestId('earned-ar').textContent).toBe('Needs a reviewer');
    expect(screen.getByText('“quote for sql”')).toBeTruthy();
  });
  it('shows the Preferred summary, results, and says they never change the score', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    expect(screen.getByTestId('preferred-summary').textContent).toBe('3 Yes · 1 No · 2 Not Clear');
    expect(within(screen.getByTestId('item-p2')).getByText('Not Clear')).toBeTruthy();
    expect(screen.getByTestId('preferred-section').textContent).toContain('never change the score or eligibility');
  });
});

describe('resolving an unclear item', () => {
  it('is offered to admins and HR managers only', () => {
    for (const role of ['viewer', 'recruiter']) {
      const { unmount } = render(<ItemScoring applicationId="app-1" userRole={role} lang="en" initial={reviewView()} />);
      expect(screen.queryByTestId('resolve-ar')).toBeNull();
      unmount();
    }
    render(<ItemScoring applicationId="app-1" userRole="hr_manager" lang="en" initial={reviewView()} />);
    expect(screen.getByTestId('resolve-ar')).toBeTruthy();
  });
  it('requires a reason, sends the choice, and shows the final score the server returns', async () => {
    const final = reviewView({
      status: 'scored', final_score: 84, final_score_raw: '84.1', range: null, unclear_count: 0, decision: 'partial', effective_decision: 'partial',
      categories: [skills(item('ar', 'Arabic fluency', 'met', 20, '20', {
        ai_result: 'unclear', resolution: { by: 'Hala', at: '2026-10-22T10:00:00Z', reason: 'Verified in interview', previous_result: 'unclear' } }), false), ...rest] });
    vi.mocked(resolveItem).mockResolvedValue(final);
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    fireEvent.click(screen.getByTestId('resolve-ar'));
    const save = screen.getByTestId('resolve-save-ar') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: 'Verified in interview' } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(screen.getByTestId('final-score').textContent).toBe('84'));
    expect(resolveItem).toHaveBeenCalledWith('app-1', 'ar', 'met', 'Verified in interview');
    expect(screen.getByTestId('item-scoring-outcome').textContent).toBe('Partial');
    expect(screen.getByTestId('resolution-ar').textContent).toContain('Hala');
    expect(screen.queryByTestId('resolve-ar')).toBeNull();
  });
  it('keeps the form and shows the server message when the save fails', async () => {
    vi.mocked(resolveItem).mockRejectedValue(new Error('Not allowed'));
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    fireEvent.click(screen.getByTestId('resolve-ar'));
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: 'because' } });
    fireEvent.click(screen.getByTestId('resolve-save-ar'));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Not allowed'));
    expect(screen.getByTestId('resolve-save-ar')).toBeTruthy();
  });
});

describe('scored, not scored and stale', () => {
  it('shows the final score, the stored decision, and the effective decision when a gate made the candidate Not Eligible', () => {
    const v = reviewView({ status: 'scored', final_score: 84, final_score_raw: '84.1', range: null, unclear_count: 0, decision: 'partial',
      eligibility_status: 'not_eligible', effective_decision: 'not_eligible' });
    render(<ItemScoring applicationId="app-1" userRole="viewer" lang="en" initial={v} />);
    expect(screen.getByTestId('final-score').textContent).toBe('84');
    expect(screen.getByTestId('stored-decision').textContent).toBe('Partial');
    expect(screen.getByTestId('eligibility').textContent).toBe('Not Eligible');
    expect(screen.getByTestId('item-scoring-outcome').textContent).toBe('Not Eligible');
  });
  it('shows Not scored (not zero) for a Preferred-only job and still lists the Preferred results', () => {
    const v = reviewView({ status: 'not_scored', final_score: null, range: null, unclear_count: 0, decision: null, effective_decision: 'not_scored', categories: [] });
    render(<ItemScoring applicationId="app-1" userRole="viewer" lang="en" initial={v} />);
    expect(screen.getByTestId('not-scored').textContent).toBe('Not scored');
    expect(screen.queryByTestId('final-score')).toBeNull();
    expect(screen.getByTestId('preferred-summary')).toBeTruthy();
  });
  it('names both revisions and offers a rescore only to those who may', async () => {
    const stale = reviewView({ stale: true, requirements_revision_used: 3, current_revision: 5 });
    vi.mocked(rescoreApplication).mockResolvedValue({ queued: true });
    const { unmount } = render(<ItemScoring applicationId="app-1" userRole="recruiter" lang="en" initial={stale} />);
    expect(screen.getByTestId('revision-line').textContent).toBe('Scored against revision 3; current revision is 5.');
    expect(screen.getByTestId('item-scoring-stale')).toBeTruthy();
    expect(screen.queryByTestId('rescore')).toBeNull();
    unmount();
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={stale} />);
    fireEvent.click(screen.getByTestId('rescore'));
    await waitFor(() => expect(screen.getByTestId('rescore-queued')).toBeTruthy());
    expect(rescoreApplication).toHaveBeenCalledWith('app-1');
  });
  it('shows nothing stale when the revisions match', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="en" initial={reviewView()} />);
    expect(screen.queryByTestId('item-scoring-stale')).toBeNull();
  });
  it('labels an earlier category-level score as legacy and renders nothing for an unscored application', () => {
    const { unmount } = render(<ItemScoring applicationId="app-1" userRole="admin" lang="en"
      initial={{ application_id: 'app-1', scoring_method: 'legacy_category', requirements_revision_used: 1, current_revision: 2, stale: true, decision: 'qualified',
        eligibility_status: null, effective_decision: 'qualified' }} />);
    expect(screen.getByTestId('item-scoring-legacy').textContent).toContain('earlier category-level method');
    expect(screen.getByTestId('item-scoring-stale')).toBeTruthy();
    unmount();
    const { container } = render(<ItemScoring applicationId="app-1" userRole="admin" lang="en"
      initial={{ application_id: 'app-1', scoring_method: 'none', requirements_revision_used: null, current_revision: null, stale: false, decision: null,
        eligibility_status: null, effective_decision: null }} />);
    expect(container.innerHTML).toBe('');
  });
});

describe('Arabic', () => {
  it('speaks Arabic for results, the range, the Preferred summary and the stale warning', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="ar" initial={reviewView({ stale: true, current_revision: 4 })} />);
    expect(screen.getByTestId('result-sql').textContent).toBe('مستوفى');
    expect(screen.getByTestId('result-py').textContent).toBe('مستوفى جزئيًا');
    expect(screen.getByTestId('result-en').textContent).toBe('غير مستوفى');
    expect(screen.getByTestId('item-scoring-outcome').textContent).toBe('مطلوب مراجعة');
    expect(screen.getByTestId('preferred-summary').textContent).toBe('3 نعم · 1 لا · 2 غير واضح');
    expect(screen.getByTestId('item-scoring-stale').textContent).toContain('قديمة');
  });
  it('has an Arabic string for every English string', () => {
    const en = itemScoringCopy('en'); const ar = itemScoringCopy('ar');
    expect(Object.keys(ar).sort()).toEqual(Object.keys(en).sort());
    for (const group of ['result', 'category', 'decisions'] as const) expect(Object.keys(ar[group]).sort()).toEqual(Object.keys(en[group]).sort());
  });
  it('marks user text for automatic direction', () => {
    render(<ItemScoring applicationId="app-1" userRole="admin" lang="ar" initial={reviewView()} />);
    expect(screen.getByText('SQL troubleshooting').getAttribute('dir')).toBe('auto');
  });
});

describe('helpers', () => {
  it('shows a dash, never zero, for a missing score', () => {
    expect(scoreText(null)).toBe('—'); expect(scoreText(0)).toBe('0'); expect(scoreText(84)).toBe('84');
  });
  it('formats ranges and outcomes', () => {
    expect(rangeText({ min: '73.5', max: '84.1' })).toBe('73.5 – 84.1'); expect(rangeText(null)).toBe('');
    expect(outcomeLabel('not_eligible', 'en')).toBe('Not Eligible'); expect(outcomeLabel('not_scored', 'ar')).toBe('بدون درجة');
    expect(outcomeLabel(null, 'en')).toBe('No decision yet'); expect(outcomeLabel('weird', 'en')).toBe('weird');
  });
  it('tones results and outcomes', () => {
    expect(resultTone('met')).toBe('green'); expect(resultTone('unclear')).toBe('amber'); expect(resultTone('not_met')).toBe('red');
    expect(outcomeTone('not_eligible')).toBe('red'); expect(outcomeTone('review_required')).toBe('amber'); expect(outcomeTone('qualified')).toBe('green');
    expect(outcomeTone('not_scored')).toBe('slate');
  });
});
