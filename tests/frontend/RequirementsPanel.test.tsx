import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RequirementsPanel from '../../components/RequirementsPanel';
import { getRequirements, RequirementsApiError, saveRequirementEdits } from '../../services/requirementsApi';
import { ids, view } from './fixtures';

vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(),
  saveRequirementEdits: vi.fn(),
}));

const getMock = vi.mocked(getRequirements);
const saveMock = vi.mocked(saveRequirementEdits);

function renderPanel(role: string, document = view(), onReadiness = vi.fn()) {
  getMock.mockResolvedValue(document);
  render(<RequirementsPanel jobId="job-1" userRole={role} onReadinessChange={onReadiness} />);
  return { onReadiness };
}

beforeEach(() => {
  saveMock.mockReset();
  getMock.mockReset();
});

describe('who may edit', () => {
  it.each(['admin', 'hr_manager'])('lets %s edit and save', async (role) => {
    renderPanel(role);
    await screen.findByRole('heading', { name: 'Requirements' });
    expect(screen.getByRole('button', { name: 'Save requirements' })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /Move .* up/ }).length).toBeGreaterThan(0);
  });

  it.each(['viewer', 'recruiter', 'super_admin', 'something_else'])('renders %s read-only', async (role) => {
    renderPanel(role);
    await screen.findByRole('heading', { name: 'Requirements' });
    expect(screen.queryByRole('button', { name: 'Save requirements' })).toBeNull();
    expect(screen.queryAllByRole('button', { name: /Move .* up/ })).toHaveLength(0);
    expect(screen.getByText(/Only administrators and HR managers can edit requirements/)).toBeInTheDocument();
    for (const field of screen.getAllByRole('textbox', { name: 'Requirement text' })) {
      expect(field).toBeDisabled();
    }
  });

  it.each(['viewer', 'recruiter'])('shows %s the category and item weights, read-only', async (role) => {
    renderPanel(role);
    await screen.findByRole('heading', { name: 'Requirements' });
    const spin = screen.getAllByRole('spinbutton');
    expect(spin.length).toBeGreaterThan(0);                                 // the weights are on screen
    for (const input of spin) expect(input).toBeDisabled();                  // and cannot be changed
    expect(screen.queryByRole('button', { name: 'Add requirement' })).toBeNull();
  });
});

describe('edits and review actions', () => {
  it('sends a text edit with the loaded revision', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('hr_manager');
    const input = (await screen.findAllByRole('textbox', { name: 'Requirement text' }))[0];
    await user.clear(input);
    await user.type(input, 'Python 3');
    await user.click(screen.getByRole('button', { name: 'Save requirements' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{ op: 'set_text', item_id: ids.py, text: 'Python 3' }]));
    expect(await screen.findByText(/Saved\. The requirements are now at revision 4\./)).toBeInTheDocument();
  });

  it('confirms the no-numeric-score document', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('admin', view({ review: { readiness: { state: 'needs_confirmation', can_proceed: false, scoring_mode: 'none', reasons: [] as never[] } } as never }));
    await user.click(await screen.findByRole('button', { name: 'Confirm: no numeric score' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{ op: 'confirm_no_numeric_score' }]));
  });

  it('accepts a classification warning', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('admin', view({ review: { classification: { acknowledgment_required: true, open_warnings: [
      { id: 'w-1', code: 'preferred_cue_not_in_job_description', item_id: ids.sql, category: 'skills', acknowledged: false }] } } as never }));
    await user.click(await screen.findByRole('button', { name: 'Accept this classification' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{ op: 'acknowledge_classification_warning', warning_id: 'w-1' }]));
  });

  it('resolves a structural finding as fixed only with evidence', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('admin', view({ review: { structural: { status: 'open', open_findings: [
      { id: 'f-1', code: 'alternatives_split_across_items', item_ids: [ids.py, ids.sql], source_text: 'Python and SQL are required',
        message: 'Check these together.' }] } } as never }));
    const resolve = await screen.findByRole('button', { name: 'Resolve this finding' });
    expect(resolve).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: 'Python' }));
    expect(resolve).toBeEnabled();
    await user.click(resolve);
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{
      op: 'resolve_structural_finding', finding_id: 'f-1', decision: 'fixed', note: null, evidence_item_ids: [ids.py] }]));
  });

  it('confirms a pending structure', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('admin', view({ review: { structure: { pending_item_ids: [ids.py] } } as never }));
    await user.click(await screen.findByRole('button', { name: 'Confirm this structure' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{ op: 'confirm_structure', item_id: ids.py }]));
  });

  it('reorders items and sends the new order', async () => {
    const user = userEvent.setup();
    saveMock.mockResolvedValue({ requirements_revision: 4 });
    renderPanel('admin');
    await user.click(await screen.findByRole('button', { name: 'Move Python down' }));
    await user.click(screen.getByRole('button', { name: 'Save requirements' }));
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith('job-1', 3, [{ op: 'reorder_items', category: 'skills', item_ids: [ids.sql, ids.py] }]));
  });
});

describe('errors keep the local work', () => {
  it('shows a revision conflict, keeps the unsaved text, and offers reload or reapply', async () => {
    const user = userEvent.setup();
    saveMock.mockRejectedValueOnce(new RequirementsApiError(409, 'revision_conflict', 'changed', 5));
    renderPanel('hr_manager');
    const input = (await screen.findAllByRole('textbox', { name: 'Requirement text' }))[0];
    await user.clear(input);
    await user.type(input, 'Python 3');
    await user.click(screen.getByRole('button', { name: 'Save requirements' }));
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('The requirements changed while you were editing.')).toBeInTheDocument();
    expect(within(alert).getByText(/current revision is 5/)).toBeInTheDocument();
    expect((screen.getAllByRole('textbox', { name: 'Requirement text' })[0] as HTMLInputElement).value).toBe('Python 3');

    saveMock.mockResolvedValueOnce({ requirements_revision: 6 });
    getMock.mockResolvedValue(view({ requirements_revision: 5 }));
    await user.click(within(alert).getByRole('button', { name: 'Apply my edits to the current requirements' }));
    await waitFor(() => expect(saveMock).toHaveBeenLastCalledWith('job-1', 5, [{ op: 'set_text', item_id: ids.py, text: 'Python 3' }]));
  });

  it('can reload instead of reapplying after a conflict', async () => {
    const user = userEvent.setup();
    saveMock.mockRejectedValueOnce(new RequirementsApiError(409, 'revision_conflict', 'changed', 5));
    renderPanel('hr_manager');
    const input = (await screen.findAllByRole('textbox', { name: 'Requirement text' }))[0];
    await user.clear(input);
    await user.type(input, 'Python 3');
    await user.click(screen.getByRole('button', { name: 'Save requirements' }));
    const alert = await screen.findByRole('alert');
    await user.click(within(alert).getByRole('button', { name: 'Reload the current requirements' }));
    expect(saveMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps the edits when the document is rejected by validation', async () => {
    const user = userEvent.setup();
    saveMock.mockRejectedValueOnce(new RequirementsApiError(422, 'invalid_requirements', 'The requirements do not pass the saving rules.', null));
    renderPanel('hr_manager');
    const input = (await screen.findAllByRole('textbox', { name: 'Requirement text' }))[0];
    await user.clear(input);
    await user.type(input, 'Python 3');
    await user.click(screen.getByRole('button', { name: 'Save requirements' }));
    expect(await screen.findByText('The requirements do not pass the saving rules.')).toBeInTheDocument();
    expect((screen.getAllByRole('textbox', { name: 'Requirement text' })[0] as HTMLInputElement).value).toBe('Python 3');
  });
});

describe('readiness gates scoring', () => {
  it('reports a blocked document to the page and explains why', async () => {
    const onReadiness = vi.fn();
    renderPanel('admin', view({ review: { readiness: { state: 'needs_classification_review', can_proceed: false, scoring_mode: null,
      reasons: [{ code: 'classification', message: 'A classification needs to be confirmed.', category: null, item_id: null }] } } as never }), onReadiness);
    expect(await screen.findByTestId('scoring-blocked')).toHaveTextContent('A classification needs to be confirmed.');
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(false));
  });

  it('reports a ready document as ready', async () => {
    const onReadiness = vi.fn();
    renderPanel('admin', view(), onReadiness);
    await screen.findByTestId('readiness-state');
    await waitFor(() => expect(onReadiness).toHaveBeenLastCalledWith(true));
    expect(screen.queryByTestId('scoring-blocked')).toBeNull();
  });
});

describe('language and private fields', () => {
  it('renders Arabic requirement text in a right-to-left-aware field', async () => {
    const arabic = view();
    arabic.document.categories.skills.items[0] = { ...arabic.document.categories.skills.items[0], text: 'بايثون',
      source_text: 'مطلوب بايثون وإتقان لغة SQL' };
    renderPanel('admin', arabic);
    const field = (await screen.findAllByRole('textbox', { name: 'Requirement text' }))[0];
    expect(field).toHaveAttribute('dir', 'auto');
    expect(field).toHaveValue('بايثون');
    expect(screen.getByText('From the job description: “مطلوب بايثون وإتقان لغة SQL”')).toHaveAttribute('dir', 'auto');
  });

  it('does not render retired ids, hashes, reviewer identities, or resolution notes', async () => {
    const leaky = view({ review: { structural: { status: 'open', open_findings: [
      { id: 'f-1', code: 'alternatives_split_across_items', item_ids: [ids.py, ids.sql], source_text: 'Python and SQL are required',
        message: 'Check these together.' }] } } as never });
    const withPrivate = { ...leaky, requirements_retired_item_ids: ['req_retired0001'], basis_hash: 'a'.repeat(64),
      reviewer_user_id: 'SECRET-REVIEWER', resolution: { note: 'SECRET-NOTE' } } as unknown as typeof leaky;
    renderPanel('admin', withPrivate);
    await screen.findByRole('heading', { name: 'Requirements' });
    const text = document.body.textContent ?? '';
    for (const secret of ['req_retired0001', 'aaaaaaaa', 'SECRET-REVIEWER', 'SECRET-NOTE', 'basis_hash']) {
      expect(text).not.toContain(secret);
    }
  });
});

describe('refused reads', () => {
  it('shows a fixed refusal, and no document, when the API refuses the role', async () => {
    getMock.mockRejectedValue(new RequirementsApiError(403, 'permission_denied', 'Your role cannot use this requirements action.', null));
    render(<RequirementsPanel jobId="job-1" userRole="super_admin" />);
    const notice = await screen.findByTestId('requirements-unavailable');
    expect(notice).toHaveTextContent('not available to your role');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByText('Python')).toBeNull();
  });
});
