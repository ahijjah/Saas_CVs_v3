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
    addTenant: 'Add Tenant',
    search: 'Search tenants...', allPlans: 'All Plans', allStatuses: 'All Statuses',
    tenant: 'Organization', plan: 'Plan', subscriptionStatus: 'Status', tenantType: 'Type',
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
    successCreate: 'Tenant created successfully',
    errorLoad: 'Failed to load tenants', errorAction: 'Action failed',
    errorCreate: 'Failed to create tenant',
    usageTitle: 'Usage & Limits',
    campaigns: 'Campaigns', cvs: 'CVs This Month', users: 'Active Users',
    used: 'used', limit: 'limit', pct: '%',
    noData: 'No tenants found',
    statusLabels: {
      trial: 'Trial', active: 'Active', suspended: 'Suspended', expired: 'Expired',
      pending_plan_selection: 'Pending Plan', pending_payment: 'Pending Payment',
      pending_sales_contact: 'Pending Sales', grace: 'Grace', trial_expired: 'Trial Expired',
      cancelled: 'Cancelled', cancelled_pending_expiry: 'Cancelling',
    } as Record<string, string>,
    // Create Tenant modal
    createTitle: 'Create New Tenant',
    orgName: 'Organization Name',
    emailDomain: 'Email Domain',
    initialPlan: 'Initial Plan',
    maxUsers: 'Max Users',
    maxJobs: 'Max Campaigns',
    tenantTypeLabel: 'Tenant Type',
    tenantTypeOptions: {
      organization: 'Organization / Employer',
      agency: 'Recruitment Agency',
      individual_recruiter: 'Independent Recruiter',
    } as Record<string, string>,
    adminSection: 'First Admin User',
    adminFullName: 'Admin Full Name',
    adminEmail: 'Admin Email',
    adminPassword: 'Temporary Password',
    adminPasswordHint: 'Min. 8 characters. Ask the admin to change it on first login.',
    creating: 'Creating...',
    create: 'Create Tenant',
    cancel: 'Cancel',
  },
  ar: {
    title: 'اشتراكات المستأجرين',
    subtitle: 'تعيين الخطط وإدارة الفترات التجريبية ومراقبة الاستخدام',
    addTenant: 'إضافة مستأجر',
    search: 'بحث في المستأجرين...', allPlans: 'كل الخطط', allStatuses: 'كل الحالات',
    tenant: 'المنظمة', plan: 'الخطة', subscriptionStatus: 'الحالة', tenantType: 'النوع',
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
    successCreate: 'تم إنشاء المستأجر بنجاح',
    errorLoad: 'فشل تحميل المستأجرين', errorAction: 'فشل الإجراء',
    errorCreate: 'فشل إنشاء المستأجر',
    usageTitle: 'الاستخدام والحدود',
    campaigns: 'الحملات', cvs: 'السير هذا الشهر', users: 'المستخدمون النشطون',
    used: 'مستخدم', limit: 'الحد', pct: '%',
    noData: 'لا توجد مستأجرون',
    statusLabels: {
      trial: 'تجريبي', active: 'نشط', suspended: 'معلق', expired: 'منتهي',
      pending_plan_selection: 'في انتظار الخطة', pending_payment: 'في انتظار الدفع',
      pending_sales_contact: 'في انتظار المبيعات', grace: 'فترة السماح',
      trial_expired: 'انتهت التجربة', cancelled: 'ملغى', cancelled_pending_expiry: 'جاري الإلغاء',
    } as Record<string, string>,
    // Create Tenant modal
    createTitle: 'إنشاء مستأجر جديد',
    orgName: 'اسم المنظمة',
    emailDomain: 'نطاق البريد الإلكتروني',
    initialPlan: 'الخطة الأولية',
    maxUsers: 'الحد الأقصى للمستخدمين',
    maxJobs: 'الحد الأقصى للحملات',
    tenantTypeLabel: 'نوع المستأجر',
    tenantTypeOptions: {
      organization: 'منظمة / صاحب عمل',
      agency: 'وكالة توظيف',
      individual_recruiter: 'مسؤول توظيف مستقل',
    } as Record<string, string>,
    adminSection: 'المسؤول الأول',
    adminFullName: 'الاسم الكامل للمسؤول',
    adminEmail: 'البريد الإلكتروني للمسؤول',
    adminPassword: 'كلمة مرور مؤقتة',
    adminPasswordHint: 'الحد الأدنى 8 أحرف. اطلب من المسؤول تغييرها عند أول تسجيل دخول.',
    creating: 'جارٍ الإنشاء...',
    create: 'إنشاء المستأجر',
    cancel: 'إلغاء',
  },
};

const STATUS_COLORS: Record<string, string> = {
  trial:                  'bg-amber-100 text-amber-700',
  active:                 'bg-green-100 text-green-700',
  suspended:              'bg-red-100 text-red-700',
  expired:                'bg-slate-100 text-slate-500',
  pending_plan_selection: 'bg-blue-100 text-blue-700',
  pending_payment:        'bg-orange-100 text-orange-700',
  pending_sales_contact:  'bg-purple-100 text-purple-700',
  grace:                  'bg-rose-100 text-rose-700',
  trial_expired:          'bg-slate-100 text-slate-500',
  cancelled:              'bg-slate-100 text-slate-500',
  cancelled_pending_expiry: 'bg-amber-50 text-amber-600',
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

  // Create Tenant modal
  const [showCreate, setShowCreate] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '',
    email_domain: '',
    plan: 'starter',
    max_users: '3',
    max_jobs: '10',
    max_clients: '1',
    api_access_enabled: false,
    branding_level: 'none' as 'none' | 'basic' | 'white_label',
    tenant_type: 'organization' as 'organization' | 'agency' | 'individual_recruiter',
    admin_full_name: '',
    admin_email: '',
    admin_password: '',
  });
  const [showAdminPw, setShowAdminPw] = useState(false);

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
    setSelectedPlan(tenant.plan || tenant.pending_plan || '');
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

  const resetCreateForm = () => setCreateForm({
    name: '', email_domain: '', plan: 'starter', max_users: '3', max_jobs: '10',
    max_clients: '1', api_access_enabled: false, branding_level: 'none',
    tenant_type: 'organization', admin_full_name: '', admin_email: '', admin_password: '',
  });

  const handleCreateTenant = async (e: React.FormEvent) => {
    e.preventDefault();
    // Validate admin fields are all-or-nothing
    const adminName = createForm.admin_full_name.trim();
    const adminEmail = createForm.admin_email.trim();
    const adminPw = createForm.admin_password;
    const hasAny = !!(adminName || adminEmail || adminPw);
    const hasAll = !!(adminName && adminEmail && adminPw);
    if (hasAny && !hasAll) {
      addToast('Provide Admin Name, Email, and Password together — or leave all empty.', 'error');
      return;
    }
    if (adminPw && adminPw.length < 8) {
      addToast('Admin password must be at least 8 characters.', 'error');
      return;
    }

    setCreateLoading(true);
    try {
      const payload: Record<string, string | number | boolean | null> = {
        name: createForm.name.trim(),
        email_domain: createForm.email_domain.trim(),
        plan: createForm.plan || 'starter',
        max_users: parseInt(createForm.max_users, 10) || 3,
        max_jobs: parseInt(createForm.max_jobs, 10) || 10,
        max_clients: createForm.max_clients === '' ? null : (parseInt(createForm.max_clients, 10) || 1),
        api_access_enabled: createForm.api_access_enabled,
        branding_level: createForm.branding_level,
        tenant_type: createForm.tenant_type,
      };
      if (hasAll) {
        payload.admin_full_name = adminName;
        payload.admin_email = adminEmail;
        payload.admin_password = adminPw;
      }
      await apiService.post(WEBHOOK_CONFIG.ADMIN_CREATE_TENANT_URL, payload, auth.token!);
      addToast(t.successCreate, 'success');
      setShowCreate(false);
      resetCreateForm();
      await loadTenants();
    } catch (err: any) {
      addToast(err.message || t.errorCreate, 'error');
    } finally {
      setCreateLoading(false);
    }
  };

  const filtered = tenants.filter((t_) => {
    const q = search.toLowerCase();
    const matchSearch = !q || (t_.tenant_name || '').toLowerCase().includes(q);
    const matchPlan = planFilter === 'all' || t_.plan === planFilter;
    const matchStatus = statusFilter === 'all' || t_.subscription_status === statusFilter;
    return matchSearch && matchPlan && matchStatus;
  });

  const uniquePlans = [...new Set(tenants.flatMap((t_) => [t_.plan, t_.pending_plan]).filter(Boolean))] as string[];

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
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-textMain">{t.title}</h2>
          <p className="text-xs text-textMuted mt-0.5">{t.subtitle}</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="shrink-0 flex items-center gap-2 px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white text-sm font-bold rounded-xl shadow-sm transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          {t.addTenant}
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
        {(['trial', 'active', 'pending_plan_selection', 'pending_payment', 'pending_sales_contact', 'suspended', 'expired'] as const).map((s) => {
          const count = tenants.filter((t_) => t_.subscription_status === s).length;
          return (
            <div key={s}
              className="bg-white rounded-2xl border border-border p-3 cursor-pointer hover:border-rose-200 transition-all"
              onClick={() => setStatusFilter(statusFilter === s ? 'all' : s)}
            >
              <div className={`text-xl font-black ${count > 0 ? 'text-textMain' : 'text-textMuted'}`}>{count}</div>
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
          {['trial', 'active', 'pending_plan_selection', 'pending_payment', 'pending_sales_contact', 'grace', 'suspended', 'expired'].map((s) => (
            <option key={s} value={s}>{t.statusLabels[s] || s}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[900px]">
            <thead className="bg-slate-50 border-b border-border">
              <tr>
                {[t.tenant, t.tenantType, t.plan, t.subscriptionStatus, t.trialEnds, t.subEnds, t.actions].map((h) => (
                  <th key={h} className="px-5 py-3.5 text-[10px] font-black text-textMuted uppercase tracking-widest">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-5 py-10 text-center text-sm text-textMuted">{t.noData}</td>
                </tr>
              )}
              {filtered.map((row) => (
                <tr key={row.tenant_id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="font-bold text-sm text-textMain">{row.tenant_name}</div>
                    <div className="text-[10px] text-textMuted">{row.email_domain || ''}</div>
                  </td>
                  <td className="px-5 py-3.5 whitespace-nowrap">
                    {row.tenant_type ? (
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-tight ${
                        row.tenant_type === 'agency'
                          ? 'bg-purple-100 text-purple-700'
                          : row.tenant_type === 'individual_recruiter'
                          ? 'bg-blue-100 text-blue-700'
                          : 'bg-slate-100 text-slate-600'
                      }`}>
                        {row.tenant_type === 'organization' ? 'Org'
                          : row.tenant_type === 'agency' ? 'Agency'
                          : 'Recruiter'}
                      </span>
                    ) : <span className="text-textMuted text-xs">—</span>}
                  </td>
                  <td className="px-5 py-3.5">
                    {row.plan ? (
                      <span className="text-xs font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-lg capitalize">
                        {row.plan}
                      </span>
                    ) : row.pending_plan ? (
                      <span className="text-xs font-bold text-orange-600 bg-orange-50 px-2 py-0.5 rounded-lg capitalize">
                        {row.pending_plan} ⏳
                      </span>
                    ) : (
                      <span className="text-xs text-textMuted">No active plan</span>
                    )}
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
              <div className="bg-slate-50 rounded-xl px-4 py-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-textMuted uppercase tracking-widest">Current Plan</span>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-black text-textMain capitalize">
                      {manageTarget.plan || (manageTarget.subscription_status === 'pending_plan_selection' ? 'No active plan' : '—')}
                    </span>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[manageTarget.subscription_status] ?? 'bg-slate-100 text-slate-600'}`}>
                      {t.statusLabels[manageTarget.subscription_status] || manageTarget.subscription_status}
                    </span>
                  </div>
                </div>
                {manageTarget.pending_plan && !manageTarget.plan && (
                  <p className="text-[11px] text-orange-700 bg-orange-50 rounded-lg px-3 py-1.5">
                    Pending plan: <strong className="capitalize">{manageTarget.pending_plan}</strong> — limits not applied until payment succeeds.
                  </p>
                )}
                {manageTarget.pending_plan && !manageTarget.plan && manageTarget.subscription_status === 'pending_sales_contact' && (
                  <p className="text-[11px] text-purple-700 bg-purple-50 rounded-lg px-3 py-1.5">
                    Enterprise enquiry received. Sales team to contact tenant.
                  </p>
                )}
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

      {/* Create Tenant Modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 z-50 overflow-y-auto p-4 backdrop-blur-sm flex items-start justify-center">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg my-auto flex flex-col max-h-[90vh]">
            {/* Header — always visible */}
            <div className="px-6 py-4 border-b border-border flex items-center justify-between shrink-0">
              <h3 className="font-black text-textMain text-sm uppercase tracking-widest">{t.createTitle}</h3>
              <button onClick={() => { setShowCreate(false); resetCreateForm(); }} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <form onSubmit={handleCreateTenant} className="flex flex-col flex-1 min-h-0">
              {/* Scrollable body */}
              <div className="p-6 space-y-4 overflow-y-auto flex-1">

                {/* Tenant Type — prominent, first field */}
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                    {t.tenantTypeLabel} <span className="text-red-500">*</span>
                  </label>
                  <div className="grid grid-cols-1 gap-2">
                    {(['organization', 'agency', 'individual_recruiter'] as const).map((tt) => (
                      <label
                        key={tt}
                        className={`flex items-center gap-3 px-4 py-3 rounded-xl border-2 cursor-pointer transition-all ${
                          createForm.tenant_type === tt
                            ? tt === 'organization'
                              ? 'border-slate-400 bg-slate-50'
                              : tt === 'agency'
                              ? 'border-purple-400 bg-purple-50'
                              : 'border-blue-400 bg-blue-50'
                            : 'border-border hover:border-slate-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name="tenant_type"
                          value={tt}
                          checked={createForm.tenant_type === tt}
                          onChange={() => setCreateForm(f => ({ ...f, tenant_type: tt }))}
                          className="sr-only"
                        />
                        <span className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${
                          createForm.tenant_type === tt
                            ? tt === 'organization' ? 'border-slate-500 bg-slate-500'
                            : tt === 'agency' ? 'border-purple-500 bg-purple-500'
                            : 'border-blue-500 bg-blue-500'
                            : 'border-slate-300'
                        }`}>
                          {createForm.tenant_type === tt && (
                            <span className="w-1.5 h-1.5 rounded-full bg-white" />
                          )}
                        </span>
                        <div>
                          <div className="text-sm font-bold text-textMain">{t.tenantTypeOptions[tt]}</div>
                          <div className="text-[10px] text-textMuted">
                            {tt === 'organization' && 'Direct employer managing internal recruitment'}
                            {tt === 'agency' && 'Staffing/recruitment agency with multiple clients'}
                            {tt === 'individual_recruiter' && 'Freelance recruiter working across clients'}
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>
                </div>

                {/* Name + Domain */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                      {t.orgName} <span className="text-red-500">*</span>
                    </label>
                    <input
                      required
                      type="text"
                      placeholder="Acme Recruiting Ltd"
                      value={createForm.name}
                      onChange={(e) => setCreateForm(f => ({ ...f, name: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                      {t.emailDomain} <span className="text-red-500">*</span>
                    </label>
                    <input
                      required
                      type="text"
                      placeholder="acme.com"
                      value={createForm.email_domain}
                      onChange={(e) => setCreateForm(f => ({ ...f, email_domain: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                    />
                  </div>
                </div>

                {/* Plan + limits */}
                <div className="grid grid-cols-3 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.initialPlan}</label>
                    <select
                      value={createForm.plan}
                      onChange={(e) => setCreateForm(f => ({ ...f, plan: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200 bg-white"
                    >
                      <option value="starter">starter</option>
                      {plans.map((p) => (
                        <option key={p.plan_id} value={p.plan_code}>{p.plan_name}</option>
                      ))}
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.maxUsers}</label>
                    <input
                      type="number" min="1" max="100"
                      value={createForm.max_users}
                      onChange={(e) => setCreateForm(f => ({ ...f, max_users: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.maxJobs}</label>
                    <input
                      type="number" min="1" max="1000"
                      value={createForm.max_jobs}
                      onChange={(e) => setCreateForm(f => ({ ...f, max_jobs: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Max Clients</label>
                    <input
                      type="number" min="0" max="1000"
                      placeholder="Blank = unlimited"
                      value={createForm.max_clients}
                      onChange={(e) => setCreateForm(f => ({ ...f, max_clients: e.target.value }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Branding Level</label>
                    <select
                      value={createForm.branding_level}
                      onChange={(e) => setCreateForm(f => ({ ...f, branding_level: e.target.value as 'none' | 'basic' | 'white_label' }))}
                      className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200 bg-white"
                    >
                      <option value="none">None</option>
                      <option value="basic">Basic</option>
                      <option value="white_label">White Label</option>
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">API Access</label>
                    <label className="flex items-center gap-2 cursor-pointer mt-2">
                      <input
                        type="checkbox"
                        checked={createForm.api_access_enabled}
                        onChange={(e) => setCreateForm(f => ({ ...f, api_access_enabled: e.target.checked }))}
                        className="w-4 h-4 rounded border-border text-primary focus:ring-primary"
                      />
                      <span className="text-sm text-textMain">Enable API access</span>
                    </label>
                  </div>
                </div>

                {/* Admin User */}
                <div className="space-y-3 pt-2 border-t border-border">
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-1.5">
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0M12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                    </svg>
                    {t.adminSection}
                  </p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                        {t.adminFullName} <span className="text-red-500">*</span>
                      </label>
                      <input
                        required
                        type="text"
                        placeholder="Jane Smith"
                        value={createForm.admin_full_name}
                        onChange={(e) => setCreateForm(f => ({ ...f, admin_full_name: e.target.value }))}
                        className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                        {t.adminEmail} <span className="text-red-500">*</span>
                      </label>
                      <input
                        required
                        type="email"
                        placeholder="admin@company.com"
                        value={createForm.admin_email}
                        onChange={(e) => setCreateForm(f => ({ ...f, admin_email: e.target.value }))}
                        className="w-full px-3 py-2.5 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">
                      {t.adminPassword} <span className="text-red-500">*</span>
                    </label>
                    <div className="relative">
                      <input
                        required
                        type={showAdminPw ? 'text' : 'password'}
                        placeholder="••••••••"
                        minLength={8}
                        value={createForm.admin_password}
                        onChange={(e) => setCreateForm(f => ({ ...f, admin_password: e.target.value }))}
                        className="w-full px-3 py-2.5 pr-10 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-rose-200"
                      />
                      <button
                        type="button"
                        onClick={() => setShowAdminPw(v => !v)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain transition-colors"
                        tabIndex={-1}
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          {showAdminPw
                            ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                            : <><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></>
                          }
                        </svg>
                      </button>
                    </div>
                    <p className="text-[10px] text-textMuted">{t.adminPasswordHint}</p>
                  </div>
                </div>
              </div>

              {/* Footer — always visible */}
              <div className="px-6 py-4 border-t border-border flex justify-end gap-3 shrink-0 bg-white">
                <button
                  type="button"
                  onClick={() => { setShowCreate(false); resetCreateForm(); }}
                  disabled={createLoading}
                  className="px-5 py-2 text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50"
                >
                  {t.cancel}
                </button>
                <button
                  type="submit"
                  disabled={
                    createLoading ||
                    !createForm.name.trim() ||
                    !createForm.email_domain.trim() ||
                    !createForm.admin_full_name.trim() ||
                    !createForm.admin_email.trim() ||
                    createForm.admin_password.length < 8
                  }
                  className="flex items-center gap-2 px-6 py-2 text-sm font-bold bg-rose-600 text-white rounded-xl hover:bg-rose-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {createLoading && (
                    <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  )}
                  {createLoading ? t.creating : t.create}
                </button>
              </div>
            </form>
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
              ) : usageData ? (() => {
                const PENDING = new Set(['pending_plan_selection', 'pending_payment', 'pending_sales_contact']);
                const isPending = PENDING.has(usageData.subscription_status);
                return (
                  <div className="space-y-5">
                    {/* Plan badge */}
                    <div className="flex items-center justify-between bg-slate-50 rounded-xl px-4 py-3">
                      <span className="text-xs font-bold text-textMuted uppercase tracking-widest">Plan</span>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-black text-textMain capitalize">{usageData.plan || '—'}</span>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${STATUS_COLORS[usageData.subscription_status || 'active']}`}>
                          {t.statusLabels[usageData.subscription_status || 'active']}
                        </span>
                      </div>
                    </div>

                    {isPending ? (
                      <div className="flex flex-col items-center gap-2 py-6 text-center">
                        <div className="w-10 h-10 rounded-full bg-blue-50 flex items-center justify-center">
                          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        </div>
                        <p className="text-sm font-bold text-textMain">No active subscription yet</p>
                        <p className="text-xs text-textMuted">Usage limits will apply after plan activation.</p>
                      </div>
                    ) : (
                      <>
                        {/* Usage bars */}
                        <UsageBar
                          label={t.campaigns}
                          used={usageData.usage.active_campaigns}
                          limit={usageData.limits.max_campaigns}
                          pct={usageData.percentage_used.campaigns}
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
                      </>
                    )}
                  </div>
                );
              })() : (
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
