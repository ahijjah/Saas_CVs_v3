// Re-analysis (recovery) in the editor. Hidden while the platform switch is off. When on: a new analysis is requested with the revision and one
// idempotency key; keep, take, discard and a merge (every difference decided, the server's refusal reported) are explicit; nothing runs over unsaved
// edits; blocked states never read as passed or ready; EN and AR, right to left in Arabic.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS, fmt } from '../../components/requirementsV2/i18n';
import type { MergePreview, RecoveryView, RequirementsView } from '../../services/requirementsV2Api';
import { apiError, makeView, weightedDoc } from './fixtures';
import { mockApi, recoveryVersion, VERSION_ID } from './mocks';
import { editWording } from './review';

const JOB = '00000000-0000-0000-0000-0000000000aa';
const S = STRINGS.en;
const A = STRINGS.ar;

const recovery = (over: Partial<RecoveryView> = {}): RecoveryView => ({
  enabled: true, can_request: true, reason: null, blockers: ['description_changed'],
  active_version: { version_id: '00000000-0000-0000-0000-0000000000a1', version_no: 1 }, open_candidate: null, versions: [], ...over,
});

// The draft is bound to older inputs: the readiness is blocked by the inputs, and the recovery state is given by the test.
const blocked = (rec: Partial<RecoveryView> = {}): RequirementsView => makeView(weightedDoc(), {
  revision: 3,
  readiness: { state: 'inputs_changed', scoring_mode: null, can_proceed: false, guarded: true, basis: 'pipeline_inputs', reasons: [
    { code: 'description_changed', message: 'The job description changed.', category: null, item_id: null, field: 'description' },
  ], open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [] },
  inputs: { state: 'inputs_changed', changed: ['description'], unverified: false },
  recovery: recovery(rec),
});

const completedRec = () => ({ open_candidate: recoveryVersion(), versions: [recoveryVersion()], can_request: false, reason: 'candidate_open' as const });

async function setup(view: RequirementsView, opts: { isAr?: boolean; canEdit?: boolean; confirmFn?: (m: string) => boolean } = {}) {
  const api = mockApi(view);
  const ui = userEvent.setup();
  const toast = vi.fn();
  const confirmFn = opts.confirmFn ?? vi.fn(() => true);
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit={opts.canEdit ?? true} addToast={toast} confirmFn={confirmFn} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return { ui, api, toast, confirmFn };
}

describe('re-analysis is hidden while the platform switch is off', () => {
  it('shows no card, no control and no wording when the server reports it disabled', async () => {
    await setup(blocked({ enabled: false, can_request: false, reason: 'disabled', blockers: [], open_candidate: null, versions: [] }));
    expect(screen.queryByTestId('recovery')).toBeNull();
    expect(screen.queryByTestId('recovery-request')).toBeNull();
    expect(screen.queryByText(S.recoveryTitle)).toBeNull();
  });

  it('shows nothing for an older server that sends no recovery field', async () => {
    const view = blocked();
    delete (view as Partial<RequirementsView>).recovery;
    await setup(view);
    expect(screen.queryByTestId('recovery')).toBeNull();
  });
});

describe('requesting a new analysis', () => {
  it('offers the request to an editor with a clean draft and sends the revision with one UUID key', async () => {
    const { ui, api } = await setup(blocked());
    await ui.click(screen.getByTestId('recovery-request'));
    await waitFor(() => expect(api.requestRecovery).toHaveBeenCalledTimes(1));
    const [jobId, revision, key] = api.requestRecovery.mock.calls[0];
    expect(jobId).toBe(JOB);
    expect(revision).toBe(3);
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(api.keepRecovery).not.toHaveBeenCalled();
  });

  it('reuses the same key after a network failure, and a new key after the server refuses', async () => {
    const { ui, api } = await setup(blocked());
    api.requestRecovery.mockRejectedValueOnce(new Error('network down'));
    api.requestRecovery.mockRejectedValueOnce(apiError(409, { code: 'candidate_open', message: 'open' }));
    await ui.click(screen.getByTestId('recovery-request'));
    await waitFor(() => expect(api.requestRecovery).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('recovery-request').disabled).toBe(false));
    await ui.click(screen.getByTestId('recovery-request'));
    await waitFor(() => expect(api.requestRecovery).toHaveBeenCalledTimes(2));
    expect(api.requestRecovery.mock.calls[1][2]).toBe(api.requestRecovery.mock.calls[0][2]);
    await waitFor(() => expect(screen.getByTestId('recovery-request').disabled).toBe(false));
    await ui.click(screen.getByTestId('recovery-request'));
    await waitFor(() => expect(api.requestRecovery).toHaveBeenCalledTimes(3));
    expect(api.requestRecovery.mock.calls[2][2]).not.toBe(api.requestRecovery.mock.calls[0][2]);
  });

  it('sends nothing more while one request is in flight', async () => {
    const { ui, api } = await setup(blocked());
    let release: (v: unknown) => void = () => {};
    api.requestRecovery.mockReturnValueOnce(new Promise(r => { release = r; }));
    await ui.click(screen.getByTestId('recovery-request'));
    await waitFor(() => expect(screen.getByTestId('recovery-request').disabled).toBe(true));
    await ui.click(screen.getByTestId('recovery-request'));
    expect(api.requestRecovery).toHaveBeenCalledTimes(1);
    release({ version: recoveryVersion({ status: 'requested' }), idempotent: false });
  });

  it('is not offered to a viewer, who is told why', async () => {
    await setup(blocked(), { canEdit: false });
    expect(screen.queryByTestId('recovery-request')).toBeNull();
    expect(screen.getByTestId('recovery-viewer').textContent).toBe(S.recoveryViewerOnly);
  });

  it('is not allowed over unsaved edits: the control is disabled and the recruiter is told to save first', async () => {
    const { ui } = await setup(blocked());
    await editWording(ui, 'Python', ' (edited)');
    expect(screen.getByTestId('recovery-request').disabled).toBe(true);
    expect(screen.getByTestId('recovery-needs-save').textContent).toBe(S.recoveryNeedsSave);
  });

  it('says why there is nothing to re-analyse when the analysis already matches the job', async () => {
    await setup(blocked({ can_request: false, reason: 'bound', blockers: [] }));
    expect(screen.queryByTestId('recovery-request')).toBeNull();
    expect(screen.getByTestId('recovery-reason').textContent).toBe(S.recoveryReasonBound);
  });
});

describe('a completed analysis: keep, take, discard', () => {
  it('keep changes nothing in the draft: only the keep call is made and the blockers stay', async () => {
    const { ui, api } = await setup(blocked(completedRec()));
    await ui.click(screen.getByTestId('recovery-keep'));
    await waitFor(() => expect(api.keepRecovery).toHaveBeenCalledWith(JOB, VERSION_ID));
    expect(api.applyRecovery).not.toHaveBeenCalled();
    expect(api.discardRecovery).not.toHaveBeenCalled();
  });

  it('take replaces the draft with the candidate and sends the revision it was shown', async () => {
    const { ui, api } = await setup(blocked(completedRec()));
    await ui.click(screen.getByTestId('recovery-take'));
    await waitFor(() => expect(api.applyRecovery).toHaveBeenCalledWith(JOB, VERSION_ID, 3, 'take'));
  });

  it('discard asks first and does nothing when the recruiter declines', async () => {
    const declining = vi.fn(() => false);
    const { ui, api, confirmFn } = await setup(blocked(completedRec()), { confirmFn: declining });
    await ui.click(screen.getByTestId('recovery-discard'));
    expect(confirmFn).toHaveBeenCalledWith(S.recoveryDiscardConfirm);
    expect(api.discardRecovery).not.toHaveBeenCalled();
  });

  it('discard is sent once the recruiter confirms', async () => {
    const { ui, api } = await setup(blocked(completedRec()));
    await ui.click(screen.getByTestId('recovery-discard'));
    await waitFor(() => expect(api.discardRecovery).toHaveBeenCalledWith(JOB, VERSION_ID));
  });

  it('says that taking or merging does not clear a stale or review blocker', async () => {
    await setup(blocked(completedRec()));
    expect(screen.getByTestId('recovery').textContent).toContain(S.recoveryNoClear);
  });

  it('never reads as passed or ready while the inputs are blocked', async () => {
    await setup(blocked(completedRec()));
    const text = screen.getByTestId('recovery').textContent ?? '';
    expect(text).not.toMatch(/\bready\b|\bpassed\b/i);
  });
});

describe('merge: every difference decided by the recruiter', () => {
  const preview: MergePreview = {
    version_id: VERSION_ID, revision: 3,
    conflicts: [{ key: 'conflict:skills:python:0', category: 'skills', text: 'Python 3', fields: ['importance'],
                  draft: { importance: 'required' }, candidate: { importance: 'preferred' } }],
    draft_only: [{ key: 'draft_only:skills:docker:0', category: 'skills', text: 'Docker', origin: 'stated' }],
    candidate_only: [{ key: 'candidate_only:skills:sql:0', category: 'skills', text: 'SQL', origin: 'stated' }],
    weights: [{ key: 'weight:skills', category: 'skills', draft: 60, candidate: 70 }],
  };

  it('lists every difference, enables the merge only when all are decided, and sends the decisions', async () => {
    const { ui, api } = await setup(blocked(completedRec()));
    api.mergePreview.mockResolvedValue(preview);
    await ui.click(screen.getByTestId('recovery-merge'));
    await waitFor(() => expect(api.mergePreview).toHaveBeenCalledWith(JOB, VERSION_ID));
    expect(screen.getAllByTestId('merge-conflict')).toHaveLength(1);
    expect(screen.getAllByTestId('merge-draft-only')).toHaveLength(1);
    expect(screen.getAllByTestId('merge-candidate-only')).toHaveLength(1);
    expect(screen.getAllByTestId('merge-weight')).toHaveLength(1);
    expect(screen.getByTestId('merge-count').textContent).toBe(fmt(S.mergeCount, { done: 0, total: 4 }));
    expect(screen.getByTestId('merge-apply').disabled).toBe(true);

    const conflict = screen.getByTestId('merge-conflict');
    await ui.click(within(conflict).getByRole('radio', { name: S.mergeUseCandidate }));
    await ui.click(within(screen.getByTestId('merge-draft-only')).getByRole('radio', { name: S.mergeKeep }));
    await ui.click(within(screen.getByTestId('merge-candidate-only')).getByRole('radio', { name: S.mergeAdd }));
    expect(screen.getByTestId('merge-count').textContent).toBe(fmt(S.mergeCount, { done: 3, total: 4 }));
    expect(screen.getByTestId('merge-apply').disabled).toBe(true);

    await ui.click(within(screen.getByTestId('merge-weight')).getByRole('radio', { name: fmt(S.mergeUseWeight, { value: 70 }) }));
    expect(screen.getByTestId('merge-apply').disabled).toBe(false);
    await ui.click(screen.getByTestId('merge-apply'));
    await waitFor(() => expect(api.applyRecovery).toHaveBeenCalledWith(JOB, VERSION_ID, 3, 'merge', {
      'conflict:skills:python:0': 'candidate', 'draft_only:skills:docker:0': 'keep', 'candidate_only:skills:sql:0': 'add', 'weight:skills': 'candidate',
    }));
  });

  it('keeps the recruiter\'s own wording when they choose it', async () => {
    const { ui, api } = await setup(blocked(completedRec()));
    api.mergePreview.mockResolvedValue({ ...preview, draft_only: [], candidate_only: [], weights: [] });
    await ui.click(screen.getByTestId('recovery-merge'));
    await ui.click(await screen.findByRole('radio', { name: S.mergeUseDraft }));
    await ui.click(screen.getByTestId('merge-apply'));
    await waitFor(() => expect(api.applyRecovery).toHaveBeenCalledWith(JOB, VERSION_ID, 3, 'merge', { 'conflict:skills:python:0': 'draft' }));
  });

  it('reports a refused merge as the server\'s reason', async () => {
    const { ui, api, toast } = await setup(blocked(completedRec()));
    api.mergePreview.mockResolvedValue({ ...preview, draft_only: [], candidate_only: [], weights: [] });
    api.applyRecovery.mockRejectedValueOnce(apiError(422, { code: 'merge_keeps_directed_text', message: 'refused' }));
    await ui.click(screen.getByTestId('recovery-merge'));
    await ui.click(await screen.findByRole('radio', { name: S.mergeUseDraft }));
    await ui.click(screen.getByTestId('merge-apply'));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(S.err_merge_keeps_directed_text, 'error'));
  });
});

describe('rejected results and the history', () => {
  it('a result rejected because the job changed says so and a new request is still offered', async () => {
    await setup(blocked({
      can_request: true, reason: null, open_candidate: null,
      versions: [recoveryVersion({ version_no: 2, status: 'rejected', rejection_code: 'inputs_changed_during_call', completed_at: null })],
    }));
    expect(screen.getByTestId('recovery-rejected').textContent).toBe(S.recoveryRejectedInputs);
    expect(screen.getByTestId('recovery-request').disabled).toBe(false);
  });

  it('lists every version with its status in the history', async () => {
    const { ui } = await setup(blocked({
      open_candidate: recoveryVersion({ version_no: 3 }), reason: 'candidate_open', can_request: false,
      versions: [recoveryVersion({ version_no: 2, status: 'discarded', completed_at: null }), recoveryVersion({ version_no: 3 })],
    }));
    await ui.click(screen.getByText(S.recoveryHistory));
    const rows = screen.getAllByTestId('recovery-version');
    expect(rows.map(r => r.getAttribute('data-status'))).toEqual(['discarded', 'completed']);
    expect(rows[0].textContent).toContain(S.recoveryStatus_discarded);
  });
});

describe('English and Arabic', () => {
  it('renders the Arabic card right to left with the same controls', async () => {
    await setup(blocked(completedRec()), { isAr: true });
    const card = screen.getByTestId('recovery');
    expect(card.closest('[dir]')?.getAttribute('dir')).toBe('rtl');
    expect(within(card).getByText(A.recoveryIntro)).toBeTruthy();
    expect(within(card).getByTestId('recovery-keep').textContent).toBe(A.recoveryKeep);
    expect(within(card).getByTestId('recovery-take').textContent).toBe(A.recoveryTake);
  });

  it('renders the English card left to right', async () => {
    await setup(blocked(completedRec()));
    expect(screen.getByTestId('requirements-v2').getAttribute('dir')).toBe('ltr');
    expect(screen.getByTestId('recovery-merge').textContent).toBe(S.recoveryMerge);
  });
});
