import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { TenantAccessGuard } from '../../components/TenantAccessGuard';

function renderAt(status: string | undefined) {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route element={<TenantAccessGuard subscriptionStatus={status} />}>
          <Route path="/dashboard" element={<div>DASHBOARD DATA</div>} />
        </Route>
        <Route path="/plan-selection" element={<div>PLAN SELECTION MARKER</div>} />
        <Route path="/plan-usage" element={<div>PLAN USAGE MARKER</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('TenantAccessGuard', () => {
  it.each(['suspended', 'expired', 'grace', 'trial_expired', 'cancelled'])(
    '%s tenant keeps read-only access: banner above the content, no redirect',
    (status) => {
      renderAt(status);

      const banner = screen.getByRole('status');
      expect(banner).toHaveTextContent('Read-only access.');
      expect(banner).toHaveTextContent(/recruitment changes are disabled until access is restored/);
      expect(within(banner).getByRole('link', { name: 'View plan & usage' })).toHaveAttribute('href', '/plan-usage');

      expect(screen.getByText('DASHBOARD DATA')).toBeInTheDocument();
      expect(screen.queryByText('PLAN USAGE MARKER')).not.toBeInTheDocument();
    },
  );

  it('active tenant renders the content without a banner', () => {
    renderAt('active');
    expect(screen.getByText('DASHBOARD DATA')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it.each(['pending_plan_selection', 'pending_payment', 'pending_sales_contact'])(
    '%s tenant is still redirected to plan selection',
    (status) => {
      renderAt(status);
      expect(screen.getByText('PLAN SELECTION MARKER')).toBeInTheDocument();
      expect(screen.queryByText('DASHBOARD DATA')).not.toBeInTheDocument();
    },
  );
});
