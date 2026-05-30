
/**
 * CandidatesWorkspace — Phase 3c / 3d
 *
 * Global recruiter view of all applications across all jobs/campaigns.
 * Uses GET /applications (tenant-wide mode, no job_id) with optional filters.
 *
 * Filter state is persisted in URL query params so views are bookmarkable.
 *
 * Navigation: clicking a candidate name or the View button opens the job-scoped
 * application detail: /applications?job_id=<job_id>&app_id=<application_id>
 *
 * Phase 4 separation: failed/blocked/pre-AI applications are excluded from
 * recruiter-operational views at the query level. The Awaiting Review quick
 * view always adds processing_status=ai_scored so only recruiter-actionable
 * candidates appear. Failed/Blocked uses the 'failed_or_blocked' backend alias
 * that expands to all system-stopped processing statuses.
 *
 * Phase 3d: Inline workflow actions. Each ai_scored row shows a compact "Move
 * to" dropdown driven by VALID_WORKFLOW_TRANSITIONS. Non-ai_scored rows show a
 * muted "System" badge. Updates call PATCH /applications/{id}/workflow-status
 * with optimistic UI and toast feedback.
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
  VALID_WORKFLOW_TRANSITIONS,
} from '../constants/workflow';
import { WorkflowActionMenu } from '../components/WorkflowActionMenu';

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
  email?: string | null;
  phone?: string | null;
  strengths?: string | null;
  gaps?: string | null;
  evaluation_notes?: string | null;
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

// ── Timeline Events ───────────────────────────────────────────────────────

interface TimelineEvent {
  id: string;
  type: 'application_submitted' | 'ai_scored' | 'workflow_transition' | 'processing_failed' | 'security_blocked' | 'duplicate_detected';
  timestamp: string; // ISO string
  actor: string;
  action: string;
  detail?: string;
  isAdvancedMove?: boolean;
}

function buildTimeline(candidate: Candidate, detail: AppDetail | null): TimelineEvent[] {
  const events: TimelineEvent[] = [];

  // Workflow history events (most important, recruiters actions)
  if (detail?.workflow_history && Array.isArray(detail.workflow_history)) {
    for (const h of detail.workflow_history) {
      if (h.created_at) {
        events.push({
          id: h.history_id,
          type: 'workflow_transition',
          timestamp: h.created_at,
          actor: h.changed_by_name || 'System',
          action: h.is_advanced_move
            ? `Advanced Move → ${WORKFLOW_STATUS_LABELS_EN[h.to_status as WorkflowStatus] || h.to_status}`
            : `Moved to ${WORKFLOW_STATUS_LABELS_EN[h.to_status as WorkflowStatus] || h.to_status}`,
          detail: h.note || undefined,
          isAdvancedMove: h.is_advanced_move,
        });
      }
    }
  }

  // System events from application record
  if (detail?.applied_at) {
    events.push({
      id: `submitted-${detail.applied_at}`,
      type: 'application_submitted',
      timestamp: detail.applied_at,
      actor: 'System',
      action: 'Application submitted',
    });
  }

  if (detail?.scored_at) {
    events.push({
      id: `scored-${detail.scored_at}`,
      type: 'ai_scored',
      timestamp: detail.scored_at,
      actor: 'AI',
      action: 'AI evaluation completed',
    });
  }

  // Processing failures
  if (detail?.processing_status === 'failed' && detail?.stopped_reason) {
    // Use updated_at or scored_at as timestamp for failure
    const ts = candidate.updated_at || detail.scored_at || new Date().toISOString();
    events.push({
      id: `failed-${ts}`,
      type: 'processing_failed',
      timestamp: ts,
      actor: 'System',
      action: 'Processing failed',
      detail: detail.stopped_reason,
    });
  }

  if (detail?.processing_status === 'security_blocked' && detail?.security_check_status) {
    const ts = candidate.updated_at || detail.scored_at || new Date().toISOString();
    events.push({
      id: `security-${ts}`,
      type: 'security_blocked',
      timestamp: ts,
      actor: 'System',
      action: 'Security block detected',
      detail: 'This application was blocked due to security concerns.',
    });
  }

  // Duplicate detection
  if (detail?.duplicate_status && detail.duplicate_status !== 'not_duplicate') {
    const ts = candidate.updated_at || detail.applied_at || new Date().toISOString();
    events.push({
      id: `duplicate-${ts}`,
      type: 'duplicate_detected',
      timestamp: ts,
      actor: 'System',
      action: 'Duplicate application detected',
      detail: `Status: ${detail.duplicate_status}`,
    });
  }

  // Sort by timestamp, newest first
  return events.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
}

function timelineEventColor(type: TimelineEvent['type']): string {
  switch (type) {
    case 'workflow_transition':
      return 'bg-indigo-100 text-indigo-700 border-indigo-200';
    case 'ai_scored':
      return 'bg-green-100 text-green-700 border-green-200';
    case 'application_submitted':
      return 'bg-blue-100 text-blue-700 border-blue-200';
    case 'processing_failed':
      return 'bg-red-100 text-red-700 border-red-200';
    case 'security_blocked':
      return 'bg-red-100 text-red-700 border-red-200';
    case 'duplicate_detected':
      return 'bg-amber-100 text-amber-700 border-amber-200';
    default:
      return 'bg-slate-100 text-slate-700 border-slate-200';
  }
}

function timelineEventDotColor(type: TimelineEvent['type']): string {
  switch (type) {
    case 'workflow_transition':
      return 'bg-indigo-400';
    case 'ai_scored':
      return 'bg-green-500';
    case 'application_submitted':
      return 'bg-blue-400';
    case 'processing_failed':
      return 'bg-red-500';
    case 'security_blocked':
      return 'bg-red-500';
    case 'duplicate_detected':
      return 'bg-amber-500';
    default:
      return 'bg-slate-400';
  }
}

function formatTimelineTimestamp(iso: string): string {
  try {
    const date = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
  } catch {
    return iso;
  }
}

// ── CandidateDetailDrawer ───────────────────────────────────────────────────
// Right-side slide panel showing detailed candidate information.
// Opens when user clicks a candidate row. Reuses WorkflowActionMenu for transitions.
// Lazy-fetches full application details from GET /applications/details?application_id=<id>
// to populate the AI Evaluation section without blocking the initial drawer open.

interface AppAnalysis {
  summary?: string;
  strengths?: string[];
  gaps_identified?: string[];
  risks?: string[];
  evaluation_notes?: string;
}

interface AppDetail {
  application_id: string;
  overall_score?: number;
  decision?: string;
  analysis?: AppAnalysis;
  recruiter_notes?: string | null;
  workflow_history?: Array<{
    history_id: string;
    from_status?: string;
    to_status: string;
    note?: string;
    changed_by_name?: string;
    created_at?: string;
    is_advanced_move?: boolean;
  }>;
  applied_at?: string;
  scored_at?: string;
  processing_status?: string;
  stopped_reason?: string;
  duplicate_status?: string;
  security_check_status?: string;
}

interface CandidateDetailDrawerProps {
  candidate: Candidate | null;
  open: boolean;
  lang: 'en' | 'ar';
  updatingId: string | null;
  token: string | null;
  detailVersion: number;
  userRole?: string;
  advancedMoveEnabled?: boolean;
  onClose: () => void;
  onWorkflowUpdate: (applicationId: string, toStatus: WorkflowStatus, note?: string, isAdvancedMove?: boolean) => void;
  onNotesUpdate: (applicationId: string, notes: string) => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

const CandidateDetailDrawer: React.FC<CandidateDetailDrawerProps> = ({
  candidate, open, lang, updatingId, token, detailVersion, userRole, advancedMoveEnabled,
  onClose, onWorkflowUpdate, onNotesUpdate, addToast,
}) => {
  const wfLabels = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;
  const t = T[lang];
  const addToastRef = useRef(addToast);
  useEffect(() => { addToastRef.current = addToast; });

  const [notesText, setNotesText] = useState('');
  const [notesChanged, setNotesChanged] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);

  const [detail, setDetail] = useState<AppDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // Tracks which application_id detail is loaded for, to avoid stale data
  const detailForRef = useRef<string | null>(null);

  // Must come before any early return — Rules of Hooks

  // Sync notes text when candidate changes; do not overwrite unsaved edits
  useEffect(() => {
    setNotesText(candidate?.recruiter_notes || '');
    setNotesChanged(false);
  }, [candidate?.application_id]);

  // Lazy-fetch full application details when drawer opens for a candidate.
  // Cache key includes detailVersion so the parent can force a re-fetch after
  // a successful workflow transition or notes save by incrementing the version.
  useEffect(() => {
    if (!candidate?.application_id || !open) return;
    const cacheKey = `${candidate.application_id}-${detailVersion}`;
    // Already loaded for this exact application + version
    if (detailForRef.current === cacheKey) return;

    let cancelled = false;
    detailForRef.current = cacheKey;
    // Keep existing detail visible while re-fetching (no flash to empty)
    setDetailError(null);
    setDetailLoading(true);

    apiService.get(
      WEBHOOK_CONFIG.APPLICATION_DETAILS_WEBHOOK_URL,
      { application_id: candidate.application_id },
      token,
    ).then((raw: unknown) => {
      if (cancelled) return;
      const detailObj: AppDetail | null = Array.isArray(raw) ? raw[0] : (raw as AppDetail);
      if (!detailObj) throw new Error('No data returned');
      setDetail(detailObj);
      // Sync notes from full detail only if recruiter hasn't made local edits
      setNotesText(prev => {
        const serverNotes = detailObj.recruiter_notes || '';
        return prev === (candidate.recruiter_notes || '') ? serverNotes : prev;
      });
    }).catch((err: any) => {
      if (cancelled) return;
      setDetailError(err?.message || 'Failed to load evaluation details');
      detailForRef.current = null; // allow retry on next open
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });

    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.application_id, open, token, detailVersion]);

  if (!candidate) return null;

  const handleNotesChange = (value: string) => {
    setNotesText(value);
    setNotesChanged(value !== (candidate.recruiter_notes || ''));
  };

  const handleSaveNotes = async () => {
    if (!notesChanged || savingNotes) return;
    setSavingNotes(true);
    try {
      await onNotesUpdate(candidate.application_id, notesText);
      setNotesChanged(false);
      addToastRef.current('Notes saved successfully', 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to save notes', 'error');
    } finally {
      setSavingNotes(false);
    }
  };

  const wfStyle = WORKFLOW_STATUS_STYLES[candidate.workflow_status] ?? 'bg-slate-100 text-slate-600';
  const wfLabel = wfLabels[candidate.workflow_status] ?? candidate.workflow_status;
  const procStyle = processingStyle(candidate.processing_status);
  const procLabel = processingLabel(candidate.processing_status, t);
  const aiStyle = aiDecisionStyle(candidate.status);
  const aiLabel = aiDecisionLabel(candidate.status, t);
  const isUpdating = updatingId === candidate.application_id;

  return (
    <>
      {/* Overlay */}
      {open && (
        <div
          className="fixed inset-0 bg-black/20 z-40"
          onClick={onClose}
        />
      )}

      {/* Drawer panel */}
      <div
        className={`fixed right-0 top-0 bottom-0 w-full sm:w-96 bg-white border-l border-slate-200 shadow-xl z-50 overflow-y-auto transition-transform duration-300 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="sticky top-0 bg-white border-b border-slate-200 px-6 py-4 flex items-start justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-slate-900">{candidate.candidate_name || '—'}</h2>
            <p className="text-xs text-slate-500 mt-1">{candidate.job_title || '—'}</p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 transition-colors"
          >
            <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
            </svg>
          </button>
        </div>

        <div className="px-6 py-4 space-y-6">
          {/* Key metrics */}
          <div className="grid grid-cols-2 gap-4">
            {candidate.score !== null && (
              <div className="space-y-1">
                <label className="block text-xs font-medium text-slate-500 uppercase">{t.colAiMatch}</label>
                <p className="text-2xl font-bold text-slate-900">{candidate.score.toFixed(0)}%</p>
              </div>
            )}
            <div className="space-y-1">
              <label className="block text-xs font-medium text-slate-500 uppercase">{t.colAiResult}</label>
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${aiStyle}`}>
                {aiLabel}
              </span>
            </div>
          </div>

          {/* Status pills */}
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 uppercase mb-2">{t.colWorkflow}</label>
              <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${wfStyle}`}>
                {wfLabel}
              </span>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 uppercase mb-2">{t.colProcessing}</label>
              <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${procStyle}`}>
                {procLabel}
              </span>
            </div>
          </div>

          {/* Job and campaign */}
          <div className="space-y-2 text-sm">
            {candidate.campaign_name && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">{T[lang].filterCampaign}</label>
                <p className="text-slate-700 mt-0.5">{candidate.campaign_name}</p>
              </div>
            )}
            {candidate.client_org_name && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">{T[lang].filterClient}</label>
                <p className="text-slate-700 mt-0.5">{candidate.client_org_name}</p>
              </div>
            )}
            {candidate.job_code && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Job Code</label>
                <p className="text-slate-700 mt-0.5">{candidate.job_code}</p>
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="h-px bg-slate-200" />

          {/* AI Evaluation Summary — lazy-loaded from detail endpoint */}
          {candidate.processing_status === 'ai_scored' && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-slate-900">AI Evaluation</h3>

              {detailLoading && (
                <div className="flex items-center gap-2 text-sm text-slate-400">
                  <span className="w-3.5 h-3.5 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin inline-block" />
                  Loading evaluation…
                </div>
              )}

              {detailError && !detailLoading && (
                <p className="text-sm text-red-500">
                  Could not load evaluation. {' '}
                  <a
                    href={`/applications?job_id=${encodeURIComponent(candidate.job_id)}&app_id=${encodeURIComponent(candidate.application_id)}`}
                    className="underline font-medium"
                  >
                    Open full application
                  </a>
                  {' '}to view details.
                </p>
              )}

              {!detailLoading && !detailError && detail && (() => {
                const summary   = detail.analysis?.summary || '';
                const strengths = detail.analysis?.strengths || [];
                const gaps      = detail.analysis?.gaps_identified || detail.analysis?.risks || [];
                const hasData   = summary || strengths.length > 0 || gaps.length > 0;

                return hasData ? (
                  <>
                    {summary && (
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1 uppercase">Executive Summary</label>
                        <p className="text-sm text-slate-700 leading-relaxed">{summary}</p>
                      </div>
                    )}
                    {strengths.length > 0 && (
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1 uppercase">Strengths</label>
                        <ul className="space-y-1">
                          {strengths.slice(0, 5).map((s, i) => (
                            <li key={i} className="flex items-start gap-1.5 text-sm text-slate-700">
                              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-green-400 flex-shrink-0" />
                              {s}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {gaps.length > 0 && (
                      <div>
                        <label className="block text-xs font-medium text-slate-600 mb-1 uppercase">Gaps / Concerns</label>
                        <ul className="space-y-1">
                          {gaps.slice(0, 5).map((g, i) => (
                            <li key={i} className="flex items-start gap-1.5 text-sm text-slate-700">
                              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" />
                              {g}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-slate-500 italic">
                    No detailed evaluation available.{' '}
                    <a
                      href={`/applications?job_id=${encodeURIComponent(candidate.job_id)}&app_id=${encodeURIComponent(candidate.application_id)}`}
                      className="text-indigo-600 hover:text-indigo-800 font-medium"
                    >
                      Open full application
                    </a>
                    {' '}to view AI evaluation details.
                  </p>
                );
              })()}
            </div>
          )}

          {/* Recruiter Workflow Actions */}
          {candidate.processing_status === 'ai_scored' && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-slate-900">Workflow Actions</h3>
              <div className="flex flex-col gap-2">
                <WorkflowActionMenu
                  applicationId={candidate.application_id}
                  currentStatus={candidate.workflow_status}
                  candidateName={candidate.candidate_name}
                  processingStatus={candidate.processing_status}
                  isUpdating={isUpdating}
                  lang={lang}
                  userRole={userRole}
                  advancedMoveEnabled={advancedMoveEnabled}
                  onTransition={onWorkflowUpdate}
                />
              </div>
              <div className="mt-3 pt-3 border-t border-slate-200">
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-xs font-medium text-slate-600 uppercase">Recruiter Notes</label>
                  {notesChanged && (
                    <button
                      onClick={handleSaveNotes}
                      disabled={savingNotes}
                      className="px-2 py-1 rounded text-xs font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors inline-flex items-center gap-1"
                    >
                      {savingNotes ? (
                        <>
                          <span className="w-2.5 h-2.5 border border-white border-t-transparent rounded-full animate-spin inline-block" />
                          Saving...
                        </>
                      ) : (
                        'Save'
                      )}
                    </button>
                  )}
                </div>
                <textarea
                  value={notesText}
                  onChange={e => handleNotesChange(e.target.value)}
                  placeholder="Add recruiter notes..."
                  className="w-full px-3 py-2 rounded-lg border border-slate-200 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-300 focus:border-transparent resize-vertical min-h-[80px]"
                />
                {notesText.length === 0 ? (
                  <p className="mt-2 text-xs text-slate-500 italic">No notes yet. Add internal recruiter notes for this candidate.</p>
                ) : (
                  <p className="mt-1 text-xs text-slate-400">{notesText.length} characters</p>
                )}
              </div>
            </div>
          )}

          {/* Divider */}
          <div className="h-px bg-slate-200" />

          {/* Activity Timeline */}
          {candidate.processing_status === 'ai_scored' && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-slate-900">Activity Timeline</h3>

              {detailLoading && (
                <div className="space-y-2">
                  {[1, 2, 3].map(i => (
                    <div key={i} className="flex gap-3 opacity-50 animate-pulse">
                      <div className="w-2 h-2 rounded-full bg-slate-300 flex-shrink-0 mt-1.5" />
                      <div className="flex-1 space-y-1">
                        <div className="h-3 bg-slate-200 rounded w-3/4" />
                        <div className="h-2 bg-slate-200 rounded w-1/2" />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {!detailLoading && detail && (() => {
                const timeline = buildTimeline(candidate, detail);
                return timeline.length > 0 ? (
                  <div className="space-y-3 text-xs">
                    {timeline.map((event, idx) => (
                      <div key={event.id} className="relative flex gap-3">
                        {/* Timeline dot */}
                        <div className="flex flex-col items-center flex-shrink-0">
                          <div className={`w-3 h-3 rounded-full ${event.isAdvancedMove ? 'bg-amber-500' : timelineEventDotColor(event.type)}`} />
                          {idx < timeline.length - 1 && <div className="w-0.5 h-8 bg-slate-200 mt-1" />}
                        </div>

                        {/* Event content */}
                        <div className="flex-1 pb-2">
                          <div className={`px-2 py-1.5 rounded border ${event.isAdvancedMove ? 'bg-amber-50 text-amber-800 border-amber-200' : timelineEventColor(event.type)}`}>
                            <div className="flex items-start justify-between gap-1">
                              <span className="font-medium">
                                {event.action}
                                {event.isAdvancedMove && (
                                  <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide bg-amber-200 text-amber-800 px-1 py-0.5 rounded">
                                    Advanced
                                  </span>
                                )}
                              </span>
                              <span className="text-xs opacity-70 flex-shrink-0">
                                {formatTimelineTimestamp(event.timestamp)}
                              </span>
                            </div>
                            {event.actor && <div className="text-xs opacity-75 mt-0.5">by {event.actor}</div>}
                            {event.detail && <div className="text-xs opacity-75 mt-1 italic line-clamp-2">{event.detail}</div>}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500 italic">No activity recorded yet.</p>
                );
              })()}
            </div>
          )}

          {/* Candidate Contact Information */}
          <div className="space-y-2 text-sm">
            {candidate.email && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Email</label>
                <p className="text-slate-700 mt-0.5 break-all">{candidate.email}</p>
              </div>
            )}
            {candidate.phone && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Phone</label>
                <p className="text-slate-700 mt-0.5">{candidate.phone}</p>
              </div>
            )}
            {candidate.applied_at && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Applied</label>
                <p className="text-slate-700 mt-0.5">{formatDate(candidate.applied_at)}</p>
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="h-px bg-slate-200" />

          {/* View full application link */}
          <a
            href={`/applications?job_id=${encodeURIComponent(candidate.job_id)}&app_id=${encodeURIComponent(candidate.application_id)}`}
            className="block w-full text-center py-2 px-4 rounded-lg border border-indigo-300 text-indigo-600 text-sm font-medium hover:bg-indigo-50 transition-colors"
          >
            View Full Application →
          </a>
        </div>
      </div>
    </>
  );
};

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
  const initialAppId = searchParams.get('app_id') || '';

  const [activeView, setActiveView] = useState<QuickView>(initialView);
  const [workflowFilter, setWorkflowFilter] = useState(initialWorkflow);
  const [processingFilter, setProcessingFilter] = useState(initialProcessing);
  const [aiResultFilter, setAiResultFilter] = useState(initialAiResult);
  const [campaignFilter, setCampaignFilter] = useState(initialCampaign);
  const [clientFilter, setClientFilter] = useState(initialClient);
  const [search, setSearch] = useState(initialSearch);
  const [page, setPage] = useState(initialPage);
  const [selectedAppId, setSelectedAppId] = useState(initialAppId);

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

  // Workflow update: tracks the application_id currently being updated
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  // Incremented after a successful workflow or notes update for the open candidate,
  // causing the drawer to re-fetch application details and refresh the timeline.
  const [drawerDetailVersion, setDrawerDetailVersion] = useState(0);

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
    if (selectedAppId)            p.app_id = selectedAppId;
    setSearchParams(p, { replace: true });
  }, [activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, debouncedSearch, page, selectedAppId, setSearchParams]);

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
    setSelectedAppId(c.application_id);
  };

  const closeCandidate = () => {
    setSelectedAppId('');
  };

  // Optimistic workflow status update
  const handleWorkflowUpdate = async (
    applicationId: string,
    toStatus: WorkflowStatus,
    note?: string,
    isAdvancedMove?: boolean,
  ) => {
    if (updatingId) return; // prevent concurrent updates
    setUpdatingId(applicationId);

    // Optimistic update: mutate local state immediately
    const prevCandidates = candidates;
    setCandidates(prev =>
      prev.map(c =>
        c.application_id === applicationId ? { ...c, workflow_status: toStatus } : c
      )
    );

    try {
      await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_WORKFLOW_STATUS_URL}/${applicationId}/workflow-status`,
        { workflow_status: toStatus, note: note || null, advanced_move: isAdvancedMove || false },
        auth.token,
      );
      const label = WORKFLOW_STATUS_LABELS_EN[toStatus];
      addToastRef.current(
        isAdvancedMove ? `Advanced Move → ${label}` : `Moved to ${label}`,
        'success',
      );
      // Refresh drawer detail so timeline picks up the new workflow_history row
      if (applicationId === selectedAppId) {
        setDrawerDetailVersion(v => v + 1);
      }
    } catch (err: any) {
      // Revert optimistic update on failure
      setCandidates(prevCandidates);
      addToastRef.current(err.message || 'Failed to update workflow status', 'error');
    } finally {
      setUpdatingId(null);
    }
  };

  // Update recruiter notes for a candidate
  const handleNotesUpdate = async (applicationId: string, notes: string) => {
    // Update local state immediately (optimistic)
    const prevCandidates = candidates;
    setCandidates(prev =>
      prev.map(c =>
        c.application_id === applicationId ? { ...c, recruiter_notes: notes } : c
      )
    );

    try {
      await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_WORKFLOW_STATUS_URL}/${applicationId}/recruiter-notes`,
        { recruiter_notes: notes },
        auth.token,
      );
      // Refresh drawer detail so timeline reflects latest state
      if (applicationId === selectedAppId) {
        setDrawerDetailVersion(v => v + 1);
      }
    } catch (err: any) {
      // Revert optimistic update on failure
      setCandidates(prevCandidates);
      throw err;
    }
  };

  // Find selected candidate for drawer
  const selectedCandidate = candidates.find(c => c.application_id === selectedAppId) || null;

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
                const isUpdating = updatingId === c.application_id;
                return (
                  <tr
                    key={c.application_id}
                    onClick={() => openCandidate(c)}
                    className={`hover:bg-slate-50 cursor-pointer transition-colors ${isUpdating ? 'opacity-70' : ''}`}
                  >
                    {/* Candidate name — click navigates to detail */}
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

                    {/* Workflow status pill */}
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

                    {/* Actions: workflow move dropdown + view link */}
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
                        <WorkflowActionMenu
                          applicationId={c.application_id}
                          currentStatus={c.workflow_status}
                          candidateName={c.candidate_name}
                          processingStatus={c.processing_status}
                          isUpdating={isUpdating}
                          lang={lang}
                          userRole={auth.user?.role}
                          advancedMoveEnabled={auth.user?.allow_advanced_workflow_move}
                          onTransition={handleWorkflowUpdate}
                        />
                        <button
                          onClick={() => openCandidate(c)}
                          className="text-xs text-indigo-600 hover:text-indigo-800 font-medium whitespace-nowrap"
                        >
                          {t.actionView} →
                        </button>
                      </div>
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

      {/* Candidate Detail Drawer */}
      <CandidateDetailDrawer
        candidate={selectedCandidate}
        open={!!selectedAppId}
        lang={lang}
        updatingId={updatingId}
        token={auth.token}
        detailVersion={drawerDetailVersion}
        userRole={auth.user?.role}
        advancedMoveEnabled={auth.user?.allow_advanced_workflow_move}
        onClose={closeCandidate}
        onWorkflowUpdate={handleWorkflowUpdate}
        onNotesUpdate={handleNotesUpdate}
        addToast={addToast}
      />
    </div>
  );
};
