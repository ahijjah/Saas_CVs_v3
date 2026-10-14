import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TenantSubscriptionsPage } from '../../pages/TenantSubscriptions';
import { apiService } from '../../services/api';
import { WEBHOOK_CONFIG } from '../../config';
import type { AuthState } from '../../types';

vi.mock('../../services/api', () => ({
  apiService: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}));

const get = apiService.get as unknown as ReturnType<typeof vi.fn>;
const patch = apiService.patch as unknown as ReturnType<typeof vi.fn>;
const auth = { token: 'tok', user: { email: 'platform@ai970.test', role: 'super_admin' } } as unknown as AuthState;

const TENANT = {
  tenant_id: 't-1',
  tenant_name: 'Acme Ltd',
  plan: 'starter',
  subscription_status: 'active',
  trial_end_at: null,
  subscription_started_at: null,
  subscription_ends_at: null,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
};

async function openManagePanel() {
  get.mockImplementation((url: string) => Promise.resolve(
    url === WEBHOOK_CONFIG.ADMIN_TENANTS_URL
      ? { tenants: [TENANT] }
      : { plans: [{ plan_id: 'p1', plan_code: 'starter', plan_name: 'Starter', status: 'active' }] },
  ));
  const addToast = vi.fn();
  render(<TenantSubscriptionsPage auth={auth} addToast={addToast} />);
  await screen.findByText('Acme Ltd');
  await userEvent.setup().click(screen.getByRole('button', { name: 'Manage' }));
  await screen.findByRole('heading', { name: 'Manage' });
  return { addToast };
}

const FUTURE_DATE = '2099-12-31';
const SUBSCRIPTION_URL = `${WEBHOOK_CONFIG.ADMIN_TENANTS_URL}/t-1/subscription`;

describe('platform-admin provisioning form', () => {
  beforeEach(() => {
    get.mockReset();
    patch.mockReset();
    patch.mockResolvedValue({});
  });

  it('blocks submit without a reason and shows the validation message', async () => {
    await openManagePanel();
    fireEvent.change(screen.getByLabelText('Valid until'), { target: { value: FUTURE_DATE } });

    const extend = screen.getByRole('button', { name: 'Extend Trial' });
    expect(extend).toBeDisabled();
    expect(screen.getByText('Reason is required.')).toBeInTheDocument();

    fireEvent.click(extend);
    expect(patch).not.toHaveBeenCalled();
  });

  it('rejects a reason shorter than 5 characters after trimming', async () => {
    await openManagePanel();
    fireEvent.change(screen.getByLabelText('Valid until'), { target: { value: FUTURE_DATE } });
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '   abc   ' } });

    expect(screen.getByText('Reason must be at least 5 characters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Extend Trial' })).toBeDisabled();
  });

  it('rejects a past valid-until date', async () => {
    await openManagePanel();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Pilot extension approved' } });
    fireEvent.change(screen.getByLabelText('Valid until'), { target: { value: '2020-01-01' } });

    expect(screen.getByText('Valid-until date must be in the future.')).toBeInTheDocument();
    const extend = screen.getByRole('button', { name: 'Extend Trial' });
    expect(extend).toBeDisabled();
    fireEvent.click(extend);
    expect(patch).not.toHaveBeenCalled();
  });

  it('sends expires_at (end of the selected UTC day) and the trimmed reason when valid', async () => {
    await openManagePanel();
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: '  Pilot extension approved  ' } });
    fireEvent.change(screen.getByLabelText('Valid until'), { target: { value: FUTURE_DATE } });

    const extend = screen.getByRole('button', { name: 'Extend Trial' });
    expect(extend).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(extend);

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith(
      SUBSCRIPTION_URL,
      { action: 'extend_trial', reason: 'Pilot extension approved', expires_at: `${FUTURE_DATE}T23:59:59Z` },
      'tok',
    );
    expect(patch.mock.calls[0][1]).not.toHaveProperty('trial_end_at');
  });

  it('suspend needs only a valid reason and sends no expires_at', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await openManagePanel();
    const suspend = screen.getByRole('button', { name: /Suspend/ });
    expect(suspend).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Non-payment under review' } });
    expect(suspend).toBeEnabled();
    fireEvent.click(suspend);

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(confirmSpy).toHaveBeenCalled();
    expect(patch).toHaveBeenCalledWith(
      SUBSCRIPTION_URL,
      { action: 'suspend', reason: 'Non-payment under review' },
      'tok',
    );
  });
});
