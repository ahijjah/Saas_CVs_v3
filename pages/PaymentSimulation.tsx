
import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, User, UserProfile } from '../types';
import { ToastType } from '../components/Toast';
import { useLanguage } from '../context/LanguageContext';

interface PaymentSimulationProps {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
  onUserUpdate: (patch: Partial<User>) => void;
}

type BillingCycle = 'monthly' | 'yearly';

const T = {
  en: {
    title: 'Complete your payment',
    testingBadge: 'Testing Mode',
    testingWarning: 'No real payment will be processed.',
    planSummary: 'Plan Summary',
    billing: 'Billing cycle',
    monthly: 'Monthly',
    yearly: 'Yearly',
    amount: 'Amount',
    users: 'Team members',
    jobs: 'Active campaigns',
    clients: 'Client organisations',
    branding: 'Branding',
    apiAccess: 'API access',
    paySuccess: 'Payment Successful',
    payFailed: 'Payment Failed / Cancel',
    processing: 'Processing…',
    backToPlan: 'Back to plan selection',
    invalidPlan: 'Invalid plan. Redirecting…',
    loadingPlan: 'Loading your plan…',
    langBtn: 'عربي',
  },
  ar: {
    title: 'أكمل عملية الدفع',
    testingBadge: 'وضع الاختبار',
    testingWarning: 'لن تُعالج أي مدفوعات حقيقية.',
    planSummary: 'ملخص الخطة',
    billing: 'دورة الفوترة',
    monthly: 'شهري',
    yearly: 'سنوي',
    amount: 'المبلغ',
    users: 'أعضاء الفريق',
    jobs: 'حملات التوظيف الفعالة',
    clients: 'منظمات العملاء',
    branding: 'العلامة التجارية',
    apiAccess: 'وصول API',
    paySuccess: 'نجاح الدفع',
    payFailed: 'فشل / إلغاء الدفع',
    processing: 'جارٍ المعالجة…',
    backToPlan: 'العودة لاختيار الخطة',
    invalidPlan: 'خطة غير صالحة. جارٍ إعادة التوجيه…',
    loadingPlan: 'جارٍ تحميل خطتك…',
    langBtn: 'English',
  },
};

interface PlanMeta {
  name: string;
  limits: { users: string; jobs: string; clients: string };
  branding: string;
  apiAccess: boolean;
  monthlyPrice: number;
  yearlyPrice: number;
}

const PLAN_META: Record<string, PlanMeta> = {
  starter: {
    name: 'Starter',
    limits: { users: '3', jobs: '10', clients: '1' },
    branding: 'None',
    apiAccess: false,
    monthlyPrice: 29,
    yearlyPrice: 290,
  },
  professional: {
    name: 'Professional',
    limits: { users: '10', jobs: '50', clients: '20' },
    branding: 'Basic',
    apiAccess: false,
    monthlyPrice: 99,
    yearlyPrice: 990,
  },
};

const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

export const PaymentSimulationPage: React.FC<PaymentSimulationProps> = ({
  auth, addToast, onUserUpdate,
}) => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { lang, setLang, isAr } = useLanguage();
  const t = T[lang];

  const [plan, setPlan] = useState<string | null>(searchParams.get('plan'));
  const [billingCycle, setBillingCycle] = useState<BillingCycle>(
    searchParams.get('billing') === 'yearly' ? 'yearly' : 'monthly'
  );
  const [loadingAction, setLoadingAction] = useState<'success' | 'failed' | null>(null);

  // If no plan in URL or plan is invalid, fetch from profile
  useEffect(() => {
    if (plan && PLAN_META[plan]) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiService.get(WEBHOOK_CONFIG.GET_PROFILE_WEBHOOK_URL, {}, auth.token!);
        const profilePlan: string = (data?.profile as UserProfile | undefined)?.plan ?? '';
        if (!cancelled) {
          if (PLAN_META[profilePlan]) {
            setPlan(profilePlan);
          } else {
            addToast(t.invalidPlan, 'error');
            navigate('/plan-selection', { replace: true });
          }
        }
      } catch {
        if (!cancelled) navigate('/plan-selection', { replace: true });
      }
    })();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSimulate = async (result: 'success' | 'failed') => {
    if (!plan || !PLAN_META[plan]) return;
    setLoadingAction(result);
    try {
      const res = await apiService.post(
        WEBHOOK_CONFIG.SIMULATE_PAYMENT_URL,
        { plan, billing_cycle: billingCycle, result },
        auth.token!,
      );
      if (result === 'success') {
        onUserUpdate({ subscription_status: 'active' });
        addToast(res?.message || 'Payment successful!', 'success');
        navigate('/jobs', { replace: true });
      } else {
        addToast(res?.message || 'Payment failed or was cancelled.', 'error');
        navigate('/plan-selection', { replace: true });
      }
    } catch (err: any) {
      addToast(err.message || 'An error occurred. Please try again.', 'error');
    } finally {
      setLoadingAction(null);
    }
  };

  const meta = plan ? PLAN_META[plan] : null;
  const price = meta ? (billingCycle === 'yearly' ? meta.yearlyPrice : meta.monthlyPrice) : null;
  const pricePeriod = billingCycle === 'yearly' ? 'year' : 'month';

  return (
    <div className={`min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col${isAr ? ' direction-rtl' : ''}`}>
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
        <div className="flex items-center gap-3">
          {auth.user?.email && (
            <span className="text-sm text-textMuted hidden sm:block">{auth.user.email}</span>
          )}
          <button
            onClick={() => setLang(isAr ? 'en' : 'ar')}
            className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-lg border border-slate-200 hover:border-primary text-textMuted hover:text-primary transition-all"
          >
            <GlobeIcon />{t.langBtn}
          </button>
        </div>
      </div>

      <div className="flex-1 flex items-center justify-center px-4 py-12">
        {!meta ? (
          <p className="text-textMuted text-sm animate-pulse">{t.loadingPlan}</p>
        ) : (
          <div className="bg-white rounded-2xl shadow-lg border border-slate-200 w-full max-w-md overflow-hidden">
            {/* Warning banner */}
            <div className="bg-amber-50 border-b border-amber-200 px-5 py-3 flex items-start gap-2.5">
              <svg className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              </svg>
              <div>
                <span className="text-xs font-bold text-amber-700">{t.testingBadge} — </span>
                <span className="text-xs text-amber-600">{t.testingWarning}</span>
              </div>
            </div>

            <div className="p-6">
              <h1 className="text-lg font-bold text-slate-900 mb-1">{t.title}</h1>
              <p className="text-sm text-textMuted mb-5">{meta.name} plan</p>

              {/* Billing cycle toggle */}
              <div className="mb-5">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.billing}</p>
                <div className="flex gap-2">
                  {(['monthly', 'yearly'] as BillingCycle[]).map(cycle => (
                    <button
                      key={cycle}
                      onClick={() => setBillingCycle(cycle)}
                      className={`flex-1 py-2 rounded-xl text-sm font-medium border transition-all ${
                        billingCycle === cycle
                          ? 'border-primary bg-primary/5 text-primary'
                          : 'border-slate-200 text-textMuted hover:border-slate-300'
                      }`}
                    >
                      {cycle === 'monthly' ? t.monthly : t.yearly}
                      {cycle === 'yearly' && (
                        <span className="ml-1.5 text-[10px] font-bold text-green-600">–17%</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {/* Amount */}
              <div className="bg-primary/5 border border-primary/20 rounded-xl px-4 py-3 mb-5 flex items-center justify-between">
                <span className="text-sm font-medium text-slate-700">{t.amount}</span>
                <div className="text-right">
                  <span className="text-xl font-bold text-slate-900">${price}</span>
                  <span className="text-sm text-textMuted ml-1">USD / {pricePeriod}</span>
                </div>
              </div>

              {/* Plan limits summary */}
              <div className="bg-slate-50 rounded-xl p-4 mb-6">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.planSummary}</p>
                {[
                  { label: t.users,     value: meta.limits.users },
                  { label: t.jobs,      value: meta.limits.jobs },
                  { label: t.clients,   value: meta.limits.clients },
                  { label: t.branding,  value: meta.branding },
                  { label: t.apiAccess, value: meta.apiAccess ? '✓' : '✗' },
                ].map(row => (
                  <div key={row.label} className="flex justify-between py-1.5 text-sm border-b border-slate-100 last:border-0">
                    <span className="text-textMuted">{row.label}</span>
                    <span className="font-medium text-slate-700">{row.value}</span>
                  </div>
                ))}
              </div>

              {/* Action buttons */}
              <div className="space-y-3">
                <button
                  onClick={() => handleSimulate('success')}
                  disabled={loadingAction !== null}
                  className="w-full py-3 rounded-xl text-sm font-semibold bg-green-600 text-white hover:bg-green-700 transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-green-500 disabled:opacity-60"
                >
                  {loadingAction === 'success' ? t.processing : t.paySuccess}
                </button>
                <button
                  onClick={() => handleSimulate('failed')}
                  disabled={loadingAction !== null}
                  className="w-full py-3 rounded-xl text-sm font-semibold bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 transition-all focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-red-300 disabled:opacity-60"
                >
                  {loadingAction === 'failed' ? t.processing : t.payFailed}
                </button>
                <button
                  onClick={() => navigate('/plan-selection')}
                  disabled={loadingAction !== null}
                  className="w-full py-2 text-sm text-textMuted hover:text-primary transition-colors disabled:opacity-40"
                >
                  ← {t.backToPlan}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
