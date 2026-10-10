// Regression tests for the weight controls and the validation messages of the requirements-v2 editor, in the review-first layout:
// changes are made in the item editor (Actions → Edit) or the category weight editor, and take effect in the draft only on Apply.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS } from '../../components/requirementsV2/i18n';
import { equalizeWeights } from '../../utils/requirementsV2';
import { apiError, doc, item, makeView } from './fixtures';
import { mockApi } from './mocks';
import { applyEditor, cardOf, categoryWeight, editorWeight, itemWeight, openCategoryEdit, openEditor, requiredTotal, rowOf, setWeight, typeIn } from './review';

const JOB = '00000000-0000-0000-0000-0000000000aa';
const S = STRINGS.en;
const AR = STRINGS.ar;

const twoRequired = () =>
  doc({
    skills: { weight: 60, items: [item('req_py', 'Python', 'required', 50), item('req_sql', 'SQL', 'required', 50), item('req_docker', 'Docker', 'preferred', null)] },
    experience: { weight: 40, items: [item('req_exp', 'Years', 'required', 100)] },
  });

async function renderPanel(api: ReturnType<typeof mockApi>, opts: { isAr?: boolean } = {}) {
  const ui = userEvent.setup();
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit addToast={vi.fn()} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return ui;
}

describe('the reported defect', () => {
  it('60 and 50 show 110% at once, with the difference, in the Required total of the category', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    expect(requiredTotal('skills')).toContain('100%');
    await setWeight(ui, 'Python', '60');
    expect(itemWeight('Python')).toBe('60');
    expect(requiredTotal('skills')).toContain('110%');
    expect(requiredTotal('skills')).toContain('+10');
  });

  it('the wrong total is shown prominently and Equalize restores 50/50 in the draft, and does not save', async () => {
    const api = mockApi(makeView(twoRequired()));
    const ui = await renderPanel(api);
    await setWeight(ui, 'Python', '60');
    expect(screen.getByTestId(`required-total-skills`).getAttribute('role')).toBe('status');
    await ui.click(within(cardOf('skills')).getByRole('button', { name: new RegExp(`^${S.equalize}`) }));
    expect(itemWeight('Python')).toBe('50');
    expect(itemWeight('SQL')).toBe('50');
    expect(requiredTotal('skills')).toContain('100%');
    expect(api.save).not.toHaveBeenCalled();
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(true);    // back to the saved state: nothing to save
  });

  it('Equalize is disabled when the Required weights already are the deterministic equal split', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    const equalize = () => within(cardOf('skills')).getByRole('button', { name: new RegExp(`^${S.equalize}`) }) as HTMLButtonElement;
    expect(equalize().disabled).toBe(true);
    expect(equalize().title).toBe(S.equalizeAlready);
    await setWeight(ui, 'Python', '60');
    expect(equalize().disabled).toBe(false);
  });

  it('three items split 34/33/33 is the equal split; 33/33/34 is not', async () => {
    const three = doc({ skills: { weight: 100, items: [item('a', 'A', 'required', 34), item('b', 'B', 'required', 33), item('c', 'C', 'required', 33)] } });
    expect(equalizeWeights(3)).toEqual([34, 33, 33]);
    await renderPanel(mockApi(makeView(three)));
    expect((within(cardOf('skills')).getByRole('button', { name: new RegExp(`^${S.equalize}`) }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Preferred items are shown without a weight', async () => {
    await renderPanel(mockApi(makeView(twoRequired())));
    expect(itemWeight('Docker')).toBeNull();
    expect(rowOf('Docker').querySelector('[data-testid^="weight-"]')).toBeNull();
  });
});

describe('the editor: review, apply, cancel', () => {
  it('nothing is editable in review: no weight input and no text box until Edit is chosen', async () => {
    await renderPanel(mockApi(makeView(twoRequired())));
    expect(screen.queryByRole('textbox', { name: /^Weight of/ })).toBeNull();
    expect(screen.queryByTestId('item-editor')).toBeNull();
  });

  it('Cancel (and Escape) discards the local values; the draft and Save are unchanged', async () => {
    const api = mockApi(makeView(twoRequired()));
    const ui = await renderPanel(api);
    const editor = await openEditor(ui, 'Python');
    await typeIn(ui, editorWeight(editor), '70');
    await ui.click(within(editor).getByRole('button', { name: S.cancelEdit }));
    expect(screen.queryByTestId('item-editor')).toBeNull();
    expect(itemWeight('Python')).toBe('50');
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(true);
    const again = await openEditor(ui, 'Python');
    await typeIn(ui, editorWeight(again), '70');
    fireEvent.keyDown(again, { key: 'Escape' });
    expect(screen.queryByTestId('item-editor')).toBeNull();
    expect(itemWeight('Python')).toBe('50');
    expect(api.save).not.toHaveBeenCalled();
  });

  it('Apply changes the draft only: nothing is saved until Save is pressed', async () => {
    const api = mockApi(makeView(twoRequired()));
    const ui = await renderPanel(api);
    await setWeight(ui, 'Python', '40');
    expect(itemWeight('Python')).toBe('40');
    expect(api.save).not.toHaveBeenCalled();
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(false);
  });

  it('a typed decimal is not applied: the editor says so, stays open, and the draft keeps its last value', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    const editor = await openEditor(ui, 'SQL');
    await typeIn(ui, editorWeight(editor), '12.5');
    await applyEditor(ui, editor);
    expect(screen.getByTestId('item-editor')).toBeTruthy();                                     // still open
    expect(screen.getByTestId('editor-error').textContent).toContain('“12.5” is not a whole number');
    expect(editorWeight(editor).getAttribute('aria-invalid')).toBe('true');
    await ui.click(within(editor).getByRole('button', { name: S.cancelEdit }));
    expect(itemWeight('SQL')).toBe('50');
  });
});

describe('clear validation messages', () => {
  it('a server refusal names the category, the actual total and the difference, and keeps the draft', async () => {
    const api = mockApi(makeView(twoRequired()));
    api.save.mockRejectedValueOnce(apiError(422, {
      code: 'invalid_requirements', message: 'The requirements cannot be saved.',
      issues: [{ code: 'required_weights_total', message: 'Required item weights total 110%, not 100%.', category: 'skills', item_id: null,
                 params: { total: 110, expected: 100, difference: 10 } }],
    }));
    const ui = await renderPanel(api);
    await setWeight(ui, 'Python', '60');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
    expect(screen.getByTestId('issue-summary').textContent).toContain('Required item weights in Skills total 110%, not 100% (difference +10).');
    expect(itemWeight('Python')).toBe('60');                                                    // the draft is preserved
  });

  it('a server message without its numbers is shown with the server words, not with invented numbers', async () => {
    const api = mockApi(makeView(twoRequired()));
    api.save.mockRejectedValueOnce(apiError(422, { code: 'invalid_requirements', message: 'The requirements cannot be saved.',
      issues: [{ code: 'required_weights_total', message: 'Required item weights total 80%, not 100%.', category: 'skills', item_id: null }] }));
    const ui = await renderPanel(api);
    await setWeight(ui, 'Python', '30');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
    expect(screen.getByTestId('issue-summary').textContent).toContain('Required item weights total 80%, not 100%.');
  });

  it('a Required item with an empty weight names the item and its category', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    const editor = await openEditor(ui, 'Python');
    await typeIn(ui, editorWeight(editor), '');
    await applyEditor(ui, editor);
    expect(screen.getByTestId('issue-summary').textContent).toContain('Python (Skills): a Required item needs a whole-number weight from 1 to 100 (now empty).');
  });

  it('an out-of-range Required weight is named with the value typed', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    await setWeight(ui, 'SQL', '150');
    expect(screen.getByTestId('issue-summary').textContent).toContain('SQL (Skills): a Required item needs a whole-number weight from 1 to 100 (now 150).');
  });

  it('a category weight above 100 and a category without Required items that carries weight are each named', async () => {
    const ui = await renderPanel(mockApi(makeView(doc({
      skills: { weight: 60, items: [item('a', 'Python', 'required', 100)] },
      certifications: { weight: 5, items: [item('p', 'PMP', 'preferred', null)] },
    }))));
    const input = await openCategoryEdit(ui, 'skills');
    await typeIn(ui, input, '120');
    await ui.click(within(cardOf('skills')).getByRole('button', { name: S.applyEdit }));
    const text = screen.getByTestId('issue-summary').textContent ?? '';
    expect(text).toContain('The weight of Skills must be a whole number from 0 to 100.');
    expect(text).toContain('Certifications has no Required items, so its weight must be 0% (now 5%).');
  });

  it('a typed decimal in the category weight stays in its editor with the message, and nothing is applied', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    const input = await openCategoryEdit(ui, 'skills');
    await typeIn(ui, input, '60.5');
    await ui.click(within(cardOf('skills')).getByRole('button', { name: S.applyEdit }));
    expect(within(cardOf('skills')).getByRole('alert').textContent).toContain('is not a whole number');
    expect(input.isConnected).toBe(true);
    expect(screen.queryByTestId('cat-weight-skills')).toBeNull();                              // the editor replaces the read-only value
    await ui.click(within(cardOf('skills')).getByRole('button', { name: S.cancelEdit }));
    expect(categoryWeight('skills')).toBe('60');
  });

  it('several problems at once are listed together, each with a way to its field', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    await setWeight(ui, 'Python', '60');                                                       // Required total 110
    const input = await openCategoryEdit(ui, 'experience');
    await typeIn(ui, input, '120');
    await ui.click(within(cardOf('experience')).getByRole('button', { name: S.applyEdit }));
    const summary = screen.getByTestId('issue-summary');
    expect(summary.textContent).toContain('Required item weights in Skills total 110%');
    expect(summary.textContent).toContain('The weight of Experience must be a whole number from 0 to 100.');
    expect(within(summary).getAllByRole('button', { name: S.goToIssue }).length).toBeGreaterThanOrEqual(2);
  });
});

describe('Arabic', () => {
  it('the totals and the issues are written in Arabic', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())), { isAr: true });
    await setWeight(ui, 'Python', '60', AR);
    const total = requiredTotal('skills');
    expect(total).toContain('مجموع أوزان المتطلبات الإلزامية');
    expect(total).toContain('الفرق +10');
    expect(screen.getByTestId('issue-summary').textContent).toContain('مجموع أوزان المتطلبات الإلزامية في');
  });
});

describe('many items in one category', () => {
  const thirty = () => {
    const weights = equalizeWeights(30);
    return doc({ skills: { weight: 100, items: weights.map((w, n) => item(`req_${n}`, `Requirement ${n + 1}`, 'required', w,
      { source_text: `Source wording ${n + 1}`, alternatives: n === 0 ? ['A', 'B'] : null })) } });
  };

  it('a category of thirty items shows compact rows; details and actions stay closed until asked for', async () => {
    await renderPanel(mockApi(makeView(thirty())));
    const rows = cardOf('skills').querySelectorAll('li[id^="req-item-"]');
    expect(rows).toHaveLength(30);
    rows.forEach(row => {
      expect(row.querySelector('[id^="req-details-"]')!.hasAttribute('hidden')).toBe(true);
      expect(row.querySelector('[id^="req-actions-"]')!.hasAttribute('hidden')).toBe(true);
    });
    expect(screen.queryByText('Source wording 2')).toBeNull();
    expect(screen.queryByText(S.sourceWording)).toBeNull();
  });

  it('a change in a large category is totalled, and Equalize restores the split', async () => {
    const ui = await renderPanel(mockApi(makeView(thirty())));
    await setWeight(ui, 'Requirement 1', '10');                                                 // 100 - 4 + 10 = 106
    expect(requiredTotal('skills')).toContain('106%');
    await ui.click(within(cardOf('skills')).getByRole('button', { name: new RegExp(`^${S.equalize}`) }));
    expect(itemWeight('Requirement 1')).toBe('4');
    expect(requiredTotal('skills')).toContain('100%');
  });

  it('the details of one item open with its source wording and OR alternatives', async () => {
    const ui = await renderPanel(mockApi(makeView(thirty())));
    const row = document.getElementById('req-item-req_0')!;
    await ui.click(within(row).getByRole('button', { name: S.details }));
    expect(within(row).getByText('Source wording 1')).toBeTruthy();
    expect(within(row).getByText(/A \/ B/)).toBeTruthy();
  });
});

describe('informational notes are collapsed at first', () => {
  it('the informational disclosure is closed and can be opened', async () => {
    const view = { ...makeView(twoRequired()), informational: { generic_model_notes: [{ index: 0, text: 'A model note', kind: 'x' }], parser_review: [] } };
    const ui = await renderPanel(mockApi(view as any));
    const box = screen.getByTestId('informational-disclosure') as HTMLDetailsElement;
    expect(box.open).toBe(false);
    await ui.click(within(box).getByText(S.informationalSummary));
    expect(box.open).toBe(true);
  });
});
