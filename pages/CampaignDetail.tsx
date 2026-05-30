import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, Campaign, CampaignJobRef, CampaignStatus, ClientOrganization, Job } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { AddJobModal } from '../components/AddJobModal';

interface CampaignDetailProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

// ── Translations ──────────────────────────────────────────────────────────────
const T = {
  en: {
    back: 'All Campaigns',
    loading: 'Loading campaign…',
    notFound: 'Campaign not found.',
    editCampaign: 'Edit Campaign',
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    name: 'Campaign Name',
    nameRequired: 'Campaign name is required.',
    description: 'Description',
    client: 'Client',
    clientPublic: 'Public / Shared (no client)',
    clientHint: 'Cannot change client while jobs are linked.',
    startDate: 'Start Date',
    endDate: 'End Date',
    targetHire: 'Target Hire Count',
    targetHirePlaceholder: 'e.g. 10',
    owner: 'Campaign Owner',
    ownerNone: 'No owner assigned',
    notes: 'Notes',
    notesPlaceholder: 'Internal operational notes…',
    publicTitle: 'Public Title (optional)',
    publicTitlePlaceholder: 'External name for future public listing…',
    namePlaceholder: 'e.g. Summer 2026 Hiring Drive',
    descPlaceholder: 'Optional notes about this campaign…',
    statusLabel: 'Status',
    status: {
      draft: 'Draft',
      active: 'Active',
      on_hold: 'On Hold',
      closed: 'Closed',
      cancelled: 'Cancelled',
    } as Record<string, string>,
    clientBadge: 'Client',
    publicBadge: 'Public / Shared',
    ownerLabel: 'Owner',
    dateRange: 'Date Range',
    targetLabel: 'Target Hires',
    statsJobs: 'Jobs',
    statsActive: 'Active',
    statsApps: 'Applications',
    statsQualified: 'Qualified',
    statsPartial: 'Potential',
    statsRejected: 'Rejected',
    statsProgress: 'Fill Rate',
    linkedJobs: 'Linked Jobs',
    addJob: 'Add Job',
    linkJob: 'Link Existing Job',
    noJobs: 'No jobs linked to this campaign yet.',
    viewJob: 'View',
    linkJobTitle: 'Link Existing Job',
    linkJobHint: 'Select a compatible job to link to this campaign.',
    linkJobNone: '— select a job —',
    linkJobBtn: 'Link',
    linkJobLoading: 'Loading…',
    linkJobOk: 'Job linked to campaign.',
    linkJobErr: 'Failed to link job.',
    unlinkJob: 'Remove from Campaign',
    unlinkConfirm: 'Remove this job from the campaign? The job will not be deleted.',
    unlinkOk: 'Job removed from campaign.',
    unlinkErr: 'Failed to remove job.',
    // Status action buttons
    activate: 'Activate',
    pause: 'Pause',
    resume: 'Resume',
    close: 'Close Campaign',
    cancelCampaign: 'Cancel Campaign',
    confirmClose: 'Mark this campaign as Closed (completed)? This is a final state.',
    confirmCancel: 'Cancel this campaign? This is a final state.',
    statusOk: 'Campaign status updated.',
    statusErr: 'Failed to update status.',
    errorLoad: 'Failed to load campaign.',
    errorSave: 'Save failed.',
    updatedOk: 'Campaign updated.',
    notesSection: 'Notes',
    noNotes: 'No notes.',
    fillRateNA: 'No target set',
  },
  ar: {
    back: 'كل الحملات',
    loading: 'جارٍ تحميل الحملة…',
    notFound: 'الحملة غير موجودة.',
    editCampaign: 'تعديل الحملة',
    save: 'حفظ',
    saving: 'جارٍ الحفظ…',
    cancel: 'إلغاء',
    name: 'اسم الحملة',
    nameRequired: 'اسم الحملة مطلوب.',
    description: 'الوصف',
    client: 'العميل',
    clientPublic: 'عام / مشترك (بدون عميل)',
    clientHint: 'لا يمكن تغيير العميل بينما توجد وظائف مرتبطة.',
    startDate: 'تاريخ البداية',
    endDate: 'تاريخ الانتهاء',
    targetHire: 'عدد التعيينات المستهدفة',
    targetHirePlaceholder: 'مثال: 10',
    owner: 'مسؤول الحملة',
    ownerNone: 'لم يُعيَّن مسؤول',
    notes: 'ملاحظات',
    notesPlaceholder: 'ملاحظات تشغيلية داخلية…',
    publicTitle: 'العنوان العام (اختياري)',
    publicTitlePlaceholder: 'اسم خارجي للقائمة العامة المستقبلية…',
    namePlaceholder: 'مثال: حملة توظيف صيف 2026',
    descPlaceholder: 'ملاحظات اختيارية عن هذه الحملة…',
    statusLabel: 'الحالة',
    status: {
      draft: 'مسودة',
      active: 'نشطة',
      on_hold: 'موقوفة',
      closed: 'مغلقة',
      cancelled: 'ملغية',
    } as Record<string, string>,
    clientBadge: 'العميل',
    publicBadge: 'عام / مشترك',
    ownerLabel: 'المسؤول',
    dateRange: 'الفترة الزمنية',
    targetLabel: 'هدف التعيين',
    statsJobs: 'الوظائف',
    statsActive: 'نشطة',
    statsApps: 'الطلبات',
    statsQualified: 'مؤهل',
    statsPartial: 'محتمل',
    statsRejected: 'مرفوض',
    statsProgress: 'نسبة الإنجاز',
    linkedJobs: 'الوظائف المرتبطة',
    addJob: 'إضافة وظيفة',
    linkJob: 'ربط وظيفة موجودة',
    noJobs: 'لا توجد وظائف مرتبطة بهذه الحملة بعد.',
    viewJob: 'عرض',
    linkJobTitle: 'ربط وظيفة موجودة',
    linkJobHint: 'اختر وظيفة متوافقة لربطها بهذه الحملة.',
    linkJobNone: '— اختر وظيفة —',
    linkJobBtn: 'ربط',
    linkJobLoading: 'جارٍ التحميل…',
    linkJobOk: 'تم ربط الوظيفة بالحملة.',
    linkJobErr: 'فشل ربط الوظيفة.',
    unlinkJob: 'إزالة من الحملة',
    unlinkConfirm: 'إزالة هذه الوظيفة من الحملة؟ لن تُحذف الوظيفة.',
    unlinkOk: 'تم إزالة الوظيفة من الحملة.',
    unlinkErr: 'فشل إزالة الوظيفة.',
    activate: 'تفعيل',
    pause: 'إيقاف مؤقت',
    resume: 'استئناف',
    close: 'إغلاق الحملة',
    cancelCampaign: 'إلغاء الحملة',
    confirmClose: 'هل تريد إغلاق هذه الحملة (اكتملت)؟ هذا إجراء نهائي.',
    confirmCancel: 'هل تريد إلغاء هذه الحملة؟ هذا إجراء نهائي.',
    statusOk: 'تم تحديث حالة الحملة.',
    statusErr: 'فشل تحديث الحالة.',
    errorLoad: 'فشل تحميل الحملة.',
    errorSave: 'فشل الحفظ.',
    updatedOk: 'تم تحديث الحملة.',
    notesSection: 'الملاحظات',
    noNotes: 'لا توجد ملاحظات.',
    fillRateNA: 'لم يُحدَّد هدف',
  },
};

// ── Status badge styles ───────────────────────────────────────────────────────
const STATUS_STYLES: Record<string, string> = {
  draft:     'bg-slate-100 text-slate-600',
  active:    'bg-emerald-100 text-emerald-700',
  on_hold:   'bg-amber-100 text-amber-700',
  closed:    'bg-blue-100 text-blue-700',
  cancelled: 'bg-red-100 text-red-600',
};

// ── Edit form state ───────────────────────────────────────────────────────────
interface EditForm {
  name: string;
  description: string;
  start_date: string;
  end_date: string;
  target_hire_count: string;
  notes: string;
  public_title: string;
}

const toEditForm = (c: Campaign): EditForm => ({
  name:              c.name,
  description:       c.description || '',
  start_date:        c.start_date || '',
  end_date:          c.end_date || '',
  target_hire_count: c.target_hire_count != null ? String(c.target_hire_count) : '',
  notes:             c.notes || '',
  public_title:      c.public_title || '',
});

// ── Component ─────────────────────────────────────────────────────────────────

export const CampaignDetailPage: React.FC<CampaignDetailProps> = ({ auth, addToast }) => {
  const { campaignId } = useParams<{ campaignId: string }>();
  const { lang } = useLanguage();
  const t = T[lang];
  const navigate = useNavigate();
  const token = auth.token!;

  const role = (auth.user?.role || '').toLowerCase();
  const canWrite = ['admin', 'hr_manager', 'super_admin'].includes(role);
  const tenantType = auth.user?.tenant_type ?? 'organization';
  const isAgency = tenantType === 'agency' || tenantType === 'individual_recruiter';

  const [campaign, setCampaign] = useState<(Campaign & { jobs: CampaignJobRef[] }) | null>(null);
  const [loading, setLoading] = useState(true);
  const [clientOrgs, setClientOrgs] = useState<ClientOrganization[]>([]);

  // Edit modal
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState<EditForm>({ name: '', description: '', start_date: '', end_date: '', target_hire_count: '', notes: '', public_title: '' });
  const [saving, setSaving] = useState(false);

  // Add Job modal
  const [showAddJob, setShowAddJob] = useState(false);

  // Link existing job modal
  const [showLink, setShowLink] = useState(false);
  const [linkableJobs, setLinkableJobs] = useState<Job[]>([]);
  const [linkableLoading, setLinkableLoading] = useState(false);
  const [selectedLinkJob, setSelectedLinkJob] = useState('');
  const [linking, setLinking] = useState(false);

  const load = useCallback(async () => {
    if (!campaignId || !token) return;
    setLoading(true);
    try {
      const data = await apiService.get(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${campaignId}`, {}, token);
      setCampaign(data);
    } catch {
      addToast(t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [campaignId, token, addToast, t.errorLoad]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!token || !isAgency) return;
    apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, token)
      .then((data: any) => {
        const orgs: ClientOrganization[] = data?.client_organizations ?? [];
        setClientOrgs(orgs.filter(o => o.status === 'active'));
      })
      .catch(() => {});
  }, [token, isAgency]);

  // ── Status transition ──────────────────────────────────────────────────────

  const transitionStatus = async (newStatus: CampaignStatus, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    try {
      await apiService.post(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${campaignId}/status`, { status: newStatus }, token);
      addToast(t.statusOk, 'success');
      await load();
    } catch (err: any) {
      addToast(err.message || t.statusErr, 'error');
    }
  };

  // ── Edit ───────────────────────────────────────────────────────────────────

  const openEdit = () => {
    if (!campaign) return;
    setEditForm(toEditForm(campaign));
    setShowEdit(true);
  };

  const submitEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = editForm.name.trim();
    if (!name) { addToast(t.nameRequired, 'error'); return; }
    setSaving(true);
    try {
      const target = editForm.target_hire_count.trim()
        ? parseInt(editForm.target_hire_count, 10)
        : null;
      await apiService.patch(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${campaignId}`, {
        name,
        description:        editForm.description.trim() || null,
        start_date:         editForm.start_date || null,
        end_date:           editForm.end_date || null,
        target_hire_count:  target && target > 0 ? target : null,
        notes:              editForm.notes.trim() || null,
        public_title:       editForm.public_title.trim() || null,
      }, token);
      addToast(t.updatedOk, 'success');
      setShowEdit(false);
      await load();
    } catch (err: any) {
      addToast(err.message || t.errorSave, 'error');
    } finally {
      setSaving(false);
    }
  };

  // ── Link existing job ──────────────────────────────────────────────────────

  const openLink = async () => {
    if (!campaign) return;
    setShowLink(true);
    setSelectedLinkJob('');
    setLinkableLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL, {}, token);
      const allJobs: Job[] = Array.isArray(data) ? data : [];
      const campClient = campaign.client_organization_id || null;
      const eligible = allJobs.filter(j => {
        const jobClient = j.client_organization_id || null;
        if (jobClient !== campClient) return false;   // wrong client
        if (j.campaign_id) return false;              // already in any campaign
        return true;
      });
      setLinkableJobs(eligible);
    } catch {
      addToast(t.linkJobErr, 'error');
      setShowLink(false);
    } finally {
      setLinkableLoading(false);
    }
  };

  const submitLink = async () => {
    if (!selectedLinkJob) return;
    setLinking(true);
    try {
      await apiService.put(`${WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL}/${selectedLinkJob}`, { campaign_id: campaignId }, token);
      addToast(t.linkJobOk, 'success');
      setShowLink(false);
      await load();
    } catch (err: any) {
      addToast(err.message || t.linkJobErr, 'error');
    } finally {
      setLinking(false);
    }
  };

  const unlinkJob = async (jobId: string) => {
    if (!window.confirm(t.unlinkConfirm)) return;
    try {
      await apiService.put(`${WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL}/${jobId}`, { campaign_id: null }, token);
      addToast(t.unlinkOk, 'success');
      await load();
    } catch (err: any) {
      addToast(err.message || t.unlinkErr, 'error');
    }
  };

  // ── Render helpers ─────────────────────────────────────────────────────────

  const StatusBadge = ({ s }: { s: string }) => (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${STATUS_STYLES[s] ?? 'bg-slate-100 text-slate-600'}`}>
      {t.status[s] ?? s}
    </span>
  );

  const JobStatusChip = ({ s }: { s: string }) => {
    const lower = (s || '').toLowerCase();
    const cls = lower === 'active' ? 'bg-emerald-50 text-emerald-700'
      : lower === 'closed' ? 'bg-slate-100 text-slate-500'
      : 'bg-amber-50 text-amber-700';
    return <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-semibold ${cls}`}>{s}</span>;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="text-center py-16 text-slate-400 text-sm">
        {t.notFound}
        <button onClick={() => navigate('/campaigns')} className="block mx-auto mt-4 text-indigo-600 hover:underline text-sm">{t.back}</button>
      </div>
    );
  }

  const c = campaign;
  const isTerminal = c.status === 'closed' || c.status === 'cancelled';
  const fillPct = c.target_hire_count && c.applications_qualified
    ? Math.min(100, Math.round((c.applications_qualified / c.target_hire_count) * 100))
    : null;

  return (
    <div className="space-y-6 max-w-5xl mx-auto">

      {/* ── Top bar ── */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate('/campaigns')}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
          </svg>
          {t.back}
        </button>
      </div>

      {/* ── Header card ── */}
      <div className="bg-white rounded-2xl border border-slate-200 p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-bold text-slate-900">{c.name}</h1>
              <StatusBadge s={c.status} />
              {c.client_organization_id ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-violet-100 text-violet-700">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                  {c.client_org_name || t.clientBadge}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-600">
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  {t.publicBadge}
                </span>
              )}
            </div>
            {c.description && (
              <p className="mt-2 text-sm text-slate-500 max-w-xl">{c.description}</p>
            )}
          </div>
          {canWrite && (
            <button
              onClick={openEdit}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-slate-50 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
              {t.editCampaign}
            </button>
          )}
        </div>

        {/* Meta row */}
        <div className="flex flex-wrap gap-6 text-sm text-slate-500 pt-2 border-t border-slate-100">
          {c.campaign_owner_name && (
            <span className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
              <span className="text-slate-600 font-medium">{c.campaign_owner_name}</span>
            </span>
          )}
          {(c.start_date || c.end_date) && (
            <span className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              {c.start_date || '—'} → {c.end_date || '—'}
            </span>
          )}
          {c.target_hire_count != null && (
            <span className="flex items-center gap-1.5">
              <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {t.targetLabel}: <span className="font-semibold text-slate-700">{c.target_hire_count}</span>
            </span>
          )}
        </div>

        {/* Status action buttons */}
        {canWrite && !isTerminal && (
          <div className="flex flex-wrap gap-2 pt-2">
            {c.status === 'draft' && (
              <button onClick={() => transitionStatus('active')}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 transition-colors">
                {t.activate}
              </button>
            )}
            {c.status === 'active' && (
              <button onClick={() => transitionStatus('on_hold')}
                className="px-4 py-1.5 rounded-lg bg-amber-500 text-white text-sm font-medium hover:bg-amber-600 transition-colors">
                {t.pause}
              </button>
            )}
            {c.status === 'on_hold' && (
              <button onClick={() => transitionStatus('active')}
                className="px-4 py-1.5 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 transition-colors">
                {t.resume}
              </button>
            )}
            {(c.status === 'active' || c.status === 'on_hold') && (
              <button onClick={() => transitionStatus('closed', t.confirmClose)}
                className="px-4 py-1.5 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors">
                {t.close}
              </button>
            )}
            {c.status !== 'cancelled' && (
              <button onClick={() => transitionStatus('cancelled', t.confirmCancel)}
                className="px-4 py-1.5 rounded-lg border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 transition-colors">
                {t.cancelCampaign}
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Stats row ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white rounded-xl border border-slate-200 p-4 text-center">
          <p className="text-2xl font-bold text-slate-800">{c.jobs_total ?? 0}</p>
          <p className="text-xs text-slate-500 mt-0.5">{t.statsJobs}</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-4 text-center">
          <p className="text-2xl font-bold text-emerald-600">{c.jobs_active ?? 0}</p>
          <p className="text-xs text-slate-500 mt-0.5">{t.statsActive}</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-4 text-center">
          <p className="text-2xl font-bold text-slate-800">{c.applications_total ?? 0}</p>
          <p className="text-xs text-slate-500 mt-0.5">{t.statsApps}</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-4 text-center">
          <p className="text-2xl font-bold text-indigo-600">{c.applications_qualified ?? 0}</p>
          <p className="text-xs text-slate-500 mt-0.5">{t.statsQualified}</p>
        </div>
      </div>

      {/* Application breakdown + fill rate */}
      {((c.applications_total ?? 0) > 0 || c.target_hire_count != null) && (
        <div className="bg-white rounded-xl border border-slate-200 p-4 space-y-3">
          {(c.applications_total ?? 0) > 0 && (
            <div className="flex flex-wrap gap-6 text-sm">
              <span><strong className="text-indigo-600">{c.applications_qualified ?? 0}</strong> <span className="text-slate-500">{t.statsQualified}</span></span>
              <span><strong className="text-amber-600">{c.applications_partial ?? 0}</strong> <span className="text-slate-500">{t.statsPartial}</span></span>
              <span><strong className="text-red-500">{c.applications_rejected ?? 0}</strong> <span className="text-slate-500">{t.statsRejected}</span></span>
            </div>
          )}
          {c.target_hire_count != null && (
            <div>
              <div className="flex justify-between text-xs text-slate-500 mb-1">
                <span>{t.statsProgress}</span>
                <span>{fillPct != null ? `${fillPct}%` : t.fillRateNA}</span>
              </div>
              <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500 rounded-full transition-all duration-500"
                  style={{ width: `${fillPct ?? 0}%` }}
                />
              </div>
              <p className="text-xs text-slate-400 mt-1">{c.applications_qualified ?? 0} / {c.target_hire_count} {t.targetLabel.toLowerCase()}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Notes ── */}
      {c.notes && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
          <p className="text-xs font-semibold text-amber-700 uppercase tracking-wider mb-1">{t.notesSection}</p>
          <p className="text-sm text-amber-900 whitespace-pre-line">{c.notes}</p>
        </div>
      )}

      {/* ── Linked jobs ── */}
      <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <h2 className="font-semibold text-slate-800">{t.linkedJobs}</h2>
          {canWrite && !isTerminal && (
            <div className="flex gap-2">
              <button
                onClick={() => setShowAddJob(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                </svg>
                {t.addJob}
              </button>
              <button
                onClick={openLink}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
                {t.linkJob}
              </button>
            </div>
          )}
        </div>

        {!c.jobs || c.jobs.length === 0 ? (
          <div className="px-6 py-10 text-center text-slate-400 text-sm">{t.noJobs}</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {c.jobs.map((j: CampaignJobRef) => (
              <div key={j.job_id} className="px-6 py-3 flex items-center justify-between gap-4 hover:bg-slate-50 transition-colors">
                <div className="min-w-0 flex items-center gap-3">
                  <span className="text-xs font-mono text-slate-400 shrink-0">{j.job_code}</span>
                  <span className="text-sm font-medium text-slate-800 truncate">{j.job_title}</span>
                  <JobStatusChip s={j.job_status} />
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => navigate(`/jobs/${j.job_id}`)}
                    className="px-3 py-1 rounded-lg bg-indigo-50 text-indigo-700 text-xs font-medium hover:bg-indigo-100 transition-colors"
                  >
                    {t.viewJob}
                  </button>
                  {canWrite && !isTerminal && (
                    <button
                      onClick={() => unlinkJob(j.job_id)}
                      className="px-3 py-1 rounded-lg border border-slate-200 text-slate-500 text-xs font-medium hover:bg-slate-100 transition-colors"
                    >
                      {t.unlinkJob}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Edit modal ── */}
      {showEdit && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden">
            <form onSubmit={submitEdit}>
              <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                <h3 className="font-semibold text-slate-900">{t.editCampaign}</h3>
                <button type="button" onClick={() => setShowEdit(false)} className="text-slate-400 hover:text-slate-600 p-1">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.name} <span className="text-red-500">*</span></label>
                  <input type="text" value={editForm.name} onChange={e => setEditForm(p => ({ ...p, name: e.target.value }))}
                    placeholder={t.namePlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.description}</label>
                  <textarea rows={2} value={editForm.description} onChange={e => setEditForm(p => ({ ...p, description: e.target.value }))}
                    placeholder={t.descPlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">{t.startDate}</label>
                    <input type="date" value={editForm.start_date} onChange={e => setEditForm(p => ({ ...p, start_date: e.target.value }))}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-500 mb-1">{t.endDate}</label>
                    <input type="date" value={editForm.end_date} onChange={e => setEditForm(p => ({ ...p, end_date: e.target.value }))}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.targetHire}</label>
                  <input type="number" min={1} value={editForm.target_hire_count} onChange={e => setEditForm(p => ({ ...p, target_hire_count: e.target.value }))}
                    placeholder={t.targetHirePlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.notes}</label>
                  <textarea rows={3} value={editForm.notes} onChange={e => setEditForm(p => ({ ...p, notes: e.target.value }))}
                    placeholder={t.notesPlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none" />
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1">{t.publicTitle}</label>
                  <input type="text" value={editForm.public_title} onChange={e => setEditForm(p => ({ ...p, public_title: e.target.value }))}
                    placeholder={t.publicTitlePlaceholder}
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
                </div>
              </div>
              <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-end gap-3">
                <button type="button" onClick={() => setShowEdit(false)}
                  className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-white transition-colors">
                  {t.cancel}
                </button>
                <button type="submit" disabled={saving}
                  className="px-5 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors">
                  {saving ? t.saving : t.save}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Link existing job modal ── */}
      {showLink && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-semibold text-slate-900">{t.linkJobTitle}</h3>
              <button onClick={() => setShowLink(false)} className="text-slate-400 hover:text-slate-600 p-1">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-sm text-slate-500">{t.linkJobHint}</p>
              {linkableLoading ? (
                <p className="text-sm text-slate-400">{t.linkJobLoading}</p>
              ) : (
                <select
                  value={selectedLinkJob}
                  onChange={e => setSelectedLinkJob(e.target.value)}
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400"
                >
                  <option value="">{t.linkJobNone}</option>
                  {linkableJobs.map(j => (
                    <option key={j.job_id} value={j.job_id}>
                      {j.job_code} — {j.job_title}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div className="px-6 py-4 bg-slate-50 border-t border-slate-100 flex justify-end gap-3">
              <button onClick={() => setShowLink(false)}
                className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-white transition-colors">
                {t.cancel}
              </button>
              <button
                onClick={submitLink}
                disabled={!selectedLinkJob || linking}
                className="px-5 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                {linking ? t.saving : t.linkJobBtn}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Add Job modal (pre-seeded with campaign) ── */}
      {showAddJob && (
        <AddJobModal
          onClose={() => setShowAddJob(false)}
          onSuccess={() => { setShowAddJob(false); load(); }}
          token={token}
          user={auth.user}
          addToast={(msg, type) => addToast(msg, type as any)}
          prefilledCampaign={{
            campaign_id:            c.campaign_id,
            name:                   c.name,
            client_organization_id: c.client_organization_id ?? null,
            client_org_name:        c.client_org_name ?? null,
          }}
        />
      )}
    </div>
  );
};

export default CampaignDetailPage;
