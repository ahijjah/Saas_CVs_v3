import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, SubscriptionPlan } from '../types';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const T = {
  en: {
    title: 'Subscription Plans',
    subtitle: 'Manage platform subscription tiers and feature limits',
    addPlan: 'New Plan', editPlan: 'Edit Plan', deletePlan: 'Delete / Deactivate',
    save: 'Save Plan', saving: 'Saving...', cancel: 'Cancel',
    confirmDeactivate: 'This plan is assigned to active tenants and will be deactivated instead of deleted. Continue?',
    confirmDelete: 'Are you sure you want to delete this plan?',
    active: 'Active', inactive: 'Inactive',
    monthly: '/mo', yearly: '/yr', trial: 'day trial',
    campaigns: 'Campaigns', cvs: 'CVs / Month', users: 'Users',
    features: 'Features', pricing: 'Pricing', limits: 'Limits',
    planCode: 'Plan Code', planName: 'Plan Name', description: 'Description',
    monthlyPrice: 'Monthly Price', yearlyPrice: 'Yearly Price',
    currency: 'Currency', trialDays: 'Trial Days',
    maxCampaigns: 'Max Campaigns', maxCvs: 'Max CVs / Month', maxUsers: 'Max Users',
    apiAccess: 'API Access', advancedAnalytics: 'Advanced Analytics',
    prioritySupport: 'Priority Support', customAiPrompts: 'Custom AI Prompts',
    displayOrder: 'Display Order',
    errorLoad: 'Failed to load plans', errorSave: 'Failed to save plan',
    saveSuccess: 'Plan saved', deleteSuccess: 'Plan removed',
    updatedBy: 'Last updated by',
  },
  ar: {
    title: 'خطط الاشتراك',
    subtitle: 'إدارة مستويات الاشتراك وحدود الميزات',
    addPlan: 'خطة جديدة', editPlan: 'تعديل الخطة', deletePlan: 'حذف / تعطيل',
    save: 'حفظ الخطة', saving: 'جاري الحفظ...', cancel: 'إلغاء',
    confirmDeactivate: 'هذه الخطة مرتبطة بمستأجرين نشطين وسيتم تعطيلها بدلاً من الحذف. هل تريد المتابعة؟',
    confirmDelete: 'هل أنت متأكد من حذف هذه الخطة؟',
    active: 'نشطة', inactive: 'غير نشطة',
    monthly: '/شهر', yearly: '/سنة', trial: 'يوم تجريبي',
    campaigns: 'حملات', cvs: 'سيرة ذاتية / شهر', users: 'مستخدمون',
    features: 'المميزات', pricing: 'التسعير', limits: 'الحدود',
    planCode: 'كود الخطة', planName: 'اسم الخطة', description: 'الوصف',
    monthlyPrice: 'السعر الشهري', yearlyPrice: 'السعر السنوي',
    currency: 'العملة', trialDays: 'أيام التجربة',
    maxCampaigns: 'أقصى حملات', maxCvs: 'أقصى سير / شهر', maxUsers: 'أقصى مستخدمين',
    apiAccess: 'وصول API', advancedAnalytics: 'تحليلات متقدمة',
    prioritySupport: 'دعم أولوية', customAiPrompts: 'مطالبات AI مخصصة',
    displayOrder: 'ترتيب العرض',
    errorLoad: 'فشل تحميل الخطط', errorSave: 'فشل حفظ الخطة',
    saveSuccess: 'تم حفظ الخطة', deleteSuccess: 'تمت إزالة الخطة',
    updatedBy: 'آخر تحديث بواسطة',
  },
};

const EMPTY_PLAN: Omit<SubscriptionPlan, 'plan_id' | 'status' | 'created_at' | 'updated_at' | 'updated_by_email'> = {
  plan_code: '', plan_name: '', description: '',
  monthly_price: 0, yearly_price: 0, currency: 'USD', trial_days: 14,
  max_campaigns: 5, max_processed_cvs_per_month: 500, max_users: 3,
  api_access: false, advanced_analytics: false,
  priority_support: false, custom_ai_prompts: false, display_order: 0,
};

const FEATURE_ICONS: Record<string, string> = {
  api_access: '🔌', advanced_analytics: '📊', priority_support: '⭐', custom_ai_prompts: '🤖',
};

const PLAN_HEADER_COLORS = ['from-sky-500 to-blue-600', 'from-violet-500 to-purple-600', 'from-amber-500 to-orange-600'];

export const SubscriptionPlansPage: React.FC<Props> = ({ auth, addToast }) => {
  const lang = (document.documentElement.lang as 'en' | 'ar') === 'ar' ? 'ar' : 'en';
  const t = T[lang];

  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editTarget, setEditTarget] = useState<SubscriptionPlan | null>(null);
  const [form, setForm] = useState({ ...EMPTY_PLAN });
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL, {}, auth.token!);
      setPlans(data.plans || []);
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, t.errorLoad]);

  useEffect(() => { load(); }, [load]);

  const openAdd = () => {
    setEditTarget(null);
    setForm({ ...EMPTY_PLAN });
    setShowModal(true);
  };

  const openEdit = (plan: SubscriptionPlan) => {
    setEditTarget(plan);
    setForm({
      plan_code: plan.plan_code, plan_name: plan.plan_name,
      description: plan.description || '', monthly_price: plan.monthly_price,
      yearly_price: plan.yearly_price, currency: plan.currency,
      trial_days: plan.trial_days, max_campaigns: plan.max_campaigns,
      max_processed_cvs_per_month: plan.max_processed_cvs_per_month,
      max_users: plan.max_users, api_access: plan.api_access,
      advanced_analytics: plan.advanced_analytics, priority_support: plan.priority_support,
      custom_ai_prompts: plan.custom_ai_prompts, display_order: plan.display_order,
    });
    setShowModal(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (editTarget) {
        await apiService.put(
          `${WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL}/${editTarget.plan_id}`,
          form, auth.token!,
        );
      } else {
        await apiService.post(WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL, form, auth.token!);
      }
      addToast(t.saveSuccess, 'success');
      setShowModal(false);
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (plan: SubscriptionPlan) => {
    const confirmed = window.confirm(t.confirmDelete);
    if (!confirmed) return;
    setDeletingId(plan.plan_id);
    try {
      const result = await apiService.delete(
        `${WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL}/${plan.plan_id}`,
        auth.token!,
      );
      if (result.action === 'deactivated') {
        addToast(result.message || 'Plan deactivated', 'info');
      } else {
        addToast(t.deleteSuccess, 'success');
      }
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const setField = <K extends keyof typeof form>(key: K, val: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: val }));

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-violet-600" />
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-textMain">{t.title}</h2>
          <p className="text-xs text-textMuted mt-0.5">{t.subtitle}</p>
        </div>
        <button
          onClick={openAdd}
          className="flex items-center gap-2 px-4 py-2 bg-violet-600 text-white text-sm font-bold rounded-xl hover:bg-violet-700 transition-all shadow-md"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          {t.addPlan}
        </button>
      </div>

      {/* Plan Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        {plans.map((plan, idx) => {
          const gradient = PLAN_HEADER_COLORS[idx % PLAN_HEADER_COLORS.length];
          const activeFeatures = [
            plan.api_access && 'api_access',
            plan.advanced_analytics && 'advanced_analytics',
            plan.priority_support && 'priority_support',
            plan.custom_ai_prompts && 'custom_ai_prompts',
          ].filter(Boolean) as string[];

          return (
            <div key={plan.plan_id} className={`bg-white rounded-2xl border border-border shadow-sm overflow-hidden flex flex-col ${plan.status === 'inactive' ? 'opacity-60' : ''}`}>
              {/* Card header */}
              <div className={`bg-gradient-to-br ${gradient} p-5 text-white`}>
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="text-lg font-black">{plan.plan_name}</h3>
                      <span className={`text-[10px] font-black px-2 py-0.5 rounded-full uppercase ${plan.status === 'active' ? 'bg-white/20' : 'bg-red-500/60'}`}>
                        {plan.status === 'active' ? t.active : t.inactive}
                      </span>
                    </div>
                    <code className="text-white/70 text-[10px] font-bold">{plan.plan_code}</code>
                  </div>
                  <div className="text-right">
                    <div className="text-2xl font-black">${plan.monthly_price}<span className="text-sm font-normal opacity-70">{t.monthly}</span></div>
                    <div className="text-xs opacity-70">${plan.yearly_price}{t.yearly}</div>
                  </div>
                </div>
                {plan.description && (
                  <p className="text-white/80 text-xs mt-2 leading-relaxed">{plan.description}</p>
                )}
                <div className="mt-3 text-xs font-bold bg-white/10 rounded-lg px-3 py-1.5 inline-block">
                  {plan.trial_days} {t.trial}
                </div>
              </div>

              {/* Limits */}
              <div className="p-4 grid grid-cols-3 gap-3 border-b border-border">
                {[
                  { label: t.campaigns, value: plan.max_campaigns },
                  { label: t.cvs, value: plan.max_processed_cvs_per_month.toLocaleString() },
                  { label: t.users, value: plan.max_users },
                ].map(({ label, value }) => (
                  <div key={label} className="text-center bg-slate-50 rounded-xl p-2">
                    <div className="text-sm font-black text-textMain">{value}</div>
                    <div className="text-[9px] font-black text-textMuted uppercase tracking-wider mt-0.5">{label}</div>
                  </div>
                ))}
              </div>

              {/* Features */}
              <div className="p-4 flex-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.features}</p>
                <div className="space-y-2">
                  {(['api_access', 'advanced_analytics', 'priority_support', 'custom_ai_prompts'] as const).map((feat) => (
                    <div key={feat} className="flex items-center gap-2">
                      <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[10px] ${plan[feat] ? 'bg-green-100 text-green-600' : 'bg-slate-100 text-slate-400'}`}>
                        {plan[feat] ? '✓' : '×'}
                      </span>
                      <span className={`text-xs font-medium ${plan[feat] ? 'text-textMain' : 'text-textMuted line-through'}`}>
                        {FEATURE_ICONS[feat]} {t[feat as keyof typeof t] as string}
                      </span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Actions */}
              <div className="px-4 pb-4 flex gap-2">
                <button
                  onClick={() => openEdit(plan)}
                  className="flex-1 text-xs font-bold py-2 border border-violet-200 text-violet-600 rounded-xl hover:bg-violet-50 transition-all"
                >
                  {t.editPlan}
                </button>
                <button
                  disabled={deletingId === plan.plan_id}
                  onClick={() => handleDelete(plan)}
                  className="text-xs font-bold py-2 px-3 border border-red-200 text-red-500 rounded-xl hover:bg-red-50 transition-all disabled:opacity-50"
                >
                  {deletingId === plan.plan_id ? '...' : '🗑'}
                </button>
              </div>
              {plan.updated_by_email && (
                <div className="px-4 pb-3">
                  <p className="text-[9px] text-textMuted truncate">{t.updatedBy}: {plan.updated_by_email}</p>
                </div>
              )}
            </div>
          );
        })}

        {plans.length === 0 && (
          <div className="col-span-3 text-center py-16 text-textMuted">
            <p className="text-sm">No subscription plans found. Add your first plan.</p>
          </div>
        )}
      </div>

      {/* Add / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-start justify-center p-4 pt-8 backdrop-blur-sm overflow-y-auto">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mb-8">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between sticky top-0 bg-white rounded-t-2xl z-10">
              <h3 className="font-black text-textMain text-sm uppercase tracking-widest">
                {editTarget ? t.editPlan : t.addPlan}
              </h3>
              <button onClick={() => setShowModal(false)} className="text-textMuted hover:text-textMain p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="p-6 space-y-6">
              {/* Identity */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">Identity</p>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.planCode}</label>
                    <input
                      value={form.plan_code}
                      disabled={!!editTarget}
                      onChange={(e) => setField('plan_code', e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ''))}
                      placeholder="e.g. starter"
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300 disabled:bg-slate-50 disabled:text-textMuted"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.planName}</label>
                    <input
                      value={form.plan_name}
                      onChange={(e) => setField('plan_name', e.target.value)}
                      placeholder="e.g. Starter"
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                </div>
                <div className="mt-3">
                  <label className="block text-xs font-bold text-textMuted mb-1">{t.description}</label>
                  <textarea
                    rows={2}
                    value={form.description}
                    onChange={(e) => setField('description', e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300 resize-none"
                  />
                </div>
              </div>

              {/* Pricing */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.pricing}</p>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.monthlyPrice}</label>
                    <input type="number" min="0" step="0.01"
                      value={form.monthly_price}
                      onChange={(e) => setField('monthly_price', parseFloat(e.target.value) || 0)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.yearlyPrice}</label>
                    <input type="number" min="0" step="0.01"
                      value={form.yearly_price}
                      onChange={(e) => setField('yearly_price', parseFloat(e.target.value) || 0)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.trialDays}</label>
                    <input type="number" min="0"
                      value={form.trial_days}
                      onChange={(e) => setField('trial_days', parseInt(e.target.value) || 0)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                </div>
              </div>

              {/* Limits */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.limits}</p>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.maxCampaigns}</label>
                    <input type="number" min="1"
                      value={form.max_campaigns}
                      onChange={(e) => setField('max_campaigns', parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.maxCvs}</label>
                    <input type="number" min="1"
                      value={form.max_processed_cvs_per_month}
                      onChange={(e) => setField('max_processed_cvs_per_month', parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.maxUsers}</label>
                    <input type="number" min="1"
                      value={form.max_users}
                      onChange={(e) => setField('max_users', parseInt(e.target.value) || 1)}
                      className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                    />
                  </div>
                </div>
              </div>

              {/* Feature flags */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.features}</p>
                <div className="grid grid-cols-2 gap-3">
                  {([
                    ['api_access', t.apiAccess],
                    ['advanced_analytics', t.advancedAnalytics],
                    ['priority_support', t.prioritySupport],
                    ['custom_ai_prompts', t.customAiPrompts],
                  ] as const).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-3 p-3 border border-border rounded-xl cursor-pointer hover:bg-slate-50 transition-all">
                      <input
                        type="checkbox"
                        checked={form[key] as boolean}
                        onChange={(e) => setField(key, e.target.checked)}
                        className="w-4 h-4 rounded text-violet-600"
                      />
                      <span className="text-sm font-medium text-textMain">
                        {FEATURE_ICONS[key]} {label}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Display order */}
              <div className="w-32">
                <label className="block text-xs font-bold text-textMuted mb-1">{t.displayOrder}</label>
                <input type="number" min="0"
                  value={form.display_order}
                  onChange={(e) => setField('display_order', parseInt(e.target.value) || 0)}
                  className="w-full px-3 py-2 text-sm border border-border rounded-xl focus:outline-none focus:ring-2 focus:ring-violet-300"
                />
              </div>
            </div>

            <div className="px-6 py-4 border-t border-border flex justify-end gap-3 sticky bottom-0 bg-white rounded-b-2xl">
              <button
                onClick={() => setShowModal(false)}
                className="px-5 py-2 text-sm font-bold border border-border text-textMuted rounded-xl hover:bg-slate-50"
              >
                {t.cancel}
              </button>
              <button
                disabled={saving || !form.plan_code || !form.plan_name}
                onClick={handleSave}
                className="px-5 py-2 text-sm font-bold bg-violet-600 text-white rounded-xl hover:bg-violet-700 disabled:opacity-50"
              >
                {saving ? t.saving : t.save}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
