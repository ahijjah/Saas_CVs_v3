// Regression tests for the weight controls and the validation messages of the requirements-v2 editor (the reported defect: two Required
// items at 50/50, one changed to 60, a total that stayed at 100%, an ineffective Equalize, and a save that said only "cannot be saved").
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS } from '../../components/requirementsV2/i18n';
import { equalizeWeights } from '../../utils/requirementsV2';
import { apiError, doc, item, makeView } from './fixtures';
import { mockApi } from './mocks';

const JOB = '00000000-0000-0000-0000-0000000000aa';
const S = STRINGS.en;

const twoRequired = (skills = [item('req_py', 'Python', 'required', 50), item('req_sql', 'SQL', 'required', 50)]) =>
  doc({
    skills: { weight: 60, items: [...skills, item('req_docker', 'Docker', 'preferred', null)] },
    experience: { weight: 40, items: [item('req_exp', 'Years', 'required', 100)] },
  });

async function renderPanel(api: ReturnType<typeof mockApi>, opts: { isAr?: boolean } = {}) {
  const ui = userEvent.setup();
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit addToast={vi.fn()} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return ui;
}

const skillsCard = () => document.getElementById('req-cat-skills')!;
const weightInput = (label: string) => screen.getByLabelText(`Weight of “${label}”`) as HTMLInputElement;
const requiredTotal = (cat: string) => screen.getByTestId(`required-total-${cat}`).textContent ?? '';

describe('the reported defect', () => {
  it('60 and 50 show 110% at once, with the difference, in the Required total of the category', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    expect(requiredTotal('skills')).toContain('100%');
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');
    expect(requiredTotal('skills')).toContain('110%');
    expect(requiredTotal('skills')).toContain('+10');
  });

  it('Equalize restores 50/50 in both inputs and in the draft, and does not save', async () => {
    const api = mockApi(makeView(twoRequired()));
    const ui = await renderPanel(api);
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');
    await ui.click(within(skillsCard()).getByRole('button', { name: new RegExp(`^${S.equalize}`) }));
    expect(weightInput('Python').value).toBe('50');
    expect(weightInput('SQL').value).toBe('50');
    expect(requiredTotal('skills')).toContain('100%');
    expect(api.save).not.toHaveBeenCalled();
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(true);    // back to the saved state: nothing to save
  });

  it('Equalize is disabled when the Required weights already are the deterministic equal split', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    const equalize = within(skillsCard()).getByRole('button', { name: new RegExp(`^${S.equalize}`) }) as HTMLButtonElement;
    expect(equalize.disabled).toBe(true);
    expect(equalize.title).toBe(S.equalizeAlready);
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');
    expect((within(skillsCard()).getByRole('button', { name: new RegExp(`^${S.equalize}`) }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('three items split 34/33/33 is the equal split; 33/33/34 is not', async () => {
    const three = doc({ skills: { weight: 100, items: [item('a', 'A', 'required', 34), item('b', 'B', 'required', 33), item('c', 'C', 'required', 33)] } });
    expect(equalizeWeights(3)).toEqual([34, 33, 33]);
    await renderPanel(mockApi(makeView(three)));
    expect((within(skillsCard()).getByRole('button', { name: new RegExp(`^${S.equalize}`) }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('Preferred items carry no weight input', async () => {
    await renderPanel(mockApi(makeView(twoRequired())));
    expect(screen.queryByLabelText('Weight of “Docker”')).toBeNull();
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
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
    expect(screen.getByTestId('issue-summary').textContent).toContain('Required item weights in Skills total 110%, not 100% (difference +10).');
    expect(weightInput('Python').value).toBe('60');                                   // the draft is preserved
    expect(weightInput('Python').getAttribute('aria-invalid')).toBe('true');          // the affected field is highlighted
    expect(weightInput('SQL').getAttribute('aria-invalid')).toBe('true');
  });

  it('a server message without its numbers is shown with the server words, not with invented numbers', async () => {
    const api = mockApi(makeView(twoRequired()));
    api.save.mockRejectedValueOnce(apiError(422, { code: 'invalid_requirements', message: 'The requirements cannot be saved.',
      issues: [{ code: 'required_weights_total', message: 'Required item weights total 80%, not 100%.', category: 'skills', item_id: null }] }));
    const ui = await renderPanel(api);
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '30');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
    expect(screen.getByTestId('issue-summary').textContent).toContain('Required item weights total 80%, not 100%.');
  });

  it('a Required item with a missing weight names the item and its category', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    await ui.clear(weightInput('Python'));
    expect(screen.getByTestId('issue-summary').textContent).toContain('Python (Skills): a Required item needs a whole-number weight from 1 to 100 (now empty).');
  });

  it('an out-of-range Required weight is named with the value typed', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    await ui.clear(weightInput('SQL'));
    await ui.type(weightInput('SQL'), '150');
    expect(screen.getByTestId('issue-summary').textContent).toContain('SQL (Skills): a Required item needs a whole-number weight from 1 to 100 (now 150).');
  });

  it('a category weight above 100 and a category without Required items that carries weight are each named', async () => {
    const ui = await renderPanel(mockApi(makeView(doc({
      skills: { weight: 60, items: [item('a', 'Python', 'required', 100)] },
      certifications: { weight: 5, items: [item('p', 'PMP', 'preferred', null)] },
    }))));
    await ui.clear(document.getElementById('req-cat-skills-w') as HTMLElement);
    await ui.type(document.getElementById('req-cat-skills-w') as HTMLElement, '120');
    const text = screen.getByTestId('issue-summary').textContent ?? '';
    expect(text).toContain('The weight of Skills must be a whole number from 0 to 100.');
    expect(text).toContain('Certifications has no Required items, so its weight must be 0% (now 5%).');
  });

  it('a typed decimal stays in the field, is named, blocks saving, and the draft keeps its last valid value', async () => {
    const api = mockApi(makeView(twoRequired()));
    const ui = await renderPanel(api);
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');                                       // a real change first, so Save is enabled
    // one change, as a paste would make it: typing would commit each whole-number prefix ("1", then "12") on the way to "12.5"
    fireEvent.change(weightInput('Python'), { target: { value: '12.5' } });
    expect(weightInput('Python').value).toBe('12.5');
    expect(weightInput('Python').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByTestId('issue-summary').textContent).toContain('“12.5” is not a whole number');
    expect(requiredTotal('skills')).toContain('110%');                               // the draft still holds the last valid 60
    await ui.click(screen.getByTestId('save'));
    expect(api.save).not.toHaveBeenCalled();
    fireEvent.change(weightInput('Python'), { target: { value: '40' } });              // a whole number again
    expect(requiredTotal('skills')).toContain('90%');
    expect(weightInput('Python').getAttribute('aria-invalid')).toBe('true');           // the Required total is still wrong
    expect(screen.queryByText(/is not a whole number/)).toBeNull();
  });

  it('after a refused save, typing supersedes the server answer: the current problem is shown, not the old one', async () => {
    const api = mockApi(makeView(twoRequired()));
    api.save.mockRejectedValueOnce(apiError(422, { code: 'invalid_requirements', message: 'The requirements cannot be saved.',
      issues: [{ code: 'required_weights_total', message: 'Required item weights total 110%, not 100%.', category: 'skills', item_id: null }] }));
    const ui = await renderPanel(api);
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');
    await ui.click(screen.getByTestId('save'));
    await screen.findByTestId('issue-summary');
    fireEvent.change(weightInput('SQL'), { target: { value: '12.5' } });
    expect(screen.getByTestId('issue-summary').textContent).toContain('“12.5” is not a whole number');
    expect(screen.getByTestId('issue-summary').textContent).not.toContain('Required item weights total 110%');
  });

  it('several problems at once are listed together, each with a way to its field', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())));
    await ui.clear(weightInput('Python'));
    await ui.type(weightInput('Python'), '60');                                       // Required total 110
    fireEvent.change(weightInput('SQL'), { target: { value: '12.5' } });             // not a whole number
    const summary = screen.getByTestId('issue-summary');
    expect(summary.textContent).toContain('Required item weights in Skills total 110%');
    expect(summary.textContent).toContain('“12.5” is not a whole number');
    expect(within(summary).getAllByRole('button', { name: S.goToIssue }).length).toBeGreaterThanOrEqual(2);
  });
});

describe('Arabic', () => {
  it('the totals and the issues are written in Arabic', async () => {
    const ui = await renderPanel(mockApi(makeView(twoRequired())), { isAr: true });
    await ui.clear(screen.getByLabelText(`وزن «Python»`) as HTMLInputElement);
    await ui.type(screen.getByLabelText(`وزن «Python»`) as HTMLInputElement, '60');
    const total = screen.getByTestId('required-total-skills').textContent ?? '';
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
    const rows = skillsCard().querySelectorAll('li[id^="req-item-"]');
    expect(rows).toHaveLength(30);
    rows.forEach(row => {
      expect(row.querySelector('[id^="req-details-"]')!.hasAttribute('hidden')).toBe(true);
      expect(row.querySelector('[id^="req-actions-"]')!.hasAttribute('hidden')).toBe(true);
    });
    expect(screen.queryByText('Source wording 2')).toBeNull();
    expect(screen.queryByText(S.sourceWording)).toBeNull();
  });

  it('a change in a large category is totalled, marked, and Equalize restores the split', async () => {
    const ui = await renderPanel(mockApi(makeView(thirty())));
    const first = within(skillsCard()).getByLabelText(`Weight of “Requirement 1”`) as HTMLInputElement;
    await ui.clear(first);
    await ui.type(first, '10');                                                       // 100 - 4 + 10 = 106
    expect(requiredTotal('skills')).toContain('106%');
    expect(first.getAttribute('aria-invalid')).toBe('true');
    await ui.click(within(skillsCard()).getByRole('button', { name: new RegExp(`^${S.equalize}`) }));
    expect((within(skillsCard()).getByLabelText(`Weight of “Requirement 1”`) as HTMLInputElement).value).toBe('4');
    expect(requiredTotal('skills')).toContain('100%');
  });

  it('the details of one item open with its source wording and OR alternatives', async () => {
    const ui = await renderPanel(mockApi(makeView(thirty())));
    const row = document.getElementById('req-item-req_0')!;
    await ui.click(within(row).getByRole('button', { name: S.details }));
    expect(within(row).getByText('Source wording 1')).toBeTruthy();
    expect(within(row).getByDisplayValue('A')).toBeTruthy();
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
