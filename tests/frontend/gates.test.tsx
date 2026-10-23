import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import GateSummary from '../../components/GateSummary';
import RequirementGateControls from '../../components/RequirementGateControls';
import QuestionGateControl from '../../components/QuestionGateControl';
import { buildOperations } from '../../services/requirementsEdits';
import type { RequirementsItem } from '../../services/requirementsApi';
import type { GateSummaryData } from '../../services/gatesApi';
import { overrideGate, clearGateOverride, recordGateResult } from '../../services/gatesApi';
import {
  GATE_COPY, draftFromItem, gateProblem, hasPassingCriteria, linkableQuestions, overridesScoreDecision, questionGatePayload,
  questionGateProblem, requirementBadges, statusLabel,
} from '../../utils/gates';
import { ids, view } from './fixtures';

vi.mock('../../services/gatesApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/gatesApi')>()),
  getGates: vi.fn(), overrideGate: vi.fn(), clearGateOverride: vi.fn(), recordGateResult: vi.fn(), evaluateGates: vi.fn(),
}));

const Q1 = '11111111-1111-4111-8111-111111111111';
const Q2 = '22222222-2222-4222-8222-222222222222';

const base: RequirementsItem = { id: ids.py, text: 'Python', importance: 'required', weight: 50, origin: 'stated', source_text: 'Python is required',
  alternatives: null, experience: null };
const gated = (over: Partial<NonNullable<RequirementsItem['gate']>> = {}): RequirementsItem => ({
  ...base, gate: { mandatory: true, verification_method: 'cv_evidence', linked_question_id: null,
    confirmed_at: '2026-10-17T12:00:00+00:00', ...over } });
const suggested: RequirementsItem = { ...base, gate_suggestion: { cue: 'must', source_text: 'Candidates must know Python' } };

function summary(over: Partial<GateSummaryData> = {}, gate: Partial<GateSummaryData['gates'][0]> = {}): GateSummaryData {
  return {
    application_id: 'app-1', eligibility_status: 'not_eligible', effective_decision: 'not_eligible', evaluated_at: null,
    gates: [{ gate_key: 'req:a', source: 'requirement', label: 'Valid driving licence', verification_method: 'manual', processing_error: false,
      requirement_item_id: 'a', question_id: null, result: 'failed', computed_result: 'failed', evidence_source: 'manual',
      evidence_summary: 'Checked the original', confidence: null, evaluated_at: null, override_reason: null, overridden_at: null, history: [], ...gate }],
    ...over,
  };
}

beforeEach(() => { vi.mocked(overrideGate).mockReset(); vi.mocked(clearGateOverride).mockReset(); vi.mocked(recordGateResult).mockReset(); });

describe('badges', () => {
  it('shows Required · Mandatory Gate · Candidate Question for a confirmed question gate', () => {
    const labels = requirementBadges(gated({ verification_method: 'candidate_question', linked_question_id: Q1 }), 'en').map((b) => b.label);
    expect(labels).toEqual(['Required', 'Mandatory Gate', 'Candidate Question']);
  });
  it('shows AI Suggested until a person confirms, and never beside a confirmed gate', () => {
    expect(requirementBadges(suggested, 'en').map((b) => b.label)).toEqual(['Required', 'AI Suggested']);
    expect(requirementBadges({ ...suggested, gate: gated().gate }, 'en').map((b) => b.label)).toEqual(['Required', 'Mandatory Gate']);
  });
  it('never shows gate badges on a Preferred item', () => {
    expect(requirementBadges({ ...gated(), importance: 'preferred' }, 'en').map((b) => b.label)).toEqual(['Preferred']);
    expect(requirementBadges({ ...suggested, importance: 'preferred' }, 'en').map((b) => b.label)).toEqual(['Preferred']);
  });
  it('speaks Arabic', () => {
    expect(requirementBadges(gated(), 'ar').map((b) => b.label)).toEqual([GATE_COPY.ar.badgeRequired, GATE_COPY.ar.mandatoryGate]);
  });
});

describe('the gate edit payloads', () => {
  const v = () => view();
  it('sends set_gate only when the gate differs from the stored one, with no confirmation fields', () => {
    const ops = buildOperations(v(), { [ids.py]: { gate: { mandatory: true, verification_method: 'manual', linked_question_id: null } } }, {}, {}, []);
    expect(ops).toEqual([{ op: 'set_gate', item_id: ids.py, verification_method: 'manual', linked_question_id: null }]);
    expect(JSON.stringify(ops)).not.toMatch(/confirmed_by|confirmed_at|mandatory/);
  });
  it('sends the link only for a candidate-question gate', () => {
    const ops = buildOperations(v(), { [ids.py]: { gate: { mandatory: true, verification_method: 'manual', linked_question_id: Q1 } } }, {}, {}, []);
    expect(ops[0]).toMatchObject({ linked_question_id: null });
    const linked = buildOperations(v(), { [ids.py]: { gate: { mandatory: true, verification_method: 'candidate_question', linked_question_id: Q1 } } }, {}, {}, []);
    expect(linked[0]).toMatchObject({ linked_question_id: Q1 });
  });
  it('sends clear_gate only when there is a stored gate to clear', () => {
    const withGate = v();
    withGate.document.categories.skills.items[0] = gated();
    expect(buildOperations(withGate, { [ids.py]: { gate: null } }, {}, {}, [])).toEqual([{ op: 'clear_gate', item_id: ids.py }]);
    expect(buildOperations(v(), { [ids.py]: { gate: null } }, {}, {}, [])).toEqual([]);
  });
  it('sends nothing for an unchanged gate and nothing for a gate on an item that becomes Preferred', () => {
    const withGate = v();
    withGate.document.categories.skills.items[0] = gated();
    expect(buildOperations(withGate, { [ids.py]: { gate: draftFromItem(gated()) } }, {}, {}, [])).toEqual([]);
    const ops = buildOperations(withGate, { [ids.py]: { importance: 'preferred', gate: draftFromItem(gated()) } }, {}, {}, []);
    expect(ops).toEqual([{ op: 'set_importance', item_id: ids.py, importance: 'preferred' }]);
  });
});

describe('client checks that mirror the server', () => {
  const d = (over = {}) => ({ mandatory: true as const, verification_method: 'cv_evidence' as const, linked_question_id: null, ...over });
  it('refuses a gate on a Preferred item, a missing link, a contradictory link and a reused question', () => {
    expect(gateProblem(d(), 'preferred', [], 'en')).toBe(GATE_COPY.en.preferredCannotBeGate);
    expect(gateProblem(d({ verification_method: 'candidate_question' }), 'required', [], 'en')).toBe(GATE_COPY.en.problems.link);
    expect(gateProblem(d({ linked_question_id: Q1 }), 'required', [], 'en')).toBe(GATE_COPY.en.problems.contradiction);
    expect(gateProblem(d({ verification_method: 'candidate_question', linked_question_id: Q1 }), 'required', [Q1], 'en')).toBe(GATE_COPY.en.problems.duplicate);
    expect(gateProblem(d(), 'required', [], 'en')).toBeNull();
    expect(gateProblem(null, 'required', [], 'en')).toBeNull();
  });
  it('lists only eligibility-gate questions as linkable', () => {
    const qs = [{ question_id: Q1, is_eligibility_gate: true }, { question_id: Q2, is_eligibility_gate: false }, { question_id: 'x' }] as any;
    expect(linkableQuestions(qs).map((q) => q.question_id)).toEqual([Q1]);
    expect(linkableQuestions(null)).toEqual([]);
  });
});

describe('RequirementGateControls', () => {
  const props = { questions: [], otherLinkedQuestionIds: [], lang: 'en' as const, onChange: vi.fn() };
  it('offers the Mandatory Gate control for a Required item', () => {
    render(<RequirementGateControls item={base} draft={undefined} importance="required" {...props} />);
    expect(screen.getByLabelText(GATE_COPY.en.mandatoryGateControl)).not.toBeChecked();
  });
  it('shows no control for a Preferred item, only the reason when it carries a gate or suggestion', () => {
    const { container, rerender } = render(<RequirementGateControls item={{ ...base, importance: 'preferred' }} draft={undefined} importance="preferred" {...props} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<RequirementGateControls item={{ ...gated(), importance: 'preferred' }} draft={undefined} importance="preferred" {...props} />);
    expect(screen.getByTestId('gate-preferred-note')).toHaveTextContent(GATE_COPY.en.preferredCannotBeGate);
    expect(screen.queryByLabelText(GATE_COPY.en.mandatoryGateControl)).toBeNull();
  });
  it('shows the AI suggestion as unconfirmed and confirms only on a click', () => {
    const onChange = vi.fn();
    render(<RequirementGateControls item={suggested} draft={undefined} importance="required" {...props} onChange={onChange} />);
    expect(screen.getByTestId('gate-suggestion')).toHaveTextContent('must');
    expect(screen.getByLabelText(GATE_COPY.en.mandatoryGateControl)).not.toBeChecked();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: GATE_COPY.en.confirmGate }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mandatory: true, verification_method: 'cv_evidence', linked_question_id: null }));
  });
  it('exposes only the mandatory switch: no failure action, no verification method, no question link', () => {
    for (const item of [base, gated(), gated({ verification_method: 'candidate_question', linked_question_id: Q1 })]) {
      const { container, unmount } = render(<RequirementGateControls item={item} draft={undefined} importance="required" {...props} />);
      expect(container.querySelectorAll('select')).toHaveLength(0);
      expect(screen.queryByText(/if the check fails/i)).toBeNull();
      expect(screen.queryByText(/hold for review/i)).toBeNull();
      expect(screen.queryByLabelText(GATE_COPY.en.verificationMethod)).toBeNull();
      expect(screen.queryByLabelText(GATE_COPY.en.linkedQuestion)).toBeNull();
      expect(screen.getByLabelText(GATE_COPY.en.mandatoryGateControl)).toBeInTheDocument();
      unmount();
    }
  });
  it('has no failure-action wording in either language', () => {
    for (const lang of ['en', 'ar'] as const) {
      const copy = GATE_COPY[lang] as Record<string, unknown>;
      for (const key of ['failureAction', 'actions', 'onFail', 'linkedGovernsAction']) expect(copy[key]).toBeUndefined();
    }
  });
  it('turns the gate off and on with the switch alone', () => {
    const onChange = vi.fn();
    render(<RequirementGateControls item={gated()} draft={undefined} importance="required" {...props} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(GATE_COPY.en.mandatoryGateControl));
    expect(onChange).toHaveBeenCalledWith(null);
  });
  it('renders in Arabic', () => {
    render(<RequirementGateControls item={base} draft={undefined} importance="required" {...props} lang="ar" />);
    expect(screen.getByLabelText(GATE_COPY.ar.mandatoryGateControl)).toBeInTheDocument();
  });
});

describe('the knockout-question gate control', () => {
  it('is off by default, so an existing question stays advisory', () => {
    render(<QuestionGateControl lang="en" question={{ question_type: 'yes_no', passingAnswers: ['yes'] }} onChange={vi.fn()} />);
    expect(screen.getByLabelText('Use as Eligibility Gate')).not.toBeChecked();
    expect(screen.queryByText(/if the check fails/i)).toBeNull();
  });
  it('reports the change when switched on and shows no failure action', () => {
    const onChange = vi.fn();
    const { rerender } = render(<QuestionGateControl lang="en" question={{ question_type: 'yes_no', passingAnswers: ['yes'] }} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('Use as Eligibility Gate'));
    expect(onChange).toHaveBeenCalledWith('is_eligibility_gate', true);
    rerender(<QuestionGateControl lang="en" question={{ question_type: 'yes_no', passingAnswers: ['yes'], is_eligibility_gate: true }} onChange={onChange} />);
    expect(screen.getByLabelText('Use as Eligibility Gate')).toBeChecked();
    expect(document.querySelectorAll('select')).toHaveLength(0);
  });
  it('asks for passing criteria only when the question is a gate without them', () => {
    const { rerender } = render(<QuestionGateControl lang="en" question={{ question_type: 'yes_no', passingAnswers: [], is_eligibility_gate: false }} onChange={vi.fn()} />);
    expect(screen.queryByRole('alert')).toBeNull();
    rerender(<QuestionGateControl lang="en" question={{ question_type: 'yes_no', passingAnswers: [], is_eligibility_gate: true }} onChange={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent(GATE_COPY.en.gateNeedsCriteria);
  });
  it('shows the linked requirement and no failure action', () => {
    render(<QuestionGateControl lang="en" onChange={vi.fn()}
      question={{ question_type: 'yes_no', passingAnswers: ['yes'], is_eligibility_gate: true,
        linked_requirement: { item_id: 'a', text: 'Valid driving licence' } }} />);
    expect(screen.getByTestId('question-linked-requirement')).toHaveTextContent('Valid driving licence');
    expect(document.querySelectorAll('select')).toHaveLength(0);
  });
  it('renders in Arabic', () => {
    render(<QuestionGateControl lang="ar" question={{ question_type: 'yes_no', passingAnswers: ['yes'] }} onChange={vi.fn()} />);
    expect(screen.getByLabelText(GATE_COPY.ar.useAsEligibilityGate)).toBeInTheDocument();
  });
  it('checks passing criteria per question type', () => {
    expect(hasPassingCriteria({ question_type: 'yes_no', passingAnswers: ['no'] })).toBe(true);
    expect(hasPassingCriteria({ question_type: 'yes_no', passingAnswers: [] })).toBe(false);
    expect(hasPassingCriteria({ question_type: 'number', passingValue: '3' })).toBe(true);
    expect(hasPassingCriteria({ question_type: 'number', passingValue: '' })).toBe(false);
    expect(hasPassingCriteria({ question_type: 'number', passingValue: 'x' })).toBe(false);
    expect(hasPassingCriteria({ question_type: 'single_choice', optionsText: 'A\nB', passingAnswers: ['A'] })).toBe(true);
    expect(hasPassingCriteria({ question_type: 'single_choice', optionsText: 'A\nB', passingAnswers: ['C'] })).toBe(false);
  });
  it('sends gate fields only as an explicit choice, keeps ids and never sends a failure action', () => {
    expect(questionGatePayload({})).toEqual({ is_eligibility_gate: false });
    expect(questionGatePayload({ question_id: Q1, is_eligibility_gate: true })).toEqual({ question_id: Q1, is_eligibility_gate: true });
    expect(questionGatePayload({ is_eligibility_gate: true, gate_failure_action: 'hold_for_review' } as any)).toEqual({ is_eligibility_gate: true });
  });
  it('blocks a save of a gate without criteria', () => {
    expect(questionGateProblem({ is_eligibility_gate: true, question_type: 'yes_no', passingAnswers: [] }, 'en')).toBe(GATE_COPY.en.gateNeedsCriteria);
    expect(questionGateProblem({ is_eligibility_gate: false, question_type: 'yes_no', passingAnswers: [] }, 'en')).toBeNull();
  });
  it('names Response Required and Eligibility Gate separately in both languages', () => {
    expect(GATE_COPY.en.responseRequired).toBe('Response Required');
    expect(GATE_COPY.en.useAsEligibilityGate).toBe('Use as Eligibility Gate');
    expect(GATE_COPY.ar.responseRequired).not.toBe(GATE_COPY.ar.useAsEligibilityGate);
  });
});

describe('GateSummary', () => {
  it('shows the overall status first and says the score cannot override a failed gate', () => {
    render(<GateSummary applicationId="app-1" userRole="viewer" lang="en" initial={summary()} />);
    expect(screen.getByTestId('gate-overall-status')).toHaveTextContent('Not Eligible');
    expect(screen.getByTestId('gate-summary')).toHaveAttribute('data-status', 'not_eligible');
    expect(screen.getByTestId('gate-score-note')).toHaveTextContent(GATE_COPY.en.scoreNote);
    expect(screen.getByTestId('gate-result-req:a')).toHaveTextContent('Failed');
  });
  it('shows nothing for a job without enforced gates', () => {
    const { container } = render(<GateSummary applicationId="app-1" userRole="admin" lang="en" initial={summary({ eligibility_status: 'not_applicable', gates: [] })} />);
    expect(container).toBeEmptyDOMElement();
  });
  it.each(['recruiter', 'viewer', null])('gives %s no override, record or evaluate controls', (role) => {
    render(<GateSummary applicationId="app-1" userRole={role as any} lang="en" initial={summary()} />);
    expect(screen.queryByRole('button', { name: GATE_COPY.en.overrideAction })).toBeNull();
    expect(screen.queryByRole('button', { name: GATE_COPY.en.recordResult })).toBeNull();
    expect(screen.queryByRole('button', { name: GATE_COPY.en.evaluateAgain })).toBeNull();
    expect(screen.getByText(GATE_COPY.en.viewOnly)).toBeInTheDocument();
  });
  it.each(['admin', 'hr_manager'])('lets %s override, but only with a reason', async (role) => {
    vi.mocked(overrideGate).mockResolvedValue(summary({ eligibility_status: 'eligible', effective_decision: 'qualified' },
      { result: 'overridden', override_reason: 'Verified in person', history: [{ event_type: 'overridden', original_result: 'failed', new_result: 'overridden', reason: 'Verified in person', at: '2026-10-17T12:00:00+00:00', by: 'Ada' }] }));
    render(<GateSummary applicationId="app-1" userRole={role} lang="en" initial={summary()} />);
    fireEvent.click(screen.getByRole('button', { name: GATE_COPY.en.overrideAction }));
    const confirm = screen.getByRole('button', { name: GATE_COPY.en.overrideConfirm });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(GATE_COPY.en.overrideReason), { target: { value: '   ' } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText(GATE_COPY.en.overrideReason), { target: { value: ' Verified in person ' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(overrideGate).toHaveBeenCalledWith('app-1', 'req:a', 'Verified in person'));
    expect(await screen.findByTestId('gate-overall-status')).toHaveTextContent('Eligible');
    expect(screen.getByTestId('gate-override-req:a')).toHaveTextContent('Failed');            // the original result stays visible
    expect(screen.getByTestId('gate-override-req:a')).toHaveTextContent('Verified in person');
  });
  it('shows the override history with original and new result, user and reason', () => {
    render(<GateSummary applicationId="app-1" userRole="admin" lang="en" initial={summary({ eligibility_status: 'eligible' },
      { result: 'overridden', override_reason: 'Verified', history: [{ event_type: 'overridden', original_result: 'failed', new_result: 'overridden', reason: 'Verified', at: null, by: 'Ada' }] })} />);
    expect(screen.getByTestId('gate-history-req:a')).toHaveTextContent('Failed → Overridden');
    expect(screen.getByTestId('gate-history-req:a')).toHaveTextContent('Ada');
  });
  it('offers to record a result only for assessment and manual gates', () => {
    const first = render(<GateSummary applicationId="app-1" userRole="admin" lang="en" initial={summary({}, { verification_method: 'cv_evidence' })} />);
    expect(screen.queryByRole('button', { name: GATE_COPY.en.recordResult })).toBeNull();
    first.unmount();
    render(<GateSummary applicationId="app-1" userRole="admin" lang="en" initial={summary({}, { verification_method: 'assessment', result: 'not_assessed', computed_result: 'not_assessed' })} />);
    expect(screen.getByRole('button', { name: GATE_COPY.en.recordResult })).toBeInTheDocument();
  });
  it('renders in Arabic with the same structure', () => {
    render(<GateSummary applicationId="app-1" userRole="admin" lang="ar" initial={summary()} />);
    expect(screen.getByTestId('gate-overall-status')).toHaveTextContent(GATE_COPY.ar.status.not_eligible);
    expect(screen.getByTestId('gate-score-note')).toHaveTextContent(GATE_COPY.ar.scoreNote);
  });
});

describe('processing errors and legacy records', () => {
  it('shows a processing error as a retry state, not as a result about the candidate', () => {
    render(<GateSummary applicationId="app-1" userRole="admin" lang="en"
      initial={summary({ eligibility_status: 'review_required', effective_decision: 'review_required' },
        { verification_method: 'cv_evidence', result: 'not_assessed', computed_result: 'not_assessed', processing_error: true,
          evidence_summary: 'Processing error: the CV check could not be completed. Evaluate again to retry.', evidence_source: 'none' })} />);
    expect(screen.getByTestId('gate-result-req:a')).toHaveTextContent('Processing error');
    expect(screen.getByTestId('gate-result-req:a')).not.toHaveTextContent('Failed');
    expect(screen.getByTestId('gate-processing-error-req:a')).toHaveTextContent('says nothing about the candidate');
    expect(screen.getByTestId('gate-overall-status')).toHaveTextContent('Review Required');
    expect(screen.getByRole('button', { name: GATE_COPY.en.evaluateAgain })).toBeInTheDocument();     // retry
  });
  it('shows the processing error in Arabic', () => {
    render(<GateSummary applicationId="app-1" userRole="admin" lang="ar"
      initial={summary({ eligibility_status: 'review_required' }, { result: 'not_assessed', computed_result: 'not_assessed', processing_error: true })} />);
    expect(screen.getByTestId('gate-result-req:a')).toHaveTextContent(GATE_COPY.ar.processingError);
  });
  it('reads an existing record that still carries the old failure-action values without showing them', () => {
    const legacy = { ...gated(), gate: { ...gated().gate, failure_action: 'hold_for_review' } } as any;
    expect(requirementBadges(legacy, 'en').map((b) => b.label)).toEqual(['Required', 'Mandatory Gate']);
    expect(draftFromItem(legacy)).toEqual({ mandatory: true, verification_method: 'cv_evidence', linked_question_id: null });
    render(<GateSummary applicationId="app-1" userRole="admin" lang="en"
      initial={{ ...summary(), gates: [{ ...summary().gates[0], failure_action: 'hold_for_review' } as any] }} />);
    expect(screen.queryByText(/hold for review/i)).toBeNull();
    expect(screen.queryByText(/if it fails|if the check fails/i)).toBeNull();
  });
  it('sends no failure action when a gate is confirmed or changed, whatever an old record carried', () => {
    const v = view();
    v.document.categories.skills.items[0] = { ...gated(), gate: { ...gated().gate, failure_action: 'hold_for_review' } } as any;
    const ops = buildOperations(v, { [ids.py]: { gate: { mandatory: true, verification_method: 'manual', linked_question_id: null } } }, {}, {}, []);
    expect(JSON.stringify(ops)).not.toMatch(/failure_action|hold_for_review/);
  });
});

describe('status helpers', () => {
  it('lets only a failed or unresolved gate replace the score outcome', () => {
    expect(overridesScoreDecision('not_eligible')).toBe(true);
    expect(overridesScoreDecision('review_required')).toBe(true);
    expect(overridesScoreDecision('eligible')).toBe(false);
    expect(overridesScoreDecision('not_applicable')).toBe(false);
    expect(overridesScoreDecision(null)).toBe(false);
  });
  it('labels every status in both languages and falls back to no gates', () => {
    for (const s of ['eligible', 'not_eligible', 'review_required', 'not_applicable']) {
      expect(statusLabel(s, 'en')).toBeTruthy();
      expect(statusLabel(s, 'ar')).toBeTruthy();
    }
    expect(statusLabel('bogus', 'en')).toBe(GATE_COPY.en.status.not_applicable);
  });
  it('keeps the English and Arabic copy in step', () => {
    const keys = (o: any): string[] => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v).map((s) => `${k}.${s}`) : [k]));
    expect(keys(GATE_COPY.ar).sort()).toEqual(keys(GATE_COPY.en).sort());
  });
});
