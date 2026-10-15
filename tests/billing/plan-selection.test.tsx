import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PlanSelectionPage } from '../../pages/PlanSelection';
import { apiService } from '../../services/api';
import { WEBHOOK_CONFIG } from '../../config';
import type { AuthState } from '../../types';

vi.mock('../../services/api', () => ({
  apiService: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));

const post = apiService.post as unknown as ReturnType<typeof vi.fn>;
const auth = { token: 'tok', user: { email: 'admin@acme.test', role: 'admin' } } as unknown as AuthState;

function renderPage() {
  const addToast = vi.fn();
  const onUserUpdate = vi.fn();
  render(
    <MemoryRouter initialEntries={['/plan-selection']}>
      <Routes>
        <Route path="/plan-selection" element={<PlanSelectionPage auth={auth} addToast={addToast} onUserUpdate={onUserUpdate} />} />
        <Route path="/payment-simulation" element={<div>PAYMENT PAGE MARKER</div>} />
        <Route path="/jobs" element={<div>JOBS MARKER</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return { addToast, onUserUpdate };
}

describe('plan selection: paid plans are selected, not activated', () => {
  beforeEach(() => { post.mockReset(); });

  it('shows the pending-activation card, never navigates to the payment page, and never simulates payment', async () => {
    post.mockResolvedValue({ success: true, subscription_status: 'pending_payment', message: 'Plan selected' });
    const user = userEvent.setup();
    const { onUserUpdate } = renderPage();

    await user.click(screen.getByRole('button', { name: 'Select Starter' }));

    expect(await screen.findByRole('heading', { name: 'Plan selected' })).toBeInTheDocument();
    expect(screen.getByText(/Activation is pending/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Email sales@ai970.cloud' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^mailto:sales@ai970\.cloud\?subject=/),
    );

    expect(screen.queryByText('PAYMENT PAGE MARKER')).not.toBeInTheDocument();
    expect(onUserUpdate).toHaveBeenCalledWith({ subscription_status: 'pending_payment' });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(WEBHOOK_CONFIG.SELECT_PLAN_URL, { plan: 'starter' }, 'tok');
    expect(post.mock.calls.some(([url]) => String(url).includes('simulate-payment'))).toBe(false);
  });

  it('enterprise still shows the sales-contact confirmation', async () => {
    post.mockResolvedValue({ success: true, subscription_status: 'pending_sales_contact', message: 'Sales will contact you' });
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole('button', { name: 'Contact Sales' }));

    expect(await screen.findByRole('heading', { name: 'Request received!' })).toBeInTheDocument();
    expect(screen.getByText('Sales will contact you')).toBeInTheDocument();
    expect(screen.queryByText('PAYMENT PAGE MARKER')).not.toBeInTheDocument();
  });
});
