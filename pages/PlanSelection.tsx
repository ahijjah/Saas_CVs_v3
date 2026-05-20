
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, User } from '../types';
import { ToastType } from '../components/Toast';

interface PlanSelectionProps {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
  onUserUpdate: (patch: Partial<User>) => void;
  onNavigate: (path: string) => void;
}

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 shrink-0 text-green-500">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

export const PlanSelectionPage: React.FC<PlanSelectionProps> = ({
  auth, addToast, onUserUpdate, onNavigate,
}) => {
  const [activating, setActivating] = useState(false);

  const handleActivateTrial = async () => {
    setActivating(true);
    try {
      await apiService.post(WEBHOOK_CONFIG.ACTIVATE_TRIAL_URL, {}, auth.token ?? undefined);
      addToast('Free trial activated! Welcome aboard.', 'success');
      onUserUpdate({ subscription_status: 'trial' });
      onNavigate('/jobs');
    } catch (err: any) {
      const msg = err.message || 'Failed to activate trial. Please try again.';
      addToast(msg, 'error');
    } finally {
      setActivating(false);
    }
  };

  const plans = [
    {
      key: 'trial',
      name: 'Free Trial',
      price: 'Free',
      duration: '14 days',
      highlight: true,
      description: 'Full access, no credit card required.',
      features: [
        'Up to 3 team members',
        'Up to 10 active job campaigns',
        'AI-powered CV scoring',
        'Email CV ingestion',
        'All core features included',
      ],
      cta: activating ? 'Activating…' : 'Start free trial',
      action: handleActivateTrial,
      disabled: activating,
    },
    {
      key: 'starter',
      name: 'Starter',
      price: 'Contact sales',
      duration: '/month',
      highlight: false,
      description: 'For small teams ready to scale.',
      features: [
        'Up to 10 team members',
        'Up to 25 active campaigns',
        'Priority AI processing',
        'Advanced analytics',
        'Email support',
      ],
      cta: 'Contact sales',
      action: () => window.open('mailto:sales@ai970.cloud?subject=Starter Plan Enquiry', '_blank'),
      disabled: false,
    },
    {
      key: 'professional',
      name: 'Professional',
      price: 'Contact sales',
      duration: '/month',
      highlight: false,
      description: 'For growing agencies and HR teams.',
      features: [
        'Unlimited team members',
        'Unlimited campaigns',
        'Custom AI prompts',
        'API access',
        'Dedicated support',
      ],
      cta: 'Contact sales',
      action: () => window.open('mailto:sales@ai970.cloud?subject=Professional Plan Enquiry', '_blank'),
      disabled: false,
    },
    {
      key: 'enterprise',
      name: 'Enterprise',
      price: 'Contact us',
      duration: '',
      highlight: false,
      description: 'Custom solutions for large organisations.',
      features: [
        'Everything in Professional',
        'Custom integrations',
        'SLA guarantee',
        'On-premise option',
        'Dedicated account manager',
      ],
      cta: 'Contact us',
      action: () => window.open('mailto:sales@ai970.cloud?subject=Enterprise Plan Enquiry', '_blank'),
      disabled: false,
    },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      {/* Header */}
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

      {/* Body */}
      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="text-center mb-10">
          <h1 className="text-3xl font-bold text-slate-900 mb-3">Choose your plan</h1>
          <p className="text-slate-500 text-base max-w-md mx-auto">
            Start with a free trial — no credit card required. Upgrade anytime to unlock more capacity.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5 w-full max-w-5xl">
          {plans.map(plan => (
            <div
              key={plan.key}
              className={`relative rounded-2xl border bg-white flex flex-col p-6 shadow-sm transition-shadow hover:shadow-md ${
                plan.highlight
                  ? 'border-primary ring-2 ring-primary/20'
                  : 'border-slate-200'
              }`}
            >
              {plan.highlight && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                  <span className="bg-primary text-white text-xs font-semibold px-3 py-1 rounded-full">
                    Recommended
                  </span>
                </div>
              )}

              <div className="mb-4">
                <h3 className="text-base font-semibold text-slate-800">{plan.name}</h3>
                <p className="text-xs text-textMuted mt-0.5">{plan.description}</p>
              </div>

              <div className="mb-5">
                <span className="text-2xl font-bold text-slate-900">{plan.price}</span>
                {plan.duration && (
                  <span className="text-sm text-textMuted ml-1">{plan.duration}</span>
                )}
              </div>

              <ul className="space-y-2 flex-1 mb-6">
                {plan.features.map(f => (
                  <li key={f} className="flex items-start gap-2 text-sm text-slate-600">
                    <CheckIcon />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              <button
                onClick={plan.action}
                disabled={plan.disabled}
                className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 ${
                  plan.highlight
                    ? 'bg-primary text-white hover:bg-primary/90 focus:ring-primary disabled:opacity-60'
                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200 focus:ring-slate-300'
                }`}
              >
                {plan.cta}
              </button>
            </div>
          ))}
        </div>

        <p className="mt-8 text-xs text-slate-400 text-center">
          Questions? Email us at{' '}
          <a href="mailto:sales@ai970.cloud" className="text-primary hover:underline">
            sales@ai970.cloud
          </a>
        </p>
      </div>
    </div>
  );
};
