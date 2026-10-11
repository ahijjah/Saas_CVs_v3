import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RequirementsV2Panel } from '../../components/requirementsV2/RequirementsV2Panel';
import { STRINGS } from '../../components/requirementsV2/i18n';
import type { RequirementsView } from '../../services/requirementsV2Api';
import { apiError, doc, makeView } from './fixtures';
import { mockApi } from './mocks';

// The status view a requirements-v2 job shows while its extraction has no document yet. This mirrors
// services/requirements_api.py build_pending_view(): a read-only skeleton, readiness basis 'extraction', no editor.
const JOB = '00000000-0000-0000-0000-0000000000a1';
const S = STRINGS.en;

const pendingView = (status: 'pending' | 'processing' | 'failed', error: string | null = null, retry = false): RequirementsView =>
  makeView(doc({}), {
    readiness: {
      state: status === 'failed' ? 'extraction_failed' : 'extraction_pending', scoring_mode: null, can_proceed: false,
      reasons: [{ code: status === 'failed' ? 'extraction_failed' : 'extraction_pending', message: error ?? '', category: null, item_id: null }],
      open_warning_ids: [], unresolved_warning_ids: [], structure_review_item_ids: [], guarded: false, basis: 'extraction',
    },
    pipeline: { status: 'not_extracted', available: false, errors: [] },
    extraction: { status, error, retry_available: retry },
    can_edit: false,
  });

const renderPanel = async (api: ReturnType<typeof mockApi>, opts: { isAr?: boolean; canEdit?: boolean } = {}) => {
  const addToast = vi.fn();
  const ui = userEvent.setup();
  render(<RequirementsV2Panel jobId={JOB} api={api} isAr={!!opts.isAr} canEdit={opts.canEdit ?? true} addToast={addToast} currentUserId="u-me" />);
  await screen.findByTestId('requirements-v2');
  return { ui, addToast };
};

describe('requirements-v2 extraction status', () => {
  it('a pending job shows the status card and no editor, and refresh reloads the status', async () => {
    const api = mockApi(pendingView('pending'));
    const { ui } = await renderPanel(api);
    const card = screen.getByTestId('extraction-status');
    expect(card.getAttribute('data-status')).toBe('pending');
    expect(screen.getByRole('heading', { name: S.extractionQueuedTitle })).toBeTruthy();
    expect(screen.getByTestId('extraction-step-pending').getAttribute('aria-current')).toBe('step');
    expect(screen.queryByTestId('save')).toBeNull();
    expect(screen.queryByTestId('extraction-retry')).toBeNull();
    expect(screen.queryByTestId('category-total')).toBeNull();

    api.get.mockResolvedValueOnce(makeView());   // the document arrives
    await ui.click(screen.getByTestId('extraction-refresh'));
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId('extraction-status')).toBeNull());
    expect(screen.getByTestId('save')).toBeTruthy();
  });

  it('a processing job shows the processing step and the processing text', async () => {
    await renderPanel(mockApi(pendingView('processing')));
    expect(screen.getByTestId('extraction-status').getAttribute('data-status')).toBe('processing');
    expect(screen.getByTestId('extraction-step-processing').getAttribute('aria-current')).toBe('step');
    expect(screen.getByText(S.extractionPendingTitle)).toBeTruthy();
    expect(screen.getByText(S.extractionPendingBody)).toBeTruthy();
  });

  it('a queued or processing job refreshes by itself and opens the editor when it completes', async () => {
    const api = mockApi(pendingView('pending'));
    api.get.mockReset();
    api.get.mockResolvedValueOnce(pendingView('processing')).mockResolvedValue(makeView());
    await renderPanel(api);
    await waitFor(() => expect(screen.queryByTestId('save')).toBeTruthy(), { timeout: 10000 });
    expect(api.get).toHaveBeenCalledTimes(2);                      // the first read (processing) and the automatic refresh (completed)
  }, 15000);

  it('a failed attempt shows the reason, and an editor can request a new attempt', async () => {
    const api = mockApi(pendingView('failed', 'The model output could not be used.', true));
    api.get.mockReset();
    api.get.mockResolvedValueOnce(pendingView('failed', 'The model output could not be used.', true))
           .mockResolvedValueOnce(pendingView('processing'));
    const { ui, addToast } = await renderPanel(api);
    expect(screen.getByTestId('extraction-status').getAttribute('data-status')).toBe('failed');
    expect(screen.getByTestId('extraction-error').textContent).toContain('The model output could not be used.');
    await ui.click(screen.getByTestId('extraction-retry'));
    await waitFor(() => expect(api.retryExtraction).toHaveBeenCalledWith(JOB));
    await waitFor(() => expect(screen.getByTestId('extraction-status').getAttribute('data-status')).toBe('processing'));
    expect(addToast).toHaveBeenCalledWith(S.extractionQueued, 'info');
  });

  it('a viewer sees the failure but no retry button (the server sets retry_available)', async () => {
    const api = mockApi(pendingView('failed', 'The model output could not be used.', false));
    await renderPanel(api, { canEdit: false });
    expect(screen.getByText(S.extractionFailedTitle)).toBeTruthy();
    expect(screen.queryByTestId('extraction-retry')).toBeNull();
  });

  it('a refused retry is shown as a message, not as a success', async () => {
    const api = mockApi(pendingView('failed', 'x', true));
    api.retryExtraction.mockRejectedValueOnce(apiError(409, { code: 'requirements_extraction_retry_not_allowed', message: 'refused' }));
    const { ui, addToast } = await renderPanel(api);
    await ui.click(screen.getByTestId('extraction-retry'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith(S.err_requirements_extraction_retry_not_allowed, 'error'));
    expect(screen.getByTestId('extraction-status').getAttribute('data-status')).toBe('failed');
  });

  it('Arabic renders the same card right-to-left', async () => {
    const api = mockApi(pendingView('pending'));
    await renderPanel(api, { isAr: true });
    expect(screen.getByRole('heading', { name: STRINGS.ar.extractionQueuedTitle })).toBeTruthy();
    expect(screen.getByTestId('requirements-v2').getAttribute('dir')).toBe('rtl');
  });

  it('a completed job (with a document) shows no extraction card', async () => {
    const api = mockApi(makeView(doc({}), { extraction: { status: 'completed', error: null, retry_available: false } }));
    await renderPanel(api);
    expect(screen.queryByTestId('extraction-status')).toBeNull();
    expect(screen.getByTestId('save')).toBeTruthy();
  });
});
