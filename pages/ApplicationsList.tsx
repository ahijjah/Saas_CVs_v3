
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, ApplicationFilter, AuthState, WorkflowStatus } from '../types';
import { ApplicationDetails } from './ApplicationDetails';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';
import {
  WORKFLOW_STATUS_STYLES,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
} from '../constants/workflow';

// ── Multi-dimensional filter types ────────────────────────────────────────────

type ProcessingFilter =
  | 'all'
  | 'pending'
  | 'in_progress'
  | 'ai_scored'
  | 'stopped_before_ai';

type StopReasonFilter =
  | 'all'
  | 'security_blocked'
  | 'duplicate_blocked'
  | 'extraction_failed'
  | 'processing_error';

type AiResultFilter = 'all' | 'qualified' | 'partial' | 'rejected_low_match' | 'not_scored';

type WorkflowFilter =
  | 'all'
  | 'awaiting_review'
  | 'under_review'
  | 'shortlisted'
  | 'interviewing'
  | 'offer_made'
  | 'hired'
  | 'rejected'
  | 'withdrawn'
  | 'on_hold';

type FlagKey = 'possible_duplicate' | 'has_notes';

interface AppFilters {
  processing: ProcessingFilter;
  stopReason: StopReasonFilter;
  aiResult:   AiResultFilter;
  workflow:   WorkflowFilter;
  flags:      Set<FlagKey>;
}

const DEFAULT_FILTERS: AppFilters = {
  processing: 'all',
  stopReason: 'all',
  aiResult:   'all',
  workflow:   'all',
  flags:      new Set(),
};

// Map legacy single-dimension ApplicationFilter → multi-dim initial state
function fromLegacyFilter(f: ApplicationFilter): AppFilters {
  const base = { ...DEFAULT_FILTERS, flags: new Set<FlagKey>() };
  switch (f) {
    case 'ai_scored':          return { ...base, processing: 'ai_scored' };
    case 'qualified':          return { ...base, processing: 'ai_scored', aiResult: 'qualified' };
    case 'partial':            return { ...base, processing: 'ai_scored', aiResult: 'partial' };
    case 'rejected':
    case 'low_match':          return { ...base, processing: 'ai_scored', aiResult: 'rejected_low_match' };
    case 'security_blocked':   return { ...base, processing: 'stopped_before_ai', stopReason: 'security_blocked' };
    case 'duplicate_blocked':  return { ...base, processing: 'stopped_before_ai', stopReason: 'duplicate_blocked' };
    case 'failed_needs_review': return { ...base, processing: 'stopped_before_ai', stopReason: 'processing_error' };
    case 'blocked':            return { ...base, processing: 'stopped_before_ai' };
    case 'possible_duplicate': return { ...base, flags: new Set<FlagKey>(['possible_duplicate']) };
    case 'workflow_awaiting_review': return { ...base, workflow: 'awaiting_review' };
    case 'workflow_under_review':  return { ...base, workflow: 'under_review' };
    case 'workflow_shortlisted':   return { ...base, workflow: 'shortlisted' };
    case 'workflow_interviewing':  return { ...base, workflow: 'interviewing' };
    case 'workflow_offer':         return { ...base, workflow: 'offer_made' };
    case 'workflow_hired':         return { ...base, workflow: 'hired' };
    case 'workflow_rejected':      return { ...base, workflow: 'rejected' };
    case 'workflow_withdrawn':     return { ...base, workflow: 'withdrawn' };
    case 'workflow_on_hold':       return { ...base, workflow: 'on_hold' };
    default:                   return base;
  }
}

function isFiltersDefault(f: AppFilters): boolean {
  return f.processing === 'all' && f.stopReason === 'all' && f.aiResult === 'all' && f.workflow === 'all' && f.flags.size === 0;
}

// ── Props & interfaces ────────────────────────────────────────────────────────

interface ApplicationsListProps {
  jobId:                   string;
  initialFilter:           ApplicationFilter;
  initialApplicationId?:   string | null;
  auth:                    AuthState;
  onBack:                  () => void;
  addToast:                (msg: string, type: 'success' | 'error') => void;
}

interface JobMeta {
  job_title:       string;
  job_client:      string | null;
  job_code:        string;
  job_status:      string;
  job_type:        string | null;
  location:        string | null;
  client_org_name: string | null;
}

// ── Translations ──────────────────────────────────────────────────────────────

const T = {
  en: {
    backToJob:        'Back to Job',
    filterPanelTitle: 'Filters',
    clearAll:         'Clear all',
    showing:          'Showing',
    of:               'of',
    results:          'results',
    // Dimension labels
    dimProcessing: 'Processing Status',
    dimStopReason: 'Stop Reason',
    dimAiResult:   'AI Result',
    dimWorkflow:   'Recruitment Workflow',
    dimFlags:      'Flags',
    // Processing options (simplified, recruiter-facing)
    procAll:          'All',
    procPending:      'Pending',
    procInProgress:   'In Progress',
    procAiScored:     'AI Scored',
    procStoppedBeforeAi: 'Stopped Before AI',
    // Stop Reason options (optional detail under Stopped Before AI)
    stopReasonAll:              'All Stop Reasons',
    stopReasonSecurity:         'Security Blocked',
    stopReasonDuplicate:        'Duplicate Blocked',
    stopReasonExtraction:       'Extraction Failed',
    stopReasonProcessingError:  'System Processing Error',
    // AI Result options
    aiAll:               'All AI Results',
    aiQualified:         'Qualified',
    aiPartial:           'Partial',
    aiRejectedLowMatch:  'Rejected / Low Match',
    aiNotScored:         'Not Scored',
    // Workflow options
    wfAll: 'All Workflow',
    // Flag labels
    flagPossibleDuplicate: 'Possible Duplicate',
    flagHasNotes:          'Has Recruiter Notes',
    // Card
    loading:       'Loading applications...',
    noApps:        'No applications match the selected filters.',
    appliedOn:     'Applied on',
    viewAnalysis:  'View full analysis →',
    loadingAnalysis: 'Loading analysis...',
    pts:           'PTS',
    possibleDuplicate: 'Possible Duplicate',
    statusFailed:     'Failed',
    statusProcessing: 'Processing',
    exitReason:    'Reason:',
    duplicateOf:   'Duplicate of another submission',
    viewCV:        'View CV',
    downloadingCV: 'Downloading…',
    client:        'Client',
    general:       'General',
    // AI decision labels (card pill)
    decQualified:  'Qualified',
    decPartial:    'Partial',
    decRejected:   'Rejected',
  },
  ar: {
    backToJob:        'العودة إلى الوظيفة',
    filterPanelTitle: 'الفلاتر',
    clearAll:         'مسح الكل',
    showing:          'عرض',
    of:               'من',
    results:          'نتائج',
    dimProcessing: 'حالة المعالجة',
    dimStopReason: 'سبب الوقف',
    dimAiResult:   'نتيجة الذكاء الاصطناعي',
    dimWorkflow:   'سير التوظيف',
    dimFlags:      'العلامات',
    procAll:          'الكل',
    procPending:      'معلق',
    procInProgress:   'قيد المعالجة',
    procAiScored:     'تم التقييم بالذكاء الاصطناعي',
    procStoppedBeforeAi: 'توقف قبل الذكاء الاصطناعي',
    stopReasonAll:              'جميع الأسباب',
    stopReasonSecurity:         'محظور أمنياً',
    stopReasonDuplicate:        'مكرر موقوف',
    stopReasonExtraction:       'فشل الاستخراج',
    stopReasonProcessingError:  'خطأ في المعالجة',
    aiAll:               'جميع نتائج الذكاء',
    aiQualified:         'مؤهل',
    aiPartial:           'جزئي',
    aiRejectedLowMatch:  'مرفوض / تطابق منخفض',
    aiNotScored:         'لم يُقيَّم',
    wfAll: 'جميع المراحل',
    flagPossibleDuplicate: 'مكرر محتمل',
    flagHasNotes:          'له ملاحظات',
    loading:          'جارٍ تحميل الطلبات...',
    noApps:           'لا توجد طلبات مطابقة للفلاتر المحددة.',
    appliedOn:        'تاريخ التقديم',
    viewAnalysis:     '← عرض التحليل الكامل',
    loadingAnalysis:  'جارٍ تحميل التحليل...',
    pts:              'نقطة',
    possibleDuplicate: 'مكرر محتمل',
    statusFailed:     'فشل',
    statusProcessing: 'قيد المعالجة',
    exitReason:       'السبب:',
    duplicateOf:      'مكرر لطلب آخر',
    viewCV:           'عرض السيرة',
    downloadingCV:    'جارٍ التحميل…',
    client:           'العميل',
    general:          'عام',
    decQualified:     'مؤهل',
    decPartial:       'جزئي',
    decRejected:      'مرفوض',
  },
};

// ── Workflow display constants ─────────────────────────────────────────────────

// WORKFLOW_STATUS_LABELS and WORKFLOW_STATUS_STYLES are imported from constants/workflow.ts

// ── Filter select component ────────────────────────────────────────────────────

interface FilterSelectProps {
  label:    string;
  value:    string;
  active:   boolean;
  onChange: (v: string) => void;
  options:  { value: string; label: string }[];
  disabled?: boolean;
}

const FilterSelect: React.FC<FilterSelectProps> = ({ label, value, active, onChange, options, disabled = false }) => (
  <div className="flex-1 min-w-[160px]">
    <p className={`text-[9px] font-black uppercase tracking-widest mb-1 ${disabled ? 'text-textMuted/50' : active ? 'text-primary' : 'text-textMuted'}`}>
      {label}
    </p>
    <div className={`relative rounded-lg border transition-colors ${disabled ? 'border-border/50 bg-slate-50' : active ? 'border-primary/40 bg-primary/5' : 'border-border bg-white'}`}>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className={`w-full appearance-none text-xs font-bold px-3 py-2 pr-7 rounded-lg bg-transparent cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/30 disabled:cursor-not-allowed ${disabled ? 'text-textMuted/50' : active ? 'text-primary' : 'text-textMain'}`}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <svg className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 ${disabled ? 'text-textMuted/30' : active ? 'text-primary' : 'text-textMuted'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M19 9l-7 7-7-7" />
      </svg>
    </div>
  </div>
);

// ── Main component ────────────────────────────────────────────────────────────

export const ApplicationsList: React.FC<ApplicationsListProps> = ({
  jobId, initialFilter, initialApplicationId, auth, onBack, addToast,
}) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const { setPageTitle } = usePageTitle();
  const [searchParams, setSearchParams] = useSearchParams();

  const [view, setView] = useState<'list' | 'details'>('list');

  const enterDetailView = (appId: string) => {
    setView('details');
    setPageTitle(lang === 'ar' ? 'تفاصيل الطلب' : 'Application Details');
    setSearchParams(prev => { const p = new URLSearchParams(prev); p.set('app_id', appId); return p; }, { replace: true });
  };

  const exitDetailView = () => {
    setView('list');
    setPageTitle(null);
    setSearchParams(prev => { const p = new URLSearchParams(prev); p.delete('app_id'); return p; }, { replace: true });
  };

  const [applicationsAll, setApplicationsAll]     = useState<Application[]>([]);
  const [selectedDetails, setSelectedDetails]     = useState<any | null>(null);
  const [filters, setFilters]                     = useState<AppFilters>(() => fromLegacyFilter(initialFilter));
  const [loading, setLoading]                     = useState(true);
  const [detailsLoading, setDetailsLoading]       = useState(false);
  const fetchInFlightRef                          = useRef<string | null>(null);
  const [jobMeta, setJobMeta]                     = useState<JobMeta | null>(null);
  const [downloadingCVId, setDownloadingCVId]     = useState<string | null>(null);

  // ── Data fetching ──────────────────────────────────────────────────────────

  const fetchApplications = async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL, { job_id: jobId }, auth.token!);
      setApplicationsAll(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('[ApplicationsList] Fetch error:', err);
      addToast('Failed to fetch applications.', 'error');
      setApplicationsAll([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchJobMeta = async () => {
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL, {}, auth.token!);
      const jobs: any[] = Array.isArray(data) ? data : [];
      const job = jobs.find(j => j.job_id === jobId);
      if (job) setJobMeta({ job_title: job.job_title, job_client: job.job_client, job_code: job.job_code, job_status: job.job_status, job_type: job.job_type || null, location: job.location || null, client_org_name: job.client_org_name || null });
    } catch { /* non-critical */ }
  };

  const handleDownloadApplicationCV = async (applicationId: string) => {
    setDownloadingCVId(applicationId);
    try {
      const url = `${WEBHOOK_CONFIG.CV_DOWNLOAD_BASE_URL}/${applicationId}/cv`;
      const token = auth.token || localStorage.getItem('token');
      if (!token) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        window.location.reload();
        return;
      }
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (resp.status === 401) {
        localStorage.removeItem('token');
        localStorage.removeItem('user');
        localStorage.removeItem('cv_analyzer_auth');
        window.location.reload();
        return;
      }
      if (!resp.ok) throw new Error('CV not available');
      const blob = await resp.blob();
      const contentDisposition = resp.headers.get('Content-Disposition');
      const filenameMatch = contentDisposition?.match(/filename[^;=\n]*=([^;\n]*)/);
      const filename = filenameMatch ? filenameMatch[1].replace(/['"]/g, '').trim() : 'cv';
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl; a.download = filename;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(objUrl), 10000);
    } catch {
      addToast('Could not download CV.', 'error');
    } finally {
      setDownloadingCVId(null);
    }
  };

  const handleViewAnalysis = async (app: Application) => {
    const appId = app.application_id || app.id;
    if (fetchInFlightRef.current === appId) return;
    fetchInFlightRef.current = appId;
    setDetailsLoading(true);
    try {
      const detailsRaw = await apiService.get(WEBHOOK_CONFIG.APPLICATION_DETAILS_WEBHOOK_URL, { application_id: appId }, auth.token!);
      if (fetchInFlightRef.current !== appId) return;
      const detailsObj = Array.isArray(detailsRaw) ? detailsRaw[0] : detailsRaw;
      if (!detailsObj) throw new Error('Application data not found');

      setSelectedDetails({
        application_id:        detailsObj?.application_id,
        candidate_name:        detailsObj?.candidate_name,
        overall_score:         Number(detailsObj?.overall_score || detailsObj?.score || 0),
        decision:              (detailsObj?.decision || detailsObj?.status || '').toLowerCase(),
        scores:                detailsObj?.scores,
        analysis:              detailsObj?.analysis || {},
        raw_ai_response:       detailsObj?.raw_ai_response,
        summary:               detailsObj?.analysis?.summary,
        strengths:             detailsObj?.analysis?.strengths || detailsObj?.raw_ai_response?.evidence_skills,
        risks:                 detailsObj?.analysis?.gaps_identified || detailsObj?.analysis?.risks,
        cv_language:           detailsObj?.cv_language,
        local_similarity_score: detailsObj?.local_similarity_score,
        skill_match_ratio:     detailsObj?.skill_match_ratio,
        gatekeeper_passed:     detailsObj?.gatekeeper_passed,
        matched_skills:        detailsObj?.matched_skills || [],
        missing_skills:        detailsObj?.missing_skills || [],
        red_flags:             detailsObj?.red_flags || detailsObj?.analysis?.red_flags || [],
        reasoning:             detailsObj?.reasoning || {},
        duplicate_status:                   detailsObj?.duplicate_status,
        duplicate_reference_application_id: detailsObj?.duplicate_reference_application_id,
        duplicate_similarity_score:         detailsObj?.duplicate_similarity_score,
        duplicate_reason:                   detailsObj?.duplicate_reason,
        duplicate_checked_at:               detailsObj?.duplicate_checked_at,
        duplicate_reference:                detailsObj?.duplicate_reference,
        submission_source:    detailsObj?.submission_source,
        applied_at:           detailsObj?.applied_at,
        email_sender_address: detailsObj?.email_sender_address,
        submitted_by_user_id: detailsObj?.submitted_by_user_id,
        submitted_by_name:    detailsObj?.submitted_by_name,
        submitted_by_email:   detailsObj?.submitted_by_email,
        original_filename:    detailsObj?.original_filename,
        processing_status:       detailsObj?.processing_status,
        evaluation_stage:        detailsObj?.evaluation_stage,
        evaluation_exit_reason:  detailsObj?.evaluation_exit_reason,
        security_check_status:      detailsObj?.security_check_status,
        security_risk_level:        detailsObj?.security_risk_level,
        security_risk_score:        detailsObj?.security_risk_score,
        security_reason_codes:      detailsObj?.security_reason_codes || [],
        security_detected_patterns: detailsObj?.security_detected_patterns || [],
        security_detected_snippets: detailsObj?.security_detected_snippets || [],
        security_checked_at:        detailsObj?.security_checked_at,
        stopped_reason:   detailsObj?.stopped_reason ?? null,
        knockout_answers: detailsObj?.knockout_answers || [],
        workflow_status:  detailsObj?.workflow_status || 'awaiting_review',
        recruiter_notes:  detailsObj?.recruiter_notes ?? null,
        workflow_history: detailsObj?.workflow_history || [],
      });
      enterDetailView(appId);
    } catch (err: any) {
      if (fetchInFlightRef.current === appId) {
        console.error('[ApplicationsList] Details fetch error:', err);
        addToast(err.message || 'Failed to load application analysis.', 'error');
      }
    } finally {
      if (fetchInFlightRef.current === appId) { fetchInFlightRef.current = null; setDetailsLoading(false); }
    }
  };

  // ── Workflow / notes callbacks ─────────────────────────────────────────────

  const handleWorkflowStatusChange = async (appId: string, newStatus: WorkflowStatus, note?: string, isAdvancedMove?: boolean) => {
    try {
      await apiService.patch(`${WEBHOOK_CONFIG.APPLICATION_WORKFLOW_STATUS_URL}/${appId}/workflow-status`, { workflow_status: newStatus, note: note || null, advanced_move: isAdvancedMove || false }, auth.token!);
      setApplicationsAll(prev => prev.map(a => (a.application_id || a.id) === appId ? { ...a, workflow_status: newStatus } : a));
      if (selectedDetails?.application_id === appId) {
        setSelectedDetails((prev: any) => prev ? {
          ...prev,
          workflow_status: newStatus,
          workflow_history: [{ history_id: Date.now().toString(), from_status: prev.workflow_status, to_status: newStatus, note: note || null, changed_by_name: null, created_at: new Date().toISOString(), is_advanced_move: isAdvancedMove || false }, ...(prev.workflow_history || [])],
        } : prev);
      }
      addToast(isAdvancedMove ? `Advanced Move → ${newStatus}` : 'Workflow status updated.', 'success');
    } catch { addToast('Failed to update workflow status.', 'error'); }
  };

  const handleRecruiterNotesChange = async (appId: string, notes: string | null) => {
    try {
      await apiService.patch(`${WEBHOOK_CONFIG.APPLICATION_RECRUITER_NOTES_URL}/${appId}/recruiter-notes`, { recruiter_notes: notes }, auth.token!);
      setApplicationsAll(prev => prev.map(a => (a.application_id || a.id) === appId ? { ...a, recruiter_notes: notes } : a));
      if (selectedDetails?.application_id === appId) {
        setSelectedDetails((prev: any) => prev ? { ...prev, recruiter_notes: notes } : prev);
      }
      addToast('Notes saved.', 'success');
    } catch { addToast('Failed to save notes.', 'error'); }
  };

  // ── Effects ────────────────────────────────────────────────────────────────

  useEffect(() => { fetchApplications(); fetchJobMeta(); }, [jobId]);

  useEffect(() => {
    if (view === 'details') setPageTitle(lang === 'ar' ? 'تفاصيل الطلب' : 'Application Details');
  }, [lang, view]);

  useEffect(() => () => { setPageTitle(null); }, []);

  useEffect(() => {
    if (!initialApplicationId || loading || applicationsAll.length === 0) return;
    const target = applicationsAll.find(a => (a.application_id || a.id) === initialApplicationId);
    if (target) handleViewAnalysis(target);
  }, [initialApplicationId, loading, applicationsAll]);

  // ── Classification predicates ──────────────────────────────────────────────

  const normaliseStatus  = (s: string) => s === 'low_match' ? 'rejected' : s;
  const isAiScored       = (a: Application) => a.processing_status === 'ai_scored';
  // Pending = submitted but not yet queued
  const isPending        = (a: Application) => a.processing_status === 'pending';
  // In Progress = actively in the scoring pipeline
  const isInProgress     = (a: Application) => ['queued', 'processing'].includes(a.processing_status ?? '');
  // Stopped Before AI = failed at any pre-AI gate
  const isStoppedBeforeAi = (a: Application) => a.processing_status === 'failed';
  // Stop reason sub-predicates
  const isSecurityBlocked = (a: Application) =>
    a.processing_status === 'failed' && (
      a.stopped_reason === 'security_blocked' ||
      (a.stopped_reason == null && a.security_check_status === 'blocked')
    );
  const isDuplicateBlocked   = (a: Application) => a.processing_status === 'failed' && a.stopped_reason === 'duplicate_blocked';
  const isExtractionFailed   = (a: Application) => a.processing_status === 'failed' && a.stopped_reason === 'extraction_failed';
  const isProcessingError    = (a: Application) =>
    a.processing_status === 'failed' &&
    !isSecurityBlocked(a) &&
    !isDuplicateBlocked(a) &&
    !isExtractionFailed(a);

  // ── Combined filter predicate ──────────────────────────────────────────────

  const matchesFilters = (a: Application): boolean => {
    // Dimension 1: Processing Status (high-level outcome)
    switch (filters.processing) {
      case 'pending':          if (!isPending(a))         return false; break;
      case 'in_progress':      if (!isInProgress(a))      return false; break;
      case 'ai_scored':        if (!isAiScored(a))         return false; break;
      case 'stopped_before_ai': if (!isStoppedBeforeAi(a)) return false; break;
    }

    // Dimension 1b: Stop Reason (only meaningful when processing = stopped_before_ai)
    if (filters.stopReason !== 'all') {
      if (!isStoppedBeforeAi(a)) return false;
      switch (filters.stopReason) {
        case 'security_blocked':   if (!isSecurityBlocked(a))  return false; break;
        case 'duplicate_blocked':  if (!isDuplicateBlocked(a)) return false; break;
        case 'extraction_failed':  if (!isExtractionFailed(a)) return false; break;
        case 'processing_error':   if (!isProcessingError(a))  return false; break;
      }
    }

    // Dimension 2: AI Result (only meaningful for ai_scored apps)
    switch (filters.aiResult) {
      case 'qualified':
        if (!(isAiScored(a) && a.status === 'qualified')) return false; break;
      case 'partial':
        if (!(isAiScored(a) && a.status === 'partial')) return false; break;
      case 'rejected_low_match':
        if (!(isAiScored(a) && normaliseStatus((a.status ?? '').toLowerCase()) === 'rejected')) return false; break;
      case 'not_scored':
        if (isAiScored(a)) return false; break;
    }

    // Dimension 3: Workflow (recruiter pipeline state, independent of processing)
    if (filters.workflow !== 'all' && a.workflow_status !== filters.workflow) return false;

    // Dimension 4: Flags (all selected flags must match — AND logic)
    if (filters.flags.has('possible_duplicate') && a.duplicate_status !== 'possible_duplicate') return false;
    if (filters.flags.has('has_notes') && !a.recruiter_notes) return false;

    return true;
  };

  // ── Detail view ────────────────────────────────────────────────────────────

  if (view === 'details' && selectedDetails) {
    return (
      <ApplicationDetails
        data={selectedDetails}
        onBack={exitDetailView}
        jobMeta={jobMeta}
        onDownloadCV={() => handleDownloadApplicationCV(selectedDetails.application_id)}
        downloadingCV={downloadingCVId === selectedDetails.application_id}
        token={auth.token!}
        userRole={auth.user?.role}
        advancedMoveEnabled={auth.user?.allow_advanced_workflow_move && selectedDetails?.job_allow_advanced_workflow_move}
        onWorkflowStatusChange={handleWorkflowStatusChange}
        onRecruiterNotesChange={handleRecruiterNotesChange}
      />
    );
  }

  // ── Derived state for list view ────────────────────────────────────────────

  const filteredApplications = applicationsAll.filter(matchesFilters);
  const isDefault = isFiltersDefault(filters);

  const updateFilters = (patch: Partial<Omit<AppFilters, 'flags'>>) => {
    // If processing status changes to something other than 'all' or 'ai_scored',
    // disable and reset AI Result (it only applies to ai_scored apps)
    if (patch.processing !== undefined && patch.processing !== 'all' && patch.processing !== 'ai_scored') {
      patch.aiResult = 'all';
    }
    setFilters(prev => ({ ...prev, ...patch }));
  };

  const toggleFlag = (key: FlagKey) =>
    setFilters(prev => {
      const next = new Set(prev.flags);
      next.has(key) ? next.delete(key) : next.add(key);
      return { ...prev, flags: next };
    });

  const clearFilters = () => setFilters({ ...DEFAULT_FILTERS, flags: new Set<FlagKey>() });

  const getAiDecisionStyles = (app: Application) => {
    const ps = app.processing_status ?? '';
    if (isStoppedBeforeAi(app))             return { pill: 'bg-red-100 text-red-700', badge: 'bg-slate-400', label: t.statusFailed, score: '—' };
    if (isPending(app) || isInProgress(app)) return { pill: 'bg-blue-100 text-blue-700', badge: 'bg-blue-300', label: t.statusProcessing, score: '…' };
    const s = normaliseStatus((app.status ?? '').toLowerCase().trim());
    if (s === 'qualified') return { pill: 'bg-green-100 text-green-800', badge: 'bg-success',  label: t.decQualified, score: app.score != null ? String(app.score) : '—' };
    if (s === 'partial')   return { pill: 'bg-amber-100 text-amber-800', badge: 'bg-warning',  label: t.decPartial,   score: app.score != null ? String(app.score) : '—' };
    return                        { pill: 'bg-red-100   text-red-800',   badge: 'bg-error',    label: t.decRejected,  score: app.score != null ? String(app.score) : '—' };
  };

  // ── Filter select option lists ─────────────────────────────────────────────

  const processingOptions = [
    { value: 'all',              label: t.procAll             },
    { value: 'pending',          label: t.procPending         },
    { value: 'in_progress',      label: t.procInProgress      },
    { value: 'ai_scored',        label: t.procAiScored        },
    { value: 'stopped_before_ai', label: t.procStoppedBeforeAi },
  ];

  const stopReasonOptions = [
    { value: 'all',               label: t.stopReasonAll             },
    { value: 'security_blocked',  label: t.stopReasonSecurity        },
    { value: 'duplicate_blocked', label: t.stopReasonDuplicate       },
    { value: 'extraction_failed', label: t.stopReasonExtraction      },
    { value: 'processing_error',  label: t.stopReasonProcessingError },
  ];

  const aiResultOptions = [
    { value: 'all',              label: t.aiAll              },
    { value: 'qualified',        label: t.aiQualified        },
    { value: 'partial',          label: t.aiPartial          },
    { value: 'rejected_low_match', label: t.aiRejectedLowMatch },
    { value: 'not_scored',       label: t.aiNotScored        },
  ];

  const wfLabels = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;
  const workflowOptions = [
    { value: 'all',             label: t.wfAll                      },
    { value: 'awaiting_review', label: wfLabels.awaiting_review     },
    { value: 'under_review',    label: wfLabels.under_review        },
    { value: 'shortlisted',     label: wfLabels.shortlisted         },
    { value: 'interviewing',    label: wfLabels.interviewing        },
    { value: 'offer_made',      label: wfLabels.offer_made          },
    { value: 'hired',           label: wfLabels.hired               },
    { value: 'rejected',        label: wfLabels.rejected            },
    { value: 'withdrawn',       label: wfLabels.withdrawn           },
    { value: 'on_hold',         label: wfLabels.on_hold             },
  ];

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-5">

      {/* ── Top bar: back + job meta ── */}
      <div className="flex items-center gap-4 flex-wrap">
        <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium gap-1 shrink-0">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t.backToJob}
        </button>

        {jobMeta && (
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 ml-2">
            <div>
              <span className="text-[9px] font-black text-textMuted uppercase tracking-widest mr-2">{jobMeta.job_code}</span>
              <span className="text-sm font-bold text-textMain">{jobMeta.job_title}</span>
            </div>
            {jobMeta.client_org_name !== undefined && (
              <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${jobMeta.client_org_name ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
                {jobMeta.client_org_name || t.general}
              </span>
            )}
            {jobMeta.job_type && <span className="text-xs text-textMuted">{jobMeta.job_type}</span>}
            {jobMeta.location && <span className="text-xs text-textMuted">{jobMeta.location}</span>}
            <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase ${
              jobMeta.job_status === 'Active' ? 'bg-green-100 text-green-800' :
              jobMeta.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
              'bg-amber-100 text-amber-800'
            }`}>{jobMeta.job_status}</span>
          </div>
        )}
      </div>

      {/* ── Multi-dimensional filter panel ── */}
      <div className="bg-white rounded-xl border border-border shadow-sm px-5 py-4 space-y-4">

        {/* Row 1: four filter selects */}
        <div className="flex flex-wrap gap-3">
          <FilterSelect
            label={t.dimProcessing}
            value={filters.processing}
            active={filters.processing !== 'all'}
            onChange={v => updateFilters({ processing: v as ProcessingFilter, stopReason: 'all' })}
            options={processingOptions}
          />
          {filters.processing === 'stopped_before_ai' && (
            <FilterSelect
              label={t.dimStopReason}
              value={filters.stopReason}
              active={filters.stopReason !== 'all'}
              onChange={v => updateFilters({ stopReason: v as StopReasonFilter })}
              options={stopReasonOptions}
            />
          )}
          <FilterSelect
            label={t.dimAiResult}
            value={filters.aiResult}
            active={filters.aiResult !== 'all'}
            onChange={v => updateFilters({ aiResult: v as AiResultFilter })}
            options={aiResultOptions}
            disabled={filters.processing !== 'all' && filters.processing !== 'ai_scored'}
          />
          <FilterSelect
            label={t.dimWorkflow}
            value={filters.workflow}
            active={filters.workflow !== 'all'}
            onChange={v => updateFilters({ workflow: v as WorkflowFilter })}
            options={workflowOptions}
          />
        </div>

        {/* Row 2: flags + result count + clear */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[9px] font-black text-textMuted uppercase tracking-widest shrink-0">{t.dimFlags}:</span>
            {(['possible_duplicate', 'has_notes'] as FlagKey[]).map(key => (
              <button
                key={key}
                onClick={() => toggleFlag(key)}
                className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border transition-all ${
                  filters.flags.has(key)
                    ? 'bg-primary/10 border-primary/40 text-primary'
                    : 'bg-slate-50 border-border text-textMuted hover:border-slate-300 hover:text-textMain'
                }`}
              >
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                  {key === 'possible_duplicate'
                    ? <path d="M10 2a8 8 0 100 16A8 8 0 0010 2zm0 3a1 1 0 011 1v4a1 1 0 11-2 0V6a1 1 0 011-1zm0 8a1 1 0 100-2 1 1 0 000 2z" />
                    : <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
                  }
                </svg>
                {key === 'possible_duplicate' ? t.flagPossibleDuplicate : t.flagHasNotes}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3 ml-auto">
            {!loading && (
              <span className="text-xs text-textMuted">
                <span className={`font-black ${!isDefault ? 'text-primary' : 'text-textMain'}`}>{filteredApplications.length}</span>
                {' '}{t.of}{' '}{applicationsAll.length}{' '}{t.results}
              </span>
            )}
            {!isDefault && (
              <button
                onClick={clearFilters}
                className="flex items-center gap-1 text-xs font-bold text-textMuted hover:text-red-600 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
                {t.clearAll}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Application cards ── */}
      <div className="grid grid-cols-1 gap-4">
        {loading ? (
          <div className="p-12 text-center text-textMuted flex flex-col items-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4" />
            {t.loading}
          </div>
        ) : filteredApplications.length === 0 ? (
          <div className="bg-white p-12 rounded-xl border border-border text-center text-textMuted">
            {t.noApps}
            {!isDefault && (
              <div className="mt-4">
                <button onClick={clearFilters} className="text-primary hover:underline text-sm font-semibold">{t.clearAll}</button>
              </div>
            )}
          </div>
        ) : (
          filteredApplications.map(app => {
            const styles  = getAiDecisionStyles(app);
            const isTerminal = !app.processing_status || app.processing_status === 'ai_scored' || app.processing_status === 'failed';
            const wfStatus = app.workflow_status as WorkflowStatus | undefined;
            return (
              <div key={app.id || app.application_id} className="bg-white p-6 rounded-xl border border-border shadow-sm flex flex-col md:flex-row md:items-center justify-between hover:border-primary/30 transition-all">

                {/* Left: score badge + info */}
                <div className="flex items-center gap-5">
                  <div className={`w-14 h-14 shrink-0 rounded-full flex flex-col items-center justify-center font-bold text-white shadow-sm ${styles.badge}`}>
                    <span className="text-lg leading-none">{styles.score}</span>
                    {app.score != null && isTerminal && <span className="text-[10px] opacity-80 uppercase leading-none mt-0.5">{t.pts}</span>}
                  </div>
                  <div>
                    <h4 className="text-lg font-bold text-textMain">{app.candidate_name}</h4>
                    <p className="text-xs text-textMuted">{t.appliedOn} {app.applied_date}</p>
                    {app.summary && <p className="text-sm text-textMain mt-2 max-w-xl italic">"{app.summary}"</p>}
                    {app.evaluation_exit_reason && (
                      <p className="text-xs text-red-500 mt-1"><span className="font-bold">{t.exitReason}</span> {app.evaluation_exit_reason}</p>
                    )}
                    {app.duplicate_reason && (
                      <p className="text-xs text-orange-500 mt-1"><span className="font-bold">{t.duplicateOf}:</span> {app.duplicate_reason}</p>
                    )}
                  </div>
                </div>

                {/* Right: status badges + actions */}
                <div className="mt-4 md:mt-0 flex flex-col items-end gap-2">
                  {/* AI decision pill */}
                  <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${styles.pill}`}>
                    {styles.label}
                  </span>
                  {/* Workflow status pill */}
                  {wfStatus && (
                    <span className={`px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider ${WORKFLOW_STATUS_STYLES[wfStatus]}`}>
                      {wfLabels[wfStatus]}
                    </span>
                  )}
                  {/* Flags */}
                  {app.duplicate_status === 'possible_duplicate' && (
                    <span className="px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-orange-100 text-orange-700">
                      {t.possibleDuplicate}
                    </span>
                  )}
                  {/* Actions */}
                  {isTerminal && (
                    <button
                      disabled={detailsLoading}
                      onClick={() => handleViewAnalysis(app)}
                      className="text-primary hover:text-primaryDark text-sm font-semibold flex items-center disabled:opacity-50"
                    >
                      {detailsLoading ? t.loadingAnalysis : t.viewAnalysis}
                    </button>
                  )}
                  <button
                    disabled={downloadingCVId === (app.application_id || app.id)}
                    onClick={() => handleDownloadApplicationCV(app.application_id || app.id)}
                    className="flex items-center gap-1 text-[11px] font-bold text-textMuted hover:text-primary transition-colors disabled:opacity-50"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    {downloadingCVId === (app.application_id || app.id) ? t.downloadingCV : t.viewCV}
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
