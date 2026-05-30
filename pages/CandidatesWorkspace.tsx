
/**
 * CandidatesWorkspace — Phase 3c
 *
 * Global recruiter view of all applications across all jobs/campaigns.
 * Uses GET /applications (tenant-wide mode, no job_id) with optional filters.
 *
 * Filter state is persisted in URL query params so views are bookmarkable.
 *
 * Navigation: clicking a row opens the job-scoped application detail:
 *   /applications?job_id=<job_id>&app_id=<application_id>
 *
 * Phase 4 separation: failed/blocked/pre-AI applications are excluded from
 * recruiter-operational views at the query level. The Awaiting Review quick
 * view always adds processing_status=ai_scored so only recruiter-actionable
 * candidates appear. Failed/Blocked uses the 'failed_or_blocked' backend alias
 * that expands to all system-stopped processing statuses.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, WorkflowStatus } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';
import {
  WORKFLOW_STATUS_STYLES,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
} from '../constants/workflow';

// ── Types ──────────────────────────────────────────────────────────────────────

interface CandidatesWorkspaceProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface Candidate {
  application_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string | null;
  job_code: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  client_organization_id: string | null;
  client_org_name: string | null;
  status: string | null;           // AI decision: qualified/partial/rejected/null
  processing_status: string;
  workflow_status: WorkflowStatus;
  score: number | null;
  applied_at: string | null;
  updated_at: string | null;
  duplicate_status: string;
  recruiter_notes: string | null;
}

interface Pagination {
  page: number;
  limit: number;
  total: number;
  has_more: boolean;
}

interface Campaign {
  campaign_id: string;
  name: string;
}

interface ClientOrganization {
  client_organization_id: string;
  organization_name: string;
}

// ── Quick view definitions ────────────────────────────────────────────────────

type QuickView =
  | 'all'
  | 'awaiting_review'
  | 'under_review'
  | 'interviewing'
  | 'in_process'
  | 'hired'
  | 'failed_blocked'
  | 'recent';

interface QuickViewDef {
  id: QuickView;
  labelEn: string;
  labelAr: string;
  params: Record<string, string>;
}

const QUICK_VIEWS: QuickViewDef[] = [
  {
    id: 'awaiting_review',
    labelEn: 'Awaiting Review',
    labelAr: 'في انتظار المراجعة',
    // Only recruiter-actionable: must be ai_scored, not failed/blocked
    params: { workflow_status: 'awaiting_review', processing_status: 'ai_scored' },
  },
  {
    id: 'under_review',
    labelEn: 'Under Review',
    labelAr: 'قيد المراجعة',
    params: { workflow_status: 'under_review' },
  },
  {
    id: 'interviewing',
    labelEn: 'Interviewing',
    labelAr: 'مقابلة',
    params: { workflow_status: 'interviewing' },
  },
  {
    id: 'in_process',
    labelEn: 'In Process',
    labelAr: 'قيد المعالجة',
    params: {},
  },
  {
    id: 'hired',
    labelEn: 'Hired',
    labelAr: 'تم التعيين',
    params: { workflow_status: 'hired' },
  },
  {
    id: 'failed_blocked',
    labelEn: 'Failed / Blocked',
    labelAr: 'فشل / محظور',
    // 'failed_or_blocked' is a backend alias that expands to all system-stopped statuses
    params: { processing_status: 'failed_or_blocked' },
  },
  {
    id: 'recent',
    labelEn: 'Recent',
    labelAr: 'حديثاً',
    params: { sort_by: 'applied_at', sort_order: 'desc' },
  },
];

// ── Translations ──────────────────────────────────────────────────────────────

const T = {
  en: {
    pageTitle: 'Candidates',
    loading: 'Loading candidates…',
    noResults: 'No candidates match the selected filters.',
    noResultsHint: 'Try clearing filters or selecting a different view.',
    // Table columns
    colCandidate: 'Candidate',
    colJob: 'Job',
    colAiMatch: 'AI Match',
    colAiResult: 'AI Result',
    colWorkflow: 'Workflow',
    colProcessing: 'Processing',
    colApplied: 'Applied',
    colActions: '',
    // Action
    actionView: 'View',
    // Filters
    filterWorkflow: 'Workflow Status',
    filterProcessing: 'Processing Status',
    filterAiResult: 'AI Result',
    filterCampaign: 'Campaign',
    filterClient: 'Client Organization',
    filterSearch: 'Search candidate…',
    clearFilters: 'Clear filters',
    allStatuses: 'All',
    // Pagination
    showing: 'Showing',
    of: 'of',
    prev: 'Previous',
    next: 'Next',
    // AI decision labels
    decQualified: 'Qualified',
    decPartial: 'Partial',
    decRejected: 'Rejected',
    decNotScored: 'Not Scored',
    // Processing labels
    procPending: 'Pending',
    procAiScored: 'AI Scored',
    procFailed: 'Failed',
    procSecurityBlocked: 'Security Blocked',
    procDuplicateBlocked: 'Duplicate Blocked',
    procExtractionFailed: 'Extraction Failed',
    procProcessingFailed: 'Processing Failed',
    procStopped: 'Stopped',
    procQueued: 'Queued',
    procProcessing: 'Processing',
  },
  ar: {
    pageTitle: 'المرشحون',
    loading: 'جارٍ تحميل المرشحين…',
    noResults: 'لا يوجد مرشحون يطابقون الفلاتر المحددة.',
    noResultsHint: 'حاول مسح الفلاتر أو اختيار عرض مختلف.',
    colCandidate: 'المرشح',
    colJob: 'الوظيفة',
    colAiMatch: 'تطابق الذكاء',
    colAiResult: 'نتيجة الذكاء',
    colWorkflow: 'مرحلة التوظيف',
    colProcessing: 'حالة المعالجة',
    colApplied: 'تاريخ التقديم',
    colActions: '',
    actionView: 'عرض',
    filterWorkflow: 'مرحلة التوظيف',
    filterProcessing: 'حالة المعالجة',
    filterAiResult: 'نتيجة الذكاء',
    filterCampaign: 'الحملة',
    filterClient: 'منظمة العميل',
    filterSearch: 'بحث عن مرشح…',
    clearFilters: 'مسح الفلاتر',
    allStatuses: 'الكل',
    showing: 'عرض',
    of: 'من',
    prev: 'السابق',
    next: 'التالي',
    decQualified: 'مؤهل',
    decPartial: 'جزئي',
    decRejected: 'مرفوض',
    decNotScored: 'غير مقيَّم',
    procPending: 'معلق',
    procAiScored: 'مقيَّم بالذكاء',
    procFailed: 'فشل',
    procSecurityBlocked: 'محظور أمنياً',
    procDuplicateBlocked: 'مكرر موقوف',
    procExtractionFailed: 'فشل الاستخراج',
    procProcessingFailed: 'فشل المعالجة',
    procStopped: 'موقوف',
    procQueued: 'في الانتظار',
    procProcessing: 'جارٍ المعالجة',
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function aiDecisionStyle(decision: string | null): string {
  if (decision === 'qualified') return 'bg-green-100 text-green-800';
  if (decision === 'partial')   return 'bg-yellow-100 text-yellow-800';
  if (decision === 'rejected' || decision === 'rejected_low_match') return 'bg-red-100 text-red-700';
  return 'bg-slate-100 text-slate-500';
}

function aiDecisionLabel(decision: string | null, t: typeof T['en']): string {
  if (decision === 'qualified') return t.decQualified;
  if (decision === 'partial')   return t.decPartial;
  if (decision === 'rejected' || decision === 'rejected_low_match') return t.decRejected;
  return t.decNotScored;
}

function processingStyle(status: string): string {
  if (status === 'ai_scored')         return 'bg-green-50 text-green-700';
  if (status === 'failed' || status === 'processing_failed' || status === 'extraction_failed') return 'bg-red-100 text-red-700';
  if (status === 'security_blocked' || status === 'duplicate_blocked') return 'bg-orange-100 text-orange-700';
  if (status === 'stopped')           return 'bg-slate-100 text-slate-600';
  if (status === 'queued' || status === 'processing') return 'bg-blue-50 text-blue-600';
  return 'bg-slate-100 text-slate-500'; // pending/unknown
}

function processingLabel(status: string, t: typeof T['en']): string {
  const map: Record<string, string> = {
    ai_scored:          t.procAiScored,
    failed:             t.procFailed,
    processing_failed:  t.procProcessingFailed,
    extraction_failed:  t.procExtractionFailed,
    security_blocked:   t.procSecurityBlocked,
    duplicate_blocked:  t.procDuplicateBlocked,
    stopped:            t.procStopped,
    queued:             t.procQueued,
    processing:         t.procProcessing,
    pending:            t.procPending,
  };
  return map[status] ?? status;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); }
  catch { return iso; }
}

// Build API params from active filters + quick view
function buildApiParams(
  view: QuickView,
  workflowFilter: string,
  processingFilter: string,
  aiResultFilter: string,
  campaignFilter: string,
  clientFilter: string,
  search: string,
  page: number,
): Record<string, string> {
  const p: Record<string, string> = {
    limit: '50',
    page: String(page),
    sort_by: 'applied_at',
    sort_order: 'desc',
  };

  // Quick view overrides specific params
  // awaiting_review: recruiter-actionable only (ai_scored excludes failed/blocked)
  if (view === 'awaiting_review')   { p.workflow_status = 'awaiting_review'; p.processing_status = 'ai_scored'; }
  else if (view === 'under_review') p.workflow_status = 'under_review';
  else if (view === 'interviewing') p.workflow_status = 'interviewing';
  else if (view === 'hired')        p.workflow_status = 'hired';
  // failed_or_blocked: backend alias for all system-stopped processing statuses
  else if (view === 'failed_blocked') { p.processing_status = 'failed_or_blocked'; }
  else if (view === 'recent')       { p.sort_by = 'applied_at'; p.sort_order = 'desc'; }
  // in_process: applied by manual workflowFilter below

  // Manual filters (override quick view if set)
  if (workflowFilter)   p.workflow_status = workflowFilter;
  if (processingFilter) p.processing_status = processingFilter;
  if (aiResultFilter)   p.ai_decision = aiResultFilter;
  if (campaignFilter)   p.campaign_id = campaignFilter;
  if (clientFilter)     p.client_organization_id = clientFilter;
  if (search.trim())    p.search = search.trim();

  return p;
}

// ── Main Component ────────────────────────────────────────────────────────────

export const CandidatesWorkspace: React.FC<CandidatesWorkspaceProps> = ({ auth, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const wfLabels = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { setPageTitle } = usePageTitle();

  // Stable ref so addToast never appears in dependency arrays (avoids infinite loops
  // caused by parent components re-creating the function on every render).
  const addToastRef = useRef(addToast);
  useEffect(() => { addToastRef.current = addToast; });

  // Derive initial state from URL
  const initialView = (searchParams.get('view') as QuickView) || 'all';
  const initialWorkflow = searchParams.get('workflow_status') || '';
  const initialProcessing = searchParams.get('processing_status') || '';
  const initialAiResult = searchParams.get('ai_decision') || '';
  const initialCampaign = searchParams.get('campaign_id') || '';
  const initialClient = searchParams.get('client_organization_id') || '';
  const initialSearch = searchParams.get('search') || '';
  const initialPage = parseInt(searchParams.get('page') || '1', 10);

  const [activeView, setActiveView] = useState<QuickView>(initialView);
  const [workflowFilter, setWorkflowFilter] = useState(initialWorkflow);
  const [processingFilter, setProcessingFilter] = useState(initialProcessing);
  const [aiResultFilter, setAiResultFilter] = useState(initialAiResult);
  const [campaignFilter, setCampaignFilter] = useState(initialCampaign);
  const [clientFilter, setClientFilter] = useState(initialClient);
  const [search, setSearch] = useState(initialSearch);
  const [page, setPage] = useState(initialPage);

  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [pagination, setPagination] = useState<Pagination | null>(null);
  const [loading, setLoading] = useState(true);

  // Campaign and client options
  const [campaignOptions, setCampaignOptions] = useState<Campaign[]>([]);
  const [campaignLoading, setCampaignLoading] = useState(false);
  const [clientOptions, setClientOptions] = useState<ClientOrganization[]>([]);
  const [clientLoading, setClientLoading] = useState(false);

  // Debounce search input
  const searchTimer = useRef<ReturnType<typeof setTimeout>>();
  const [debouncedSearch, setDebouncedSearch] = useState(initialSearch);

  // Check if user is agency/freelancer
  const isAgency = auth.user?.tenant_type === 'agency' || auth.user?.tenant_type === 'individual_recruiter';

  useEffect(() => {
    setPageTitle(t.pageTitle);
  }, [setPageTitle, t.pageTitle]);

  // Fetch campaigns once on mount
  useEffect(() => {
    if (!auth.token) return;
    setCampaignLoading(true);
    const fetchCampaigns = async () => {
      try {
        const data = await apiService.get(WEBHOOK_CONFIG.CAMPAIGNS_URL, {}, auth.token);
        if (Array.isArray(data)) {
          setCampaignOptions(data);
        } else if (data && Array.isArray(data.campaigns)) {
          setCampaignOptions(data.campaigns);
        } else {
          setCampaignOptions([]);
        }
      } catch (err: any) {
        addToastRef.current(err.message || 'Failed to load campaigns', 'error');
        setCampaignOptions([]);
      } finally {
        setCampaignLoading(false);
      }
    };
    fetchCampaigns();
  }, [auth.token]); // addToast intentionally omitted — using ref to avoid loop

  // Fetch client organizations once on mount (agency/freelancer only)
  useEffect(() => {
    if (!auth.token || !isAgency) return;
    setClientLoading(true);
    const fetchClients = async () => {
      try {
        const data = await apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, auth.token);
        if (Array.isArray(data)) {
          setClientOptions(data);
        } else if (data && Array.isArray(data.client_organizations)) {
          setClientOptions(data.client_organizations);
        } else {
          setClientOptions([]);
        }
      } catch (err: any) {
        addToastRef.current(err.message || 'Failed to load client organizations', 'error');
        setClientOptions([]);
      } finally {
        setClientLoading(false);
      }
    };
    fetchClients();
  }, [auth.token, isAgency]); // addToast intentionally omitted — using ref to avoid loop

  // Sync URL when filters change
  useEffect(() => {
    const p: Record<string, string> = {};
    if (activeView !== 'all')     p.view = activeView;
    if (workflowFilter)           p.workflow_status = workflowFilter;
    if (processingFilter)         p.processing_status = processingFilter;
    if (aiResultFilter)           p.ai_decision = aiResultFilter;
    if (campaignFilter)           p.campaign_id = campaignFilter;
    if (clientFilter)             p.client_organization_id = clientFilter;
    if (debouncedSearch)          p.search = debouncedSearch;
    if (page > 1)                 p.page = String(page);
    setSearchParams(p, { replace: true });
  }, [activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, debouncedSearch, page, setSearchParams]);

  // Debounce search field
  const handleSearchChange = (value: string) => {
    setSearch(value);
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(value);
      setPage(1);
    }, 400);
  };

  // Fetch candidates from backend
  const fetchCandidates = useCallback(async () => {
    if (!auth.token) return;
    setLoading(true);
    try {
      const params = buildApiParams(activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, debouncedSearch, page);
      const data = await apiService.get(WEBHOOK_CONFIG.CANDIDATES_SEARCH_URL, params, auth.token);
      // Tenant-wide mode returns { candidates, pagination }
      if (data && typeof data === 'object' && Array.isArray(data.candidates)) {
        setCandidates(data.candidates);
        setPagination(data.pagination);
      } else if (Array.isArray(data)) {
        // Fallback: job-scoped response (should not happen without job_id)
        setCandidates(data);
        setPagination(null);
      } else {
        setCandidates([]);
        setPagination(null);
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to load candidates', 'error');
      setCandidates([]);
      setPagination(null);
    } finally {
      setLoading(false);
    }
  // addToast intentionally omitted — using ref to avoid infinite loop
  }, [auth.token, activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, debouncedSearch, page]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchCandidates();
  }, [fetchCandidates]);

  // Switch quick view: clear manual filters, reset page
  const handleViewChange = (view: QuickView) => {
    setActiveView(view);
    setWorkflowFilter('');
    setProcessingFilter('');
    setAiResultFilter('');
    setCampaignFilter('');
    setClientFilter('');
    setSearch('');
    setDebouncedSearch('');
    setPage(1);
  };

  // Clear all manual filters
  const handleClearFilters = () => {
    setWorkflowFilter('');
    setProcessingFilter('');
    setAiResultFilter('');
    setCampaignFilter('');
    setClientFilter('');
    setSearch('');
    setDebouncedSearch('');
    setPage(1);
  };

  const hasManualFilters = workflowFilter || processingFilter || aiResultFilter || campaignFilter || clientFilter || debouncedSearch;

  const openCandidate = (c: Candidate) => {
    navigate(`/applications?job_id=${encodeURIComponent(c.job_id)}&app_id=${encodeURIComponent(c.application_id)}`);
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4 max-w-7xl mx-auto">

      {/* Quick View Pills */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => handleViewChange('all')}
          className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
            activeView === 'all'
              ? 'bg-indigo-600 text-white'
              : 'bg-white border border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600'
          }`}
        >
          All
        </button>
        {QUICK_VIEWS.map(v => (
          <button
            key={v.id}
            onClick={() => handleViewChange(v.id)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
              activeView === v.id
                ? 'bg-indigo-600 text-white'
                : 'bg-white border border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600'
            }`}
          >
            {lang === 'ar' ? v.labelAr : v.labelEn}
          </button>
        ))}
      </div>

      {/* Filter Bar */}
      <div className="bg-white rounded-xl border border-slate-200 px-4 py-3 flex flex-wrap gap-3 items-center">
        {/* Workflow filter */}
        <select
          value={workflowFilter}
          onChange={e => { setWorkflowFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
        >
          <option value="">{t.filterWorkflow}</option>
          {(Object.keys(WORKFLOW_STATUS_LABELS_EN) as WorkflowStatus[]).map(s => (
            <option key={s} value={s}>{wfLabels[s]}</option>
          ))}
        </select>

        {/* Processing filter */}
        <select
          value={processingFilter}
          onChange={e => { setProcessingFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
        >
          <option value="">{t.filterProcessing}</option>
          <option value="pending">Pending</option>
          <option value="ai_scored">AI Scored</option>
          <option value="failed">Failed</option>
          <option value="processing_failed">Processing Failed</option>
          <option value="extraction_failed">Extraction Failed</option>
          <option value="security_blocked">Security Blocked</option>
          <option value="duplicate_blocked">Duplicate Blocked</option>
          <option value="stopped">Stopped</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
        </select>

        {/* AI result filter */}
        <select
          value={aiResultFilter}
          onChange={e => { setAiResultFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
        >
          <option value="">{t.filterAiResult}</option>
          <option value="qualified">Qualified</option>
          <option value="partial">Partial</option>
          <option value="rejected_low_match">Rejected / Low Match</option>
        </select>

        {/* Campaign filter */}
        <select
          value={campaignFilter}
          onChange={e => { setCampaignFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:opacity-50"
          disabled={campaignLoading}
        >
          <option value="">{t.filterCampaign}</option>
          {campaignOptions.map(c => (
            <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
          ))}
        </select>

        {/* Client organization filter (agency only) */}
        {isAgency && (
          <select
            value={clientFilter}
            onChange={e => { setClientFilter(e.target.value); setPage(1); }}
            className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:opacity-50"
            disabled={clientLoading}
          >
            <option value="">{t.filterClient}</option>
            {clientOptions.map(c => (
              <option key={c.client_organization_id} value={c.client_organization_id}>{c.organization_name}</option>
            ))}
          </select>
        )}

        {/* Name search */}
        <input
          type="text"
          value={search}
          onChange={e => handleSearchChange(e.target.value)}
          placeholder={t.filterSearch}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-300 min-w-[180px]"
        />

        {/* Clear filters */}
        {hasManualFilters && (
          <button
            onClick={handleClearFilters}
            className="text-xs text-slate-500 hover:text-indigo-600 underline"
          >
            {t.clearFilters}
          </button>
        )}

        {/* Result count */}
        {pagination && (
          <span className="ml-auto text-xs text-slate-400">
            {t.showing} {candidates.length} {t.of} {pagination.total}
          </span>
        )}
      </div>

      {/* Candidate Table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-7 h-7 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : candidates.length === 0 ? (
          <div className="text-center py-16 space-y-2">
            <p className="text-slate-500 text-sm">{t.noResults}</p>
            <p className="text-slate-400 text-xs">{t.noResultsHint}</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colCandidate}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colJob}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colAiMatch}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colAiResult}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colWorkflow}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide hidden lg:table-cell">{t.colProcessing}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide hidden md:table-cell">{t.colApplied}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colActions}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {candidates.map(c => {
                const wfStyle = WORKFLOW_STATUS_STYLES[c.workflow_status] ?? 'bg-slate-100 text-slate-600';
                const wfLabel = wfLabels[c.workflow_status] ?? c.workflow_status;
                return (
                  <tr
                    key={c.application_id}
                    onClick={() => openCandidate(c)}
                    className="hover:bg-slate-50 cursor-pointer transition-colors"
                  >
                    {/* Candidate */}
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-900 leading-tight">{c.candidate_name || '—'}</div>
                      {c.client_org_name && (
                        <div className="text-xs text-slate-400 mt-0.5">{c.client_org_name}</div>
                      )}
                    </td>

                    {/* Job */}
                    <td className="px-4 py-3">
                      <div className="text-slate-700 leading-tight">{c.job_title || '—'}</div>
                      {c.campaign_name && (
                        <div className="text-xs text-indigo-500 mt-0.5">{c.campaign_name}</div>
                      )}
                      {c.job_code && (
                        <div className="text-xs text-slate-400 mt-0.5">{c.job_code}</div>
                      )}
                    </td>

                    {/* AI Match score */}
                    <td className="px-4 py-3">
                      {c.score !== null ? (
                        <span className="font-semibold text-slate-800 tabular-nums">
                          {c.score.toFixed(0)}%
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>

                    {/* AI Result */}
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${aiDecisionStyle(c.status)}`}>
                        {aiDecisionLabel(c.status, t)}
                      </span>
                    </td>

                    {/* Workflow status */}
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${wfStyle}`}>
                        {wfLabel}
                      </span>
                    </td>

                    {/* Processing status */}
                    <td className="px-4 py-3 hidden lg:table-cell">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${processingStyle(c.processing_status)}`}>
                        {processingLabel(c.processing_status, t)}
                      </span>
                    </td>

                    {/* Applied date */}
                    <td className="px-4 py-3 hidden md:table-cell text-slate-500 text-xs whitespace-nowrap">
                      {formatDate(c.applied_at)}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={() => openCandidate(c)}
                        className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                      >
                        {t.actionView} →
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {pagination && pagination.total > pagination.limit && (
        <div className="flex items-center justify-between text-sm">
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 disabled:opacity-40 hover:bg-slate-50 transition-colors"
          >
            {t.prev}
          </button>
          <span className="text-slate-500 text-xs">
            Page {page} / {Math.ceil(pagination.total / pagination.limit)}
          </span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={!pagination.has_more}
            className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 disabled:opacity-40 hover:bg-slate-50 transition-colors"
          >
            {t.next}
          </button>
        </div>
      )}
    </div>
  );
};
