import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, SubscriptionPlan, TenantSubscriptionRow, TenantUsage } from '../types';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const T = {
  en: {
    title: 'Tenant Subscriptions',
    subtitle: 'Assign plans, manage trials and monitor usage across all organizations',
    search: 'Search tenants...', allPlans: 'All Plans', allStatuses: 'All Statuses',
    tenant: 'Organization', plan: 'Plan', subscriptionStatus: 'Status',
    trialEnds: 'Trial Ends', subEnds: 'Sub. Ends', actions: 'Actions',
    usage: 'Usage', viewUsage: 'View Usage', manage: 'Manage',
    close: 'Close', save: 'Save Changes', saving: 'Saving...',
    assignPlan: 'Assign Plan', extendTrial: 'Extend Trial',
    suspend: 'Suspend', reactivate: 'Reactivate',
    trialEndDate: 'New Trial End Date',
    selectPlan: 'Select a plan...',
    confirmSuspend: 'Suspend this tenant subscription?',
    confirmReactivate: 'Reactivate this tenant subscription?',
    successAction: 'Subscription updated',
    errorLoad: 'Failed to load tenants', errorAction: 'Action failed',
    usageTitle: 'Usage & Limits',
    campaigns: 'Campaigns', cvs: 'CVs This Month', users: 'Active Users',
    used: 'used', limit: 'limit', pct: '%',
    noData: 'No tenants found',
    statusLabels: {
      trial: 'Trial', active: 'Active', suspended: 'Suspended', expired: 'Expired',
    } as Record<string, string>,
  },
  ar: {
    title: 'اشتراكات المستأجرين',
    subtitle: 'تعيين الخطط وإدارة الفترات التجريبية ومراقبة الاستخدام',
    search: 'بحث في المستأجرين...', allPlans: 'كل الخطط', allStatuses: 'كل الحالات',
    tenant: 'المنظمة', plan: 'الخطة', subscriptionStatus: 'الحالة',
    trialEnds: 'نهاية التجربة', subEnds: 'نهاية الاشتراك', actions: 'إجراءات',
    usage: 'الاستخدام', viewUsage: 'عرض الاستخدام', manage: 'إدارة',
    close: 'إغلاق', save: 'حفظ التغييرات', saving: 'جاري الحفظ...',
    assignPlan: 'تعيين خطة', extendTrial: 'تمديد التجربة',
    suspend: 'تعليق', reactivate: 'إعادة تفعيل',
    trialEndDate: 'تاريخ انتهاء التجربة الجديد',
    selectPlan: 'اختر خطة...',
    confirmSuspend: 'هل تريد تعليق اشتراك هذا المستأجر؟',
    confirmReactivate: 'هل تريد إعادة تفعيل اشتراك هذا المستأجر؟',
    successAction: 'تم تحديث الاشتراك',
    errorLoad: 'فشل تحميل المستأجرين', errorAction: 'فشل الإجراء',
    usageTitle: 'الاستخدام والحدود',
    campaigns: 'الحملات', cvs: 'السير هذا الشهر', users: 'المستخدمون النشطون',
    used: 'مستخدم', limit: 'الحد', pct: '%',
    noData: 'لا توجد مستأجرون',
    statusLabels: {
      trial: 'تجريبي', active: 'نشط', suspended: 'معلق', expired: 'منتهي',
    } as Record<string, string>,
  },
};

const STATUS_COLORS: Record<string, string> = {
  trial:     'bg-amber-100 text-amber-700',
  active:    'bg-green-100 text-green-700',
  suspended: 'bg-red-100 text-red-700',
  expired:   'bg-slate-100 text-slate-500',
};

function UsageBar({ label, used, limit, pct }: { label: string; used: number; limit: number; pct: number }) {
  const color = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-500' : 'bg-green-500';
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="font-bold text-textMain">{label}</span>
        <span className="text-textMuted">{used.toLocaleString()} / {limit.toLocaleString()}</span>
      </div>
      <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
        <div className={`h-2 rounded-full transition-all ${color}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <p className="text-[10px] text-textMuted text-right">{pct}%</p>
    </div>
  );
}

export const TenantSubscriptionsPage: React.FC<Props> = ({ auth, addToast }) => {
  const lang = (document.documentElement.lang as 'en' | 'ar') === 'ar' ? 'ar' : 'en';
  const t = T[lang];

  const [tenants, setTenants] = useState<TenantSubscriptionRow[]>([]);
  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [planFilter, setPlanFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  // Manage modal
  const [manageTarget, setManageTarget] = useState<TenantSubscriptionRow | null>(null);
  const [selectedPlan, setSelectedPlan] = useState('');
  const [trialEndAt, setTrialEndAt] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  // Usage modal
  const [usageTarget, setUsageTarget] = useState<TenantSubscriptionRow | null>(null);
  const [usageData, setUsageData] = useState<TenantUsage | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.ADMIN_TENANTS_URL, { limit: '200' }, auth.token!);
      setTenants(data.tenants || []);
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, t.errorLoad]);

  const loadPlans = useCallback(async () => {
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL, {}, auth.token!);
      setPlans((data.plans || []).filter((p: SubscriptionPlan) => p.status === 'active'));
    } catch { /* non-critical */ }
  }, [auth.token]);

  useEffect(() => {
    loadTenants();
    loadPlans();
  }, [loadTenants, loadPlans]);

  const openManage = (tenant: TenantSubscriptionRow) => {
    setManageTarget(tenant);
    setSelectedPlan(tenant.plan || '');
    setTrialEndAt(tenant.trial_end_at ? tenant.trial_end_at.slice(0, 10) : '');
  };

  const openUsage = async (tenant: TenantSubscriptionRow) => {
    setUsageTarget(tenant);
    setUsageData(null);
    setUsageLoading(true);
    try {
      const data = await apiService.get(
        `${WEBHOOK_CONFIG.ADMIN_TENANTS_URL}/${tenant.tenant_id}/usage`,
        {},
        auth.token!,
      );
      setUsageData(data);
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setUsageLoading(false);
    }
  };

  const runAction = async (action: string, extra: Record<string, string> = {}) => {
    if (!manageTarget) return;
    if (action === 'suspend' && !window.confirm(t.confirmSuspend)) return;
    if (action === 'reactivate' && !window.confirm(t.confirmReactivate)) return;

    setActionLoading(true);
    try {
      await apiService.patch(
        `${WEBHOOK_CONFIG.ADMIN_TENANTS_URL}/${manageTarget.tenant_id}/subscription`,
        { action, ...extra },
        auth.token!,
      );
      addToast(t.successAction, 'success');
      setManageTarget(null);
      await loadTenants();
    } catch (err: any) {
      addToast(err.message || t.errorAction, 'error');
    } finally {
      setActionLoading(false);
    }
  };

  const filtered = tenants.filter((t_) => {
    const q = search.toLowerCase();
    const matchSearch = !q || (t_.tenant_name || '').toLowerCase().includes(q);
    const matchPlan = planFilter === 'all' || t_.plan === planFilter;
    const matchStatus = statusFilter === 'all' || t_.subscription_status === statusFilter;
    return matchSearch && matchPlan && matchStatus;
  });

  const uniquePlans = [...new Set(tenants.map((t_) => t_.plan).filter(Boolean))];

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-rose-500" />
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-black text-textMain">{t.title}</h2>
        <p className="text-xs text-textMuted mt-0.5">{t.subtitle}</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {(['trial', 'active', 'suspended', 'expired'] as const).map((s) => {
          const count = tenants.filter((t_) => t_.subscription_status === s).length;
          return (
            <div key={s}
              className="bg-white rounded-2xl border border-border p-4 cursor-pointer hover:border-rose-200 transition-all"
              onClick={() => setStatusFilter(statusFilter === s ? 'all' : s)}
            >
              <div className={`text-2xl font-black ${count > 0 ? 'text-textMain' : 'text-textMuted'}`}>{count}</div>
              <span className={`mt-1 inline-block px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[s]}`}>
                {t.statusLabels[s]}
              </span>
            </div>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="text" value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t.search}
          className="flex-1 px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
        />
        <select value={planFilter} onChange={(e) => setPlanFilter(e.target.value)}
          className="px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none bg-white">
          <option value="all">{t.allPlans}</option>
          {uniquePlans.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          className="px-4 py-2.5 text-sm border border-border rounded-xl focus:outline-none bg-white">
          <option value="all">{t.allStatuses}</option>
          {['trial', 'active', 'suspended', 'expired'].map((s) => (
            <option key={s} value={s}>{t.statusLabels[s]}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[900px]">
            <thead className="bg-slate-50 border-b border-border">
              <tr>
                {[t.tenant, t.plan, t.subscriptionStatus, t.trialEnds, t.subEnds, t.actions].map((h) => (
                  <th key={h} className="px-5 py-3.5 text-[10px] font-black text-textMuted uppercase tracking-widest">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-sm text-textMuted">{t.noData}</td>
                </tr>
              )}
              {filtered.map((row) => (
                <tr key={row.tenant_id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="font-bold text-sm text-textMain">{row.tenant_name}</div>
                    <div className="text-[10px] text-textMuted">{row.email_domain || ''}</div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="text-xs font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-lg capitalize">
                      {row.plan || '—'}
                    </span>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[row.subscription_status || 'trial']}`}>
                      {t.statusLabels[row.subscription_status || 'trial'] || row.subscription_status || '—'}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-xs text-textMuted">
                    {row.trial_end_at ? new Date(row.trial_end_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-5 py-3.5 text-xs text-textMuted">
                    {row.subscription_ends_at ? new Date(row.subscription_ends_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => openUsage(row)}
                        className="text-xs font-bold px-3 py-1.5 border border-slate-200 text-textMuted rounded-lg hover:bg-slate-50 transition-all"
                      >
                        📊 {t.viewUsage}
                      </button>
                      <button
                        onClick={() => openManage(row)}
                        className="text-xs font-bold px-3 py-1.5 border border-rose-200 text-rose-600 rounded-lg hover:bg-rose-50 transition-all"
                      >
                        {t.manage}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Manage Modal */}
      {manageTarget && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <div>
                <h3 className="font-black text-textMain text-sm uppercase tracking-widest">{t.manage}</h3>
                <p className="text-xs text-textMuted mt-0.5">{manageTarget.tenant_name}</p>
              </div>
              <button onClick={() => setManageTarget(null)} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="p-6 space-y-5">
              {/* Current status */}
              <div className="flex items-center justify-between bg-slate-50 rounded-xl px-4 py-3">
                <span className="text-xs font-bold text-textMuted uppercase tracking-widest">Current Plan</span>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-black text-textMain capitalize">{manageTarget.plan || '—'}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[manageTarget.subscription_status || 'trial']}`}>
                    {t.statusLabels[manageTarget.subscription_status || 'trial']}
                  </span>
                </div>
              </div>

              {/* Assign plan */}
              <div>
                <p className="text-xs font-black text-textMuted uppercase tracking-widest mb-2">{t.assignPlan}</p>
                <div className="flex gap-2">
                  <select
                    value={selectedPlan}
                    onChange={(e) => setSelectedPlan(e.target.value)}
                    className="flex-1 px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200 bg-white"
                  >
                    <option value="">{t.selectPlan}</option>
                    {plans.map((p) => (
                      <option key={p.plan_id} value={p.plan_code}>{p.plan_name}</option>
                    ))}
                  </select>
                  <button
                    disabled={!selectedPlan || actionLoading}
                    onClick={() => runAction('assign_plan', { plan_code: selectedPlan })}
                    className="px-4 py-2.5 text-sm font-bold bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 disabled:opacity-50 whitespace-nowrap"
                  >
                    {actionLoading ? '...' : t.assignPlan}
                  </button>
                </div>
              </div>

              {/* Extend trial */}
              <div>
                <p className="text-xs font-black text-textMuted uppercase tracking-widest mb-2">{t.extendTrial}</p>
                <div className="flex gap-2">
                  <input
                    type="date"
                    value={trialEndAt}
                    onChange={(e) => setTrialEndAt(e.target.value)}
                    className="flex-1 px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                  />
                  <button
                    disabled={!trialEndAt || actionLoading}
                    onClick={() => runAction('extend_trial', { trial_end_at: trialEndAt })}
                    className="px-4 py-2.5 text-sm font-bold bg-amber-500 text-white rounded-xl hover:bg-amber-600 disabled:opacity-50 whitespace-nowrap"
                  >
                    {actionLoading ? '...' : t.extendTrial}
                  </button>
                </div>
              </div>

              {/* Suspend / Reactivate */}
              <div className="flex gap-3 pt-2 border-t border-border">
                {manageTarget.subscription_status !== 'suspended' ? (
                  <button
                    disabled={actionLoading}
                    onClick={() => runAction('suspend')}
                    className="flex-1 py-2.5 text-sm font-bold border border-red-200 text-red-600 rounded-xl hover:bg-red-50 disabled:opacity-50"
                  >
                    🚫 {t.suspend}
                  </button>
                ) : (
                  <button
                    disabled={actionLoading}
                    onClick={() => runAction('reactivate')}
                    className="flex-1 py-2.5 text-sm font-bold border border-green-200 text-green-600 rounded-xl hover:bg-green-50 disabled:opacity-50"
                  >
                    ✅ {t.reactivate}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Usage Modal */}
      {usageTarget && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <div>
                <h3 className="font-black text-textMain text-sm uppercase tracking-widest">{t.usageTitle}</h3>
                <p className="text-xs text-textMuted mt-0.5">{usageTarget.tenant_name}</p>
              </div>
              <button onClick={() => setUsageTarget(null)} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="p-6">
              {usageLoading ? (
                <div className="flex items-center justify-center py-10">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-rose-500" />
                </div>
              ) : usageData ? (
                <div className="space-y-5">
                  {/* Plan badge */}
                  <div className="flex items-center justify-between bg-slate-50 rounded-xl px-4 py-3">
                    <span className="text-xs font-bold text-textMuted uppercase tracking-widest">Plan</span>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-black text-textMain capitalize">{usageData.plan}</span>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[usageData.subscription_status || 'active']}`}>
                        {t.statusLabels[usageData.subscription_status || 'active']}
                      </span>
                    </div>
                  </div>

                  {/* Usage bars */}
                  <UsageBar
                    label={t.campaigns}
                    used={usageData.usage.active_campaigns}
                    limit={usageData.limits.max_campaigns}
                    pct={usageData.percentage_used.campaigns}
                  />
                  <UsageBar
                    label={t.cvs}
                    used={usageData.usage.processed_cvs_this_month}
                    limit={usageData.limits.max_processed_cvs_per_month}
                    pct={usageData.percentage_used.cvs}
                  />
                  <UsageBar
                    label={t.users}
                    used={usageData.usage.active_users}
                    limit={usageData.limits.max_users}
                    pct={usageData.percentage_used.users}
                  />

                  {/* Features */}
                  <div className="pt-2 border-t border-border">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">Features</p>
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(usageData.plan_features).map(([feat, enabled]) => (
                        <div key={feat} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium ${enabled ? 'bg-green-50 text-green-700' : 'bg-slate-50 text-slate-400'}`}>
                          <span>{enabled ? '✓' : '×'}</span>
                          <span className="capitalize">{feat.replace(/_/g, ' ')}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <p className="text-center text-sm text-textMuted py-6">No usage data available</p>
              )}
            </div>

            <div className="px-6 py-4 border-t border-border flex justify-end">
              <button
                onClick={() => setUsageTarget(null)}
                className="px-5 py-2 text-sm font-bold border border-border text-textMuted rounded-xl hover:bg-slate-50"
              >
                {t.close}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
