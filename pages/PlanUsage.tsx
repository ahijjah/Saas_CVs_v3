import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, PlanFeature } from '../types';
import { usePageTitle } from '../context/PageTitleContext';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

interface UsageData {
  plan_code: string;
  plan_name: string;
  subscription_status: string;
  pending_plan?: string | null;
  tenant_type?: string;
  trial_end_at: string | null;
  subscription_started_at: string | null;
  subscription_ends_at: string | null;
  limits: { max_campaigns: number; max_users: number; max_processed_cvs_per_month: number };
  usage: { active_campaigns: number; active_users: number; processed_cvs_this_month: number };
  percentage_used: { campaigns: number; users: number; cvs: number };
  features: PlanFeature[];
}

interface PublicPlan {
  plan_id: string;
  plan_code: string;
  plan_name: string;
  description: string | null;
  monthly_price: number;
  yearly_price: number;
  trial_days: number;
  features: PlanFeature[];
}

const T = {
  en: {
    title: 'Plan & Usage',
    subtitle: 'Your current subscription and resource usage',
    currentPlan: 'Current Plan',
    usageThisMonth: 'Usage This Month',
    availablePlans: 'Available Plans',
    featureComparison: 'Feature Comparison',
    billing: 'Billing & Invoices',
    billingPlaceholder: 'Payment integration coming soon',
    billingNote: 'Invoice history and payment management will be available here.',
    cvsProcessed: 'CVs Processed',
    activeCampaigns: 'Active Campaigns',
    teamMembers: 'Team Members',
    retentionPeriod: 'CV Retention',
    ofLimit: 'of',
    subscribe: 'Subscribe',
    subscribing: 'Subscribing...',
    currentPlanLabel: 'Current',
    contactUs: 'Contact Us',
    startTrial: 'Start Trial',
    trialDays: 'day trial',
    trialEnds: 'Trial ends',
    subSince: 'Active since',
    subEnds: 'Renews',
    adminOnly: 'Only tenant admins can change the subscription plan.',
    confirmSubscribe: 'Switch to the {plan} plan?',
    subscribeSuccess: 'Plan updated successfully',
    errorLoad: 'Failed to load usage data',
    errorSubscribe: 'Failed to update plan',
    statusLabels: {
      trial: 'Trial', active: 'Active', suspended: 'Suspended', expired: 'Expired',
    } as Record<string, string>,
    showFeatures: 'Show Feature Comparison',
    hideFeatures: 'Hide Feature Comparison',
    month: '/mo',
    free: 'Free',
    enterprise: 'Enterprise / Custom Plan',
    enterpriseDesc: 'For agencies and multi-unit organizations. Custom limits, dedicated onboarding, and SLA.',
    everythingInPro: 'Everything in Professional',
    customLimits: 'Custom limits & integrations',
    dedicatedSupport: 'Dedicated onboarding & SLA',
  },
  ar: {
    title: 'الخطة والاستخدام',
    subtitle: 'اشتراكك الحالي وبيانات الاستخدام',
    currentPlan: 'الخطة الحالية',
    usageThisMonth: 'الاستخدام هذا الشهر',
    availablePlans: 'الخطط المتاحة',
    featureComparison: 'مقارنة الميزات',
    billing: 'الفواتير والدفع',
    billingPlaceholder: 'تكامل الدفع قريباً',
    billingNote: 'سيكون سجل الفواتير وإدارة المدفوعات متاحاً هنا.',
    cvsProcessed: 'سير ذاتية محللة',
    activeCampaigns: 'حملات نشطة',
    teamMembers: 'أعضاء الفريق',
    retentionPeriod: 'مدة الاحتفاظ بالسيرة',
    ofLimit: 'من',
    subscribe: 'اشترك',
    subscribing: 'جاري الاشتراك...',
    currentPlanLabel: 'الحالية',
    contactUs: 'تواصل معنا',
    startTrial: 'ابدأ التجربة',
    trialDays: 'يوم تجريبي',
    trialEnds: 'تنتهي التجربة',
    subSince: 'نشط منذ',
    subEnds: 'يتجدد',
    adminOnly: 'فقط مسؤول المستأجر يمكنه تغيير خطة الاشتراك.',
    confirmSubscribe: 'التحويل إلى خطة {plan}؟',
    subscribeSuccess: 'تم تحديث الخطة',
    errorLoad: 'فشل تحميل بيانات الاستخدام',
    errorSubscribe: 'فشل تحديث الخطة',
    statusLabels: {
      trial: 'تجريبي', active: 'نشط', suspended: 'معلق', expired: 'منتهي',
    } as Record<string, string>,
    showFeatures: 'عرض مقارنة الميزات',
    hideFeatures: 'إخفاء مقارنة الميزات',
    month: '/شهر',
    free: 'مجاني',
    enterprise: 'المؤسسات / خطة مخصصة',
    enterpriseDesc: 'للوكالات والمنظمات متعددة الوحدات. حدود مخصصة وتأهيل مخصص واتفاقية مستوى الخدمة.',
    everythingInPro: 'كل ما في الخطة الاحترافية',
    customLimits: 'حدود وتكاملات مخصصة',
    dedicatedSupport: 'تأهيل مخصص واتفاقية مستوى خدمة',
  },
};

const PLAN_NAME_AR: Record<string, string> = {
  trial:        'التجربة المجانية',
  starter:      'البداية',
  professional: 'الاحترافي',
  enterprise:   'المؤسسات',
};
function localPlanName(code: string, name: string, lang: string): string {
  return lang === 'ar' ? (PLAN_NAME_AR[code] ?? name) : name;
}

const STATUS_COLORS: Record<string, string> = {
  trial:     'bg-amber-100 text-amber-700',
  active:    'bg-emerald-100 text-emerald-700',
  suspended: 'bg-red-100 text-red-700',
  expired:   'bg-slate-100 text-slate-500',
};

const PLAN_COLORS = [
  'from-slate-400 to-slate-500',
  'from-sky-500 to-blue-600',
  'from-violet-500 to-purple-600',
  'from-amber-500 to-orange-600',
];

function UsageBar({ label, used, limit, pct, color }: {
  label: string; used: number; limit: number; pct: number; color: string;
}) {
  const pctClamped = Math.min(pct, 100);
  const isWarning = pctClamped >= 80;
  return (
    <div className="bg-white border border-border rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-black text-textMuted uppercase tracking-widest">{label}</span>
        <span className={`text-xs font-bold ${isWarning ? 'text-red-500' : 'text-textMuted'}`}>
          {pctClamped.toFixed(0)}%
        </span>
      </div>
      <div className="flex items-end gap-1 mb-3">
        <span className="text-2xl font-black text-textMain">{used.toLocaleString()}</span>
        <span className="text-sm text-textMuted mb-0.5">/ {limit.toLocaleString()}</span>
      </div>
      <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${isWarning ? 'bg-red-500' : color}`}
          style={{ width: `${pctClamped}%` }}
        />
      </div>
    </div>
  );
}

function featureVal(feat: PlanFeature): string {
  if (feat.value_type === 'boolean') return feat.value_boolean ? '✓' : '✗';
  if (feat.value_type === 'number')  return feat.value_number !== null ? String(feat.value_number) : '—';
  return feat.value_text ?? '—';
}

function featureValClass(feat: PlanFeature): string {
  if (feat.value_type === 'boolean')
    return feat.value_boolean ? 'text-emerald-600 font-black' : 'text-slate-400';
  return 'text-textMain font-semibold';
}

export const PlanUsagePage: React.FC<Props> = ({ auth, addToast }) => {
  const lang = (document.documentElement.lang as 'en' | 'ar') === 'ar' ? 'ar' : 'en';
  const t = T[lang];
  const { setPageTitle } = usePageTitle();

  const role = auth.user?.role?.toLowerCase() ?? '';
  const isAdmin = role === 'admin';

  // All hooks must run unconditionally — access guard renders after them
  const [usage, setUsage]             = useState<UsageData | null>(null);
  const [publicPlans, setPublicPlans] = useState<PublicPlan[]>([]);
  const [loading, setLoading]         = useState(true);
  const [subscribing, setSubscribing] = useState<string | null>(null);
  const [showFeatures, setShowFeatures] = useState(false);

  useEffect(() => {
    setPageTitle(t.title);
    return () => { setPageTitle(null); };
  }, [setPageTitle, t.title]);

  const loadAll = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    try {
      const [usageData, dashboardData, plansData] = await Promise.all([
        apiService.get(WEBHOOK_CONFIG.TENANT_USAGE_URL, {}, auth.token!),
        apiService.get(WEBHOOK_CONFIG.DASHBOARD_SUMMARY_URL, {}, auth.token!).catch(() => null),
        fetch(WEBHOOK_CONFIG.SUBSCRIPTION_PUBLIC_URL).then((r) => r.json()).catch(() => ({ plans: [] })),
      ]);
      // Use active_campaigns from dashboard summary (more reliable) if available
      if (dashboardData?.kpis?.active_campaigns !== undefined) {
        usageData.usage.active_campaigns = dashboardData.kpis.active_campaigns;
      }
      setUsage(usageData);
      setPublicPlans(plansData.plans || []);
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, auth.token, t.errorLoad]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Hard block: only tenant admin may access this page
  if (!isAdmin) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center gap-4">
        <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
          <svg className="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
        </div>
        <h2 className="text-xl font-bold text-textMain">{lang === 'ar' ? 'غير مصرح' : 'Access Denied'}</h2>
        <p className="text-textSecondary max-w-sm">
          {lang === 'ar'
            ? 'هذه الصفحة مخصصة لمسؤولي المستأجرين فقط.'
            : 'This page is accessible to tenant administrators only.'}
        </p>
      </div>
    );
  }

  const handleSubscribe = async (planCode: string, planName: string) => {
    if (!isAdmin) return;
    if (!window.confirm(t.confirmSubscribe.replace('{plan}', planName))) return;
    setSubscribing(planCode);
    try {
      await apiService.post(WEBHOOK_CONFIG.SUBSCRIPTION_SUBSCRIBE_URL, { plan_code: planCode }, auth.token!);
      addToast(t.subscribeSuccess, 'success');
      await loadAll();
    } catch (err: any) {
      addToast(err.message || t.errorSubscribe, 'error');
    } finally {
      setSubscribing(null);
    }
  };

  // Collect all unique features across all public plans for comparison table
  const allFeatureKeys = (() => {
    const seen = new Map<string, { name: string; order: number }>();
    for (const plan of publicPlans) {
      for (const f of plan.features) {
        if (!seen.has(f.feature_key))
          seen.set(f.feature_key, { name: f.feature_name, order: f.display_order });
      }
    }
    return [...seen.entries()].sort((a, b) => a[1].order - b[1].order);
  })();

  const currentRetention = usage?.features?.find((f) => f.feature_key === 'cv_retention_period');

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-violet-600" />
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading...</p>
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-5xl">
      {/* ── Header ── */}
      <div>
        <h2 className="text-xl font-black text-textMain">{t.title}</h2>
        <p className="text-xs text-textMuted mt-0.5">{t.subtitle}</p>
      </div>

      {/* ── Current Plan Banner ── */}
      {usage && (
        <div className="bg-white border border-border rounded-2xl p-6 flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="flex-1">
            <div className="flex items-center gap-3 flex-wrap">
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.currentPlan}</p>
              <span className={`text-xs font-black px-2.5 py-0.5 rounded-full uppercase ${STATUS_COLORS[usage.subscription_status] || STATUS_COLORS.active}`}>
                {t.statusLabels[usage.subscription_status] || usage.subscription_status}
              </span>
            </div>
            <h3 className="text-2xl font-black text-textMain mt-1 capitalize">{localPlanName(usage.plan_code, usage.plan_name || usage.plan_code, lang)}</h3>
            <div className="flex flex-wrap gap-4 mt-2 text-xs text-textMuted">
              {usage.subscription_status === 'trial' && usage.trial_end_at && (
                <span>{t.trialEnds}: <strong className="text-amber-600">{new Date(usage.trial_end_at).toLocaleDateString()}</strong></span>
              )}
              {usage.subscription_started_at && (
                <span>{t.subSince}: {new Date(usage.subscription_started_at).toLocaleDateString()}</span>
              )}
              {usage.subscription_ends_at && (
                <span>{t.subEnds}: {new Date(usage.subscription_ends_at).toLocaleDateString()}</span>
              )}
              {currentRetention && (
                <span>{t.retentionPeriod}: <strong className="text-textMain">{currentRetention.value_text}</strong></span>
              )}
            </div>
          </div>
          {!isAdmin && (
            <p className="text-xs text-textMuted italic max-w-xs">{t.adminOnly}</p>
          )}
        </div>
      )}

      {/* ── Usage This Month ── */}
      {usage && (
        <div>
          <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.usageThisMonth}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <UsageBar
              label={t.activeCampaigns}
              used={usage.usage.active_campaigns}
              limit={usage.limits.max_campaigns}
              pct={usage.percentage_used.campaigns}
              color="bg-sky-500"
            />
            <UsageBar
              label={t.teamMembers}
              used={usage.usage.active_users}
              limit={usage.limits.max_users}
              pct={usage.percentage_used.users}
              color="bg-emerald-500"
            />
          </div>
        </div>
      )}

      {/* ── Available Plans ── */}
      <div>
        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">{t.availablePlans}</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {/* Dynamic plans from API (Trial / Starter / Professional) */}
          {publicPlans.filter(p => p.plan_code !== 'enterprise').map((plan, idx) => {
            const isCurrent = usage?.plan_code === plan.plan_code;
            const gradient = PLAN_COLORS[idx % (PLAN_COLORS.length - 1) + 0];
            const isHighlight = plan.plan_code === 'professional';
            return (
              <div
                key={plan.plan_id}
                className={`relative rounded-2xl border overflow-hidden flex flex-col transition-all ${
                  isHighlight
                    ? 'border-violet-400 shadow-xl shadow-violet-100'
                    : 'border-border hover:border-violet-200 hover:shadow-md'
                }`}
              >
                {isHighlight && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-violet-600 text-white text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-full whitespace-nowrap">
                    Most Popular
                  </div>
                )}
                <div className={`bg-gradient-to-br ${gradient} ${isHighlight ? 'pt-8 px-5 pb-5' : 'p-5'} text-white`}>
                  <div className="font-black text-sm uppercase tracking-widest opacity-80">{localPlanName(plan.plan_code, plan.plan_name, lang)}</div>
                  <div className="text-3xl font-black mt-1">
                    {plan.monthly_price === 0 ? t.free : `$${plan.monthly_price}`}
                    {plan.monthly_price > 0 && <span className="text-sm font-normal opacity-70">{t.month}</span>}
                  </div>
                  {plan.trial_days > 0 && plan.monthly_price === 0 && (
                    <div className="text-xs mt-1 opacity-80">{plan.trial_days} {t.trialDays}</div>
                  )}
                </div>
                <div className="p-4 flex-1 flex flex-col">
                  {plan.description && (
                    <p className="text-xs text-textMuted mb-4 leading-relaxed">{plan.description}</p>
                  )}
                  <div className="flex-1" />
                  {isCurrent ? (
                    <div className="w-full py-2 text-center text-xs font-black text-violet-600 bg-violet-50 rounded-xl border border-violet-200">
                      {t.currentPlanLabel}
                    </div>
                  ) : isAdmin ? (
                    <button
                      disabled={subscribing === plan.plan_code}
                      onClick={() => handleSubscribe(plan.plan_code, plan.plan_name)}
                      className="w-full py-2 text-xs font-bold text-white bg-violet-600 hover:bg-violet-700 rounded-xl disabled:opacity-50 transition-all"
                    >
                      {subscribing === plan.plan_code
                        ? t.subscribing
                        : plan.monthly_price === 0 ? t.startTrial : t.subscribe}
                    </button>
                  ) : (
                    <div className="w-full py-2 text-center text-xs text-textMuted bg-slate-50 rounded-xl border border-border">
                      {t.subscribe}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {/* Enterprise — always "Contact Us" */}
          <div className="relative rounded-2xl border border-border hover:border-amber-200 hover:shadow-md overflow-hidden flex flex-col transition-all">
            <div className="bg-gradient-to-br from-amber-500 to-orange-600 p-5 text-white">
              <div className="font-black text-sm uppercase tracking-widest opacity-80">{t.enterprise}</div>
              <div className="text-2xl font-black mt-1">Custom</div>
            </div>
            <div className="p-4 flex-1 flex flex-col">
              <p className="text-xs text-textMuted mb-4 leading-relaxed">{t.enterpriseDesc}</p>
              <ul className="space-y-1.5 mb-4 flex-1">
                {[t.everythingInPro, t.customLimits, t.dedicatedSupport].map((f) => (
                  <li key={f} className="flex items-start gap-2 text-xs text-textMuted">
                    <span className="text-amber-500 mt-0.5 shrink-0">✓</span>{f}
                  </li>
                ))}
              </ul>
              <a
                href="mailto:sales@cvanalyzer.ai"
                className="w-full py-2 text-xs font-bold text-white bg-amber-500 hover:bg-amber-600 rounded-xl text-center block transition-all"
              >
                {t.contactUs}
              </a>
            </div>
          </div>
        </div>
      </div>

      {/* ── Feature Comparison (toggle) ── */}
      {publicPlans.length > 0 && allFeatureKeys.length > 0 && (
        <div>
          <button
            onClick={() => setShowFeatures((v) => !v)}
            className="flex items-center gap-2 text-xs font-bold text-violet-600 hover:text-violet-700 transition-colors"
          >
            <svg className={`w-4 h-4 transition-transform ${showFeatures ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
            </svg>
            {showFeatures ? t.hideFeatures : t.showFeatures}
          </button>

          {showFeatures && (
            <div className="mt-4 bg-white border border-border rounded-2xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="border-b border-border bg-slate-50">
                      <th className="text-left px-5 py-3 text-xs font-black text-textMuted uppercase tracking-widest w-52">
                        {t.featureComparison}
                      </th>
                      {publicPlans.filter(p => p.plan_code !== 'enterprise').map((plan, idx) => (
                        <th key={plan.plan_id} className="px-4 py-3 text-center min-w-[120px]">
                          <div className={`inline-block px-3 py-1 rounded-lg text-xs font-black text-white bg-gradient-to-br ${PLAN_COLORS[idx % (PLAN_COLORS.length - 1)]}`}>
                            {localPlanName(plan.plan_code, plan.plan_name, lang)}
                          </div>
                          {usage?.plan_code === plan.plan_code && (
                            <div className="text-[9px] text-violet-600 font-black mt-0.5">{t.currentPlanLabel}</div>
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {allFeatureKeys.map(([key, meta], rowIdx) => (
                      <tr key={key} className={`border-b border-border last:border-0 ${rowIdx % 2 === 0 ? 'bg-white' : 'bg-slate-50/40'}`}>
                        <td className="px-5 py-2.5">
                          <span className="text-xs font-semibold text-textMain">{meta.name}</span>
                        </td>
                        {publicPlans.filter(p => p.plan_code !== 'enterprise').map((plan) => {
                          const feat = plan.features.find((f) => f.feature_key === key);
                          return (
                            <td key={plan.plan_id} className={`px-4 py-2.5 text-center text-xs ${feat ? featureValClass(feat) : 'text-textMuted'}`}>
                              {feat ? featureVal(feat) : '—'}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Billing Placeholder ── */}
      <div className="bg-white border border-border rounded-2xl p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-8 h-8 bg-slate-100 rounded-xl flex items-center justify-center">
            <svg className="w-4 h-4 text-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
            </svg>
          </div>
          <div>
            <p className="text-xs font-black text-textMuted uppercase tracking-widest">{t.billing}</p>
            <p className="text-sm font-bold text-textMain mt-0.5">{t.billingPlaceholder}</p>
          </div>
        </div>
        <p className="text-xs text-textMuted">{t.billingNote}</p>
        {/* Invoice list placeholder rows */}
        <div className="mt-4 space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-8 bg-slate-50 rounded-lg animate-pulse" />
          ))}
        </div>
      </div>
    </div>
  );
};
