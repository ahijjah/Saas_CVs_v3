// Helpers for the review-first editor: rows are read in review; changes go through Actions → Edit (or the category pencil) and Apply.
import { screen, within } from '@testing-library/react';
import type userEvent from '@testing-library/user-event';
import { fmt, STRINGS, type Strings } from '../../components/requirementsV2/i18n';

type Ui = ReturnType<typeof userEvent.setup>;

export const rowOf = (text: string) => screen.getByText(text, { selector: 'p[id^="req-text-"]' }).closest('li') as HTMLElement;
export const cardOf = (category: string) => document.getElementById(`req-cat-${category}`) as HTMLElement;

// Opens the item's editor: Actions (⋯) → Edit. Returns the editor form.
export async function openEditor(ui: Ui, text: string, s: Strings = STRINGS.en) {
  const row = rowOf(text);
  await ui.click(within(row).getByRole('button', { name: `${s.moreActions}: ${text}` }));
  await ui.click(within(row).getByRole('button', { name: s.editItem }));
  return screen.getByTestId('item-editor') as HTMLFormElement;
}

export async function applyEditor(ui: Ui, editor: HTMLElement, s: Strings = STRINGS.en) {
  await ui.click(within(editor).getByRole('button', { name: s.applyEdit }));
}

export async function typeIn(ui: Ui, el: HTMLElement, value: string) {
  await ui.clear(el);
  if (value !== '') await ui.type(el, value);
}

// The weight input inside an open editor (its accessible name is "Weight of “<item>”").
export const editorWeight = (editor: HTMLElement) => within(editor).getByRole('textbox', { name: /^(Weight of|وزن)/ }) as HTMLInputElement;

// Opens the category weight editor (pencil) and returns its input.
export async function openCategoryEdit(ui: Ui, category: string, s: Strings = STRINGS.en) {
  await ui.click(within(cardOf(category)).getByRole('button', { name: fmt(s.editCategoryWeight, { name: s.categories[category as keyof Strings['categories']] }) }));
  return document.getElementById(`req-cat-${category}-w`) as HTMLInputElement;
}

export const pctOf = (el: Element | null) => (el?.textContent ?? '').match(/(\d+)%/)?.[1] ?? null;
// the Required weight shown in review ("Weight: 50%"), or null when the row has none (Preferred)
export const itemWeight = (text: string) => pctOf(rowOf(text).querySelector('[data-testid^="weight-"]'));
export const categoryWeight = (category: string) => pctOf(screen.getByTestId(`cat-weight-${category}`));
export const requiredTotal = (category: string) => screen.getByTestId(`required-total-${category}`).textContent ?? '';

// Shows the wording of an item in review (null when no row shows it).
export const wording = (text: string) => screen.queryByText(text, { selector: 'p[id^="req-text-"]' });

// Appends text to an item's wording through the editor, then Apply.
export async function editWording(ui: Ui, text: string, append: string, s: Strings = STRINGS.en) {
  const editor = await openEditor(ui, text, s);
  await ui.type(within(editor).getByLabelText(s.itemText), append);
  await applyEditor(ui, editor, s);
}

// Sets a Required item's weight through the editor, then Apply.
export async function setWeight(ui: Ui, text: string, value: string, s: Strings = STRINGS.en) {
  const editor = await openEditor(ui, text, s);
  await typeIn(ui, editorWeight(editor), value);
  await applyEditor(ui, editor, s);
}

// Actions (⋯) → the named action in the item's row (for example "Delete" or "Make preferred").
export async function rowAction(ui: Ui, text: string, action: string | RegExp, s: Strings = STRINGS.en) {
  const row = rowOf(text);
  await ui.click(within(row).getByRole('button', { name: `${s.moreActions}: ${text}` }));
  await ui.click(within(row).getByRole('button', { name: action }));
}

export const deleteItem = (ui: Ui, text: string, s: Strings = STRINGS.en) => rowAction(ui, text, `${s.deleteItem}: ${text}`, s);
