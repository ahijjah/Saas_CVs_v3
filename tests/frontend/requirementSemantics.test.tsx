import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RequirementsPanel from '../../components/RequirementsPanel';
import RequirementLogic from '../../components/RequirementLogic';
import OptionResults from '../../components/OptionResults';
import ItemScoring from '../../components/ItemScoring';
import MatrixEvidenceDrawer from '../../components/MatrixEvidenceDrawer';
import { LanguageProvider } from '../../context/LanguageContext';
import { getRequirements, saveRequirementEdits } from '../../services/requirementsApi';
import type { RequirementsItem, RequirementsLogic } from '../../services/requirementsApi';
import { buildOperations } from '../../services/requirementsEdits';
import type { ItemScoringView, ItemView } from '../../utils/itemScoring';
import { buildLogicOperation, hasLogic, logicCopy, logicProblem, normalizeInput, toInput } from '../../utils/requirementLogic';
import { matrixCopy, FLAG_KINDS } from '../../utils/matrix';
import { view } from './fixtures';

vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(), saveRequirementEdits: vi.fn(),
}));

const getMock = vi.mocked(getRequirements);
const saveMock = vi.mocked(saveRequirementEdits);

// Criteria from fields unrelated to each other: the structure, not the subject matter, is what the screens show.
const ROUTE: RequirementsLogic = {
  options: [
    { id: 'o1', text: 'Diploma in civil engineering', acceptance: 'direct', all_of: null },
    { id: 'o2', text: 'Degree with two years on construction sites', acceptance: 'direct', all_of: ['Degree in an engineering field', 'Two years on construction sites'] },
    { id: 'o3', text: 'Long site experience without a diploma', acceptance: 'review', all_of: null },
  ],
  examples: null, ambiguity: null,
};
const SOFTWARE: RequirementsLogic = { options: null, examples: ['spreadsheets', 'a project planner'], ambiguity: null };
const SETTING: RequirementsLogic = { options: null, examples: null, ambiguity: { note: 'It is unclear whether both settings are needed or either one.' } };

const item = (id: string, text: string, logic: RequirementsLogic | null, extra: Partial<RequirementsItem> = {}): RequirementsItem => ({
  id, text, importance: 'required', weight: 25, origin: 'stated', source_text: `Source for ${text}`,
  alternatives: logic?.options ? logic.options.map((o) => o.text) : null, experience: null, logic, ...extra });

const ROUTE_ITEM = item('req_route', 'Diploma, or a degree with site experience', ROUTE);
const SOFTWARE_ITEM = item('req_tools', 'Scheduling software', SOFTWARE);
const SETTING_ITEM = item('req_setting', 'Clinical setting experience', SETTING);
const MERGED_ITEM = item('req_merged', 'Dispatch experience', null, { also_source_texts: ['Dispatch experience of at least 3 years is required.'] });

function semanticView(pending: string[] = []) {
  const base = view();
  base.document.categories.skills.items = [ROUTE_ITEM, SOFTWARE_ITEM, SETTING_ITEM, MERGED_ITEM];
  base.review.structure = { pending_item_ids: pending };
  return base;
}

function renderPanel(role: string, document = semanticView()) {
  getMock.mockResolvedValue(document);
  return render(<LanguageProvider><RequirementsPanel jobId="job-1" userRole={role} onReadinessChange={vi.fn()} /></LanguageProvider>);
}

beforeEach(() => { getMock.mockReset(); saveMock.mockReset(); localStorage.setItem('app_lang', 'en'); });

describe('Job Analysis shows how each criterion combines, beside the source wording', () => {
  it('shows any-one-of options, the parts of a route, a review-only option, examples and the merged sources', async () => {
    renderPanel('viewer');
    await screen.findByRole('heading', { name: 'Job analysis' });
    const options = screen.getByTestId('logic-options-req_route');
    expect(options.textContent).toContain('Any one of');
    expect(screen.getByTestId('logic-option-req_route-0').getAttribute('data-acceptance')).toBe('direct');
    expect(screen.getByTestId('logic-parts-req_route-1').textContent).toBe('All of: Degree in an engineering field + Two years on construction sites');
    const review = screen.getByTestId('logic-option-req_route-2');
    expect(review.getAttribute('data-acceptance')).toBe('review');
    expect(review.textContent).toContain('Accepted only after review');
    expect(review.textContent).toContain('a person decides');
    expect(screen.getByTestId('logic-examples-req_tools').textContent).toContain('spreadsheets · a project planner');
    expect(screen.getByTestId('logic-examples-req_tools').textContent).toContain('not requirements');
    expect(screen.getByTestId('logic-also-req_merged').textContent).toContain('Dispatch experience of at least 3 years is required.');
  });
  it('does not list a criterion that has options twice (the options replace the plain alternatives line)', async () => {
    renderPanel('viewer');
    await screen.findByRole('heading', { name: 'Job analysis' });
    expect(screen.queryByText(/Accepted alternatives:/)).toBeNull();
  });
  it('flags a meaningful ambiguity for the recruiter, with the source wording, in the structure-to-confirm list', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue(semanticView());
    renderPanel('admin', semanticView(['req_setting']));
    const pending = await screen.findByText('Structure to confirm');
    const section = pending.closest('div')!;
    expect(within(section).getByTestId('logic-ambiguity-req_setting').textContent).toContain('It is unclear whether both settings are needed');
    expect(section.textContent).toContain('From the job description: “Source for Clinical setting experience”');
    await user.click(within(section).getByRole('button', { name: 'Confirm this structure' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    expect(JSON.stringify(saveMock.mock.calls[0])).toContain('"confirm_structure"');
  });
  it('shows an item with no structure exactly as before', async () => {
    renderPanel('viewer', view());
    await screen.findByRole('heading', { name: 'Job analysis' });
    expect(screen.queryByTestId(/^logic-/)).toBeNull();
  });
});

describe('the recruiter corrects the AI interpretation before approval', () => {
  it('toggles an option between accepted directly and accepted only after review, and saves one set_logic operation', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue(semanticView());
    renderPanel('hr_manager');
    await screen.findByRole('heading', { name: 'Job analysis' });
    await user.click(screen.getByRole('button', { name: 'Edit analysis' }));
    await user.click(screen.getByTestId('logic-toggle-req_route-1'));
    expect(screen.getByTestId('logic-option-req_route-1').getAttribute('data-acceptance')).toBe('review');
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    const body = JSON.stringify(saveMock.mock.calls[0]);
    expect(body).toContain('"op":"set_logic"');
    expect(body).toContain('"item_id":"req_route"');
    expect(body).not.toContain('"id":"o1"');                                              // option ids are the server's
    expect(body).toContain('"acceptance":"review"');
  });
  it('refuses to save options that nothing could ever satisfy automatically', async () => {
    const user = userEvent.setup();
    renderPanel('admin');
    await screen.findByRole('heading', { name: 'Job analysis' });
    await user.click(screen.getByRole('button', { name: 'Edit analysis' }));
    await user.click(screen.getByTestId('logic-toggle-req_route-0'));
    await user.click(screen.getByTestId('logic-toggle-req_route-1'));
    expect(screen.getByRole('alert').textContent).toContain('At least one option must be accepted directly');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });
  it('clears a flagged ambiguity and edits the examples, each as one correction with the examples normalized', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 } as never);
    renderPanel('admin');
    await screen.findByRole('heading', { name: 'Job analysis' });
    await user.click(screen.getByRole('button', { name: 'Edit analysis' }));
    await user.click(screen.getByTestId('logic-clear-ambiguity-req_setting'));
    expect(screen.queryByTestId('logic-ambiguity-req_setting')).toBeNull();
    fireEvent.change(within(screen.getByTestId('logic-examples-req_tools')).getByRole('textbox'), { target: { value: 'spreadsheets\n\n a scheduling tool ' } });
    await user.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    const ops = saveMock.mock.calls[0][2] as { op: string; item_id: string; logic: unknown }[];
    expect(ops).toContainEqual({ op: 'set_logic', item_id: 'req_setting', logic: null });
    expect(ops).toContainEqual({ op: 'set_logic', item_id: 'req_tools', logic: { options: null, examples: ['spreadsheets', 'a scheduling tool'], ambiguity: null } });
    expect(ops.filter((o) => o.op === 'set_logic')).toHaveLength(2);
  });
  it('does not offer the correction controls to a role that cannot edit', async () => {
    renderPanel('recruiter');
    await screen.findByRole('heading', { name: 'Job analysis' });
    expect(screen.queryByTestId('logic-toggle-req_route-0')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit analysis' })).toBeNull();
  });
});

describe('the correction helpers', () => {
  it('send nothing when the draft equals the stored interpretation, and one operation when it differs', () => {
    expect(buildLogicOperation(ROUTE_ITEM, toInput(ROUTE))).toBeNull();
    expect(buildLogicOperation(ROUTE_ITEM, undefined)).toBeNull();
    const changed = toInput(ROUTE)!;
    changed.options![2].acceptance = 'direct';
    expect(buildLogicOperation(ROUTE_ITEM, changed)).toEqual({ op: 'set_logic', item_id: 'req_route', logic: changed });
    expect(buildLogicOperation(ROUTE_ITEM, null)).toEqual({ op: 'set_logic', item_id: 'req_route', logic: null });
  });
  it('normalizes examples and an empty draft', () => {
    expect(normalizeInput({ options: null, examples: [' a ', '', 'b'], ambiguity: { note: '  ' } })).toEqual({ options: null, examples: ['a', 'b'], ambiguity: null });
    expect(normalizeInput({ options: null, examples: [' '], ambiguity: null })).toBeNull();
  });
  it('mirrors the server rules for options', () => {
    expect(logicProblem({ options: [{ text: 'a', acceptance: 'direct', all_of: null }], examples: null, ambiguity: null }, 'en')).toContain('at least two');
    expect(logicProblem({ options: [{ text: 'a', acceptance: 'review', all_of: null }, { text: 'b', acceptance: 'review', all_of: null }], examples: null, ambiguity: null }, 'en')).toContain('directly');
    expect(logicProblem(toInput(ROUTE), 'en')).toBeNull();
    expect(logicProblem(null, 'en')).toBeNull();
  });
  it('knows which items carry an interpretation', () => {
    expect(hasLogic(ROUTE_ITEM) && hasLogic(SETTING_ITEM) && hasLogic(MERGED_ITEM)).toBe(true);
    expect(hasLogic(item('x', 'Plain', null))).toBe(false);
  });
  it('has an Arabic string for every English one', () => {
    expect(Object.keys(logicCopy('ar')).sort()).toEqual(Object.keys(logicCopy('en')).sort());
  });
});

describe('the interpretation in Arabic', () => {
  it('reads in Arabic with the text of the requirement left to the browser to orient', () => {
    const arabic = item('req_ar', 'درجة البكالوريوس في المحاسبة أو الإدارة المالية', {
      options: [{ id: 'o1', text: 'البكالوريوس في المحاسبة', acceptance: 'direct', all_of: null }, { id: 'o2', text: 'خبرة عملية طويلة بديلاً عن الدرجة', acceptance: 'review', all_of: null }],
      examples: ['برنامج كذا'], ambiguity: { note: 'غير واضح إن كان المقصود الاثنين معًا.' } });
    render(<RequirementLogic item={arabic} lang="ar" />);
    expect(screen.getByTestId('logic-options-req_ar').textContent).toContain('أيٌّ من');
    expect(screen.getByTestId('logic-option-req_ar-1').textContent).toContain('مقبول بعد المراجعة فقط');
    expect(screen.getByTestId('logic-examples-req_ar').textContent).toContain('وليست متطلبات');
    expect(screen.getByTestId('logic-ambiguity-req_ar').textContent).toContain('يحتاج إلى مراجعتك');
    expect(screen.getByText('البكالوريوس في المحاسبة').getAttribute('dir')).toBe('auto');
  });
});

// ── candidate scoring and the matrix show the same criteria and outcomes ───────────────────────────────────────────

const evalItem = (over: Partial<ItemView> = {}): ItemView => ({
  item_id: 'req_route', text: 'Diploma, or a degree with site experience', weight: 100, result: 'unclear', ai_result: 'unclear', earned: null, evidence: ['Ten years of site experience'],
  explanation: 'x', confidence: '0.900', source: 'ai', evaluated_at: null, resolution: null, history: [], review_reason: 'conditional_alternative',
  matched_option: 'Long site experience without a diploma',
  option_results: [
    { option_id: 'o1', text: 'Diploma in civil engineering', acceptance: 'direct', result: 'not_met', parts: null, evidence: ['no formal diploma'], explanation: '', confidence: '0.9', review_reason: null },
    { option_id: 'o2', text: 'Degree with two years on construction sites', acceptance: 'direct', result: 'not_met', parts: [{ text: 'Degree in an engineering field', result: 'met' }, { text: 'Two years on construction sites', result: 'not_met' }], evidence: [], explanation: '', confidence: '0.9', review_reason: null },
    { option_id: 'o3', text: 'Long site experience without a diploma', acceptance: 'review', result: 'met', parts: null, evidence: ['Ten years of site experience'], explanation: '', confidence: '0.9', review_reason: null },
  ], ...over });

function scoring(item: ItemView): ItemScoringView {
  return { application_id: 'app-1', scoring_method: 'item_level_v2', status: 'review_required', requirements_revision_used: 3, current_revision: 3, stale: false, final_score: null,
    final_score_raw: null, range: { min: '0', max: '100' }, unclear_count: 1, decision: null, eligibility_status: 'review_required', effective_decision: 'review_required',
    qualified_threshold: 85, partial_threshold: 65, categories: [{ category: 'skills', weight: 100, score: null, score_min: '0', score_max: '100', contribution: null, contribution_min: '0', contribution_max: '100', items: [item] }],
    preferred: { summary: { yes: 0, no: 0, not_clear: 0 }, items: [] }, narrative: {}, model: 'm', scored_at: null };
}

describe('candidate scoring shows which alternative was met and why a case needs review', () => {
  it('names the matched option, every option result, the part results and the review-only note', () => {
    render(<ItemScoring applicationId="app-1" userRole="viewer" lang="en" initial={scoring(evalItem())} />);
    expect(screen.getByTestId('matched-req_route').textContent).toBe('Met through: Long site experience without a diploma');
    expect(screen.getByTestId('option-req_route-o1').getAttribute('data-result')).toBe('not_met');
    expect(screen.getByTestId('option-req_route-o3').textContent).toContain('accepted only after review');
    expect(screen.getByTestId('option-req_route-o2').textContent).toContain('Degree in an engineering field — Met');
    expect(screen.getByTestId('reason-req_route').textContent).toContain('a person decides');
    expect(screen.getByTestId('result-req_route').textContent).toBe('Unclear');                  // a conditional alternative is never shown as met or not met
  });
  it('explains an ambiguous requirement and shows nothing extra for a plain item', () => {
    render(<ItemScoring applicationId="app-1" userRole="viewer" lang="en" initial={scoring(evalItem({ review_reason: 'ambiguous_requirement', matched_option: null, option_results: null }))} />);
    expect(screen.getByTestId('reason-req_route').textContent).toContain('ambiguous');
    expect(screen.queryByTestId('matched-req_route')).toBeNull();
    const { container } = render(<OptionResults item={evalItem({ review_reason: null, matched_option: null, option_results: null })} lang="en" />);
    expect(container.innerHTML).toBe('');
  });
  it('does not show a stale review reason once a person has resolved the item', () => {
    const { container } = render(<OptionResults item={evalItem({ result: 'met', ai_result: 'unclear', option_results: null, matched_option: null })} lang="en" />);
    expect(container.innerHTML).toBe('');
  });
  it('reads in Arabic', () => {
    render(<ItemScoring applicationId="app-1" userRole="viewer" lang="ar" initial={scoring(evalItem())} />);
    expect(screen.getByTestId('matched-req_route').textContent).toContain('تم الاستيفاء عبر');
    expect(screen.getByTestId('option-req_route-o3').textContent).toContain('مقبول بعد المراجعة فقط');
    expect(screen.getByTestId('reason-req_route').textContent).toContain('يقرر شخص');
  });
});

describe('the matrix drawer and flags carry the same outcomes', () => {
  it('shows the options, the matched alternative and the reason in the evidence drawer', () => {
    render(<MatrixEvidenceDrawer applicationId="app-1" candidateName="Ana" lang="en" focus={{ itemIds: ['req_route'], gateKeys: [] }} onClose={() => {}} onOpenCandidate={() => {}}
      initial={{ scoring: scoring(evalItem()), gates: null }} />);
    const drawer = screen.getByTestId('drawer-item-req_route');
    expect(within(drawer).getByTestId('matched-req_route')).toBeTruthy();
    expect(within(drawer).getByTestId('option-req_route-o1')).toBeTruthy();
    expect(within(drawer).getByTestId('reason-req_route').textContent).toContain('accepted only after review');
  });
  it('has English and Arabic wording for the two new review flags', () => {
    expect(FLAG_KINDS).toContain('conditional_alternative');
    expect(FLAG_KINDS).toContain('ambiguous_requirement');
    for (const kind of ['conditional_alternative', 'ambiguous_requirement'] as const) {
      expect(matrixCopy('en').flag[kind]).toBeTruthy();
      expect(matrixCopy('ar').flag[kind]).toBeTruthy();
    }
  });
});
