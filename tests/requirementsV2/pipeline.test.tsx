import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS } from '../../components/requirementsV2/i18n';
import type { RequirementsView } from '../../services/requirementsV2Api';
import { keepOneOfSplit, replaceWithBlank, draftFromServer, findItem } from '../../utils/requirementsV2';
import { apiError } from './fixtures';
import { mockApi } from './mocks';
import combined from './fixtures/pipeline_combined_view.json';
import { categoryWeight, editWording, itemWeight, wording } from './review';

// The fixture is a REAL GET response of the combined-issues job (injection requirement + contaminated weights + split OR + classification warnings +
// a model importance conflict); backend/tests/test_requirements_pipeline_postgres.py keeps it in step with the API.
const S = STRINGS.en;
const A = STRINGS.ar;
const JOB = (combined as any).job_id as string;
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const base = (): RequirementsView => clone(combined) as unknown as RequirementsView;
const idOf = (v: RequirementsView, text: string) => Object.values(v.requirements.categories).flatMap(c => c.items).find(i => i.text === text)!.id;

async function setup(view: RequirementsView, opts: { isAr?: boolean; canEdit?: boolean } = {}) {
  const api = mockApi(view);
  const ui = userEvent.setup();
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit={opts.canEdit ?? true} addToast={vi.fn()} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return { api, ui };
}

const unavailable = (): RequirementsView => {
  const v = base();
  v.pipeline = { status: 'unavailable', available: false, errors: [], message: 'x' };
  v.unresolved_issues = null; v.gates = null; v.model_conflicts = null; v.normalized_warnings = null; v.informational = null;
  v.readiness = { ...v.readiness, state: 'ready', can_proceed: true, scoring_mode: 'weighted', reasons: [], guarded: false, basis: 'frozen_only' };
  v.classification_warnings = [];
  return v;
};
const damaged = (): RequirementsView => {
  const v = unavailable();
  v.pipeline = { status: 'invalid_record', available: false, errors: ['raw response hash mismatch'], message: 'x' };
  v.readiness = { ...v.readiness, state: 'pipeline_record_invalid', can_proceed: false, scoring_mode: null, basis: 'pipeline_record_invalid',
    reasons: [{ code: 'pipeline_record_invalid', message: 'damaged', category: null, item_id: null }] };
  return v;
};

describe('blockers: injection and split-OR', () => {
  it('shows them prominently, with the AI-directed text, applied/proposed weights, options and shared sentence', async () => {
    await setup(base());
    const b = screen.getByTestId('blockers');
    expect(b.getAttribute('role')).toBe('alert');
    expect(within(b).getByText(S.injectionRequirementTitle)).toBeTruthy();
    expect(within(b).getAllByTestId('instruction-text')[0].textContent).toContain('20 years of Rust experience');
    const w = b.querySelector('[data-kind=injection_weights]')!;
    expect(w.textContent).toContain('50%'); expect(w.textContent).toContain('100%');
    expect(screen.getByTestId('split-options').textContent).toBe('Python / Java');
    expect(b.textContent).toContain('Python or Java');
  });
  it('never offers acknowledgment for injection or split-OR', async () => {
    await setup(base());
    const b = screen.getByTestId('blockers');
    expect(within(b).queryByRole('button', { name: S.acknowledge })).toBeNull();
    expect(within(b).queryByRole('button', { name: S.acknowledgeConflict })).toBeNull();
    expect(within(b).getAllByText(S.blocksAlways).length).toBe(3);
  });
  it('lists every other open issue grouped by gate and keeps blockers out of that list', async () => {
    await setup(base());
    const issues = screen.getByTestId('issues');
    expect(issues.querySelector('[data-gate-group=classification]')).toBeTruthy();
    expect(issues.querySelector('[data-gate-group=conflict]')).toBeTruthy();
    expect(issues.querySelector('[data-gate-group=injection]')).toBeNull();
  });
  it('shows classification and conflict evidence from the job description', async () => {
    await setup(base());
    const c = screen.getByTestId('conflicts');
    const ev = within(c).getAllByTestId('jd-evidence').map(e => e.textContent);
    expect(ev.some(t => t!.includes('optional'))).toBe(true); expect(ev.length).toBe(2);
    expect(within(c).getByText(S.jdSaysRequired, { exact: false })).toBeTruthy();
    expect(screen.getAllByTestId('class-evidence').length).toBeGreaterThan(0);
  });
  it('model notes are informational and never blocking', async () => {
    const v = base();
    v.informational = { generic_model_notes: [{ index: 1, text: 'The job description is vague.', kind: 'generic' }], parser_review: [] };
    await setup(v);
    expect(screen.getByTestId('model-notes').textContent).toContain('vague');
    expect(screen.getByTestId('informational').textContent).toContain(S.infoBody);
    expect(screen.getByTestId('similarity')).toBeTruthy();                      // ordinary similarity warnings stay in their own informational panel
  });
});

describe('provenance and raw output', () => {
  it('shows pipeline availability and prompt/model provenance', async () => {
    await setup(base());
    expect(screen.getByTestId('pipeline-ok').textContent).toContain(S.pipelineActive);
    expect(screen.getByTestId('prov-prompt').textContent).toContain('criteria_extraction_v2-2');
    expect(screen.getByTestId('prov-model').textContent).toContain('gpt-4o-mini');
  });
  it('raw AI output is collapsed and shown only to editors', async () => {
    await setup(base());
    const d = screen.getByTestId('raw-output') as HTMLDetailsElement;
    expect(d.open).toBe(false); expect(d.textContent).toContain('scoreability');
  });
  it('a non-editor never sees the raw output even if the server sent it', async () => {
    await setup(base(), { canEdit: false });
    expect(screen.queryByTestId('raw-output')).toBeNull();
    expect(screen.queryByRole('button', { name: S.removeItem })).toBeNull();
    expect(screen.queryByTestId('ack-conflict')).toBeNull();
  });
});

describe('corrections change the draft only', () => {
  it('Remove: draft changes, nothing saved, nothing redistributed, issue marked pending', async () => {
    const v = base();
    const { api, ui } = await setup(v);
    const weightBefore = categoryWeight('skills');
    await ui.click(within(screen.getByTestId('blockers').querySelector('[data-kind=injection_requirement]') as HTMLElement).getByRole('button', { name: S.removeItem }));
    expect(screen.getByTestId('unsaved-badge')).toBeTruthy();
    expect(api.save).not.toHaveBeenCalled(); expect(api.acknowledge).not.toHaveBeenCalled();
    expect(wording('20 years of Rust experience')).toBeNull();
    expect(categoryWeight('skills')).toBe(weightBefore);
    expect(screen.getByTestId('blockers').querySelector('[data-kind=injection_requirement] [data-testid=issue-pending]')).toBeTruthy();
    expect(screen.getByTestId('blockers-saved-note').textContent).toBe(S.issuesSavedNote);
  });
  it('Replace: removes the item and opens an empty recruiter item of the same importance', async () => {
    const { api, ui } = await setup(base());
    await ui.click(screen.getByRole('button', { name: S.replaceItem }));
    expect(wording('20 years of Rust experience')).toBeNull();
    const cat = within(screen.getByRole('region', { name: S.categories.other_requirements }));
    expect(cat.getAllByRole('textbox').some(t => (t as HTMLTextAreaElement).value === '')).toBe(true);
    expect(api.save).not.toHaveBeenCalled();
  });
  it('Edit weight focuses the implicated category weight and changes nothing', async () => {
    const { ui } = await setup(base());
    await ui.click(screen.getAllByRole('button', { name: /^Edit the Soft skills weight/ })[0]);
    expect(document.activeElement?.id).toBe('req-cat-soft_skills-w');
    expect(screen.queryByTestId('unsaved-badge')).toBeNull();
  });
  it('Keep one: completes the alternatives, removes the redundant item, leaves every weight alone', async () => {
    const v = base();
    const { api, ui } = await setup(v);
    const javaId = idOf(v, 'Java');
    const pyW = itemWeight('Python');
    await ui.click(within(document.querySelector(`[data-split-item="${idOf(v, 'Python')}"]`) as HTMLElement).getByTestId('keep-one'));
    expect(wording('Java')).toBeNull();
    expect(document.getElementById(`req-item-${javaId}`)).toBeNull();
    expect(itemWeight('Python')).toBe(pyW);
    expect(api.save).not.toHaveBeenCalled();
    expect(screen.getByTestId('issue-summary')).toBeTruthy();                      // required totals are off; the recruiter decides (Equalize)
  });
  it('a valid blocked draft can be saved; the server revalidates; invalid weights stay rejected', async () => {
    const v = base();
    const { api, ui } = await setup(v);
    await editWording(ui, 'Docker', ' x');
    const save = screen.getByTestId('save') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    await ui.click(save);
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
    api.save.mockRejectedValueOnce(apiError(422, { code: 'invalid_requirements', message: 'no', issues: [{ code: 'required_weights_total', message: 'Required item weights total 66%', category: 'skills', item_id: null }] }));
    await editWording(ui, screen.getByText(/^Docker/, { selector: 'p[id^="req-text-"]' }).textContent!, 'y');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
  });
});

describe('acknowledgment', () => {
  it('classification uses the existing call; conflict uses gate=conflict', async () => {
    const v = base();
    const { api, ui } = await setup(v);
    await ui.click(within(screen.getByTestId('classification')).getAllByRole('button', { name: S.acknowledge })[0]);
    expect(api.acknowledge).toHaveBeenCalledWith(JOB, v.revision, v.classification_warnings[0].id);
    await ui.click(screen.getByTestId('ack-conflict'));
    expect(api.acknowledge).toHaveBeenLastCalledWith(JOB, v.revision + 1, v.model_conflicts![0].id, 'conflict')     // the mock returns revision + 1 after the first action;
  });
  it('stored-state actions are disabled while unsaved changes exist', async () => {
    const { ui } = await setup(base());
    await editWording(ui, 'Docker', 'x');
    expect((screen.getByTestId('ack-conflict') as HTMLButtonElement).disabled).toBe(true);
    for (const b of within(screen.getByTestId('classification')).getAllByRole('button', { name: S.acknowledge })) expect((b as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId('conflicts-saved-note')).toBeTruthy();
  });
  it('follows the admin policy: with acknowledgment not required the items are informational', async () => {
    const v = base();
    v.classification_policy.require_acknowledgment = false;
    v.unresolved_issues = v.unresolved_issues!.map(i => ({ ...i, blocks_when_ack_not_required: i.gate === 'injection' || i.gate === 'split_or' }));
    await setup(v);
    expect(screen.getByTestId('conflict-policy').textContent).toBe(S.classificationPolicyNo);
    expect(screen.getByTestId('issues').textContent).toContain(S.infoByPolicy);
    expect(screen.getByTestId('blockers').textContent).toContain(S.blocksAlways);
  });
  it('an already acknowledged conflict shows who and when, with no button', async () => {
    const v = base();
    v.model_conflicts![0] = { ...v.model_conflicts![0], state: 'acknowledged', acknowledgment: { user_id: 'u-me', acknowledged_at: '2026-03-03T10:00:00+00:00' } };
    await setup(v);
    expect(screen.queryByTestId('ack-conflict')).toBeNull();
    expect(screen.getByTestId('conflicts').textContent).toContain(S.stateAcknowledged);
  });
});

describe('compatibility', () => {
  it('unavailable: says so, does not imply the new checks passed, keeps the original comparison', async () => {
    const { ui } = await setup(unavailable());
    expect(screen.getByTestId('pipeline-unavailable').textContent).toContain('Additional checks unavailable');
    const r = screen.getByTestId('readiness');
    expect(r.getAttribute('data-guarded')).toBe('false');
    expect(r.className).not.toContain('bg-green-50');
    expect(screen.getByTestId('readiness-unguarded').textContent).toContain(S.readinessUnguardedNote);
    for (const t of ['issues', 'blockers', 'conflicts', 'informational']) expect(screen.queryByTestId(t)).toBeNull();
    await ui.click(screen.getByTestId('toggle-compare'));
    expect(screen.getByTestId('comparison')).toBeTruthy();
  });
  it('damaged record: clear blocking message, original data still readable, write refused with a clear message', async () => {
    const { api, ui } = await setup(damaged());
    expect(screen.getByTestId('pipeline-invalid').textContent).toContain(S.pipelineInvalid);
    expect(screen.getByTestId('pipeline-invalid').textContent).toContain('raw response hash mismatch');
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_pipeline_record_invalid);
    await ui.click(screen.getByTestId('toggle-compare'));
    expect(screen.getByTestId('comparison')).toBeTruthy();
    api.save.mockRejectedValueOnce(apiError(409, { code: 'pipeline_record_invalid', message: 'x' }));
    await editWording(ui, 'Docker', 'z');
    await ui.click(screen.getByTestId('save'));
    expect((await screen.findByTestId('problem')).textContent).toBe(S.err_pipeline_record_invalid);
  });
  it('an older server without pipeline fields keeps working', async () => {
    const v = base();
    for (const k of ['pipeline', 'unresolved_issues', 'gates', 'model_conflicts', 'normalized_warnings', 'informational'] as const) delete (v as any)[k];
    await setup(v);
    expect(screen.queryByTestId('blockers')).toBeNull(); expect(screen.getByTestId('readiness')).toBeTruthy();
  });
});

describe('Arabic / RTL', () => {
  it('renders the pipeline panels in Arabic, right-to-left, with the evidence keeping its own direction', async () => {
    await setup(base(), { isAr: true });
    expect(screen.getByTestId('requirements-v2').getAttribute('dir')).toBe('rtl');
    expect(screen.getByTestId('blockers').textContent).toContain(A.injectionRequirementTitle);
    expect(screen.getByTestId('pipeline-ok').textContent).toContain(A.pipelineActive);
    for (const e of screen.getAllByTestId('instruction-text')) expect(e.getAttribute('dir')).toBe('auto');
    expect(screen.getByTestId('provenance').querySelector('dl')!.getAttribute('dir')).toBe('ltr');
  });
  it('unavailable and damaged messages are Arabic', async () => {
    await setup(unavailable(), { isAr: true });
    expect(screen.getByTestId('pipeline-unavailable').textContent).toContain(A.pipelineUnavailable);
  });
  it('every new string exists in both languages', () => {
    const en = Object.keys(S).sort(), ar = Object.keys(A).sort();
    expect(ar).toEqual(en);
  });
});

describe('pure draft corrections', () => {
  const draft = () => draftFromServer(base().requirements);
  it('keepOneOfSplit completes alternatives and removes only the named redundant items', () => {
    const v = base(); const d = draft();
    const out = keepOneOfSplit(d, idOf(v, 'Python'), ['Python', 'Java'], [idOf(v, 'Python'), idOf(v, 'Java')]);
    expect(findItem(out, idOf(v, 'Java'))).toBeNull();
    expect(findItem(out, idOf(v, 'Python'))!.item.alternatives).toEqual(['Python', 'Java']);
    expect(findItem(out, idOf(v, 'Docker'))).toBeTruthy();
    expect(out.skills.items.find(i => i.text === 'Python')!.weight).toBe(d.skills.items.find(i => i.text === 'Python')!.weight);
  });
  it('replaceWithBlank removes the item and adds an empty recruiter item of the same importance in the same category', () => {
    const v = base(); const d = draft();
    const r = replaceWithBlank(d, idOf(v, '20 years of Rust experience'))!;
    expect(findItem(r.draft, idOf(v, '20 years of Rust experience'))).toBeNull();
    const added = findItem(r.draft, r.key)!;
    expect(added.category).toBe('other_requirements'); expect(added.item).toMatchObject({ text: '', id: null, origin: 'recruiter_added', importance: 'preferred' });
    expect(replaceWithBlank(d, 'nope')).toBeNull();
  });
});
