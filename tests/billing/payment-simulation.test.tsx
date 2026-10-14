import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PaymentSimulationPage } from '../../pages/PaymentSimulation';
import { LanguageProvider } from '../../context/LanguageContext';
import { apiService } from '../../services/api';
import { WEBHOOK_CONFIG } from '../../config';
import type { AuthState } from '../../types';

vi.mock('../../services/api', () => ({
  apiService: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));

const post = apiService.post as unknown as ReturnType<typeof vi.fn>;
const get = apiService.get as unknown as ReturnType<typeof vi.fn>;
const auth = { token: 'tok', user: { email: 'admin@acme.test', role: 'admin' } } as unknown as AuthState;

function renderPage() {
  const addToast = vi.fn();
  const onUserUpdate = vi.fn();
  render(
    <LanguageProvider>
      <MemoryRouter initialEntries={['/payment-simulation?plan=starter&billing=monthly']}>
        <Routes>
          <Route path="/payment-simulation" element={<PaymentSimulationPage auth={auth} addToast={addToast} onUserUpdate={onUserUpdate} />} />
          <Route path="/plan-selection" element={<div>PLAN SELECTION MARKER</div>} />
          <Route path="/jobs" element={<div>JOBS MARKER</div>} />
        </Routes>
      </MemoryRouter>
    </LanguageProvider>,
  );
  return { addToast, onUserUpdate };
}

describe('payment page: production build (flag off)', () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    vi.stubEnv('VITE_ALLOW_SIMULATED_PAYMENT', 'false');
  });

  it('shows the activation-pending page with no simulate buttons and no simulate call', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole('heading', { name: 'Activation pending' })).toBeInTheDocument();
    expect(screen.getByText(/No payment is taken on this page/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /payment successful|payment failed/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Testing Mode/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Back to plan selection/ }));
    expect(await screen.findByText('PLAN SELECTION MARKER')).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it('treats any value other than the exact string "true" as off', () => {
    vi.stubEnv('VITE_ALLOW_SIMULATED_PAYMENT', 'TRUE');
    renderPage();
    expect(screen.getByRole('heading', { name: 'Activation pending' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /payment successful/i })).not.toBeInTheDocument();
  });
});

describe('payment page: dev/test flag on', () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    vi.stubEnv('VITE_ALLOW_SIMULATED_PAYMENT', 'true');
  });

  it('keeps the simulation UI and calls the simulate endpoint on success', async () => {
    post.mockResolvedValue({ message: 'Simulated payment ok' });
    const user = userEvent.setup();
    const { onUserUpdate } = renderPage();

    expect(screen.getByText(/Testing Mode/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Activation pending' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Payment Successful' }));

    expect(post).toHaveBeenCalledWith(
      WEBHOOK_CONFIG.SIMULATE_PAYMENT_URL,
      { plan: 'starter', billing_cycle: 'monthly', result: 'success' },
      'tok',
    );
    expect(onUserUpdate).toHaveBeenCalledWith({ subscription_status: 'active' });
    expect(await screen.findByText('JOBS MARKER')).toBeInTheDocument();
  });
});
