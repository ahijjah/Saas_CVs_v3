import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, Campaign, ClientOrganization } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface CampaignsProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

// ── Translations ──────────────────────────────────────────────────────────────
const T = {
  en: {
    title: 'Campaigns',
    sub: 'Optionally group related jobs together for easier filtering and reporting. Jobs can always stay standalone.',
    addCampaign: 'New Campaign',
    loading: 'Loading...',
    none: 'No campaigns yet. Create one to group related jobs — jobs can always remain standalone.',
    name: 'Campaign Name',
    nameRequired: 'Campaign name is required.',
    description: 'Description',
    client: 'Client',
    clientPublic: 'Public / Shared (no client)',
    clientPublicShort: 'Public / Shared',
    clientHint: 'A client campaign may only contain jobs for that same client. A public campaign may only contain public jobs.',
    status: 'Status',
    statusActive: 'Active',
    statusArchived: 'Archived',
    jobs: 'Jobs',
    activeJobs: 'Active',
    viewJobs: 'View jobs',
    edit: 'Edit',
    archive: 'Archive',
    unarchive: 'Reactivate',
    delete: 'Delete',
    save: 'Save',
    cancel: 'Cancel',
    saving: 'Saving...',
    create: 'Create Campaign',
    modalAdd: 'New Campaign',
    modalEdit: 'Edit Campaign',
    namePlaceholder: 'e.g. Summer 2026 Hiring Drive',
    descPlaceholder: 'Optional notes about this campaign…',
    confirmDelete: 'Delete this campaign? Linked jobs will NOT be deleted — they simply become standalone again.',
    createdOk: 'Campaign created.',
    updatedOk: 'Campaign updated.',
    deletedOk: 'Campaign deleted. Linked jobs are now standalone.',
    errorLoad: 'Failed to load campaigns.',
    errorSave: 'Save failed.',
    includeArchived: 'Show archived',
    notLayerNote: 'Campaigns are an organizational layer only — they never change who can access a job.',
  },
  ar: {
    title: 'الحملات',
    sub: 'اجمع الوظائف المرتبطة معاً اختيارياً لتسهيل التصفية والتقارير. يمكن أن تبقى الوظائف مستقلة دائماً.',
    addCampaign: 'حملة جديدة',
    loading: 'جارٍ التحميل...',
    none: 'لا توجد حملات بعد. أنشئ حملة لتجميع الوظائف المرتبطة — يمكن أن تبقى الوظائف مستقلة دائماً.',
    name: 'اسم الحملة',
    nameRequired: 'اسم الحملة مطلوب.',
    description: 'الوصف',
    client: 'العميل',
    clientPublic: 'عام / مشترك (بدون عميل)',
    clientPublicShort: 'عام / مشترك',
    clientHint: 'حملة العميل قد تحتوي فقط على وظائف لنفس العميل. الحملة العامة قد تحتوي فقط على وظائف عامة.',
    status: 'الحالة',
    statusActive: 'نشطة',
    statusArchived: 'مؤرشفة',
    jobs: 'الوظائف',
    activeJobs: 'نشطة',
    viewJobs: 'عرض الوظائف',
    edit: 'تعديل',
    archive: 'أرشفة',
    unarchive: 'إعادة تنشيط',
    delete: 'حذف',
    save: 'حفظ',
    cancel: 'إلغاء',
    saving: 'جارٍ الحفظ...',
    create: 'إنشاء حملة',
    modalAdd: 'حملة جديدة',
    modalEdit: 'تعديل الحملة',
    namePlaceholder: 'مثال: حملة توظيف صيف 2026',
    descPlaceholder: 'ملاحظات اختيارية عن هذه الحملة…',
    confirmDelete: 'حذف هذه الحملة؟ لن تُحذف الوظائف المرتبطة — ستصبح مستقلة مرة أخرى.',
    createdOk: 'تم إنشاء الحملة.',
    updatedOk: 'تم تحديث الحملة.',
    deletedOk: 'تم حذف الحملة. الوظائف المرتبطة أصبحت مستقلة الآن.',
    errorLoad: 'فشل تحميل الحملات.',
    errorSave: 'فشل الحفظ.',
    includeArchived: 'إظهار المؤرشفة',
    notLayerNote: 'الحملات طبقة تنظيمية فقط — لا تغيّر أبداً من يمكنه الوصول إلى وظيفة.',
  },
};

interface FormState {
  name: string;
  description: string;
  client_organization_id: string;  // '' = public/shared
  status: 'active' | 'archived';
}

const BLANK: FormState = { name: '', description: '', client_organization_id: '', status: 'active' };

export const CampaignsPage: React.FC<CampaignsProps> = ({ auth, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const navigate = useNavigate();
  const token = auth.token;

  const tenantType = auth.user?.tenant_type ?? 'organization';
  const isAgency = tenantType === 'agency' || tenantType === 'individual_recruiter';

  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [clientOrgs, setClientOrgs] = useState<ClientOrganization[]>([]);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(BLANK);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (includeArchived) params.include_archived = 'true';
      const [campData, clientData] = await Promise.all([
        apiService.get(WEBHOOK_CONFIG.CAMPAIGNS_URL, params, token),
        isAgency
          ? apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, token).catch(() => null)
          : Promise.resolve(null),
      ]);
      setCampaigns(campData?.campaigns ?? []);
      if (clientData) {
        const orgs: ClientOrganization[] = clientData.client_organizations ?? [];
        setClientOrgs(orgs.filter(o => o.status === 'active'));
      }
    } catch (err: any) {
      addToast(err.message || t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [token, includeArchived, isAgency, addToast, t.errorLoad]);

  useEffect(() => { load(); }, [load]);

  const openAdd = () => { setEditingId(null); setForm(BLANK); setShowForm(true); };
  const openEdit = (c: Campaign) => {
    setEditingId(c.campaign_id);
    setForm({
      name: c.name,
      description: c.description || '',
      client_organization_id: c.client_organization_id || '',
      status: c.status,
    });
    setShowForm(true);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = form.name.trim();
    if (!name) { addToast(t.nameRequired, 'error'); return; }
    setSaving(true);
    try {
      if (editingId) {
        await apiService.patch(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${editingId}`, {
          name,
          description: form.description.trim() || null,
          status: form.status,
          client_organization_id: form.client_organization_id || null,
        }, token!);
        addToast(t.updatedOk, 'success');
      } else {
        await apiService.post(WEBHOOK_CONFIG.CAMPAIGNS_URL, {
          name,
          description: form.description.trim() || null,
          client_organization_id: form.client_organization_id || null,
        }, token!);
        addToast(t.createdOk, 'success');
      }
      setShowForm(false);
      setForm(BLANK);
      setEditingId(null);
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setSaving(false);
    }
  };

  const toggleArchive = async (c: Campaign) => {
    try {
      await apiService.patch(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${c.campaign_id}`, {
        status: c.status === 'active' ? 'archived' : 'active',
      }, token!);
      addToast(t.updatedOk, 'success');
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    }
  };

  const remove = async (c: Campaign) => {
    if (!window.confirm(t.confirmDelete)) return;
    try {
      await apiService.delete(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${c.campaign_id}`, token!);
      addToast(t.deletedOk, 'success');
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    }
  };

  const clientLabel = (c: Campaign) =>
    c.client_org_name || (c.client_organization_id ? '—' : t.clientPublicShort);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{t.title}</h1>
          <p className="mt-1 text-sm text-slate-500 max-w-2xl">{t.sub}</p>
        </div>
        <button
          onClick={openAdd}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          {t.addCampaign}
        </button>
      </div>

      {/* Informational note — campaigns are not a security layer */}
      <div className="bg-sky-50 border border-sky-200 rounded-xl px-4 py-3 flex gap-3 items-start">
        <svg className="w-4 h-4 text-sky-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <p className="text-sm text-sky-800">{t.notLayerNote}</p>
      </div>

      {/* Archived toggle */}
      <label className="inline-flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
        <input
          type="checkbox"
          checked={includeArchived}
          onChange={e => setIncludeArchived(e.target.checked)}
          className="rounded border-slate-300 text-indigo-600"
        />
        {t.includeArchived}
      </label>

      {/* Body */}
      {loading ? (
        <div className="flex items-center justify-center h-48">
          <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : campaigns.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-200 px-6 py-12 text-center text-slate-400 text-sm">
          {t.none}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {campaigns.map(c => (
            <div key={c.campaign_id} className="bg-white rounded-2xl border border-slate-200 p-5 flex flex-col gap-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-semibold text-slate-900 truncate">{c.name}</h3>
                  <span className={`inline-block mt-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                    c.client_organization_id ? 'bg-violet-100 text-violet-700' : 'bg-slate-100 text-slate-600'
                  }`}>
                    {clientLabel(c)}
                  </span>
                </div>
                <span className={`shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                  c.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
                }`}>
                  {c.status === 'active' ? t.statusActive : t.statusArchived}
                </span>
              </div>

              {c.description && (
                <p className="text-sm text-slate-500 line-clamp-2">{c.description}</p>
              )}

              <div className="flex items-center gap-4 text-xs text-slate-500">
                <span><strong className="text-slate-700">{c.jobs_total ?? 0}</strong> {t.jobs}</span>
                <span><strong className="text-slate-700">{c.jobs_active ?? 0}</strong> {t.activeJobs}</span>
              </div>

              <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-100 mt-auto">
                <button
                  onClick={() => navigate(`/jobs?campaign=${c.campaign_id}`)}
                  className="px-3 py-1 rounded-lg bg-indigo-50 text-indigo-700 text-xs font-medium hover:bg-indigo-100 transition-colors"
                >
                  {t.viewJobs}
                </button>
                <button
                  onClick={() => openEdit(c)}
                  className="px-3 py-1 rounded-lg border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors"
                >
                  {t.edit}
                </button>
                <button
                  onClick={() => toggleArchive(c)}
                  className="px-3 py-1 rounded-lg border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors"
                >
                  {c.status === 'active' ? t.archive : t.unarchive}
                </button>
                <button
                  onClick={() => remove(c)}
                  className="px-3 py-1 rounded-lg border border-red-200 text-red-600 text-xs font-medium hover:bg-red-50 transition-colors"
                >
                  {t.delete}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add / Edit modal */}
      {showForm && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden">
            <form onSubmit={submit}>
              <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                <h3 className="font-semibold text-slate-900">{editingId ? t.modalEdit : t.modalAdd}</h3>
                <button type="button" onClick={() => setShowForm(false)} className="text-slate-400 hover:text-slate-600 p-1">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div className="p-6 space-y-4">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.name} <span className="text-red-500">*</span></label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                    placeholder={t.namePlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.description}</label>
                  <textarea
                    rows={3}
                    value={form.description}
                    onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
                    placeholder={t.descPlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
                  />
                </div>

                {/* Client selector — agency tenants only. Organisation tenants are always public. */}
                {isAgency && (
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">{t.client}</label>
                    <select
                      value={form.client_organization_id}
                      onChange={e => setForm(p => ({ ...p, client_organization_id: e.target.value }))}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="">{t.clientPublic}</option>
                      {clientOrgs.map(o => (
                        <option key={o.client_organization_id} value={o.client_organization_id}>
                          {o.organization_name}
                        </option>
                      ))}
                    </select>
                    <p className="text-[11px] text-slate-400 mt-1">{t.clientHint}</p>
                  </div>
                )}

                {/* Status — edit only */}
                {editingId && (
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">{t.status}</label>
                    <select
                      value={form.status}
                      onChange={e => setForm(p => ({ ...p, status: e.target.value as 'active' | 'archived' }))}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400"
                    >
                      <option value="active">{t.statusActive}</option>
                      <option value="archived">{t.statusArchived}</option>
                    </select>
                  </div>
                )}
              </div>

              <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-white transition-colors"
                >
                  {t.cancel}
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="px-5 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                >
                  {saving ? t.saving : editingId ? t.save : t.create}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default CampaignsPage;
