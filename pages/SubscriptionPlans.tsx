import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, PlanFeature, SubscriptionPlan } from '../types';

interface Props {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const T = {
  en: {
    title: 'Subscription Plans',
    subtitle: 'Manage platform subscription tiers and feature matrix',
    addPlan: 'New Plan', editPlan: 'Edit Plan', deletePlan: 'Delete / Deactivate',
    save: 'Save Plan', saving: 'Saving...', cancel: 'Cancel',
    confirmDeactivate: 'This plan is assigned to active tenants and will be deactivated instead of deleted. Continue?',
    confirmDelete: 'Are you sure you want to delete this plan?',
    active: 'Active', inactive: 'Inactive',
    monthly: '/mo', yearly: '/yr', trial: 'day trial',
    features: 'Features', pricing: 'Pricing', limits: 'Limits', identity: 'Identity',
    planCode: 'Plan Code', planName: 'Plan Name', description: 'Description',
    monthlyPrice: 'Monthly Price', yearlyPrice: 'Yearly Price',
    currency: 'Currency', trialDays: 'Trial Days',
    maxCampaigns: 'Max Campaigns', maxCvs: 'Max CVs / Month', maxUsers: 'Max Users',
    displayOrder: 'Display Order',
    featureMatrix: 'Feature Matrix',
    errorLoad: 'Failed to load plans', errorSave: 'Failed to save plan',
    saveSuccess: 'Plan saved', deleteSuccess: 'Plan removed',
    featureSaved: 'Feature updated',
    updatedBy: 'Last updated by',
    noPlans: 'No subscription plans found. Add your first plan.',
  },
  ar: {
    title: 'خطط الاشتراك',
    subtitle: 'إدارة مستويات الاشتراك ومصفوفة الميزات',
    addPlan: 'خطة جديدة', editPlan: 'تعديل الخطة', deletePlan: 'حذف / تعطيل',
    save: 'حفظ الخطة', saving: 'جاري الحفظ...', cancel: 'إلغاء',
    confirmDeactivate: 'هذه الخطة مرتبطة بمستأجرين نشطين وسيتم تعطيلها بدلاً من الحذف. هل تريد المتابعة؟',
    confirmDelete: 'هل أنت متأكد من حذف هذه الخطة؟',
    active: 'نشطة', inactive: 'غير نشطة',
    monthly: '/شهر', yearly: '/سنة', trial: 'يوم تجريبي',
    features: 'المميزات', pricing: 'التسعير', limits: 'الحدود', identity: 'الهوية',
    planCode: 'كود الخطة', planName: 'اسم الخطة', description: 'الوصف',
    monthlyPrice: 'السعر الشهري', yearlyPrice: 'السعر السنوي',
    currency: 'العملة', trialDays: 'أيام التجربة',
    maxCampaigns: 'أقصى حملات', maxCvs: 'أقصى سير / شهر', maxUsers: 'أقصى مستخدمين',
    displayOrder: 'ترتيب العرض',
    featureMatrix: 'مصفوفة الميزات',
    errorLoad: 'فشل تحميل الخطط', errorSave: 'فشل حفظ الخطة',
    saveSuccess: 'تم حفظ الخطة', deleteSuccess: 'تمت إزالة الخطة',
    featureSaved: 'تم تحديث الميزة',
    updatedBy: 'آخر تحديث بواسطة',
    noPlans: 'لا توجد خطط اشتراك. أضف خطتك الأولى.',
  },
};

const EMPTY_PLAN = {
  plan_code: '', plan_name: '', description: '',
  monthly_price: 0, yearly_price: 0, currency: 'USD', trial_days: 14,
  max_campaigns: 5, max_processed_cvs_per_month: 500, max_users: 3,
  display_order: 0,
};

const PLAN_HEADER_COLORS = [
  'from-sky-500 to-blue-600',
  'from-violet-500 to-purple-600',
  'from-amber-500 to-orange-600',
  'from-emerald-500 to-teal-600',
];

function featureDisplayValue(feat: PlanFeature): string {
  if (feat.value_type === 'boolean') return feat.value_boolean ? '✓' : '✗';
  if (feat.value_type === 'number')  return feat.value_number !== null ? String(feat.value_number) : '—';
  return feat.value_text ?? '—';
}

function featureValueClass(feat: PlanFeature): string {
  if (feat.value_type === 'boolean') {
    return feat.value_boolean
      ? 'text-emerald-600 font-black'
      : 'text-slate-400';
  }
  return 'text-textMain font-bold';
}

// ── Inline feature editor cell ─────────────────────────────────────────────────
interface FeatureCellProps {
  planId: string;
  feat: PlanFeature;
  token: string;
  onSaved: (planId: string, featureKey: string, updated: Partial<PlanFeature>) => void;
  onError: (msg: string) => void;
}

const FeatureCell: React.FC<FeatureCellProps> = ({ planId, feat, token, onSaved, onError }) => {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [boolVal, setBoolVal] = useState(feat.value_boolean ?? false);
  const [numVal,  setNumVal]  = useState(feat.value_number  ?? 0);
  const [txtVal,  setTxtVal]  = useState(feat.value_text    ?? '');

  const startEdit = () => {
    setBoolVal(feat.value_boolean ?? false);
    setNumVal(feat.value_number   ?? 0);
    setTxtVal(feat.value_text     ?? '');
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> =
        feat.value_type === 'boolean' ? { value_boolean: boolVal }
        : feat.value_type === 'number' ? { value_number: numVal }
        : { value_text: txtVal };

      await apiService.patch(
        `${WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL}/${planId}/features/${feat.feature_key}`,
        body,
        token,
      );
      onSaved(planId, feat.feature_key, body as Partial<PlanFeature>);
      setEditing(false);
    } catch (err: any) {
      onError(err.message || 'Failed to save feature');
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button
        onClick={startEdit}
        title="Click to edit"
        className={`w-full text-center py-1 rounded hover:bg-slate-100 transition-all cursor-pointer ${featureValueClass(feat)}`}
      >
        {featureDisplayValue(feat)}
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1 justify-center">
      {feat.value_type === 'boolean' && (
        <select
          value={boolVal ? 'yes' : 'no'}
          onChange={(e) => setBoolVal(e.target.value === 'yes')}
          className="text-xs border border-violet-300 rounded px-1 py-0.5 focus:outline-none focus:ring-1 focus:ring-violet-400"
        >
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      )}
      {feat.value_type === 'number' && (
        <input
          type="number"
          value={numVal}
          onChange={(e) => setNumVal(parseFloat(e.target.value) || 0)}
          className="text-xs border border-violet-300 rounded px-1 py-0.5 w-20 focus:outline-none focus:ring-1 focus:ring-violet-400"
        />
      )}
      {feat.value_type === 'text' && (
        <input
          type="text"
          value={txtVal}
          onChange={(e) => setTxtVal(e.target.value)}
          className="text-xs border border-violet-300 rounded px-1 py-0.5 w-20 focus:outline-none focus:ring-1 focus:ring-violet-400"
        />
      )}
      <button
        disabled={saving}
        onClick={save}
        className="text-[10px] font-black text-emerald-600 hover:text-emerald-700 disabled:opacity-50"
      >
        {saving ? '…' : '✓'}
      </button>
      <button
        onClick={() => setEditing(false)}
        className="text-[10px] text-slate-400 hover:text-slate-600"
      >
        ✗
      </button>
    </div>
  );
};


// ── Main page ──────────────────────────────────────────────────────────────────
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
  const [activeTab, setActiveTab] = useState<'cards' | 'matrix'>('cards');

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
      plan_code: plan.plan_code,
      plan_name: plan.plan_name,
      description: plan.description || '',
      monthly_price: plan.monthly_price,
      yearly_price: plan.yearly_price,
      currency: plan.currency,
      trial_days: plan.trial_days,
      max_campaigns: plan.max_campaigns,
      max_processed_cvs_per_month: plan.max_processed_cvs_per_month,
      max_users: plan.max_users,
      display_order: plan.display_order,
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
    if (!window.confirm(t.confirmDelete)) return;
    setDeletingId(plan.plan_id);
    try {
      const result = await apiService.delete(
        `${WEBHOOK_CONFIG.SUBSCRIPTION_PLANS_URL}/${plan.plan_id}`,
        auth.token!,
      );
      addToast(result.action === 'deactivated' ? (result.message || 'Plan deactivated') : t.deleteSuccess,
        result.action === 'deactivated' ? 'info' : 'success');
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleFeatureSaved = (planId: string, featureKey: string, updated: Partial<PlanFeature>) => {
    setPlans((prev) => prev.map((p) => {
      if (p.plan_id !== planId) return p;
      return {
        ...p,
        features: p.features.map((f) =>
          f.feature_key === featureKey ? { ...f, ...updated } : f
        ),
      };
    }));
    addToast(t.featureSaved, 'success');
  };

  const setField = <K extends keyof typeof form>(key: K, val: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: val }));

  // Plans that participate in the feature matrix (have at least one feature seeded).
  // Enterprise and any other plans without features are excluded automatically.
  const matrixPlans = plans.filter((p) => p.features.length > 0);

  // Collect all unique feature keys in display order (from matrix plans only)
  const allFeatureKeys = (() => {
    const seen = new Map<string, { name: string; order: number; vtype: string }>();
    for (const plan of matrixPlans) {
      for (const f of plan.features) {
        if (!seen.has(f.feature_key)) {
          seen.set(f.feature_key, { name: f.feature_name, order: f.display_order, vtype: f.value_type });
        }
      }
    }
    return [...seen.entries()].sort((a, b) => a[1].order - b[1].order);
  })();

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
        <div className="flex items-center gap-3">
          {/* View toggle */}
          <div className="flex items-center bg-slate-100 rounded-xl p-1 gap-1">
            <button
              onClick={() => setActiveTab('cards')}
              className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all ${activeTab === 'cards' ? 'bg-white text-textMain shadow-sm' : 'text-textMuted hover:text-textMain'}`}
            >
              Cards
            </button>
            <button
              onClick={() => setActiveTab('matrix')}
              className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all ${activeTab === 'matrix' ? 'bg-white text-textMain shadow-sm' : 'text-textMuted hover:text-textMain'}`}
            >
              {t.featureMatrix}
            </button>
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
      </div>

      {/* ── Cards view ─────────────────────────────────────────────────────────── */}
      {activeTab === 'cards' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          {plans.map((plan, idx) => {
            const gradient = PLAN_HEADER_COLORS[idx % PLAN_HEADER_COLORS.length];
            return (
              <div
                key={plan.plan_id}
                className={`bg-white rounded-2xl border border-border shadow-sm overflow-hidden flex flex-col ${plan.status === 'inactive' ? 'opacity-60' : ''}`}
              >
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
                      <div className="text-2xl font-black">
                        ${plan.monthly_price}<span className="text-sm font-normal opacity-70">{t.monthly}</span>
                      </div>
                      <div className="text-xs opacity-70">${plan.yearly_price}{t.yearly}</div>
                    </div>
                  </div>
                  {plan.description && (
                    <p className="text-white/80 text-xs mt-2 leading-relaxed">{plan.description}</p>
                  )}
                </div>

                {/* Limits strip */}
                <div className="p-4 grid grid-cols-2 gap-3 border-b border-border">
                  {[
                    { label: 'Campaigns', value: plan.max_campaigns },
                    { label: 'Users', value: plan.max_users },
                  ].map(({ label, value }) => (
                    <div key={label} className="text-center bg-slate-50 rounded-xl p-2">
                      <div className="text-sm font-black text-textMain">{value}</div>
                      <div className="text-[9px] font-black text-textMuted uppercase tracking-wider mt-0.5">{label}</div>
                    </div>
                  ))}
                </div>

                {/* Feature list */}
                <div className="p-4 flex-1 overflow-auto max-h-56">
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.features}</p>
                  <div className="space-y-1.5">
                    {plan.features.map((feat) => (
                      <div key={feat.feature_key} className="flex items-center justify-between gap-2">
                        <span className="text-xs text-textMuted truncate flex-1">{feat.feature_name}</span>
                        <span className={`text-xs shrink-0 ${featureValueClass(feat)}`}>
                          {featureDisplayValue(feat)}
                        </span>
                      </div>
                    ))}
                    {plan.features.length === 0 && (
                      <p className="text-xs text-textMuted italic">No features defined</p>
                    )}
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
                    {deletingId === plan.plan_id ? '…' : '🗑'}
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
              <p className="text-sm">{t.noPlans}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Matrix view ────────────────────────────────────────────────────────── */}
      {activeTab === 'matrix' && (
        <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left px-5 py-4 text-xs font-black text-textMuted uppercase tracking-widest w-64 bg-slate-50">
                    {t.features}
                  </th>
                  {matrixPlans.map((plan, idx) => {
                    const gradient = PLAN_HEADER_COLORS[idx % PLAN_HEADER_COLORS.length];
                    return (
                      <th key={plan.plan_id} className="px-4 py-4 text-center min-w-[160px]">
                        <div className={`inline-flex flex-col items-center gap-1 px-4 py-2 rounded-xl bg-gradient-to-br ${gradient} text-white`}>
                          <span className="font-black text-sm">{plan.plan_name}</span>
                          <span className="text-[10px] opacity-80">${plan.monthly_price}/mo</span>
                        </div>
                        <div className="mt-2 flex justify-center gap-1">
                          <button
                            onClick={() => openEdit(plan)}
                            className="text-[10px] font-bold text-violet-600 hover:underline"
                          >
                            Edit
                          </button>
                          <span className="text-textMuted text-[10px]">·</span>
                          <button
                            disabled={deletingId === plan.plan_id}
                            onClick={() => handleDelete(plan)}
                            className="text-[10px] font-bold text-red-500 hover:underline disabled:opacity-50"
                          >
                            {deletingId === plan.plan_id ? '…' : 'Delete'}
                          </button>
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {allFeatureKeys.map(([key, meta], rowIdx) => (
                  <tr
                    key={key}
                    className={`border-b border-border last:border-0 ${rowIdx % 2 === 0 ? 'bg-white' : 'bg-slate-50/50'}`}
                  >
                    <td className="px-5 py-3 bg-inherit">
                      <div className="font-semibold text-textMain text-xs">{meta.name}</div>
                      <div className="text-[10px] text-textMuted capitalize">{meta.vtype}</div>
                    </td>
                    {matrixPlans.map((plan) => {
                      const feat = plan.features.find((f) => f.feature_key === key);
                      if (!feat) {
                        return (
                          <td key={plan.plan_id} className="px-4 py-3 text-center text-textMuted text-xs">
                            —
                          </td>
                        );
                      }
                      return (
                        <td key={plan.plan_id} className="px-4 py-3">
                          <FeatureCell
                            planId={plan.plan_id}
                            feat={feat}
                            token={auth.token!}
                            onSaved={handleFeatureSaved}
                            onError={(msg) => addToast(msg, 'error')}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {allFeatureKeys.length === 0 && (
                  <tr>
                    <td colSpan={matrixPlans.length + 1} className="text-center py-12 text-textMuted text-sm">
                      No features found. Run migration 024 to seed the feature matrix.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Add / Edit Modal ───────────────────────────────────────────────────── */}
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
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.identity}</p>
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
                <div className="grid grid-cols-2 gap-4">
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
                </div>
              </div>

              {/* Limits */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.limits}</p>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-bold text-textMuted mb-1">{t.maxCampaigns}</label>
                    <input type="number" min="1"
                      value={form.max_campaigns}
                      onChange={(e) => setField('max_campaigns', parseInt(e.target.value) || 1)}
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
