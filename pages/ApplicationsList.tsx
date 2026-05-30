
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, AuthState, ApplicationFilter, WorkflowStatus } from '../types';
import { ApplicationDetails } from './ApplicationDetails';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';

interface ApplicationsListProps {
  jobId: string;
  initialFilter: ApplicationFilter;
  initialApplicationId?: string | null;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface JobMeta {
  job_title: string;
  job_client: string | null;
  job_code: string;
  job_status: string;
  job_type: string | null;
  location: string | null;
  client_org_name: string | null;
}

const T = {
  en: {
    backToCampaigns: 'Back to Job',
    filterAll: 'All',
    filterQualified: 'Qualified',
    filterPartial: 'Partial',
    filterRejected: 'Rejected',
    filterLowMatch: 'Low Match',
    filterPossibleDuplicate: 'Possible Duplicates',
    filterAiScored: 'AI Scored',
    filterBlocked: 'Blocked',
    filterSecurityBlocked: 'Security Blocked',
    filterDuplicateBlocked: 'Duplicate Blocked',
    filterFailedNeedsReview: 'Failed / Needs Review',
    filterWorkflowAiProcessed: 'AI Processed',
    filterWorkflowUnderReview: 'Under Review',
    filterWorkflowShortlisted: 'Shortlisted',
    filterWorkflowInterviewing: 'Interviewing',
    filterWorkflowOffer: 'Offer Made',
    filterWorkflowHired: 'Hired',
    filterWorkflowRejected: 'Rejected',
    filterWorkflowWithdrawn: 'Withdrawn',
    filterWorkflowOnHold: 'On Hold',
    loading: 'Loading applications...',
    noApps: 'No applications found matching this criteria.',
    appliedOn: 'Applied on',
    viewAnalysis: 'View full analysis →',
    loadingAnalysis: 'Loading analysis...',
    pts: 'PTS',
    possibleDuplicate: 'Possible Duplicate',
    statusFailed: 'Failed',
    statusPending: 'Pending',
    statusProcessing: 'Processing',
    exitReason: 'Reason:',
    duplicateOf: 'Duplicate of another submission',
    viewCV: 'View CV',
    downloadingCV: 'Downloading…',
    client: 'Client',
    general: 'General',
  },
  ar: {
    backToCampaigns: 'العودة إلى الوظيفة',
    filterAll: 'الكل',
    filterQualified: 'مؤهلون',
    filterPartial: 'جزئيون',
    filterRejected: 'مرفوضون',
    filterLowMatch: 'تطابق منخفض',
    filterPossibleDuplicate: 'مكررات محتملة',
    filterAiScored: 'مُقيَّم بالذكاء الاصطناعي',
    filterBlocked: 'موقوف',
    filterSecurityBlocked: 'محظور أمنياً',
    filterDuplicateBlocked: 'مكرر موقوف',
    filterFailedNeedsReview: 'فشل / يحتاج مراجعة',
    filterWorkflowAiProcessed: 'معالج بالذكاء',
    filterWorkflowUnderReview: 'قيد المراجعة',
    filterWorkflowShortlisted: 'مختصرون',
    filterWorkflowInterviewing: 'مقابلة',
    filterWorkflowOffer: 'عرض مقدم',
    filterWorkflowHired: 'تم التعيين',
    filterWorkflowRejected: 'مرفوض',
    filterWorkflowWithdrawn: 'منسحب',
    filterWorkflowOnHold: 'متوقف مؤقتاً',
    loading: 'جارٍ تحميل الطلبات...',
    noApps: 'لا توجد طلبات مطابقة لهذه المعايير.',
    appliedOn: 'تاريخ التقديم',
    viewAnalysis: '← عرض التحليل الكامل',
    loadingAnalysis: 'جارٍ تحميل التحليل...',
    pts: 'نقطة',
    possibleDuplicate: 'مكرر محتمل',
    statusFailed: 'فشل',
    statusPending: 'قيد الانتظار',
    statusProcessing: 'قيد المعالجة',
    exitReason: 'السبب:',
    duplicateOf: 'مكرر لطلب آخر',
    viewCV: 'عرض السيرة',
    downloadingCV: 'جارٍ التحميل…',
    client: 'العميل',
    general: 'عام',
  },
};

const WORKFLOW_STATUS_LABELS: Record<WorkflowStatus, string> = {
  new:            'New',
  ai_processed:   'AI Processed',
  under_review:   'Under Review',
  shortlisted:    'Shortlisted',
  interviewing:   'Interviewing',
  offer_made:     'Offer Made',
  hired:          'Hired',
  rejected:       'Rejected',
  withdrawn:      'Withdrawn',
  on_hold:        'On Hold',
};

const WORKFLOW_STATUS_STYLES: Record<WorkflowStatus, string> = {
  new:            'bg-slate-100 text-slate-500',
  ai_processed:   'bg-sky-100 text-sky-700',
  under_review:   'bg-blue-100 text-blue-700',
  shortlisted:    'bg-indigo-100 text-indigo-700',
  interviewing:   'bg-purple-100 text-purple-700',
  offer_made:     'bg-amber-100 text-amber-700',
  hired:          'bg-green-100 text-green-800',
  rejected:       'bg-red-100 text-red-700',
  withdrawn:      'bg-orange-100 text-orange-700',
  on_hold:        'bg-yellow-100 text-yellow-700',
};

// low_match is an internal status; it maps to 'rejected' for display purposes
const FILTER_KEYS: ApplicationFilter[] = ['all', 'ai_scored', 'qualified', 'partial', 'rejected', 'blocked', 'security_blocked', 'duplicate_blocked', 'possible_duplicate', 'failed_needs_review', 'workflow_ai_processed', 'workflow_under_review', 'workflow_shortlisted', 'workflow_interviewing', 'workflow_offer', 'workflow_hired', 'workflow_rejected', 'workflow_withdrawn', 'workflow_on_hold'];

export const ApplicationsList: React.FC<ApplicationsListProps> = ({
  jobId, initialFilter, initialApplicationId, auth, onBack, addToast
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
  const [applicationsAll, setApplicationsAll] = useState<Application[]>([]);
  const [selectedDetails, setSelectedDetails] = useState<any | null>(null);
  const [filter, setFilter] = useState<ApplicationFilter>(initialFilter);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const fetchInFlightRef = useRef<string | null>(null);
  const [jobMeta, setJobMeta] = useState<JobMeta | null>(null);
  const [downloadingCVId, setDownloadingCVId] = useState<string | null>(null);

  const filterLabels: Record<ApplicationFilter, string> = {
    all: t.filterAll,
    qualified: t.filterQualified,
    partial: t.filterPartial,
    rejected: t.filterRejected,
    low_match: t.filterLowMatch,
    possible_duplicate: t.filterPossibleDuplicate,
    ai_scored: t.filterAiScored,
    blocked: t.filterBlocked,
    security_blocked: t.filterSecurityBlocked,
    duplicate_blocked: t.filterDuplicateBlocked,
    failed_needs_review: t.filterFailedNeedsReview,
    workflow_ai_processed: t.filterWorkflowAiProcessed,
    workflow_under_review: t.filterWorkflowUnderReview,
    workflow_shortlisted: t.filterWorkflowShortlisted,
    workflow_interviewing: t.filterWorkflowInterviewing,
    workflow_offer: t.filterWorkflowOffer,
    workflow_hired: t.filterWorkflowHired,
    workflow_rejected: t.filterWorkflowRejected,
    workflow_withdrawn: t.filterWorkflowWithdrawn,
    workflow_on_hold: t.filterWorkflowOnHold,
  };

  const fetchApplications = async () => {
    setLoading(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL,
        { job_id: jobId },
        auth.token!
      );
      const arr = Array.isArray(data) ? data : [];
      setApplicationsAll(arr);
    } catch (err) {
      console.error("[ApplicationsList] Fetch error:", err);
      addToast("Failed to fetch applications.", "error");
      setApplicationsAll([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchJobMeta = async () => {
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL,
        {},
        auth.token!
      );
      const jobs: any[] = Array.isArray(data) ? data : [];
      const job = jobs.find(j => j.job_id === jobId);
      if (job) {
        setJobMeta({
          job_title: job.job_title,
          job_client: job.job_client,
          job_code: job.job_code,
          job_status: job.job_status,
          job_type: job.job_type || null,
          location: job.location || null,
          client_org_name: job.client_org_name || null,
        });
      }
    } catch { /* non-critical */ }
  };

  const handleDownloadApplicationCV = async (applicationId: string) => {
    setDownloadingCVId(applicationId);
    try {
      const url = `${WEBHOOK_CONFIG.CV_DOWNLOAD_BASE_URL}/${applicationId}/cv`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${auth.token!}` } });
      if (!resp.ok) throw new Error('CV not available');
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = 'cv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
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
      const detailsRaw = await apiService.get(
        WEBHOOK_CONFIG.APPLICATION_DETAILS_WEBHOOK_URL,
        { application_id: appId },
        auth.token!
      );

      if (fetchInFlightRef.current !== appId) return;

      const detailsObj = Array.isArray(detailsRaw) ? detailsRaw[0] : detailsRaw;

      if (!detailsObj) {
        throw new Error("Application data not found");
      }

      const normalized = {
        application_id:        detailsObj?.application_id,
        candidate_name:        detailsObj?.candidate_name,
        overall_score:         Number(detailsObj?.overall_score || detailsObj?.score || 0),
        decision:              (detailsObj?.decision || detailsObj?.status || "").toLowerCase(),
        scores:                detailsObj?.scores,
        analysis:              detailsObj?.analysis || {},
        raw_ai_response:       detailsObj?.raw_ai_response,
        summary:               detailsObj?.analysis?.summary,
        strengths:             detailsObj?.analysis?.strengths || detailsObj?.raw_ai_response?.evidence_skills,
        risks:                 detailsObj?.analysis?.gaps_identified || detailsObj?.analysis?.risks,
        // Intelligence pipeline fields
        cv_language:           detailsObj?.cv_language,
        local_similarity_score: detailsObj?.local_similarity_score,
        skill_match_ratio:     detailsObj?.skill_match_ratio,
        gatekeeper_passed:     detailsObj?.gatekeeper_passed,
        matched_skills:        detailsObj?.matched_skills || [],
        missing_skills:        detailsObj?.missing_skills || [],
        red_flags:             detailsObj?.red_flags || detailsObj?.analysis?.red_flags || [],
        reasoning:             detailsObj?.reasoning || {},
        // Duplicate detection fields
        duplicate_status:                   detailsObj?.duplicate_status,
        duplicate_reference_application_id: detailsObj?.duplicate_reference_application_id,
        duplicate_similarity_score:         detailsObj?.duplicate_similarity_score,
        duplicate_reason:                   detailsObj?.duplicate_reason,
        duplicate_checked_at:               detailsObj?.duplicate_checked_at,
        duplicate_reference:                detailsObj?.duplicate_reference,
        // Intake metadata
        submission_source:   detailsObj?.submission_source,
        applied_at:          detailsObj?.applied_at,
        email_sender_address: detailsObj?.email_sender_address,
        submitted_by_user_id: detailsObj?.submitted_by_user_id,
        submitted_by_name:   detailsObj?.submitted_by_name,
        submitted_by_email:  detailsObj?.submitted_by_email,
        original_filename:   detailsObj?.original_filename,
        // Processing / evaluation state
        processing_status:        detailsObj?.processing_status,
        evaluation_stage:         detailsObj?.evaluation_stage,
        evaluation_exit_reason:   detailsObj?.evaluation_exit_reason,
        // Security check fields
        security_check_status:      detailsObj?.security_check_status,
        security_risk_level:        detailsObj?.security_risk_level,
        security_risk_score:        detailsObj?.security_risk_score,
        security_reason_codes:      detailsObj?.security_reason_codes || [],
        security_detected_patterns: detailsObj?.security_detected_patterns || [],
        security_detected_snippets: detailsObj?.security_detected_snippets || [],
        security_checked_at:        detailsObj?.security_checked_at,
        // Stopped reason (Phase 2 field — null for historical rows)
        stopped_reason:             detailsObj?.stopped_reason ?? null,
        // Knockout question answers
        knockout_answers:           detailsObj?.knockout_answers || [],
        // Recruiter workflow fields
        workflow_status:            detailsObj?.workflow_status || 'new',
        recruiter_notes:            detailsObj?.recruiter_notes ?? null,
        workflow_history:           detailsObj?.workflow_history || [],
      };

      setSelectedDetails(normalized);
      enterDetailView(appId);
    } catch (err: any) {
      if (fetchInFlightRef.current === appId) {
        console.error("[ApplicationsList] Details fetch error:", err);
        addToast(err.message || "Failed to load application analysis.", "error");
      }
    } finally {
      if (fetchInFlightRef.current === appId) {
        fetchInFlightRef.current = null;
        setDetailsLoading(false);
      }
    }
  };

  useEffect(() => {
    fetchApplications();
    fetchJobMeta();
  }, [jobId]);

  // Keep context title in sync when language switches while in detail view
  useEffect(() => {
    if (view === 'details') {
      setPageTitle(lang === 'ar' ? 'تفاصيل الطلب' : 'Application Details');
    }
  }, [lang, view]);

  // Clear context title on unmount so other pages are unaffected
  useEffect(() => () => { setPageTitle(null); }, []);

  // Auto-open a specific application when navigated from JobDetails duplicate section
  useEffect(() => {
    if (!initialApplicationId || loading || applicationsAll.length === 0) return;
    const target = applicationsAll.find(
      (a) => (a.application_id || a.id) === initialApplicationId
    );
    if (target) handleViewAnalysis(target);
  }, [initialApplicationId, loading, applicationsAll]);

  // low_match is treated as rejected for all display/filter purposes
  const normaliseStatus = (s: string) => s === 'low_match' ? 'rejected' : s;
  // Classification based on processing_status (primary) and flags (secondary)
  const isAiScored = (a: Application) => a.processing_status === 'scored';
  // stopped_reason-first; fall back to security_check_status for historical rows (stopped_reason=null)
  const isSecurityBlocked = (a: Application) =>
    a.processing_status === 'failed' && (
      a.stopped_reason === 'security_blocked' ||
      (a.stopped_reason == null && a.security_check_status === 'blocked')
    );
  const isDuplicateBlocked = (a: Application) =>
    a.processing_status === 'failed' && a.stopped_reason === 'duplicate_blocked';
  const isFailedNeedsReview = (a: Application) =>
    a.processing_status === 'failed' &&
    a.stopped_reason !== 'security_blocked' &&
    a.stopped_reason !== 'duplicate_blocked' &&
    !(a.stopped_reason == null && a.security_check_status === 'blocked');
  // possible_duplicate is a flag only — an app can have any processing_status
  const isPossibleDuplicate = (a: Application) => a.duplicate_status === 'possible_duplicate';

  const filteredApplications = (() => {
    if (filter === 'all') return applicationsAll;
    if (filter === 'ai_scored')          return applicationsAll.filter(isAiScored);
    if (filter === 'security_blocked')   return applicationsAll.filter(isSecurityBlocked);
    if (filter === 'duplicate_blocked')  return applicationsAll.filter(isDuplicateBlocked);
    if (filter === 'failed_needs_review') return applicationsAll.filter(isFailedNeedsReview);
    if (filter === 'blocked')            return applicationsAll.filter(a => a.processing_status === 'failed');
    if (filter === 'possible_duplicate') return applicationsAll.filter(isPossibleDuplicate);
    if (filter === 'qualified') return applicationsAll.filter(a => isAiScored(a) && a.status === 'qualified');
    if (filter === 'partial')   return applicationsAll.filter(a => isAiScored(a) && a.status === 'partial');
    if (filter === 'rejected')  return applicationsAll.filter(a => isAiScored(a) && normaliseStatus((a.status ?? '').toLowerCase()) === 'rejected');
    if (filter === 'workflow_ai_processed')  return applicationsAll.filter(a => a.workflow_status === 'ai_processed');
    if (filter === 'workflow_under_review')  return applicationsAll.filter(a => a.workflow_status === 'under_review');
    if (filter === 'workflow_shortlisted')   return applicationsAll.filter(a => a.workflow_status === 'shortlisted');
    if (filter === 'workflow_interviewing')  return applicationsAll.filter(a => a.workflow_status === 'interviewing');
    if (filter === 'workflow_offer')         return applicationsAll.filter(a => a.workflow_status === 'offer_made');
    if (filter === 'workflow_hired')         return applicationsAll.filter(a => a.workflow_status === 'hired');
    if (filter === 'workflow_rejected')      return applicationsAll.filter(a => a.workflow_status === 'rejected');
    if (filter === 'workflow_withdrawn')     return applicationsAll.filter(a => a.workflow_status === 'withdrawn');
    if (filter === 'workflow_on_hold')       return applicationsAll.filter(a => a.workflow_status === 'on_hold');
    return applicationsAll.filter(a => normaliseStatus((a.status ?? '').toLowerCase().trim()) === filter);
  })();

  const handleWorkflowStatusChange = async (appId: string, newStatus: WorkflowStatus, note?: string) => {
    try {
      await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_WORKFLOW_STATUS_URL}/${appId}/workflow-status`,
        { workflow_status: newStatus, note: note || null },
        auth.token!
      );
      setApplicationsAll(prev => prev.map(a =>
        (a.application_id || a.id) === appId ? { ...a, workflow_status: newStatus } : a
      ));
      if (selectedDetails && selectedDetails.application_id === appId) {
        setSelectedDetails((prev: any) => prev ? {
          ...prev,
          workflow_status: newStatus,
          workflow_history: [
            { history_id: Date.now().toString(), from_status: prev.workflow_status, to_status: newStatus, note: note || null, changed_by_name: null, created_at: new Date().toISOString() },
            ...(prev.workflow_history || []),
          ],
        } : prev);
      }
      addToast('Workflow status updated.', 'success');
    } catch {
      addToast('Failed to update workflow status.', 'error');
    }
  };

  const handleRecruiterNotesChange = async (appId: string, notes: string | null) => {
    try {
      await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_RECRUITER_NOTES_URL}/${appId}/recruiter-notes`,
        { recruiter_notes: notes },
        auth.token!
      );
      setApplicationsAll(prev => prev.map(a =>
        (a.application_id || a.id) === appId ? { ...a, recruiter_notes: notes } : a
      ));
      if (selectedDetails && selectedDetails.application_id === appId) {
        setSelectedDetails((prev: any) => prev ? { ...prev, recruiter_notes: notes } : prev);
      }
      addToast('Notes saved.', 'success');
    } catch {
      addToast('Failed to save notes.', 'error');
    }
  };

  if (view === 'details' && selectedDetails) {
    return (
      <ApplicationDetails
        data={selectedDetails}
        onBack={exitDetailView}
        jobMeta={jobMeta}
        onDownloadCV={() => handleDownloadApplicationCV(selectedDetails.application_id)}
        downloadingCV={downloadingCVId === selectedDetails.application_id}
        token={auth.token!}
        onWorkflowStatusChange={handleWorkflowStatusChange}
        onRecruiterNotesChange={handleRecruiterNotesChange}
      />
    );
  }

  const getStatusStyles = (app: Application) => {
    const ps = app.processing_status ?? '';
    if (ps === 'failed')     return { pill: 'bg-red-100 text-red-700',    badge: 'bg-slate-400', label: t.statusFailed,    scoreDisplay: '—' };
    if (ps === 'pending' || ps === 'queued' || ps === 'processing')
                             return { pill: 'bg-blue-100 text-blue-700',  badge: 'bg-blue-300',  label: t.statusProcessing, scoreDisplay: '…' };
    const s = normaliseStatus((app.status ?? '').toLowerCase().trim());
    if (s === 'qualified')   return { pill: 'bg-green-100 text-green-800', badge: 'bg-success',  label: t.filterQualified,  scoreDisplay: app.score != null ? String(app.score) : '—' };
    if (s === 'partial')     return { pill: 'bg-amber-100 text-amber-800', badge: 'bg-warning',  label: t.filterPartial,    scoreDisplay: app.score != null ? String(app.score) : '—' };
    return                          { pill: 'bg-red-100 text-red-800',     badge: 'bg-error',    label: t.filterRejected,   scoreDisplay: app.score != null ? String(app.score) : '—' };
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium gap-1">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t.backToCampaigns}
        </button>

        <div className="flex bg-slate-100 p-1 rounded-lg">
          {FILTER_KEYS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-md text-xs font-bold uppercase transition-all ${
                filter === f ? 'bg-white text-primary shadow-sm' : 'text-textMuted hover:text-textMain'
              }`}
            >
              {filterLabels[f]}
            </button>
          ))}
        </div>
      </div>

      {/* Job metadata header */}
      {jobMeta && (
        <div className="bg-white rounded-xl border border-border px-5 py-4 flex flex-wrap items-center gap-x-6 gap-y-2 shadow-sm">
          <div className="min-w-0">
            <p className="text-xs font-black text-textMuted uppercase tracking-widest mb-0.5">{jobMeta.job_code}</p>
            <h2 className="text-base font-bold text-textMain truncate">{jobMeta.job_title}</h2>
          </div>
          {jobMeta.client_org_name !== undefined && (
            <div className="shrink-0">
              <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-0.5">{t.client}</p>
              <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${jobMeta.client_org_name ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
                {jobMeta.client_org_name || t.general}
              </span>
            </div>
          )}
          {jobMeta.job_type && (
            <div className="shrink-0">
              <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-0.5">Type</p>
              <p className="text-xs font-bold text-textMain">{jobMeta.job_type}</p>
            </div>
          )}
          {jobMeta.location && (
            <div className="shrink-0">
              <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-0.5">Location</p>
              <p className="text-xs font-bold text-textMain">{jobMeta.location}</p>
            </div>
          )}
          <div className="shrink-0 ml-auto">
            <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase ${
              jobMeta.job_status === 'Active' ? 'bg-green-100 text-green-800' :
              jobMeta.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
              'bg-amber-100 text-amber-800'
            }`}>{jobMeta.job_status}</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4">
        {loading ? (
          <div className="p-12 text-center text-textMuted flex flex-col items-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4"></div>
            {t.loading}
          </div>
        ) : filteredApplications.length === 0 ? (
          <div className="bg-white p-12 rounded-xl border border-border text-center text-textMuted">
            {t.noApps}
          </div>
        ) : (
          filteredApplications.map((app) => {
            const styles = getStatusStyles(app);
            const isTerminal = !app.processing_status || app.processing_status === 'scored' || app.processing_status === 'failed' || app.processing_status === 'low_match';
            return (
              <div key={app.id || app.application_id} className="bg-white p-6 rounded-xl border border-border shadow-sm flex flex-col md:flex-row md:items-center justify-between hover:border-primary/30 transition-all">
                <div className="flex items-center gap-6">
                  <div className={`w-14 h-14 aspect-square shrink-0 flex-none rounded-full flex flex-col items-center justify-center font-bold text-white shadow-sm ${styles.badge}`}>
                    <span className="text-lg leading-none">{styles.scoreDisplay}</span>
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

                <div className="mt-4 md:mt-0 flex flex-col items-end gap-2">
                  <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${styles.pill}`}>
                    {styles.label}
                  </span>
                  {app.workflow_status && app.workflow_status !== 'new' && (
                    <span className={`px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider ${WORKFLOW_STATUS_STYLES[app.workflow_status]}`}>
                      {WORKFLOW_STATUS_LABELS[app.workflow_status]}
                    </span>
                  )}
                  {app.duplicate_status === 'possible_duplicate' && (
                    <span className="px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-orange-100 text-orange-700">
                      {t.possibleDuplicate}
                    </span>
                  )}
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
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
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
