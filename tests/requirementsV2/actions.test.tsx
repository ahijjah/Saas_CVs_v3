// The item Actions menu: a compact dropdown (Edit first, Delete last behind a divider), keyboard use, and a delete that is draft-only
// and needs a confirmation naming the item. Importance is changed only inside Edit.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { fmt, STRINGS } from '../../components/requirementsV2/i18n';
import { doc, item, makeView } from './fixtures';
import { mockApi } from './mocks';
import { cardOf, itemWeight, openEditor, rowOf, wording } from './review';

const JOB = '00000000-0000-0000-0000-0000000000aa';
const S = STRINGS.en;
const AR = STRINGS.ar;

const sample = () => doc({
  skills: { weight: 100, items: [item('req_py', 'Python', 'required', 60), item('req_sql', 'SQL', 'required', 40), item('req_docker', 'Docker', 'preferred', null)] },
});

async function renderPanel(api = mockApi(makeView(sample())), isAr = false) {
  const ui = userEvent.setup();
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={isAr} canEdit addToast={vi.fn()} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return { ui, api };
}

const openMenu = async (ui: ReturnType<typeof userEvent.setup>, text: string, s = S) =>
  ui.click(within(rowOf(text)).getByRole('button', { name: `${s.moreActions}: ${text}` }));

describe('the Actions dropdown', () => {
  it('is closed until asked for, then lists Edit first and Delete last, with a divider between', async () => {
    const { ui } = await renderPanel();
    const trigger = within(rowOf('Python')).getByRole('button', { name: `${S.moreActions}: Python` });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('menu')).toBeNull();
    await openMenu(ui, 'Python');
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    const menu = screen.getByRole('menu');
    const items = within(menu).getAllByRole('menuitem').map(b => b.textContent);
    expect(items).toEqual([S.editItem, S.delete]);
    const children = Array.from(menu.children).map(c => c.getAttribute('role'));
    expect(children).toEqual(['none', 'separator', 'none']);                             // Edit, divider, Delete
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(2);                       // no importance action here: it is changed in Edit only
  });

  it('opens with the focus on Edit; arrow keys, Home and End move between the items; Escape closes and returns the focus', async () => {
    const { ui } = await renderPanel();
    const trigger = within(rowOf('Python')).getByRole('button', { name: `${S.moreActions}: Python` });
    await openMenu(ui, 'Python');
    expect(document.activeElement).toBe(within(screen.getByRole('menu')).getByRole('menuitem', { name: S.editItem }));
    await ui.keyboard('{ArrowDown}');
    expect(document.activeElement?.textContent).toBe(S.delete);
    await ui.keyboard('{ArrowDown}');                                                    // wraps to the first item
    expect(document.activeElement?.textContent).toBe(S.editItem);
    await ui.keyboard('{End}');
    expect(document.activeElement?.textContent).toBe(S.delete);
    await ui.keyboard('{Home}');
    expect(document.activeElement?.textContent).toBe(S.editItem);
    await ui.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('closes when the focus leaves it (Tab) and when the page is clicked elsewhere', async () => {
    const { ui } = await renderPanel();
    await openMenu(ui, 'Python');
    await ui.tab();                                                                     // Edit → Delete: still inside the menu
    expect(screen.getByRole('menu')).toBeTruthy();
    await ui.tab();                                                                     // leaves the menu
    expect(screen.queryByRole('menu')).toBeNull();
    await openMenu(ui, 'Python');
    await ui.click(screen.getByTestId('requirements-v2'));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('Edit from the menu opens the editor, where importance is changed', async () => {
    const { ui } = await renderPanel();
    await openMenu(ui, 'Python');
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: S.editItem }));
    const ed = screen.getByTestId('item-editor');
    expect(within(ed).getByRole('radio', { name: S.required }).getAttribute('aria-checked') ?? (within(ed).getByRole('radio', { name: S.required }) as HTMLInputElement).checked).toBeTruthy();
    await ui.click(within(ed).getByRole('radio', { name: S.preferred }));
    await ui.click(within(ed).getByRole('button', { name: S.cancelEdit }));
    expect(itemWeight('Python')).toBe('60');                                              // Cancel changed nothing
  });
});

describe('delete: confirmed, draft-only', () => {
  it('asks first and names the item; Cancel leaves the draft and the saved version as they were', async () => {
    const { ui, api } = await renderPanel();
    await openMenu(ui, 'Docker');
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: `${S.deleteItem}: Docker` }));
    const confirm = screen.getByTestId('delete-confirm');
    expect(confirm.getAttribute('role')).toBe('alertdialog');
    expect(confirm.textContent).toContain(fmt(S.deleteConfirmTitle, { name: 'Docker' }));
    expect(document.activeElement).toBe(within(confirm).getByRole('button', { name: S.cancelEdit }));   // the safe choice has the focus
    expect(wording('Docker')).toBeTruthy();                                              // nothing is removed yet
    await ui.click(within(confirm).getByRole('button', { name: S.cancelEdit }));
    expect(screen.queryByTestId('delete-confirm')).toBeNull();
    expect(wording('Docker')).toBeTruthy();
    expect(screen.queryByTestId('unsaved-badge')).toBeNull();                            // the draft is unchanged
    expect((screen.getByTestId('save') as HTMLButtonElement).disabled).toBe(true);
    expect(api.save).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(within(rowOf('Docker')).getByRole('button', { name: `${S.moreActions}: Docker` }));
  });

  it('Escape in the confirmation cancels the same way', async () => {
    const { ui } = await renderPanel();
    await openMenu(ui, 'Docker');
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: `${S.deleteItem}: Docker` }));
    await ui.keyboard('{Escape}');
    expect(screen.queryByTestId('delete-confirm')).toBeNull();
    expect(wording('Docker')).toBeTruthy();
    expect(screen.queryByTestId('unsaved-badge')).toBeNull();
  });

  it('Delete from draft removes the item from the draft only; nothing is saved until Save', async () => {
    const { ui, api } = await renderPanel();
    await openMenu(ui, 'Docker');
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: `${S.deleteItem}: Docker` }));
    await ui.click(within(screen.getByTestId('delete-confirm')).getByRole('button', { name: S.deleteConfirmButton }));
    expect(wording('Docker')).toBeNull();
    expect(screen.getByTestId('unsaved-badge')).toBeTruthy();
    expect(api.save).not.toHaveBeenCalled();
    await ui.click(screen.getByTestId('save'));
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1));
    expect(JSON.stringify(api.save.mock.calls[0][2])).not.toContain('Docker');
  });

  it('the confirmation names the item in Arabic, with Arabic quotation marks and the Arabic labels', async () => {
    const { ui } = await renderPanel(mockApi(makeView(sample())), true);
    await openMenu(ui, 'SQL', AR);
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: `${AR.deleteItem}: SQL` }));
    const confirm = screen.getByTestId('delete-confirm');
    expect(confirm.textContent).toContain(fmt(AR.deleteConfirmTitle, { name: 'SQL' }));
    expect(confirm.textContent).toContain(AR.deleteConfirmButton);
    expect(confirm.textContent).toContain(AR.cancelEdit);
    expect(confirm.closest('[dir="rtl"]')).toBeTruthy();
  });

  it('a cancelled delete of the last Required item removes nothing and sets no category weight', async () => {
    const { ui } = await renderPanel();
    await openMenu(ui, 'Python');
    await ui.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: `${S.deleteItem}: Python` }));
    await ui.click(within(screen.getByTestId('delete-confirm')).getByRole('button', { name: S.cancelEdit }));
    expect(itemWeight('Python')).toBe('60');
    expect(cardOf('skills').textContent).toContain(fmt(S.requiredCount, { n: 2 }));
  });
});
