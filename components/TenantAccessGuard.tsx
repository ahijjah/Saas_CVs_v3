import React from 'react';
import { Link, Navigate, Outlet } from 'react-router-dom';

// Statuses that require plan selection before the product is reachable.
export const PLAN_GATE_STATUSES = new Set([
  'pending_plan_selection', 'pending_payment', 'pending_sales_contact',
]);

// Lapsed tenants keep READ-ONLY access to their data. The backend blocks their
// writes (HTTP 403), so the UI shows a banner instead of locking them out.
export const READ_ONLY_STATUSES = new Set([
  'grace', 'trial_expired', 'suspended', 'cancelled', 'expired',
]);

const STATUS_TEXT: Record<string, string> = {
  grace: 'grace period',
  trial_expired: 'trial expired',
  suspended: 'suspended',
  cancelled: 'cancelled',
  expired: 'expired',
};

export const ReadOnlyBanner: React.FC<{ subscriptionStatus: string }> = ({ subscriptionStatus }) => (
  <div
    role="status"
    data-testid="read-only-banner"
    className="mb-4 flex flex-col sm:flex-row sm:items-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900"
  >
    <p className="flex-1">
      <strong className="font-semibold">Read-only access.</strong>{' '}
      Subscription status: {STATUS_TEXT[subscriptionStatus] ?? subscriptionStatus}. You can still view your data,
      but recruitment changes are disabled until access is restored.
    </p>
    <Link to="/plan-usage" className="font-semibold text-amber-800 underline hover:text-amber-950 whitespace-nowrap">
      View plan &amp; usage
    </Link>
  </div>
);

export const TenantAccessGuard: React.FC<{ subscriptionStatus: string | undefined }> = ({ subscriptionStatus }) => {
  if (subscriptionStatus && PLAN_GATE_STATUSES.has(subscriptionStatus)) {
    return <Navigate to="/plan-selection" replace />;
  }
  if (subscriptionStatus && READ_ONLY_STATUSES.has(subscriptionStatus)) {
    return (
      <>
        <ReadOnlyBanner subscriptionStatus={subscriptionStatus} />
        <Outlet />
      </>
    );
  }
  return <Outlet />;
};
