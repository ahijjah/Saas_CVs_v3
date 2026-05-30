
import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, Campaign, CampaignJobRef, CampaignStatus, ClientOrganization, Job } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { AddJobModal } from '../components/AddJobModal';

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
    unlinkJob: 'Remove',
    unlinkConfirm: 'Remove this job from the campaign? The job will not be deleted.',
    unlinkOk: 'Job removed from campaign.',
    unlinkErr: 'Failed to remove job.',
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
    // Tabs
    tabOverview: 'Overview',
    tabJobs: 'Jobs',
    tabCandidates: 'Candidates',
    tabActivity: 'Activity',
    tabReports: 'Reports',
    // Overview KPI cards
    kpiActiveJobs: 'Active Jobs',
    kpiTotalApps: 'Total Applications',
    kpiAwaitingReview: 'Awaiting Review',
    kpiShortlisted: 'Shortlisted',
    kpiInterviewing: 'Interviewing',
    kpiHired: 'Hired',
    kpiRejected: 'Rejected',
    kpiTimeOpen: 'Time Open',
    kpiTimeOpenUnit: 'days',
    kpiFillRate: 'Fill Rate',
    kpiHiredOf: 'hired of',
    kpiTarget: 'target',
    // Overview
    overviewMeta: 'Campaign Details',
    overviewDescription: 'Description',
    overviewNotes: 'Notes',
    overviewOwner: 'Campaign Owner',
    overviewDates: 'Date Range',
    overviewTargetHires: 'Target Hires',
    overviewNoDescription: 'No description.',
    overviewNoNotes: 'No notes.',
    overviewNoOwner: 'No owner assigned.',
    overviewNoDates: 'No dates set.',
    // Placeholder tabs
    phaseComingSoon: 'Coming in a future phase.',
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
    unlinkJob: 'إزالة',
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
    tabOverview: 'نظرة عامة',
    tabJobs: 'الوظائف',
    tabCandidates: 'المرشحون',
    tabActivity: 'النشاط',
    tabReports: 'التقارير',
    kpiActiveJobs: 'وظائف نشطة',
    kpiTotalApps: 'إجمالي الطلبات',
    kpiAwaitingReview: 'في انتظار المراجعة',
    kpiShortlisted: 'المختارون',
    kpiInterviewing: 'المقابلات',
    kpiHired: 'تم التعيين',
    kpiRejected: 'مرفوض',
    kpiTimeOpen: 'مدة مفتوحة',
    kpiTimeOpenUnit: 'يوم',
    kpiFillRate: 'نسبة الإنجاز',
    kpiHiredOf: 'معيَّن من',
    kpiTarget: 'هدف',
    overviewMeta: 'تفاصيل الحملة',
    overviewDescription: 'الوصف',
    overviewNotes: 'الملاحظات',
    overviewOwner: 'مسؤول الحملة',
    overviewDates: 'الفترة الزمنية',
    overviewTargetHires: 'هدف التعيين',
    overviewNoDescription: 'لا يوجد وصف.',
    overviewNoNotes: 'لا توجد ملاحظات.',
    overviewNoOwner: 'لم يُعيَّن مسؤول.',
    overviewNoDates: 'لم تُحدَّد تواريخ.',
    phaseComingSoon: 'قادم في مرحلة مستقبلية.',
  },
};

// ── Types ─────────────────────────────────────────────────────────────────────

type TabId = 'overview' | 'jobs' | 'candidates' | 'activity' | 'reports';

interface CampaignSummary {
  campaign_id:        string;
  jobs_total:         number;
  active_jobs:        number;
  total_applications: number;
  awaiting_review:    number;
  under_review:       number;
  shortlisted:        number;
  interviewing:       number;
  offer_made:         number;
  hired:              number;
  rejected:           number;
  withdrawn:          number;
  on_hold:            number;
  target_hire_count:  number | null;
  fill_rate_pct:      number | null;
  time_open_days:     number | null;
}

interface EditForm {
  name: string;
  description: string;
  start_date: string;
  end_date: string;
  target_hire_count: string;
  notes: string;
  public_title: string;
}

interface CampaignDetailProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

// ── Style maps ────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  draft:     'bg-slate-100 text-slate-600',
  active:    'bg-emerald-100 text-emerald-700',
  on_hold:   'bg-amber-100 text-amber-700',
  closed:    'bg-blue-100 text-blue-700',
  cancelled: 'bg-red-100 text-red-600',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const toEditForm = (c: Campaign): EditForm => ({
  name:              c.name,
  description:       c.description || '',
  start_date:        c.start_date || '',
  end_date:          c.end_date || '',
  target_hire_count: c.target_hire_count != null ? String(c.target_hire_count) : '',
  notes:             c.notes || '',
  public_title:      c.public_title || '',
});

// ── Sub-components ────────────────────────────────────────────────────────────

const StatusBadge: React.FC<{ s: string; labels: Record<string, string> }> = ({ s, labels }) => (
  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${STATUS_STYLES[s] ?? 'bg-slate-100 text-slate-600'}`}>
    {labels[s] ?? s}
  </span>
);

const JobStatusChip: React.FC<{ s: string }> = ({ s }) => {
  const lower = (s || '').toLowerCase();
  const cls = lower === 'active' ? 'bg-emerald-50 text-emerald-700'
    : lower === 'closed' ? 'bg-slate-100 text-slate-500'
    : 'bg-amber-50 text-amber-700';
  return <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-semibold ${cls}`}>{s}</span>;
};

interface KpiCardProps {
  label:    string;
  value:    React.ReactNode;
  color?:   string;
  sub?:     string;
  onClick?: () => void;
}
const KpiCard: React.FC<KpiCardProps> = ({ label, value, color = 'text-slate-800', sub, onClick }) => (
  <div
    onClick={onClick}
    className={`bg-white rounded-xl border border-slate-200 p-4 flex flex-col items-center justify-center text-center min-h-[90px] ${onClick ? 'cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors' : ''}`}
  >
    <p className={`text-2xl font-bold ${color}`}>{value}</p>
    <p className="text-xs text-slate-500 mt-0.5 leading-tight">{label}</p>
    {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
  </div>
);

// ── Main component ────────────────────────────────────────────────────────────

export const CampaignDetailPage: React.FC<CampaignDetailProps> = ({ auth, addToast }) => {
  const { campaignId } = useParams<{ campaignId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const { lang } = useLanguage();
  const t = T[lang];
  const navigate = useNavigate();
  const token = auth.token!;

  const role = (auth.user?.role || '').toLowerCase();
  const canWrite = ['admin', 'hr_manager', 'super_admin'].includes(role);
  const tenantType = auth.user?.tenant_type ?? 'organization';
  const isAgency = tenantType === 'agency' || tenantType === 'individual_recruiter';

  const activeTab = (searchParams.get('tab') || 'overview') as TabId;
  const setTab = (tab: TabId) => setSearchParams({ tab });

  // ── Core campaign state ───────────────────────────────────────────────────
  const [campaign, setCampaign] = useState<(Campaign & { jobs: CampaignJobRef[] }) | null>(null);
  const [loading, setLoading] = useState(true);

  // ── Summary state (Overview KPIs) ─────────────────────────────────────────
  const [summary, setSummary] = useState<CampaignSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  // ── Candidates state ──────────────────────────────────────────────────────
  const [candidates, setCandidates] = useState<any[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesFilter, setCandidatesFilter] = useState<string | null>(null);

  // ── Edit modal state ──────────────────────────────────────────────────────
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState<EditForm>({ name: '', description: '', start_date: '', end_date: '', target_hire_count: '', notes: '', public_title: '' });
  const [saving, setSaving] = useState(false);

  // ── Add / Link Job state ──────────────────────────────────────────────────
  const [showAddJob, setShowAddJob] = useState(false);
  const [showLink, setShowLink] = useState(false);
  const [linkableJobs, setLinkableJobs] = useState<Job[]>([]);
  const [linkableLoading, setLinkableLoading] = useState(false);
  const [selectedLinkJob, setSelectedLinkJob] = useState('');
  const [linking, setLinking] = useState(false);

  // ── Data loaders ──────────────────────────────────────────────────────────

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

  const loadSummary = useCallback(async () => {
    if (!campaignId || !token) return;
    setSummaryLoading(true);
    try {
      const data = await apiService.get(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${campaignId}/summary`, {}, token);
      setSummary(data);
    } catch {
      // Non-fatal; Overview shows empty state if summary fails
    } finally {
      setSummaryLoading(false);
    }
  }, [campaignId, token]);

  const loadCandidates = useCallback(async (filter: string | null = null) => {
    if (!campaignId || !token) return;
    setCandidatesLoading(true);
    try {
      const params: Record<string, string> = {};
      if (filter) params.workflow_status = filter;
      const data = await apiService.get(`${WEBHOOK_CONFIG.CAMPAIGNS_URL}/${campaignId}/candidates`, params, token);
      setCandidates(data.candidates || []);
      setCandidatesFilter(filter);
    } catch {
      setCandidates([]);
    } finally {
      setCandidatesLoading(false);
    }
  }, [campaignId, token]);

  useEffect(() => { load(); }, [load]);

  // Load summary when the Overview tab is active
  useEffect(() => {
    if (activeTab === 'overview') loadSummary();
  }, [activeTab, loadSummary]);

  // Load candidates when the Candidates tab is active
  useEffect(() => {
    if (activeTab === 'candidates') loadCandidates(candidatesFilter);
  }, [activeTab, loadCandidates, candidatesFilter]);

  useEffect(() => {
    if (!token || !isAgency) return;
    apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, token)
      .then((data: any) => {
        const orgs: ClientOrganization[] = data?.client_organizations ?? [];
        // clientOrgs used for potential future edit form enhancement; keep load silent
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
      if (activeTab === 'overview') loadSummary();
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
        description:       editForm.description.trim() || null,
        start_date:        editForm.start_date || null,
        end_date:          editForm.end_date || null,
        target_hire_count: target && target > 0 ? target : null,
        notes:             editForm.notes.trim() || null,
        public_title:      editForm.public_title.trim() || null,
      }, token);
      addToast(t.updatedOk, 'success');
      setShowEdit(false);
      await load();
      if (activeTab === 'overview') loadSummary();
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
        if (jobClient !== campClient) return false;
        if (j.campaign_id) return false;
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
      if (activeTab === 'overview') loadSummary();
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
      if (activeTab === 'overview') loadSummary();
    } catch (err: any) {
      addToast(err.message || t.unlinkErr, 'error');
    }
  };

  // ── Loading / not found guards ─────────────────────────────────────────────

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

  // ── Tab definitions ────────────────────────────────────────────────────────

  const tabs: { id: TabId; label: string }[] = [
    { id: 'overview',   label: t.tabOverview   },
    { id: 'jobs',       label: t.tabJobs       },
    { id: 'candidates', label: t.tabCandidates },
    { id: 'activity',   label: t.tabActivity   },
    { id: 'reports',    label: t.tabReports    },
  ];

  // ── Render: Overview tab ───────────────────────────────────────────────────

  const renderOverview = () => {
    const s = summary;
    const fillPct = s?.fill_rate_pct ?? null;
    const hired = s?.hired ?? 0;
    const target = s?.target_hire_count ?? c.target_hire_count ?? null;

    return (
      <div className="space-y-5">
        {/* KPI cards row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <KpiCard
            label={t.kpiActiveJobs}
            value={summaryLoading ? '…' : (s?.active_jobs ?? '—')}
            color="text-emerald-600"
            onClick={() => setTab('jobs')}
          />
          <KpiCard
            label={t.kpiTotalApps}
            value={summaryLoading ? '…' : (s?.total_applications ?? '—')}
            color="text-slate-800"
            onClick={() => { setTab('candidates'); setCandidatesFilter(null); }}
          />
          <KpiCard
            label={t.kpiAwaitingReview}
            value={summaryLoading ? '…' : (s?.awaiting_review ?? '—')}
            color="text-sky-700"
            onClick={() => { setTab('candidates'); setCandidatesFilter('awaiting_review'); }}
          />
          <KpiCard
            label={t.kpiShortlisted}
            value={summaryLoading ? '…' : (s?.shortlisted ?? '—')}
            color="text-indigo-600"
            onClick={() => { setTab('candidates'); setCandidatesFilter('shortlisted'); }}
          />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <KpiCard
            label={t.kpiInterviewing}
            value={summaryLoading ? '…' : (s?.interviewing ?? '—')}
            color="text-purple-600"
            onClick={() => { setTab('candidates'); setCandidatesFilter('interviewing'); }}
          />
          <KpiCard
            label={t.kpiHired}
            value={summaryLoading ? '…' : hired}
            color="text-green-700"
            onClick={() => { setTab('candidates'); setCandidatesFilter('hired'); }}
          />
          <KpiCard
            label={t.kpiRejected}
            value={summaryLoading ? '…' : (s?.rejected ?? '—')}
            color="text-red-600"
            onClick={() => { setTab('candidates'); setCandidatesFilter('rejected'); }}
          />
          <KpiCard
            label={t.kpiTimeOpen}
            value={summaryLoading ? '…' : (s?.time_open_days != null ? s.time_open_days : '—')}
            color="text-slate-600"
            sub={s?.time_open_days != null ? t.kpiTimeOpenUnit : undefined}
          />
        </div>

        {/* Fill-rate progress (hired / target) */}
        {target != null && (
          <div className="bg-white rounded-xl border border-slate-200 p-5">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-slate-700">{t.kpiFillRate}</span>
              <span className="text-sm text-slate-500">
                {hired} {t.kpiHiredOf} {target} {t.kpiTarget}
                {fillPct != null && <span className="ml-2 font-bold text-slate-700">{fillPct}%</span>}
              </span>
            </div>
            <div className="h-2.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-green-500 rounded-full transition-all duration-500"
                style={{ width: `${fillPct ?? 0}%` }}
              />
            </div>
            {fillPct == null && (
              <p className="text-xs text-slate-400 mt-1">{t.fillRateNA}</p>
            )}
          </div>
        )}

        {/* Campaign meta */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">
          <h3 className="text-sm font-semibold text-slate-700">{t.overviewMeta}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">{t.overviewOwner}</p>
              <p className="text-slate-700">{c.campaign_owner_name || <span className="text-slate-400">{t.overviewNoOwner}</span>}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">{t.overviewDates}</p>
              <p className="text-slate-700">
                {c.start_date || c.end_date
                  ? `${c.start_date || '—'} → ${c.end_date || '—'}`
                  : <span className="text-slate-400">{t.overviewNoDates}</span>}
              </p>
            </div>
            {target != null && (
              <div>
                <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">{t.overviewTargetHires}</p>
                <p className="text-slate-700 font-semibold">{target}</p>
              </div>
            )}
          </div>
          {c.description && (
            <div>
              <p className="text-xs font-medium text-slate-400 uppercase tracking-wide mb-1">{t.overviewDescription}</p>
              <p className="text-sm text-slate-600 whitespace-pre-line">{c.description}</p>
            </div>
          )}
          {c.notes && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
              <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1">{t.overviewNotes}</p>
              <p className="text-sm text-amber-900 whitespace-pre-line">{c.notes}</p>
            </div>
          )}
        </div>
      </div>
    );
  };

  // ── Render: Jobs tab ───────────────────────────────────────────────────────

  const renderJobs = () => (
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
  );

  // ── Render: Candidates tab ────────────────────────────────────────────────

  const renderCandidates = () => (
    <div className="space-y-5">
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        {/* Header with filter info */}
        <div className="px-6 py-4 border-b border-slate-100">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <h2 className="font-semibold text-slate-800">
              {candidatesFilter
                ? `${candidatesFilter.replace(/_/g, ' ').charAt(0).toUpperCase() + candidatesFilter.replace(/_/g, ' ').slice(1).toLowerCase()}`
                : 'All Candidates'}
            </h2>
            {candidatesFilter && (
              <button
                onClick={() => setCandidatesFilter(null)}
                className="text-xs text-slate-500 hover:text-slate-800 underline transition-colors"
              >
                Clear filter
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500 mt-2">
            {candidatesLoading ? 'Loading…' : `${candidates.length} candidate${candidates.length !== 1 ? 's' : ''}`}
          </p>
        </div>

        {/* Content */}
        {candidatesLoading ? (
          <div className="px-6 py-10 flex items-center justify-center">
            <div className="w-6 h-6 border-3 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : candidates.length === 0 ? (
          <div className="px-6 py-10 text-center text-slate-400 text-sm">
            <p>No candidates in this campaign{candidatesFilter ? ' with this status' : ''}.</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-600 uppercase tracking-wider">Name</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-600 uppercase tracking-wider">Job</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-600 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-600 uppercase tracking-wider">Score</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-slate-600 uppercase tracking-wider">Applied</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map(cand => (
                  <tr key={cand.application_id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-3 text-slate-900 font-medium">{cand.candidate_name}</td>
                    <td className="px-6 py-3 text-slate-700">{cand.job_title}</td>
                    <td className="px-6 py-3">
                      <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${
                        cand.workflow_status === 'awaiting_review' ? 'bg-sky-100 text-sky-700' :
                        cand.workflow_status === 'under_review' ? 'bg-blue-100 text-blue-700' :
                        cand.workflow_status === 'shortlisted' ? 'bg-indigo-100 text-indigo-700' :
                        cand.workflow_status === 'interviewing' ? 'bg-purple-100 text-purple-700' :
                        cand.workflow_status === 'offer_made' ? 'bg-amber-100 text-amber-700' :
                        cand.workflow_status === 'hired' ? 'bg-green-100 text-green-800' :
                        cand.workflow_status === 'rejected' ? 'bg-red-100 text-red-700' :
                        cand.workflow_status === 'withdrawn' ? 'bg-orange-100 text-orange-700' :
                        cand.workflow_status === 'on_hold' ? 'bg-yellow-100 text-yellow-700' :
                        'bg-slate-100 text-slate-700'
                      }`}>
                        {cand.workflow_status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-6 py-3 text-slate-700">{cand.ai_score != null ? `${(cand.ai_score * 100).toFixed(0)}%` : '—'}</td>
                    <td className="px-6 py-3 text-slate-500 text-xs">
                      {cand.applied_at ? new Date(cand.applied_at).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Coming soon note */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
        <p className="text-sm text-blue-900">
          <strong>Full candidate management</strong> (profile view, action history, direct messaging) coming in the next phase.
        </p>
      </div>
    </div>
  );

  // ── Render: placeholder tabs ───────────────────────────────────────────────

  const renderPlaceholder = (tabLabel: string) => (
    <div className="bg-white rounded-xl border border-slate-200 flex flex-col items-center justify-center py-16 text-center gap-3">
      <svg className="w-10 h-10 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <p className="text-sm font-semibold text-slate-500">{tabLabel}</p>
      <p className="text-xs text-slate-400">{t.phaseComingSoon}</p>
    </div>
  );

  // ── Main render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5 max-w-5xl mx-auto">

      {/* ── Back nav ── */}
      <button
        onClick={() => navigate('/campaigns')}
        className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
        </svg>
        {t.back}
      </button>

      {/* ── Persistent header card ── */}
      <div className="bg-white rounded-2xl border border-slate-200 p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="text-2xl font-bold text-slate-900">{c.name}</h1>
              <StatusBadge s={c.status} labels={t.status} />
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

        {/* Status action buttons */}
        {canWrite && !isTerminal && (
          <div className="flex flex-wrap gap-2 pt-1">
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

      {/* ── Tab strip ── */}
      <div className="flex border-b border-slate-200 bg-white rounded-t-xl overflow-hidden">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setTab(tab.id)}
            className={`
              px-5 py-3 text-sm font-medium transition-colors border-b-2 -mb-px
              ${activeTab === tab.id
                ? 'border-indigo-600 text-indigo-700 bg-white'
                : 'border-transparent text-slate-500 hover:text-slate-800 hover:bg-slate-50'}
            `}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div>
        {activeTab === 'overview'   && renderOverview()}
        {activeTab === 'jobs'       && renderJobs()}
        {activeTab === 'candidates' && renderCandidates()}
        {activeTab === 'activity'   && renderPlaceholder(t.tabActivity)}
        {activeTab === 'reports'    && renderPlaceholder(t.tabReports)}
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

      {/* ── Add Job modal ── */}
      {showAddJob && (
        <AddJobModal
          onClose={() => setShowAddJob(false)}
          onSuccess={() => { setShowAddJob(false); load(); if (activeTab === 'overview') loadSummary(); }}
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
