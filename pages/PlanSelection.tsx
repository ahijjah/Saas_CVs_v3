
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, User, SubscriptionStatus } from '../types';
import { ToastType } from '../components/Toast';

interface PlanSelectionProps {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
  onUserUpdate: (patch: Partial<User>) => void;
}

type BillingCycle = 'monthly' | 'yearly';

interface PlanCard {
  key: string;
  name: string;
  highlight: boolean;
  badge: string | null;
  description: string;
  bestFor: string;
  limits: { users: string; jobs: string; clients: string };
  features: string[];
  support: string;
  apiAccess: boolean;
  branding: string;
  cta: string;
  planCode?: string;
  // null for trial/enterprise
  monthlyPrice: number | null;
  yearlyPrice: number | null;
  yearlyNote: string | null;
  // for trial/enterprise static label
  priceLabel: string;
  priceSuffix: string;
}

const PLAN_CARDS: PlanCard[] = [
  {
    key: 'professional',
    name: 'Professional',
    highlight: true,
    badge: 'Most Popular',
    description: 'For growing agencies and HR teams.',
    bestFor: 'Best for scaling recruitment teams',
    limits: { users: '10 team members', jobs: '50 active campaigns', clients: '20 client orgs' },
    features: ['Everything in Starter', 'Custom AI prompts', 'Basic branding'],
    support: 'Priority',
    apiAccess: false,
    branding: 'Basic',
    cta: 'Select Professional',
    planCode: 'professional',
    monthlyPrice: 99,
    yearlyPrice: 990,
    yearlyNote: 'Save 2 months',
    priceLabel: '',
    priceSuffix: '',
  },
  {
    key: 'starter',
    name: 'Starter',
    highlight: false,
    badge: 'Recommended for Small Teams',
    description: 'For small teams ready to scale.',
    bestFor: 'Best for small HR teams',
    limits: { users: '3 team members', jobs: '10 active campaigns', clients: '1 client org' },
    features: ['Everything in Free Trial', 'Priority AI processing', 'Advanced analytics'],
    support: 'Email',
    apiAccess: false,
    branding: 'None',
    cta: 'Select Starter',
    planCode: 'starter',
    monthlyPrice: 29,
    yearlyPrice: 290,
    yearlyNote: 'Save 2 months',
    priceLabel: '',
    priceSuffix: '',
  },
  {
    key: 'enterprise',
    name: 'Enterprise',
    highlight: false,
    badge: 'Custom Solution',
    description: 'Custom solutions for large organisations.',
    bestFor: 'Best for enterprises and agencies',
    limits: { users: 'Unlimited', jobs: 'Unlimited', clients: 'Unlimited' },
    features: ['Everything in Professional', 'API access', 'White-label branding', 'Custom integrations', 'SLA guarantee'],
    support: 'Dedicated',
    apiAccess: true,
    branding: 'White-label',
    cta: 'Contact Sales',
    planCode: 'enterprise',
    monthlyPrice: null,
    yearlyPrice: null,
    yearlyNote: null,
    priceLabel: 'Custom',
    priceSuffix: '',
  },
  {
    key: 'trial',
    name: 'Free Trial',
    highlight: false,
    badge: 'Evaluation',
    description: 'Evaluate the platform before subscribing.',
    bestFor: 'Best for teams exploring the platform',
    limits: { users: '2 team members', jobs: '3 active campaigns', clients: '1 client org' },
    features: ['AI-powered CV scoring', 'Email CV ingestion', 'All core features'],
    support: 'Community',
    apiAccess: false,
    branding: 'None',
    cta: 'Start Evaluation',
    monthlyPrice: null,
    yearlyPrice: null,
    yearlyNote: null,
    priceLabel: 'Free',
    priceSuffix: '14 days',
  },
];

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5 shrink-0 text-green-500">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

const LimitRow = ({ label, value }: { label: string; value: string }) => (
  <div className="flex justify-between text-xs py-1 border-b border-slate-100 last:border-0">
    <span className="text-textMuted">{label}</span>
    <span className="font-medium text-slate-700">{value}</span>
  </div>
);

export const PlanSelectionPage: React.FC<PlanSelectionProps> = ({
  auth, addToast, onUserUpdate,
}) => {
  const navigate = useNavigate();
  const [billingCycle, setBillingCycle] = useState<BillingCycle>('monthly');
  const [loading, setLoading] = useState<string | null>(null);
  const [confirmedStatus, setConfirmedStatus] = useState<SubscriptionStatus | null>(null);
  const [confirmMessage, setConfirmMessage] = useState<string | null>(null);

  const handleActivateTrial = async () => {
    setLoading('trial');
    try {
      await apiService.post(WEBHOOK_CONFIG.ACTIVATE_TRIAL_URL, {}, auth.token!);
      addToast('Your evaluation workspace is ready. Welcome!', 'success');
      onUserUpdate({ subscription_status: 'trial' });
      navigate('/jobs', { replace: true });
    } catch (err: any) {
      addToast(err.message || 'Failed to activate trial. Please try again.', 'error');
    } finally {
      setLoading(null);
    }
  };

  const handleSelectPlan = async (planCode: string) => {
    setLoading(planCode);
    try {
      const res = await apiService.post(WEBHOOK_CONFIG.SELECT_PLAN_URL, { plan: planCode }, auth.token!);
      const newStatus = res?.subscription_status as SubscriptionStatus;
      onUserUpdate({ subscription_status: newStatus });
      if (newStatus === 'pending_payment') {
        navigate(`/payment-simulation?plan=${planCode}&billing=${billingCycle}`, { replace: true });
      } else {
        // enterprise → pending_sales_contact: show inline confirmation
        setConfirmedStatus(newStatus);
        setConfirmMessage(res?.message || 'Plan selected.');
        addToast(res?.message || 'Plan selected.', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to select plan. Please try again.', 'error');
    } finally {
      setLoading(null);
    }
  };

  const getCardAction = (card: PlanCard) => {
    if (card.key === 'trial') return handleActivateTrial;
    if (card.planCode) return () => handleSelectPlan(card.planCode!);
    return undefined;
  };

  if (confirmedStatus && confirmMessage) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex items-center justify-center px-4">
        <div className="bg-white rounded-2xl shadow-lg p-10 max-w-md w-full text-center">
          <div className="w-14 h-14 rounded-full bg-purple-100 flex items-center justify-center mx-auto mb-5">
            <svg viewBox="0 0 24 24" fill="none" stroke="#7c3aed" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-7 h-7">
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.15 13a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.07 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L7.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 21 16.92z" />
            </svg>
          </div>
          <h2 className="text-xl font-bold text-slate-900 mb-3">Request received!</h2>
          <p className="text-slate-500 text-sm mb-6">{confirmMessage}</p>
          <a href="mailto:sales@ai970.cloud" className="inline-block px-5 py-2.5 bg-primary text-white rounded-xl text-sm font-semibold hover:bg-primary/90 transition-all">
            Email sales@ai970.cloud
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          </div>
          <span className="text-xl font-bold text-slate-800 tracking-tight">CV Analyzer</span>
        </div>
        {auth.user?.email && (
          <span className="text-sm text-textMuted hidden sm:block">{auth.user.email}</span>
        )}
      </div>

      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="text-center mb-6">
          <h1 className="text-3xl font-bold text-slate-900 mb-3">Choose your plan</h1>
          <p className="text-slate-500 text-base max-w-md mx-auto">
            Start with a free trial — no credit card required. Upgrade anytime to unlock more capacity.
          </p>
        </div>

        {/* Billing cycle toggle */}
        <div className="flex items-center gap-1 bg-slate-100 rounded-xl p-1 mb-8">
          <button
            onClick={() => setBillingCycle('monthly')}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition-all ${
              billingCycle === 'monthly' ? 'bg-white shadow-sm text-slate-800' : 'text-textMuted hover:text-slate-700'
            }`}
          >
            Monthly
          </button>
          <button
            onClick={() => setBillingCycle('yearly')}
            className={`px-5 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-1.5 ${
              billingCycle === 'yearly' ? 'bg-white shadow-sm text-slate-800' : 'text-textMuted hover:text-slate-700'
            }`}
          >
            Yearly
            <span className="text-[10px] font-bold text-green-600 bg-green-50 px-1.5 py-0.5 rounded-full">
              Save 17%
            </span>
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 w-full max-w-5xl">
          {PLAN_CARDS.map(card => {
            const isActioning = loading === (card.key === 'trial' ? 'trial' : card.planCode);
            const action = getCardAction(card);
            const displayPrice = card.monthlyPrice !== null
              ? (billingCycle === 'yearly' ? card.yearlyPrice : card.monthlyPrice)
              : null;

            return (
              <div
                key={card.key}
                className={`relative rounded-2xl border flex flex-col p-5 shadow-sm transition-shadow hover:shadow-md ${
                  card.highlight
                    ? 'bg-white border-primary ring-2 ring-primary/20'
                    : card.key === 'trial'
                    ? 'bg-slate-50 border-slate-200 opacity-90'
                    : 'bg-white border-slate-200'
                }`}
              >
                {card.badge && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap">
                    <span className={`text-xs font-semibold px-3 py-1 rounded-full ${
                      card.highlight
                        ? 'bg-primary text-white'
                        : card.key === 'trial'
                        ? 'bg-slate-200 text-slate-600'
                        : 'bg-slate-700 text-white'
                    }`}>{card.badge}</span>
                  </div>
                )}

                <div className="mb-3">
                  <h3 className={`text-base font-semibold ${card.key === 'trial' ? 'text-slate-600' : 'text-slate-800'}`}>{card.name}</h3>
                  <p className="text-xs text-textMuted mt-0.5">{card.description}</p>
                  <p className="text-[11px] text-slate-400 mt-0.5 italic">{card.bestFor}</p>
                </div>

                {/* Price block */}
                <div className="mb-4">
                  {displayPrice !== null ? (
                    <div className="flex flex-wrap items-baseline gap-1">
                      <span className="text-2xl font-bold text-slate-900">${displayPrice}</span>
                      <span className="text-sm text-textMuted">/ {billingCycle === 'yearly' ? 'year' : 'month'}</span>
                      {billingCycle === 'yearly' && card.yearlyNote && (
                        <span className="text-xs font-semibold text-green-600">{card.yearlyNote}</span>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-baseline gap-1">
                      <span className="text-2xl font-bold text-slate-900">{card.priceLabel}</span>
                      {card.priceSuffix && <span className="text-sm text-textMuted">{card.priceSuffix}</span>}
                    </div>
                  )}
                  {/* Show both cadences as reference for paid plans */}
                  {card.monthlyPrice !== null && (
                    <p className="text-[11px] text-textMuted mt-0.5">
                      {billingCycle === 'yearly'
                        ? `$${card.monthlyPrice}/mo billed monthly`
                        : `$${card.yearlyPrice}/yr billed yearly`}
                    </p>
                  )}
                </div>

                {/* Limits */}
                <div className="mb-4 bg-slate-50 rounded-xl p-3">
                  <LimitRow label="Users" value={card.limits.users} />
                  <LimitRow label="Active jobs" value={card.limits.jobs} />
                  <LimitRow label="Clients" value={card.limits.clients} />
                  <LimitRow label="Branding" value={card.branding} />
                  <LimitRow label="API access" value={card.apiAccess ? 'Yes' : 'No'} />
                  <LimitRow label="Support" value={card.support} />
                </div>

                {/* Features */}
                <ul className="space-y-1.5 flex-1 mb-5">
                  {card.features.map(f => (
                    <li key={f} className="flex items-start gap-1.5 text-xs text-slate-600">
                      <CheckIcon /><span>{f}</span>
                    </li>
                  ))}
                </ul>

                <button
                  onClick={action}
                  disabled={isActioning || !action}
                  className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 ${
                    card.highlight
                      ? 'bg-primary text-white hover:bg-primary/90 focus:ring-primary disabled:opacity-60'
                      : 'bg-slate-100 text-slate-700 hover:bg-slate-200 focus:ring-slate-300 disabled:opacity-60'
                  }`}
                >
                  {isActioning ? 'Please wait…' : card.cta}
                </button>
              </div>
            );
          })}
        </div>

        <p className="mt-8 text-xs text-slate-400 text-center">
          Questions? Email us at{' '}
          <a href="mailto:sales@ai970.cloud" className="text-primary hover:underline">sales@ai970.cloud</a>
        </p>
      </div>
    </div>
  );
};
