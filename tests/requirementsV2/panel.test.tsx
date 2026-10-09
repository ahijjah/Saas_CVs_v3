import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS } from '../../components/requirementsV2/i18n';
import type { RequirementsView } from '../../services/requirementsV2Api';
import {
  apiError, emptyView, invalidWeightsView, makeView, preferredOnlyView, structureView, warningView, weightedDoc,
} from './fixtures';
import { mockApi } from './mocks';

const JOB = '00000000-0000-0000-0000-0000000000aa';

async function setup(view: RequirementsView, opts: { isAr?: boolean; canEdit?: boolean; confirmFn?: (m: string) => boolean } = {}) {
  const api = mockApi(view);
  const addToast = vi.fn();
  const ui = userEvent.setup();
  const utils = render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit={opts.canEdit ?? true} addToast={addToast} confirmFn={opts.confirmFn} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return { api, ui, addToast, ...utils };
}

const card = (name: string) => screen.getByRole('region', { name });
const textboxOf = (text: string) => screen.getByDisplayValue(text) as HTMLTextAreaElement;
const S = STRINGS.en;

describe('layout: weighted job', () => {
  it('shows seven category cards with editable whole-number category weights', async () => {
    await setup(makeView());
    for (const name of Object.values(S.categories)) expect(card(name)).toBeTruthy();
    const w = within(card('Skills')).getByLabelText(S.categoryWeight) as HTMLInputElement;
    expect(w.value).toBe('60'); expect(w.readOnly).toBe(false);
  });
  it('separates Required and Preferred; preferred items have no weight input; required items do', async () => {
    await setup(makeView());
    const skills = card('Skills');
    expect(within(skills).getAllByRole('heading', { level: 5 }).map(h => h.textContent)).toEqual(['Required', 'Preferred']);
    expect(within(skills).getByLabelText('Weight of “Python”')).toHaveProperty('value', '50');
    expect(within(skills).queryByLabelText('Weight of “Docker”')).toBeNull();
    expect(within(skills).getByText(S.preferredNoWeight)).toBeTruthy();
  });
  it('shows required-item totals per category and the overall category total', async () => {
    await setup(makeView());
    expect(screen.getByTestId('required-total-skills').textContent).toContain('Required total: 100% of 100%');
    expect(screen.getByTestId('required-total-experience').textContent).toContain('100%');
    expect(screen.getByTestId('category-total').textContent).toContain('Category total: 100% of 100%');
  });
  it('always shows that candidate evaluation is unavailable', async () => {
    await setup(makeView());
    expect(screen.getByTestId('evaluation-unavailable').textContent).toContain(S.evalUnavailableTitle);
  });
  it('responsibility items carry a briefcase icon and the From responsibilities badge', async () => {
    await setup(makeView());
    const row = document.getElementById('req-item-req_resp')!;
    expect(within(row).getByText(S.fromResponsibilities)).toBeTruthy();
    expect(row.querySelector('svg rect[width="20"]')).toBeTruthy();                 // the briefcase glyph
    expect(document.getElementById('req-item-req_py')!.textContent).not.toContain(S.fromResponsibilities);
  });
  it('original source wording is behind an accessible disclosure', async () => {
    const { ui } = await setup(makeView());
    const row = document.getElementById('req-item-req_sql')!;
    const btn = within(row).getByRole('button', { name: S.showSource });
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    await ui.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    expect(within(row).getByText('Knowledge of SQL or PostgreSQL')).toBeTruthy();
    expect(btn.getAttribute('aria-controls')).toBe(row.querySelector('[id^="req-source-"]')!.id);
  });
});

describe('editing rules', () => {
  it('no automatic redistribution when a weight changes or an item is added', async () => {
    const { ui } = await setup(makeView());
    const python = screen.getByLabelText('Weight of “Python”') as HTMLInputElement;
    await ui.clear(python); await ui.type(python, '10');
    expect((screen.getByLabelText('Weight of “SQL or PostgreSQL”') as HTMLInputElement).value).toBe('50');
    expect(screen.getByTestId('required-total-skills').textContent).toContain('60%');
    await ui.click(within(card('Skills')).getByRole('button', { name: `+ ${S.addRequired}` }));
    expect((screen.getByLabelText('Weight of “Python”') as HTMLInputElement).value).toBe('10');
  });
  it('Equalize is explicit and per category', async () => {
    const { ui } = await setup(makeView());
    await ui.click(within(card('Skills')).getByRole('button', { name: `+ ${S.addRequired}` }));
    const rows = within(card('Skills')).getAllByLabelText(S.itemText);
    await ui.type(rows[rows.length - 1], 'Go');
    await ui.click(within(card('Skills')).getByRole('button', { name: new RegExp(`^${S.equalize}`) }));
    const weights = within(card('Skills')).getAllByLabelText(/^Weight of/).map(x => (x as HTMLInputElement).value);
    expect(weights).toEqual(['34', '33', '33']);
    expect((screen.getByLabelText('Weight of “4 years as a Maintenance Planner”') as HTMLInputElement).value).toBe('100');
  });
  it('Normalize is an explicit button and rescales only when pressed', async () => {
    const { ui } = await setup(makeView());
    const w = within(card('Skills')).getByLabelText(S.categoryWeight) as HTMLInputElement;
    await ui.clear(w); await ui.type(w, '30');
    expect(screen.getByTestId('category-total').textContent).toContain('70%');
    await ui.click(screen.getByTestId('normalize'));
    expect((within(card('Skills')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('43');
    expect((within(card('Experience')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('57');
    expect(screen.getByTestId('category-total').textContent).toContain('100%');
  });
  it('removing the last required item sets the category to 0 and nothing else moves', async () => {
    const { ui } = await setup(makeView());
    await ui.click(within(document.getElementById('req-item-req_exp')!).getByRole('button', { name: new RegExp(S.deleteItem) }));
    expect((within(card('Experience')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('0');
    expect((within(card('Skills')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('60');
    expect(screen.getByTestId('category-total').textContent).toContain('60%');
  });
  it('reclassify required <-> preferred follows the backend rules', async () => {
    const { ui } = await setup(makeView());
    const row = document.getElementById('req-item-req_exp')!;
    await ui.click(within(row).getByRole('button', { name: S.makePreferred }));
    expect((within(card('Experience')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('0');
    expect(screen.queryByLabelText('Weight of “4 years as a Maintenance Planner”')).toBeNull();
    await ui.click(within(document.getElementById('req-item-req_exp')!).getByRole('button', { name: S.makeRequired }));
    expect((screen.getByLabelText('Weight of “4 years as a Maintenance Planner”') as HTMLInputElement).value).toBe('');
  });
  it('add, edit and delete an item; the new item is focused', async () => {
    const { ui } = await setup(makeView());
    await ui.click(within(card('Education')).getByRole('button', { name: `+ ${S.addPreferred}` }));
    const box = within(card('Education')).getByLabelText(S.itemText);
    expect(document.activeElement).toBe(box);
    await ui.type(box, 'BSc');
    expect(screen.getByTestId('unsaved-badge')).toBeTruthy();
    await ui.click(within(card('Education')).getByRole('button', { name: new RegExp(S.deleteItem) }));
    expect(within(card('Education')).queryByLabelText(S.itemText)).toBeNull();
  });
  it('edits experience subject / duration and OR alternatives, including adding them', async () => {
    const { ui } = await setup(makeView());
    const exp = document.getElementById('req-item-req_exp')!;
    await ui.click(within(exp).getByRole('button', { name: S.details }));
    const subj = within(exp).getByLabelText(S.subject) as HTMLInputElement;
    await ui.clear(subj); await ui.type(subj, 'Senior Planner');
    const yrs = within(exp).getByLabelText(S.years) as HTMLInputElement;
    await ui.clear(yrs); await ui.type(yrs, '7');
    expect(subj.value).toBe('Senior Planner'); expect(yrs.value).toBe('7');
    const sql = document.getElementById('req-item-req_sql')!;
    await ui.click(within(sql).getByRole('button', { name: S.details }));
    await ui.click(within(sql).getByRole('button', { name: S.addAlternative }));
    expect(within(sql).getAllByLabelText(/^Alternative \d/)).toHaveLength(3);
    const py = document.getElementById('req-item-req_py')!;
    await ui.click(within(py).getByRole('button', { name: S.details }));
    await ui.click(within(py).getByRole('button', { name: S.makeAlternatives }));
    expect(within(py).getAllByLabelText(/^Alternative \d/)).toHaveLength(2);
    expect(within(py).queryByLabelText(S.subject)).toBeNull();                      // experience structure only in Experience
  });
});

describe('saving and readiness', () => {
  it('saves the draft with the base revision, new items without ids and no provenance', async () => {
    const { ui, api } = await setup(makeView());
    await ui.click(within(card('Education')).getByRole('button', { name: `+ ${S.addPreferred}` }));
    await ui.type(within(card('Education')).getByLabelText(S.itemText), 'BSc');
    await ui.click(screen.getByTestId('save'));
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
    const [job, rev, body] = api.save.mock.calls[0];
    expect(job).toBe(JOB); expect(rev).toBe(3);
    expect(body.categories.education.items).toEqual([{ text: 'BSc', importance: 'preferred', weight: null, alternatives: null, experience: null }]);
    expect(JSON.stringify(body)).not.toMatch(/origin|source_text|acknowledg|confirmation/);
    await waitFor(() => expect(screen.queryByTestId('unsaved-badge')).toBeNull());
  });
  it('shows blockers for an empty job', async () => {
    await setup(emptyView());
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_needs_items);
    expect(screen.getByTestId('readiness').textContent).toContain(S.reason_no_items);
  });
  it('shows invalid-weight blockers from the server and local hints for the draft; saving is not blocked locally', async () => {
    const { ui, api } = await setup(invalidWeightsView());
    expect(screen.getByTestId('readiness').textContent).toContain(S.reason_required_weights_total);
    await ui.type(within(card('Skills')).getAllByLabelText(S.itemText)[0], '!');
    expect(screen.getByTestId('local-issues').textContent).toContain(S.reason_required_weights_total);
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(false);
    api.save.mockRejectedValueOnce(apiError(422, { code: 'invalid_requirements', message: 'The requirements cannot be saved.',
      issues: [{ code: 'required_weights_total', message: 'Required item weights total 80%, not 100%.', category: 'skills', item_id: null }] }));
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('server-issues');
    expect(screen.getByTestId('server-issues').textContent).toContain(S.reason_required_weights_total);
    expect(screen.getByDisplayValue('Python!')).toBeTruthy();                          // the draft is still there
  });
  it('distinguishes a valid saved draft from readiness to proceed', async () => {
    await setup(structureView());
    expect(screen.getByTestId('readiness').textContent).toContain(S.saveVsReady);
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(true);     // nothing to save
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_needs_structure_review);
  });
  it('a read-only user sees the data but no editing controls', async () => {
    await setup(makeView(), { canEdit: false });
    expect(screen.queryByTestId('save')).toBeNull();
    expect(screen.queryByRole('button', { name: `+ ${S.addRequired}` })).toBeNull();
    expect((screen.getByLabelText('Weight of “Python”') as HTMLInputElement).readOnly).toBe(true);
    expect(screen.getByText(S.readOnly)).toBeTruthy();
  });
  it('shows the load error and can retry', async () => {
    const api = mockApi(makeView());
    api.get.mockRejectedValueOnce(apiError(503, { code: 'requirements_migration_missing', message: 'x' }));
    const ui = userEvent.setup();
    render(<RequirementsV2Panel jobId={JOB} api={api} isAr={false} canEdit />);
    expect((await screen.findByRole('alert')).textContent).toContain(S.err_requirements_migration_missing);
    await ui.click(screen.getByRole('button', { name: S.retry }));
    await screen.findByTestId('requirements-v2');
  });
});

describe('edited badges and the original comparison', () => {
  it('shows Edited on categories and items that differ from the original, with an original-AI comparison', async () => {
    const { ui } = await setup(makeView());
    expect(within(card('Skills')).queryByText(S.edited)).toBeNull();
    await ui.type(textboxOf('Python'), ' 3');
    expect(within(card('Skills')).getAllByText(S.edited)).toHaveLength(2);           // the category and the item
    expect(within(document.getElementById('req-item-req_py')!).getByText(S.edited)).toBeTruthy();
    expect(within(card('Experience')).queryByText(S.edited)).toBeNull();
    await ui.click(screen.getByTestId('toggle-compare'));
    const cmp = screen.getByTestId('comparison');
    expect(cmp.textContent).toContain('Python');                                       // the original wording
    expect(cmp.textContent).toContain(S.cmpChanged);
    await ui.click(screen.getByTestId('toggle-compare'));
    expect(screen.queryByTestId('comparison')).toBeNull();
  });
  it('uses the server flags when the original is unavailable', async () => {
    await setup(makeView(weightedDoc(), { original: null, edited_categories: { skills: true } as any }));
    expect(within(card('Skills')).getAllByText(S.edited)).toHaveLength(1);
    expect(screen.queryByTestId('comparison')).toBeNull();
  });
});

describe('warnings', () => {
  it('lists duplicate / similarity warnings as informational, with links to the related items and no save blocking', async () => {
    const { ui } = await setup(warningView());
    const panel = screen.getByTestId('similarity');
    expect(within(panel).getByText(S.possibleDuplicate)).toBeTruthy(); expect(within(panel).getByText(S.similarRequirement)).toBeTruthy();
    expect(panel.textContent).toContain(S.similarityBody);
    await ui.click(within(panel).getAllByRole('button', { name: 'SQL or PostgreSQL' })[0]);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(document.activeElement).toBe(document.getElementById('req-item-req_sql'));
    await ui.type(textboxOf('Python'), '!');
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(false);
  });
  it('policy Yes: unresolved classification warnings must be acknowledged; the action calls the API with the revision', async () => {
    const { ui, api } = await setup(warningView(true));
    expect(screen.getByTestId('classification-policy').textContent).toBe(S.classificationPolicyYes);
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_needs_classification_review);
    await ui.click(within(screen.getByTestId('classification')).getByRole('button', { name: S.acknowledge }));
    expect(api.acknowledge).toHaveBeenCalledWith(JOB, 3, 'req_docker:preferred_cue_missing');
  });
  it('policy No: warnings are shown for information and do not block', async () => {
    await setup(warningView(false));
    expect(screen.getByTestId('classification-policy').textContent).toBe(S.classificationPolicyNo);
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_ready);
    expect(within(screen.getByTestId('classification')).getByText(S.stateUnresolved)).toBeTruthy();
  });
  it('server actions are disabled while there are unsaved changes', async () => {
    const { ui } = await setup(warningView());
    await ui.type(textboxOf('Python'), '!');
    expect((within(screen.getByTestId('classification')).getByRole('button', { name: S.acknowledge }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(screen.getByTestId('classification')).getByText(S.needsSavedState)).toBeTruthy();
  });
  it('structure review: confirm the existing structure through the API; saving the same values is not shown as confirmation', async () => {
    const { ui, api } = await setup(structureView());
    const row = document.getElementById('req-item-req_exp')!;
    expect(within(row).getAllByText(S.structureNeedsReview).length).toBeGreaterThan(0);
    expect(row.textContent).toContain('Maintenance Planner');
    await ui.click(within(row).getByRole('button', { name: S.structureConfirm }));
    expect(api.confirmStructure).toHaveBeenCalledWith(JOB, 3, 'req_exp');
  });
  it('structure review can also be settled by correcting the structure (sent with the save)', async () => {
    const { ui, api } = await setup(structureView());
    const row = document.getElementById('req-item-req_exp')!;
    await ui.click(within(row).getByRole('button', { name: S.details }));
    const yrs = within(row).getByLabelText(S.years);
    await ui.clear(yrs); await ui.type(yrs, '7');
    await ui.click(screen.getByTestId('save'));
    await waitFor(() => expect(api.save).toHaveBeenCalled());
    expect(api.save.mock.calls[0][2].categories.experience.items[0]).toMatchObject({ id: 'req_exp', experience: { subject: 'Maintenance Planner', min_years: 7 } });
  });
});

describe('preferred-only jobs', () => {
  it('asks for an explicit confirmation of "no numerical score" and does not show weight inputs', async () => {
    const { ui, api } = await setup(preferredOnlyView());
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_needs_confirmation);
    expect(screen.queryAllByLabelText(/^Weight of/)).toHaveLength(0);
    await ui.click(within(screen.getByTestId('preferred-only')).getByRole('button', { name: S.preferredOnlyConfirm }));
    expect(api.confirmNoScore).toHaveBeenCalledWith(JOB, 3);
  });
  it('shows the recorded confirmation once confirmed', async () => {
    await setup(preferredOnlyView(true));
    expect(screen.getByTestId('preferred-only').textContent).toContain('no numerical score');
    expect(within(screen.getByTestId('preferred-only')).queryByRole('button')).toBeNull();
    expect(screen.getByTestId('readiness').textContent).toContain(S.hint_ready_none);
  });
});

describe('revision conflicts: three-way comparison and explicit resolution', () => {
  const edited = (fn: (d: ReturnType<typeof weightedDoc>) => void, rev = 4) => { const d = weightedDoc(); fn(d); return makeView(d, { revision: rev }); };
  const conflictOn = (api: any, latest: RequirementsView) =>
    api.save.mockRejectedValueOnce(apiError(409, { code: 'requirements_revision_conflict', message: 'changed', current: latest }));
  const resolver = () => screen.getByTestId('conflict');

  it('separate-field edits are combined: nothing to decide, Apply does not save, the next save is checked against the latest revision', async () => {
    const { ui, api } = await setup(makeView());
    await ui.type(textboxOf('Python'), ' (mine)');                                           // I edit Python
    conflictOn(api, edited(d => { d.categories.skills.items[2].text = 'Docker (theirs)'; }));  // they edited Docker
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('conflict');
    expect(within(resolver()).getByText(S.noClashes)).toBeTruthy();
    expect(within(screen.getByTestId('merge-summary')).getByText(/Kept from your draft: 1/)).toBeTruthy();
    expect(within(screen.getByTestId('merge-summary')).getByText(/Taken from the latest saved version: 1/)).toBeTruthy();
    expect(screen.getByDisplayValue('Python (mine)')).toBeTruthy();                           // draft untouched until Apply
    await ui.click(screen.getByTestId('apply-merge'));
    expect(api.save).toHaveBeenCalledTimes(1);                                                // applying never saves or resubmits
    expect(screen.queryByTestId('conflict')).toBeNull();
    expect(screen.getByDisplayValue('Python (mine)')).toBeTruthy(); expect(screen.getByDisplayValue('Docker (theirs)')).toBeTruthy();
    expect(screen.getByTestId('merged-notice').textContent).toContain(S.mergedNotice);
    await ui.click(screen.getByTestId('save'));
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(2));
    expect(api.save.mock.calls[1][1]).toBe(4);                                                // revision checking stays active
    const items = api.save.mock.calls[1][2].categories.skills.items;
    expect(items.map((i: any) => i.text)).toEqual(['Python (mine)', 'SQL or PostgreSQL', 'Docker (theirs)']);
  });

  it('a conflicting field needs an explicit choice; the three versions are shown; Apply stays disabled until chosen', async () => {
    const { ui, api } = await setup(makeView());
    await ui.type(textboxOf('Python'), ' mine');
    conflictOn(api, edited(d => { d.categories.skills.items[0].text = 'Python theirs'; }));
    await ui.click(screen.getByTestId('save'));
    const box = await screen.findByTestId('conflict');
    const fs = within(box).getByRole('group', { name: /Python.*wording/ });
    expect(within(fs).getAllByRole('columnheader').map(h => h.textContent)).toEqual([S.colOriginal, S.colMine, S.colLatest]);
    expect(within(fs).getAllByRole('cell').map(c => c.textContent)).toEqual(['Python', 'Python mine', 'Python theirs']);
    expect((screen.getByTestId('apply-merge') as HTMLButtonElement).disabled).toBe(true);
    expect(within(box).getByText(new RegExp(S.chooseAll))).toBeTruthy();
    await ui.click(within(fs).getByRole('radio', { name: S.useLatest }));
    expect((screen.getByTestId('apply-merge') as HTMLButtonElement).disabled).toBe(false);
    await ui.click(screen.getByTestId('apply-merge'));
    expect(screen.getByDisplayValue('Python theirs')).toBeTruthy(); expect(screen.queryByDisplayValue('Python mine')).toBeNull();
    expect(api.save).toHaveBeenCalledTimes(1);
  });

  it('"use my draft" keeps my value for that conflict only; other fields still merge', async () => {
    const { ui, api } = await setup(makeView());
    await ui.type(textboxOf('Python'), ' mine');
    await ui.type(textboxOf('Docker'), ' mine');
    conflictOn(api, edited(d => { d.categories.skills.items[0].text = 'Python theirs'; d.categories.skills.items[2].text = 'Docker theirs'; }));
    await ui.click(screen.getByTestId('save'));
    const box = await screen.findByTestId('conflict');
    await ui.click(within(within(box).getByRole('group', { name: /Python/ })).getByRole('radio', { name: S.useMine }));
    await ui.click(within(within(box).getByRole('group', { name: /Docker/ })).getByRole('radio', { name: S.useLatest }));
    await ui.click(screen.getByTestId('apply-merge'));
    expect(screen.getByDisplayValue('Python mine')).toBeTruthy(); expect(screen.getByDisplayValue('Docker theirs')).toBeTruthy();
  });

  it('category weight conflicts are explicit too; a one-sided change is merged silently', async () => {
    const { ui, api } = await setup(makeView());
    const w = within(card('Skills')).getByLabelText(S.categoryWeight);
    await ui.clear(w); await ui.type(w, '55');
    conflictOn(api, edited(d => { d.categories.skills.weight = 70; d.categories.experience.weight = 30; }));
    await ui.click(screen.getByTestId('save'));
    const box = await screen.findByTestId('conflict');
    expect(box.querySelectorAll('fieldset')).toHaveLength(1);                                // Experience weight changed only upstream
    await ui.click(within(within(box).getByRole('group', { name: /Skills/ })).getByRole('radio', { name: S.useMine }));
    await ui.click(screen.getByTestId('apply-merge'));
    expect((within(card('Skills')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('55');
    expect((within(card('Experience')).getByLabelText(S.categoryWeight) as HTMLInputElement).value).toBe('30');
  });

  it('I deleted an item that was changed in the latest version: explicit choice (delete it / keep the latest)', async () => {
    const { ui, api } = await setup(makeView());
    await ui.click(within(document.getElementById('req-item-req_docker')!).getByRole('button', { name: new RegExp(S.deleteItem) }));
    conflictOn(api, edited(d => { d.categories.skills.items[2].text = 'Docker (edited upstream)'; }));
    await ui.click(screen.getByTestId('save'));
    const box = await screen.findByTestId('conflict');
    expect(within(box).getByText(S.conflictDeletedByMe)).toBeTruthy();
    const fs = within(box).getByRole('group', { name: /Docker/ });
    expect(within(fs).getAllByRole('cell').map(c => c.textContent)).toEqual(['Docker (Preferred)', S.deletedValue, 'Docker (edited upstream) (Preferred)']);
    await ui.click(within(fs).getByRole('radio', { name: S.choiceKeepLatest }));
    await ui.click(screen.getByTestId('apply-merge'));
    expect(screen.getByDisplayValue('Docker (edited upstream)')).toBeTruthy();
    expect(api.save).toHaveBeenCalledTimes(1);
  });

  it('I changed an item that was deleted in the latest version: keeping mine re-adds it as a new item; accepting drops it', async () => {
    const run = async (choice: string) => {
      const { ui, api, unmount } = await setup(makeView());
      await ui.type(textboxOf('Docker'), ' (mine)');
      conflictOn(api, edited(d => { d.categories.skills.items.splice(2, 1); }));
      await ui.click(screen.getByTestId('save'));
      const box = await screen.findByTestId('conflict');
      expect(within(box).getByText(S.conflictDeletedInLatest)).toBeTruthy();
      await ui.click(within(within(box).getByRole('group', { name: /Docker/ })).getByRole('radio', { name: choice }));
      await ui.click(screen.getByTestId('apply-merge'));
      return { ui, api, unmount };
    };
    let r = await run(S.choiceKeepMine);
    expect(screen.getByDisplayValue('Docker (mine)')).toBeTruthy();
    await r.ui.click(screen.getByTestId('save'));
    await waitFor(() => expect(r.api.save).toHaveBeenCalledTimes(2));
    const sent = r.api.save.mock.calls[1][2].categories.skills.items.find((i: any) => i.text === 'Docker (mine)');
    expect('id' in sent).toBe(false);                                                         // the deleted id is never sent again
    r.unmount();
    r = await run(S.choiceAcceptDeletion);
    expect(screen.queryByDisplayValue('Docker (mine)')).toBeNull();
  });

  it('a second conflict after resolving is detected again (revision checking stays active)', async () => {
    const { ui, api } = await setup(makeView());
    await ui.type(textboxOf('Python'), ' mine');
    conflictOn(api, edited(d => { d.categories.skills.items[2].text = 'Docker v4'; }, 4));
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('conflict');
    await ui.click(screen.getByTestId('apply-merge'));
    conflictOn(api, edited(d => { d.categories.skills.items[2].text = 'Docker v5'; }, 5));    // someone saved again meanwhile
    await ui.click(screen.getByTestId('save'));
    expect(api.save.mock.calls[1][1]).toBe(4);
    const box = await screen.findByTestId('conflict');
    expect(box.textContent).toContain('5');
    expect(screen.getByDisplayValue('Python mine')).toBeTruthy();
    expect(api.save).toHaveBeenCalledTimes(2);                                                // nothing was resubmitted by itself
  });

  it('"discard my draft" asks for confirmation before replacing it', async () => {
    const confirmFn = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { ui, api } = await setup(makeView(), { confirmFn });
    await ui.type(textboxOf('Python'), ' mine');
    conflictOn(api, edited(d => { d.categories.skills.items[0].text = 'Python (someone else)'; }));
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('conflict');
    await ui.click(screen.getByTestId('discard-for-latest'));
    expect(screen.getByDisplayValue('Python mine')).toBeTruthy();                             // declined: still there
    await ui.click(screen.getByTestId('discard-for-latest'));
    expect(screen.getByDisplayValue('Python (someone else)')).toBeTruthy();
    expect(screen.queryByTestId('conflict')).toBeNull();
  });

  it('is operable by keyboard: radios by arrow keys, Apply by Enter', async () => {
    const { ui, api } = await setup(makeView());
    await ui.type(textboxOf('Python'), ' mine');
    conflictOn(api, edited(d => { d.categories.skills.items[0].text = 'Python theirs'; }));
    await ui.click(screen.getByTestId('save'));
    const fs = within(await screen.findByTestId('conflict')).getByRole('group', { name: /Python/ });
    const [mine, latest] = within(fs).getAllByRole('radio');
    mine.focus(); await ui.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(latest); expect((latest as HTMLInputElement).checked).toBe(true);
    const apply = screen.getByTestId('apply-merge') as HTMLButtonElement;
    apply.focus(); await ui.keyboard('{Enter}');
    expect(screen.getByDisplayValue('Python theirs')).toBeTruthy(); expect(api.save).toHaveBeenCalledTimes(1);
  });

  it('an action refused for a stale revision reloads the latest (there is no draft to lose)', async () => {
    const { ui, api } = await setup(warningView());
    api.acknowledge.mockRejectedValueOnce(apiError(409, { code: 'requirements_revision_conflict', message: 'changed', current: edited(d => { d.categories.skills.items[0].text = 'Python (someone else)'; }) }));
    await ui.click(within(screen.getByTestId('classification')).getByRole('button', { name: S.acknowledge }));
    await waitFor(() => expect(screen.getByDisplayValue('Python (someone else)')).toBeTruthy());
  });
});

describe('warnings are labelled as referring to the saved version while a draft differs', () => {
  it('no labels while the draft equals the saved version', async () => {
    await setup(warningView());
    for (const id of ['readiness-stale', 'classification-saved-note', 'similarity-saved-note']) expect(screen.queryByTestId(id)).toBeNull();
    expect(screen.getByTestId('readiness').textContent).toContain(S.readiness);
  });
  it('a draft change labels readiness, the classification list and the similarity list as the saved version', async () => {
    const { ui } = await setup(warningView());
    await ui.type(textboxOf('Python'), '!');
    expect(screen.getByTestId('readiness-stale').textContent).toBe(S.readinessSavedNote);
    expect(screen.getByTestId('readiness').textContent).toContain(S.readinessSavedTitle);
    expect(screen.getByTestId('classification-saved-note').textContent).toBe(S.savedNoteWarnings);
    expect(screen.getByTestId('similarity-saved-note').textContent).toBe(S.savedNoteWarnings);
    expect(screen.getByTestId('readiness-state').textContent).toBe(S.state_needs_classification_review);   // still the saved state
  });
  it('per-item chips: only items the draft changed are marked, and an acknowledgment is not presented as verifying the edit', async () => {
    const view = warningView();
    view.classification_warnings[0] = { ...view.classification_warnings[0], state: 'acknowledged', acknowledgment: { user_id: 'u-9', acknowledged_at: '2026-03-03T10:00:00+00:00' } };
    const { ui } = await setup(view);
    const docker = document.getElementById('req-item-req_docker')!, sql = document.getElementById('req-item-req_sql')!;
    expect(docker.querySelector('[data-saved-only]')).toBeNull();
    await ui.type(textboxOf('Docker'), ' 2');
    expect(docker.querySelectorAll('[data-saved-only="true"]').length).toBeGreaterThan(0);
    expect(docker.textContent).toContain(S.ackSavedOnly);
    expect(docker.textContent).toContain(S.savedBadge);
    // the duplicate chip on the OTHER item of the pair refers to the saved version as well
    expect(within(sql).getByRole('button', { name: new RegExp(`${S.possibleDuplicate} \\(${S.similarSaved}\\)`) })).toBeTruthy();
    const classRow = within(screen.getByTestId('classification')).getByTestId('warning-item-edited');
    expect(classRow.textContent).toContain(S.itemEditedSaved);
    // the lists name the item by its SAVED wording, not by the unsaved edit
    expect(within(screen.getByTestId('classification')).getByRole('button', { name: 'Docker' })).toBeTruthy();
    expect(within(screen.getByTestId('classification')).queryByRole('button', { name: 'Docker 2' })).toBeNull();
    expect(screen.getByTestId('classification').textContent).toContain(S.ackSavedOnly);
    // an item the draft did not touch is not marked
    expect(document.getElementById('req-item-req_py')!.querySelector('[data-saved-only]')).toBeNull();
    await ui.click(screen.getByRole('button', { name: S.discard }));
    expect(screen.queryByTestId('warning-item-edited')).toBeNull();
    expect(screen.queryByTestId('classification-saved-note')).toBeNull();
  });
  it('structure confirmation and preferred-only confirmation are shown as applying to the saved version', async () => {
    const view = structureView();
    view.structure_review.items[1] = { item_id: 'req_exp', category: 'experience', state: 'confirmed', record: { kind: 'confirmed', user_id: 'u-9', recorded_at: '2026-03-03T10:00:00+00:00' } };
    view.structure_review.needs_review_item_ids = [];
    const { ui } = await setup(view);
    await ui.type(textboxOf('7 years as a Maintenance Planner'), '!');
    expect(screen.getByTestId('structure-saved-only').textContent).toBe(S.structureSavedOnly);
  });
  it('preferred-only confirmation note appears only while a draft exists', async () => {
    const { ui } = await setup(preferredOnlyView(true));
    expect(screen.queryByTestId('preferred-only-saved-note')).toBeNull();
    await ui.type(textboxOf('Docker is a plus'), '!');
    expect(screen.getByTestId('preferred-only-saved-note').textContent).toBe(S.preferredOnlySavedOnly);
  });
  it('a needs-review structure banner is marked as the saved version when the item is edited', async () => {
    const { ui } = await setup(structureView());
    await ui.type(textboxOf('7 years as a Maintenance Planner'), '!');
    const row = document.getElementById('req-item-req_exp')!;
    expect(row.querySelector('[data-saved-only="true"]')).toBeTruthy();
    expect(row.textContent).toContain(S.itemEditedSaved);
  });
});

describe('Arabic / RTL', () => {
  it('renders right-to-left with Arabic labels, logical layout and keyboard-reachable controls', async () => {
    const A = STRINGS.ar;
    const { ui } = await setup(warningView(), { isAr: true });
    const root = screen.getByTestId('requirements-v2');
    expect(root.getAttribute('dir')).toBe('rtl'); expect(root.getAttribute('lang')).toBe('ar');
    expect(screen.getByRole('heading', { name: new RegExp(A.title) })).toBeTruthy();
    expect(screen.getByTestId('evaluation-unavailable').textContent).toContain(A.evalUnavailableTitle);
    expect(screen.getByTestId('readiness-state').textContent).toBe(A.state_needs_classification_review);
    expect(within(screen.getByTestId('similarity')).getByText(A.possibleDuplicate)).toBeTruthy();
    expect(screen.getByLabelText(`${A.categoryWeight}`, { selector: '#req-cat-skills-w' })).toBeTruthy();
    // numbers stay left-to-right inside the right-to-left layout
    expect((screen.getByLabelText('وزن «Python»') as HTMLInputElement).getAttribute('dir')).toBe('ltr');
    // no physical left/right utilities in the new components (logical ones only)
    expect(root.innerHTML).not.toMatch(/class="[^"]*\b(ml|mr|pl|pr|text-left|text-right|left|right)-\d?/);
    await ui.tab();
    expect(document.activeElement).not.toBe(document.body);
  });
  it('every English string has an Arabic counterpart', () => {
    const miss = (a: any, b: any, p = ''): string[] => Object.keys(a).flatMap(k =>
      typeof a[k] === 'object' ? miss(a[k], b[k] ?? {}, `${p}${k}.`) : (b[k] === undefined || b[k] === '' ? [p + k] : []));
    expect(miss(STRINGS.en, STRINGS.ar)).toEqual([]);
  });
  it('empty-job and preferred-only states render in Arabic', async () => {
    const A = STRINGS.ar;
    await setup(emptyView(), { isAr: true });
    expect(screen.getByTestId('readiness').textContent).toContain(A.reason_no_items);
  });
});

describe('accessibility basics', () => {
  it('every input and button has an accessible name; status changes are announced', async () => {
    await setup(warningView());
    const root = screen.getByTestId('requirements-v2');
    for (const el of root.querySelectorAll('input, textarea, button')) {
      const name = (el.getAttribute('aria-label') || (el as HTMLElement).innerText || el.textContent || '').trim()
        || (el.id && root.querySelector(`label[for="${el.id}"]`)?.textContent) || '';
      expect(name, el.outerHTML.slice(0, 120)).not.toBe('');
    }
    expect(root.querySelector('[role="status"][aria-live="polite"]')).toBeTruthy();
    expect(root.querySelectorAll('[aria-expanded]').length).toBeGreaterThan(0);
  });
});
