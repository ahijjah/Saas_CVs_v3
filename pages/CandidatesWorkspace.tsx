
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

import React, { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiService, APIService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import {
  AuthState, WorkflowStatus, CandidateComment, AssignableUser,
  CandidateInterview, InterviewFeedback, CandidateApproval, CandidateTag,
  MessageTemplate, CandidateCommunication,
} from '../types';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';
import {
  WORKFLOW_STATUS_STYLES,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
  VALID_WORKFLOW_TRANSITIONS,
} from '../constants/workflow';
import { WorkflowActionMenu } from '../components/WorkflowActionMenu';
import { TagChip } from '../components/TagChip';

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
  job_allow_advanced_workflow_move?: boolean;
  assigned_user_id?: string | null;
  assigned_user_name?: string | null;
  assigned_at?: string | null;
  is_talent_pool?: boolean;
  preferred_contact_email?: string | null;
  preferred_contact_source?: string | null;
}

interface Pagination {
  page: number;
  limit: number;
  total: number;
  has_more: boolean;
}

interface SavedViewFilters {
  activeView?:       string;
  workflowFilter?:   string;
  processingFilter?: string;
  aiResultFilter?:   string;
  campaignFilter?:   string;
  clientFilter?:     string;
  jobFilter?:        string;
  search?:           string;
  assignedFilter?:   string;
  tagFilter?:        string[];
  talentPoolOnly?:   boolean;
}

interface SavedView {
  saved_view_id:   string;
  name:            string;
  filters:         SavedViewFilters;
  visibility_type: 'private' | 'tenant_shared';
  created_by:      string | null;
  created_by_name: string | null;
  is_own:          boolean;
  created_at:      string;
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
    procInProgress: 'In Progress',
    procAiScored: 'AI Scored',
    procStoppedBeforeAi: 'Stopped Before AI',
    // Legacy labels kept for existing saved-view backward compat
    procFailed: 'Failed',
    procSecurityBlocked: 'Security Blocked',
    procDuplicateBlocked: 'Duplicate Blocked',
    procExtractionFailed: 'Extraction Failed',
    procProcessingFailed: 'Processing Failed',
    procStopped: 'Stopped',
    procQueued: 'Queued',
    procProcessing: 'Processing',
    // Saved views
    savedViews: 'Saved Views',
    saveView: 'Save View',
    saveViewPlaceholder: 'View name…',
    saveViewConfirm: 'Save',
    saveViewCancel: 'Cancel',
    savedViewApplied: 'View applied',
    savedViewDeleted: 'View deleted',
    savedViewSaved: 'View saved',
    savedViewDeleteConfirm: 'Delete this saved view?',
    // Bulk actions
    bulkSelect: '{count} selected',
    bulkMove: 'Move',
    bulkClear: 'Clear',
    bulkConfirmTitle: 'Bulk Move Candidates',
    bulkConfirmMessage: 'Move {count} candidates to:',
    bulkNoteRequired: 'Note required for {status}',
    bulkNoteRecommended: 'Note recommended for {status}',
    bulkNotePlaceholder: 'Reason for bulk move…',
    bulkConfirm: 'Move',
    bulkCancel: 'Cancel',
    bulkUpdated: '{updated} candidates moved, {skipped} skipped.',
    bulkMixedStateWarning: 'Selected candidates are currently in multiple workflow stages. Some moves may be treated as backward transitions.',
    // Assignment
    colAssigned: 'Assigned',
    filterAssigned: 'Assignment',
    assignedToMe: 'Assigned to me',
    unassigned: 'Unassigned',
    anyAssignment: 'Any Assignment',
    assignTo: 'Assign to…',
    bulkAssign: 'Assign',
    bulkAssignTitle: 'Bulk Assign Candidates',
    bulkAssignMessage: 'Assign {count} candidates to:',
    unassignOption: 'Unassign',
    assignSelf: 'Assign to myself',
    assignedTo: 'Assigned to',
    assignmentUpdated: 'Assignment updated',
    // Shared views
    sharedView: 'Shared',
    privateView: 'Private',
    makeShared: 'Make shared (admin)',
    savedViewShared: 'Shared view saved',
    // Discussion / comments
    discussion: 'Discussion',
    addComment: 'Add comment…',
    submitComment: 'Post',
    editComment: 'Edit',
    deleteComment: 'Delete',
    noComments: 'No comments yet. Start a discussion.',
    commentPosted: 'Comment posted',
    commentDeleted: 'Comment deleted',
    commentUpdated: 'Comment updated',
    // Interviews tab
    interviews: 'Interviews',
    noInterviews: 'No interviews scheduled yet.',
    scheduleInterview: 'Schedule Interview',
    editInterview: 'Edit Interview',
    cancelInterview: 'Cancel',
    saveInterview: 'Save',
    interviewType: 'Type',
    scheduledAt: 'Scheduled At',
    durationMin: 'Duration (min)',
    location: 'Location',
    interviewStatus: 'Status',
    interviewNotes: 'Notes',
    interviewers: 'Interviewers (comma-separated)',
    interviewTypeLabels: {
      phone_screen: 'Phone Screen', technical: 'Technical', hr: 'HR',
      panel: 'Panel', final: 'Final', other: 'Other',
    } as Record<string, string>,
    interviewStatusLabels: {
      scheduled: 'Scheduled', completed: 'Completed', cancelled: 'Cancelled', no_show: 'No Show',
    } as Record<string, string>,
    addFeedback: 'Add Feedback',
    feedbackRating: 'Overall Rating',
    feedbackRecommendation: 'Recommendation',
    feedbackNotes: 'Feedback Notes',
    recommendationLabels: {
      strong_yes: 'Strong Yes', yes: 'Yes', neutral: 'Neutral', no: 'No', strong_no: 'Strong No',
    } as Record<string, string>,
    interviewCreated: 'Interview scheduled',
    interviewUpdated: 'Interview updated',
    interviewDeleted: 'Interview deleted',
    feedbackSubmitted: 'Feedback submitted',
    // Approvals tab
    approvalsTab: 'Approvals',
    noApprovals: 'No approval stages yet.',
    initiateApprovals: 'Initiate Approval Chain',
    addApprovalStage: 'Add Stage',
    approve: 'Approve',
    reject: 'Reject',
    needsRevision: 'Needs Revision',
    approvalDecisionComment: 'Comment (optional)',
    approvalLabels: {
      pending: 'Pending', approved: 'Approved', rejected: 'Rejected', needs_revision: 'Needs Revision',
    } as Record<string, string>,
    approvalUpdated: 'Approval updated',
    approvalInitiated: 'Approval chain initiated',
    approvalStageAdded: 'Approval stage added',
    approvalStageDeleted: 'Approval stage deleted',
    // Communication tab
    communicationTab: 'Communication',
    noCommunications: 'No communications logged yet.',
    newCommunication: 'New Message',
    chooseTemplate: 'Choose template…',
    commSubject: 'Subject',
    commBody: 'Message',
    saveDraft: 'Save Draft',
    logCommunication: 'Log as Sent',
    commSaved: 'Communication logged.',
    commCategories: {
      interview_invitation: 'Interview Invitation',
      rejection: 'Rejection',
      shortlisted: 'Shortlisted',
      offer: 'Offer',
      request_info: 'Request Info',
      talent_pool: 'Talent Pool',
      general: 'General',
    } as Record<string, string>,
    // Screening tab
    screeningTab: 'Screening',
    screeningNoQuestions: 'No knockout questions configured for this job.',
    knockoutNotAnswered: 'Not Answered',
    knockoutSource: 'Source',
    knockoutSourceCandidate: 'Candidate (Form)',
    knockoutSourceCandidateEmail: 'Candidate (Email)',
    knockoutSourceRecruiter: 'Recruiter Entered',
    knockoutSourceAI: 'AI Extracted',
    knockoutSourceAICV: 'AI (CV)',
    knockoutSourceAIEmail: 'AI (Email)',
    knockoutSourceAICVEmail: 'AI (CV + Email)',
    knockoutMethodDirect: 'Direct',
    knockoutMethodInferred: 'Inferred',
    knockoutMethodManual: 'Manual',
    knockoutEditAnswer: 'Edit',
    knockoutSaveAnswer: 'Save',
    knockoutCancelEdit: 'Cancel',
    knockoutEditPlaceholder: 'Enter answer',
    knockoutRequired: 'Required',
    knockoutPassingCriteria: 'Passing Criteria',
    knockoutPassed: 'Passed',
    knockoutFailed: 'Failed',
    knockoutNoCriteria: 'No Criteria',
    knockoutNotEvaluated: 'Not Evaluated',
    knockoutAnswer: 'Answer',
    knockoutCriteriaPass: (op: string, val: string) => `Pass if ${op} ${val}`,
    knockoutCriteriaAnswers: (answers: string[]) => `Must answer: ${answers.join(' or ')}`,
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
    procInProgress: 'قيد المعالجة',
    procAiScored: 'مقيَّم بالذكاء',
    procStoppedBeforeAi: 'توقف قبل الذكاء',
    // Legacy labels kept for backward compat
    procFailed: 'فشل',
    procSecurityBlocked: 'محظور أمنياً',
    procDuplicateBlocked: 'مكرر موقوف',
    procExtractionFailed: 'فشل الاستخراج',
    procProcessingFailed: 'فشل المعالجة',
    procStopped: 'موقوف',
    procQueued: 'في الانتظار',
    procProcessing: 'جارٍ المعالجة',
    // Saved views
    savedViews: 'العروض المحفوظة',
    saveView: 'حفظ العرض',
    saveViewPlaceholder: 'اسم العرض…',
    saveViewConfirm: 'حفظ',
    saveViewCancel: 'إلغاء',
    savedViewApplied: 'تم تطبيق العرض',
    savedViewDeleted: 'تم حذف العرض',
    savedViewSaved: 'تم حفظ العرض',
    savedViewDeleteConfirm: 'هل تريد حذف هذا العرض؟',
    // Bulk actions
    bulkSelect: '{count} محدد',
    bulkMove: 'نقل',
    bulkClear: 'مسح',
    bulkConfirmTitle: 'نقل المرشحين بكميات كبيرة',
    bulkConfirmMessage: 'نقل {count} مرشح إلى:',
    bulkNoteRequired: 'ملاحظة مطلوبة لـ {status}',
    bulkNoteRecommended: 'ملاحظة موصى بها لـ {status}',
    bulkNotePlaceholder: 'سبب النقل بكميات كبيرة…',
    bulkConfirm: 'نقل',
    bulkCancel: 'إلغاء',
    bulkUpdated: '{updated} مرشح تم نقله، {skipped} تم تخطيه.',
    bulkMixedStateWarning: 'المرشحون المحددون في مراحل توظيف متعددة. قد تُعدّ بعض التحويلات رجوعاً للوراء.',
    // Assignment
    colAssigned: 'المسؤول',
    filterAssigned: 'التعيين',
    assignedToMe: 'معيّن لي',
    unassigned: 'غير معيّن',
    anyAssignment: 'أي تعيين',
    assignTo: 'تعيين إلى…',
    bulkAssign: 'تعيين',
    bulkAssignTitle: 'تعيين المرشحين بكميات كبيرة',
    bulkAssignMessage: 'تعيين {count} مرشح إلى:',
    unassignOption: 'إلغاء التعيين',
    assignSelf: 'تعيين لي',
    assignedTo: 'معيّن إلى',
    assignmentUpdated: 'تم تحديث التعيين',
    // Shared views
    sharedView: 'مشترك',
    privateView: 'خاص',
    makeShared: 'جعله مشتركاً (مشرف)',
    savedViewShared: 'تم حفظ العرض المشترك',
    // Discussion / comments
    discussion: 'نقاش',
    addComment: 'أضف تعليقاً…',
    submitComment: 'نشر',
    editComment: 'تعديل',
    deleteComment: 'حذف',
    noComments: 'لا تعليقات حتى الآن. ابدأ نقاشاً.',
    commentPosted: 'تم نشر التعليق',
    commentDeleted: 'تم حذف التعليق',
    commentUpdated: 'تم تحديث التعليق',
    // Interviews tab
    interviews: 'المقابلات',
    noInterviews: 'لم يتم جدولة أي مقابلات بعد.',
    scheduleInterview: 'جدولة مقابلة',
    editInterview: 'تعديل المقابلة',
    cancelInterview: 'إلغاء',
    saveInterview: 'حفظ',
    interviewType: 'النوع',
    scheduledAt: 'موعد المقابلة',
    durationMin: 'المدة (دقيقة)',
    location: 'الموقع',
    interviewStatus: 'الحالة',
    interviewNotes: 'ملاحظات',
    interviewers: 'المحاورون (مفصولون بفاصلة)',
    interviewTypeLabels: {
      phone_screen: 'هاتفي', technical: 'تقني', hr: 'موارد بشرية',
      panel: 'لجنة', final: 'نهائي', other: 'آخر',
    } as Record<string, string>,
    interviewStatusLabels: {
      scheduled: 'مجدول', completed: 'مكتمل', cancelled: 'ملغي', no_show: 'غائب',
    } as Record<string, string>,
    addFeedback: 'إضافة تقييم',
    feedbackRating: 'التقييم العام',
    feedbackRecommendation: 'التوصية',
    feedbackNotes: 'ملاحظات التقييم',
    recommendationLabels: {
      strong_yes: 'نعم بقوة', yes: 'نعم', neutral: 'محايد', no: 'لا', strong_no: 'لا بشدة',
    } as Record<string, string>,
    interviewCreated: 'تم جدولة المقابلة',
    interviewUpdated: 'تم تحديث المقابلة',
    interviewDeleted: 'تم حذف المقابلة',
    feedbackSubmitted: 'تم إرسال التقييم',
    // Approvals tab
    approvalsTab: 'الموافقات',
    noApprovals: 'لا توجد مراحل موافقة بعد.',
    initiateApprovals: 'بدء سلسلة الموافقة',
    addApprovalStage: 'إضافة مرحلة',
    approve: 'موافقة',
    reject: 'رفض',
    needsRevision: 'يحتاج مراجعة',
    approvalDecisionComment: 'تعليق (اختياري)',
    approvalLabels: {
      pending: 'معلق', approved: 'موافق عليه', rejected: 'مرفوض', needs_revision: 'يحتاج مراجعة',
    } as Record<string, string>,
    approvalUpdated: 'تم تحديث الموافقة',
    approvalInitiated: 'تم بدء سلسلة الموافقة',
    approvalStageAdded: 'تمت إضافة مرحلة الموافقة',
    approvalStageDeleted: 'تم حذف مرحلة الموافقة',
    // Communication tab
    communicationTab: 'التواصل',
    noCommunications: 'لم يتم تسجيل أي رسائل بعد.',
    newCommunication: 'رسالة جديدة',
    chooseTemplate: 'اختر قالباً…',
    commSubject: 'الموضوع',
    commBody: 'الرسالة',
    saveDraft: 'حفظ كمسودة',
    logCommunication: 'تسجيل كمُرسَل',
    commSaved: 'تم تسجيل الرسالة.',
    commCategories: {
      interview_invitation: 'دعوة مقابلة',
      rejection: 'رفض',
      shortlisted: 'قائمة مختصرة',
      offer: 'عرض',
      request_info: 'طلب معلومات',
      talent_pool: 'مجموعة مواهب',
      general: 'عام',
    } as Record<string, string>,
    // Screening tab
    screeningTab: 'الفرز',
    screeningNoQuestions: 'لا توجد أسئلة فرز مسبق لهذه الوظيفة.',
    knockoutNotAnswered: 'لم تُجب',
    knockoutSource: 'المصدر',
    knockoutSourceCandidate: 'المتقدم (نموذج)',
    knockoutSourceCandidateEmail: 'المتقدم (بريد)',
    knockoutSourceRecruiter: 'أدخله المسؤول',
    knockoutSourceAI: 'مستخرج بالذكاء الاصطناعي',
    knockoutSourceAICV: 'ذكاء اصطناعي (السيرة)',
    knockoutSourceAIEmail: 'ذكاء اصطناعي (البريد)',
    knockoutSourceAICVEmail: 'ذكاء اصطناعي (السيرة + البريد)',
    knockoutMethodDirect: 'مباشر',
    knockoutMethodInferred: 'استنتاج',
    knockoutMethodManual: 'يدوي',
    knockoutEditAnswer: 'تعديل',
    knockoutSaveAnswer: 'حفظ',
    knockoutCancelEdit: 'إلغاء',
    knockoutEditPlaceholder: 'أدخل الإجابة',
    knockoutRequired: 'إلزامي',
    knockoutPassingCriteria: 'معايير الاجتياز',
    knockoutPassed: 'اجتاز',
    knockoutFailed: 'لم يجتز',
    knockoutNoCriteria: 'لا معايير',
    knockoutNotEvaluated: 'غير مُقيَّم',
    knockoutAnswer: 'الإجابة',
    knockoutCriteriaPass: (op: string, val: string) => `ينجح إذا ${op} ${val}`,
    knockoutCriteriaAnswers: (answers: string[]) => `يجب الإجابة بـ: ${answers.join(' أو ')}`,
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
    // Primary display labels
    ai_scored:          t.procAiScored,
    pending:            t.procPending,
    queued:             t.procInProgress,
    processing:         t.procInProgress,
    // All failed sub-types map to the same recruiter-facing label
    failed:             t.procStoppedBeforeAi,
    security_blocked:   t.procSecurityBlocked,
    duplicate_blocked:  t.procDuplicateBlocked,
    extraction_failed:  t.procExtractionFailed,
    processing_failed:  t.procProcessingFailed,
    stopped:            t.procStoppedBeforeAi,
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
  type: 'application_submitted' | 'ai_scored' | 'workflow_transition' | 'processing_failed' | 'security_blocked' | 'duplicate_detected' | 'communication';
  timestamp: string; // ISO string
  actor: string;
  action: string;
  detail?: string;
  isAdvancedMove?: boolean;
  // Communication-specific fields
  communicationData?: {
    communication_id: string;
    subject: string | null;
    body: string | null;
    status: string;
    error_message?: string | null;
    candidate_email?: string | null;
    template_name: string | null;
    template_category: string | null;
    is_automated?: boolean;
    event_type?: string | null;
  };
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
            ? `Exceptional Move → ${WORKFLOW_STATUS_LABELS_EN[h.to_status as WorkflowStatus] || h.to_status}`
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

  // Communication events
  if (detail?.communications && Array.isArray(detail.communications)) {
    for (const comm of detail.communications) {
      if (comm.created_at) {
        const statusLabel = comm.status === 'sent' ? 'Email sent' : comm.status === 'failed' ? 'Email failed' : comm.status === 'draft' ? 'Draft created' : 'Communication logged';
        const actor = comm.created_by_name || 'System';
        const action = comm.subject
          ? `${statusLabel}: ${comm.subject}`
          : statusLabel;

        events.push({
          id: `comm-${comm.communication_id}`,
          type: 'communication',
          timestamp: comm.created_at,
          actor: actor,
          action: action,
          detail: comm.body || undefined,
          communicationData: {
            communication_id: comm.communication_id,
            subject: comm.subject,
            body: comm.body,
            status: comm.status,
            error_message: comm.error_message,
            candidate_email: comm.candidate_email,
            template_name: comm.template_name,
            template_category: comm.template_category,
            is_automated: comm.is_automated,
            event_type: comm.event_type,
          },
        });
      }
    }
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
    case 'communication':
      return 'bg-purple-100 text-purple-700 border-purple-200';
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
    case 'communication':
      return 'bg-purple-500';
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

function filterCommunications(
  communications: CandidateCommunication[],
  filter: 'all' | 'draft' | 'sent' | 'failed' | 'logged' | 'automated' | 'manual'
): CandidateCommunication[] {
  switch (filter) {
    case 'draft':
      return communications.filter(c => c.status === 'draft');
    case 'sent':
      return communications.filter(c => c.status === 'sent');
    case 'failed':
      return communications.filter(c => c.status === 'failed');
    case 'logged':
      return communications.filter(c => c.status === 'logged');
    case 'automated':
      return communications.filter(c => c.is_automated);
    case 'manual':
      return communications.filter(c => !c.is_automated);
    default:
      return communications;
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
  candidate_email?: string | null;
  candidate_email_from_cv?: string | null;
  email_sender_address?: string | null;
  preferred_contact_email?: string | null;
  preferred_contact_source?: string | null;
  submission_source?: string | null;
  communications?: CandidateCommunication[];
  knockout_answers?: import('../types').KnockoutAnswerRecord[];
  job_id?: string;
}

interface CandidateDetailDrawerProps {
  candidate: Candidate | null;
  open: boolean;
  lang: 'en' | 'ar';
  updatingId: string | null;
  token: string | null;
  detailVersion: number;
  userRole?: string;
  currentUserId?: string;
  advancedMoveEnabled?: boolean;
  assignableUsers: AssignableUser[];
  isAdmin: boolean;
  // Tag & talent pool props
  candidateTags: CandidateTag[];
  allTags: CandidateTag[];
  availableJobs: any[];
  onAddTag: (tagId: string) => Promise<void>;
  onRemoveTag: (tagId: string) => Promise<void>;
  onCreateAndAddTag: (name: string) => Promise<void>;
  onToggleTalentPool: () => Promise<void>;
  onReuseCandidate: (targetJobId: string) => Promise<void>;
  onClose: () => void;
  onWorkflowUpdate: (applicationId: string, toStatus: WorkflowStatus, note?: string, isAdvancedMove?: boolean) => void;
  onNotesUpdate: (applicationId: string, notes: string) => Promise<void>;
  onAssign: (applicationId: string, userId: string | null) => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

const CandidateDetailDrawer: React.FC<CandidateDetailDrawerProps> = ({
  candidate, open, lang, updatingId, token, detailVersion, userRole, currentUserId,
  advancedMoveEnabled, assignableUsers, isAdmin,
  candidateTags, allTags, availableJobs,
  onAddTag, onRemoveTag, onCreateAndAddTag, onToggleTalentPool, onReuseCandidate,
  onClose, onWorkflowUpdate, onNotesUpdate, onAssign, addToast,
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

  // Tab state
  const [activeTab, setActiveTab] = useState<'overview' | 'discussion' | 'interviews' | 'approvals' | 'communication' | 'screening'>('overview');
  const [comments, setComments] = useState<CandidateComment[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [newComment, setNewComment] = useState('');
  const [postingComment, setPostingComment] = useState(false);
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState('');

  // @mention autocomplete state
  const [showMentionDropdown, setShowMentionDropdown] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionMatches, setMentionMatches] = useState<AssignableUser[]>([]);
  const [selectedMentionIdx, setSelectedMentionIdx] = useState(0);
  const [atPosition, setAtPosition] = useState(-1);

  // Assignment state
  const [assignDropdownOpen, setAssignDropdownOpen] = useState(false);

  // Interviews state
  const [interviews, setInterviews] = useState<CandidateInterview[]>([]);
  const [interviewsLoading, setInterviewsLoading] = useState(false);
  const [showInterviewForm, setShowInterviewForm] = useState(false);
  const [editingInterviewId, setEditingInterviewId] = useState<string | null>(null);
  const [interviewForm, setInterviewForm] = useState({
    interview_type: 'phone_screen',
    scheduled_at: '',
    duration_min: '',
    location: '',
    interviewers: '',
    status: 'scheduled',
    notes: '',
  });
  const [savingInterview, setSavingInterview] = useState(false);
  const [expandedFeedbackId, setExpandedFeedbackId] = useState<string | null>(null);
  const [interviewFeedback, setInterviewFeedback] = useState<Record<string, InterviewFeedback[]>>({});
  const [feedbackForm, setFeedbackForm] = useState({
    overall_rating: '',
    recommendation: '',
    notes: '',
  });
  const [showFeedbackForm, setShowFeedbackForm] = useState<string | null>(null);
  const [savingFeedback, setSavingFeedback] = useState(false);

  // Approvals state
  const [approvals, setApprovals] = useState<CandidateApproval[]>([]);
  const [approvalsLoading, setApprovalsLoading] = useState(false);
  const [initiatingApprovals, setInitiatingApprovals] = useState(false);
  const [decidingApprovalId, setDecidingApprovalId] = useState<string | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<{ decision: string; comment: string }>({ decision: '', comment: '' });
  const [showAddApprovalForm, setShowAddApprovalForm] = useState(false);
  const [newApprovalStage, setNewApprovalStage] = useState('');
  const [addingApproval, setAddingApproval] = useState(false);

  // Communication state
  const [communications, setCommunications] = useState<CandidateCommunication[]>([]);
  const [commsLoading, setCommsLoading] = useState(false);
  const [showCommForm, setShowCommForm] = useState(false);
  const [commForm, setCommForm] = useState({ subject: '', body: '', template_id: '' });
  const [savingComm, setSavingComm] = useState(false);
  const [commTemplates, setCommTemplates] = useState<MessageTemplate[]>([]);
  const [showSendConfirm, setShowSendConfirm] = useState(false);
  const [sendDialogToEmail, setSendDialogToEmail] = useState('');
  // When non-null, we're editing or sending an existing record (not creating a new one)
  const [editingCommId, setEditingCommId] = useState<string | null>(null);
  const [sendingCommId, setSendingCommId] = useState<string | null>(null);
  const [deletingCommId, setDeletingCommId] = useState<string | null>(null);
  // Communication filtering
  const [commFilter, setCommFilter] = useState<'all' | 'draft' | 'sent' | 'failed' | 'logged' | 'automated' | 'manual'>('all');
  // Send dialog email validation error
  const [sendEmailError, setSendEmailError] = useState<string | null>(null);
  // Preferred contact local state — shadows candidate prop after recruiter saves
  const [showPrefContactEdit, setShowPrefContactEdit] = useState(false);
  const [prefContactEmailInput, setPrefContactEmailInput] = useState('');
  const [savingPrefContact, setSavingPrefContact] = useState(false);
  const [localPreferred, setLocalPreferred] = useState<{ email: string | null; source: string | null } | null>(null);

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
    if (!candidate?.application_id || !open || !token) return;
    const cacheKey = `${candidate.application_id}-${detailVersion}`;
    // Already loaded for this exact application + version
    if (detailForRef.current === cacheKey) return;

    let cancelled = false;
    detailForRef.current = cacheKey;
    // Keep existing detail visible while re-fetching (no flash to empty)
    setDetailError(null);
    setDetailLoading(true);

    const api = new APIService(token);
    Promise.all([
      apiService.get(
        WEBHOOK_CONFIG.APPLICATION_DETAILS_WEBHOOK_URL,
        { application_id: candidate.application_id },
        token,
      ),
      api.listCommunications(candidate.application_id),
    ]).then(([raw, commsData]: any[]) => {
      if (cancelled) return;
      const detailObj: AppDetail | null = Array.isArray(raw) ? raw[0] : (raw as AppDetail);
      if (!detailObj) throw new Error('No data returned');
      // Merge communications into detail
      detailObj.communications = commsData?.communications || [];
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

  // Load comments when switching to Discussion tab or when candidate changes
  useEffect(() => {
    if (!candidate?.application_id || !open || !token || activeTab !== 'discussion') return;
    let cancelled = false;
    setCommentsLoading(true);
    apiService.get(
      `${WEBHOOK_CONFIG.APPLICATION_COMMENTS_URL}/${candidate.application_id}/comments`,
      {},
      token,
    ).then((data: any) => {
      if (!cancelled && data?.comments) setComments(data.comments);
    }).catch(() => {
      if (!cancelled) setComments([]);
    }).finally(() => {
      if (!cancelled) setCommentsLoading(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.application_id, open, token, activeTab]);

  // Load interviews when switching to Interviews tab
  useEffect(() => {
    if (!candidate?.application_id || !open || !token || activeTab !== 'interviews') return;
    let cancelled = false;
    setInterviewsLoading(true);
    apiService.get(
      `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews`,
      {},
      token,
    ).then((data: any) => {
      if (!cancelled && data?.interviews) setInterviews(data.interviews);
    }).catch(() => {
      if (!cancelled) setInterviews([]);
    }).finally(() => {
      if (!cancelled) setInterviewsLoading(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.application_id, open, token, activeTab]);

  // Load approvals when switching to Approvals tab
  useEffect(() => {
    if (!candidate?.application_id || !open || !token || activeTab !== 'approvals') return;
    let cancelled = false;
    setApprovalsLoading(true);
    apiService.get(
      `${WEBHOOK_CONFIG.APPLICATION_APPROVALS_URL}/${candidate.application_id}/approvals`,
      {},
      token,
    ).then((data: any) => {
      if (!cancelled && data?.approvals) setApprovals(data.approvals);
    }).catch(() => {
      if (!cancelled) setApprovals([]);
    }).finally(() => {
      if (!cancelled) setApprovalsLoading(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.application_id, open, token, activeTab]);

  // Load communications and templates when switching to Communication tab
  useEffect(() => {
    if (!candidate?.application_id || !open || !token || activeTab !== 'communication') return;
    let cancelled = false;
    setCommsLoading(true);
    const api = new APIService(token);
    Promise.all([
      api.listCommunications(candidate.application_id),
      api.listTemplates({ active_only: true }),
    ]).then(([commsData, templatesData]: any[]) => {
      if (!cancelled) {
        setCommunications(commsData?.communications || []);
        setCommTemplates(templatesData?.templates || []);
      }
    }).catch(() => {
      if (!cancelled) setCommunications([]);
    }).finally(() => {
      if (!cancelled) setCommsLoading(false);
    });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidate?.application_id, open, token, activeTab]);

  // Tag/talent pool local UI state
  const [showTagCreator, setShowTagCreator] = useState(false);
  const [newTagName, setNewTagName] = useState('');
  const [showReuseModal, setShowReuseModal] = useState(false);
  const [reuseTargetJob, setReuseTargetJob] = useState('');

  // Reset tab and all tab data when candidate changes
  useEffect(() => {
    setActiveTab('overview');
    setComments([]);
    setNewComment('');
    setEditingCommentId(null);
    setAssignDropdownOpen(false);
    setShowMentionDropdown(false);
    setMentionQuery('');
    setInterviews([]);
    setShowInterviewForm(false);
    setEditingInterviewId(null);
    setExpandedFeedbackId(null);
    setInterviewFeedback({});
    setShowFeedbackForm(null);
    setApprovals([]);
    setDecidingApprovalId(null);
    setShowAddApprovalForm(false);
    setCommunications([]);
    setShowCommForm(false);
    setCommForm({ subject: '', body: '', template_id: '' });
    setEditingCommId(null);
    setSendingCommId(null);
    setDeletingCommId(null);
    setCommFilter('all');
    setSendEmailError(null);
    setShowPrefContactEdit(false);
    setPrefContactEmailInput('');
    setLocalPreferred(null);
    setShowTagCreator(false);
    setNewTagName('');
    setShowReuseModal(false);
    setReuseTargetJob('');
  }, [candidate?.application_id]);

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

  // Refresh communications list and keep detail.communications in sync for the timeline.
  // Call this after any mutation (send/draft/log/delete/retry) so both the Communication
  // tab and the Activity Timeline reflect the latest state without a full page reload.
  const refreshCommunications = async (applicationId: string) => {
    if (!token) return;
    try {
      const api = new APIService(token);
      const commsData = await api.listCommunications(applicationId);
      const updated: CandidateCommunication[] = commsData?.communications || [];
      setCommunications(updated);
      // Also patch detail.communications so buildTimeline picks up the change
      setDetail(prev => prev ? { ...prev, communications: updated } : prev);
    } catch {
      // silently ignore — optimistic state already applied
    }
  };

  const handlePostComment = async () => {
    const text = newComment.trim();
    if (!text || postingComment) return;
    setPostingComment(true);
    try {
      const data: any = await apiService.post(
        `${WEBHOOK_CONFIG.APPLICATION_COMMENTS_URL}/${candidate.application_id}/comments`,
        { comment_text: text },
        token!,
      );
      if (data?.comment) {
        setComments(prev => [...prev, data.comment]);
        setNewComment('');
        addToastRef.current(T[lang].commentPosted, 'success');
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to post comment', 'error');
    } finally {
      setPostingComment(false);
    }
  };

  const handleEditComment = async (commentId: string) => {
    const text = editingText.trim();
    if (!text) return;
    try {
      const data: any = await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_COMMENTS_URL}/${candidate.application_id}/comments/${commentId}`,
        { comment_text: text },
        token!,
      );
      if (data?.comment) {
        setComments(prev => prev.map(c => c.comment_id === commentId ? data.comment : c));
        setEditingCommentId(null);
        addToastRef.current(T[lang].commentUpdated, 'success');
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to update comment', 'error');
    }
  };

  const handleDeleteComment = async (commentId: string) => {
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.APPLICATION_COMMENTS_URL}/${candidate.application_id}/comments/${commentId}`,
        token!,
      );
      setComments(prev => prev.filter(c => c.comment_id !== commentId));
      addToastRef.current(T[lang].commentDeleted, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to delete comment', 'error');
    }
  };

  // Render @mentions in comment text as highlighted spans
  const renderCommentText = (text: string) => {
    const parts = text.split(/(@\w[\w.\- ]*)/g);
    return parts.map((part, i) =>
      part.startsWith('@')
        ? <span key={i} className="text-indigo-600 font-medium">{part}</span>
        : <span key={i}>{part}</span>
    );
  };

  // Handle @mention detection in comment input
  const handleNewCommentChange = (text: string) => {
    setNewComment(text);

    // Detect mention pattern: @ followed by word chars at end of string
    const mentionMatch = text.match(/@(\w*)$/);

    if (mentionMatch) {
      const query = mentionMatch[1].toLowerCase();
      const matches = assignableUsers.filter(u =>
        u.full_name.toLowerCase().includes(query) ||
        u.email.toLowerCase().includes(query)
      );

      if (query.length >= 1 && matches.length > 0) {
        setMentionMatches(matches);
        setShowMentionDropdown(true);
        setMentionQuery(query);
        setAtPosition(text.lastIndexOf('@'));
        setSelectedMentionIdx(0);
      } else if (query.length >= 1) {
        // Query typed but no matches
        setShowMentionDropdown(false);
      } else {
        // Just @ typed
        setShowMentionDropdown(true);
        setMentionMatches(assignableUsers);
        setMentionQuery('');
        setAtPosition(text.lastIndexOf('@'));
        setSelectedMentionIdx(0);
      }
    } else {
      setShowMentionDropdown(false);
    }
  };

  const insertMention = (user: AssignableUser) => {
    const beforeMention = newComment.substring(0, atPosition);
    const afterMention = newComment.substring(atPosition + 1 + mentionQuery.length);
    setNewComment(beforeMention + '@' + user.full_name + ' ' + afterMention);
    setShowMentionDropdown(false);
    setMentionQuery('');
  };

  const handleCommentKeydown = (e: React.KeyboardEvent) => {
    if (showMentionDropdown && mentionMatches.length > 0) {
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedMentionIdx(prev => (prev > 0 ? prev - 1 : mentionMatches.length - 1));
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedMentionIdx(prev => (prev < mentionMatches.length - 1 ? prev + 1 : 0));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        insertMention(mentionMatches[selectedMentionIdx]);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setShowMentionDropdown(false);
      }
      return;
    }

    // Normal comment submission
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      handlePostComment();
    }
  };

  // Interview handlers
  const handleSaveInterview = async () => {
    if (savingInterview) return;
    setSavingInterview(true);
    try {
      const payload: Record<string, any> = {
        interview_type: interviewForm.interview_type,
        status: interviewForm.status,
        scheduled_at: interviewForm.scheduled_at || null,
        duration_min: interviewForm.duration_min ? parseInt(interviewForm.duration_min) : null,
        location: interviewForm.location || null,
        interviewers: interviewForm.interviewers
          ? interviewForm.interviewers.split(',').map(s => s.trim()).filter(Boolean)
          : [],
        notes: interviewForm.notes || null,
      };
      let data: any;
      if (editingInterviewId) {
        data = await apiService.patch(
          `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews/${editingInterviewId}`,
          payload, token!,
        );
        if (data?.interview) {
          setInterviews(prev => prev.map(i => i.interview_id === editingInterviewId ? data.interview : i));
          addToastRef.current(T[lang].interviewUpdated, 'success');
        }
      } else {
        data = await apiService.post(
          `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews`,
          payload, token!,
        );
        if (data?.interview) {
          setInterviews(prev => [...prev, data.interview]);
          addToastRef.current(T[lang].interviewCreated, 'success');
        }
      }
      setShowInterviewForm(false);
      setEditingInterviewId(null);
      setInterviewForm({ interview_type: 'phone_screen', scheduled_at: '', duration_min: '', location: '', interviewers: '', status: 'scheduled', notes: '' });
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to save interview', 'error');
    } finally {
      setSavingInterview(false);
    }
  };

  const handleDeleteInterview = async (interviewId: string) => {
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews/${interviewId}`,
        token!,
      );
      setInterviews(prev => prev.filter(i => i.interview_id !== interviewId));
      addToastRef.current(T[lang].interviewDeleted, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to delete interview', 'error');
    }
  };

  const handleLoadFeedback = async (interviewId: string) => {
    if (expandedFeedbackId === interviewId) {
      setExpandedFeedbackId(null);
      return;
    }
    setExpandedFeedbackId(interviewId);
    if (interviewFeedback[interviewId]) return;
    try {
      const data: any = await apiService.get(
        `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews/${interviewId}/feedback`,
        {}, token,
      );
      if (data?.feedback) setInterviewFeedback(prev => ({ ...prev, [interviewId]: data.feedback }));
    } catch {
      setInterviewFeedback(prev => ({ ...prev, [interviewId]: [] }));
    }
  };

  const handleSubmitFeedback = async (interviewId: string) => {
    if (savingFeedback) return;
    setSavingFeedback(true);
    try {
      const data: any = await apiService.post(
        `${WEBHOOK_CONFIG.APPLICATION_INTERVIEWS_URL}/${candidate.application_id}/interviews/${interviewId}/feedback`,
        {
          overall_rating: feedbackForm.overall_rating ? parseInt(feedbackForm.overall_rating) : null,
          recommendation: feedbackForm.recommendation || null,
          notes: feedbackForm.notes || null,
          scorecard: {},
        },
        token!,
      );
      if (data?.feedback) {
        setInterviewFeedback(prev => {
          const existing = prev[interviewId] || [];
          const idx = existing.findIndex(f => f.feedback_id === data.feedback.feedback_id);
          return {
            ...prev,
            [interviewId]: idx >= 0
              ? existing.map(f => f.feedback_id === data.feedback.feedback_id ? data.feedback : f)
              : [...existing, data.feedback],
          };
        });
        setInterviews(prev => prev.map(i =>
          i.interview_id === interviewId ? { ...i, feedback_count: i.feedback_count + 1 } : i
        ));
        addToastRef.current(T[lang].feedbackSubmitted, 'success');
        setShowFeedbackForm(null);
        setFeedbackForm({ overall_rating: '', recommendation: '', notes: '' });
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to submit feedback', 'error');
    } finally {
      setSavingFeedback(false);
    }
  };

  // Approval handlers
  const handleInitiateApprovals = async () => {
    if (initiatingApprovals) return;
    setInitiatingApprovals(true);
    try {
      const data: any = await apiService.post(
        `${WEBHOOK_CONFIG.APPLICATION_APPROVALS_URL}/${candidate.application_id}/approvals/initiate`,
        { approver_ids: {} },
        token!,
      );
      if (data?.approvals) {
        setApprovals(data.approvals);
        addToastRef.current(T[lang].approvalInitiated, 'success');
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to initiate approvals', 'error');
    } finally {
      setInitiatingApprovals(false);
    }
  };

  const handleDecideApproval = async (approvalId: string) => {
    if (!approvalDecision.decision) return;
    try {
      const data: any = await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_APPROVALS_URL}/${candidate.application_id}/approvals/${approvalId}`,
        { decision: approvalDecision.decision, comment: approvalDecision.comment || null },
        token!,
      );
      if (data?.approval) {
        setApprovals(prev => prev.map(a => a.approval_id === approvalId ? data.approval : a));
        addToastRef.current(T[lang].approvalUpdated, 'success');
        setDecidingApprovalId(null);
        setApprovalDecision({ decision: '', comment: '' });
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to update approval', 'error');
    }
  };

  const handleDeleteApproval = async (approvalId: string) => {
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.APPLICATION_APPROVALS_URL}/${candidate.application_id}/approvals/${approvalId}`,
        token!,
      );
      setApprovals(prev => prev.filter(a => a.approval_id !== approvalId));
      addToastRef.current(T[lang].approvalStageDeleted, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to delete approval stage', 'error');
    }
  };

  const handleAddApprovalStage = async () => {
    if (!newApprovalStage.trim() || addingApproval) return;
    setAddingApproval(true);
    try {
      const data: any = await apiService.post(
        `${WEBHOOK_CONFIG.APPLICATION_APPROVALS_URL}/${candidate.application_id}/approvals`,
        { approval_stage: newApprovalStage.trim(), stage_order: approvals.length },
        token!,
      );
      if (data?.approval) {
        setApprovals(prev => [...prev, data.approval]);
        addToastRef.current(T[lang].approvalStageAdded, 'success');
        setNewApprovalStage('');
        setShowAddApprovalForm(false);
      }
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to add approval stage', 'error');
    } finally {
      setAddingApproval(false);
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
        className={`fixed right-0 top-0 bottom-0 w-full md:w-[700px] bg-white border-l border-slate-200 shadow-xl z-50 overflow-y-auto transition-transform duration-300 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header + Tabs wrapper — single sticky block to avoid gaps */}
        <div className="sticky top-0 z-10 bg-white">
          {/* Header */}
          <div className="border-b border-slate-200 px-6 py-4 flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <h2 className="text-lg font-semibold text-slate-900 truncate">{candidate.candidate_name || '—'}</h2>
              <p className="text-xs text-slate-500 mt-0.5 truncate">{candidate.job_title || '—'}</p>
              {/* Assignee chip */}
              {candidate.assigned_user_name && (
                <div className="flex items-center gap-1 mt-1.5">
                  <div className="w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 text-[9px] font-bold flex items-center justify-center">
                    {candidate.assigned_user_name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                  </div>
                  <span className="text-[10px] text-emerald-700">{candidate.assigned_user_name}</span>
                </div>
              )}
            </div>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-600 transition-colors flex-shrink-0"
            >
              <svg className="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </button>
          </div>

          {/* Tab navigation */}
          <div className="border-b border-slate-200 px-6 flex gap-1 overflow-x-auto">
          {([
            { key: 'overview', label: 'Overview', badge: null },
            { key: 'screening', label: t.screeningTab, badge: (detail?.knockout_answers?.length ?? 0) > 0 ? detail!.knockout_answers!.length : null },
            { key: 'interviews', label: t.interviews, badge: interviews.length > 0 ? interviews.length : null },
            { key: 'approvals', label: t.approvalsTab, badge: approvals.length > 0 ? approvals.length : null },
            { key: 'discussion', label: t.discussion, badge: comments.length > 0 ? comments.length : null },
            { key: 'communication', label: t.communicationTab, badge: communications.length > 0 ? communications.length : null },
          ] as const).map(({ key, label, badge }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key as any)}
              className={`py-2.5 px-1 text-xs font-semibold border-b-2 whitespace-nowrap transition-colors ${
                activeTab === key
                  ? 'border-indigo-500 text-indigo-700'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              {label}
              {badge !== null && (
                <span className="ml-1.5 bg-indigo-100 text-indigo-600 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                  {badge}
                </span>
              )}
            </button>
          ))}
          </div>
        </div>

        {/* Discussion Tab */}
        {activeTab === 'discussion' && (
          <div className="px-6 py-4 space-y-4">
            {commentsLoading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
                <span className="w-3.5 h-3.5 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin inline-block" />
                Loading discussion…
              </div>
            ) : comments.length === 0 ? (
              <p className="text-sm text-slate-400 italic py-4">{t.noComments}</p>
            ) : (
              <div className="space-y-4">
                {comments.map(comment => (
                  <div key={comment.comment_id} className="group">
                    <div className="flex items-start gap-2">
                      <div className="w-7 h-7 rounded-full bg-slate-200 text-slate-600 text-[10px] font-bold flex items-center justify-center flex-shrink-0">
                        {comment.author_name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-semibold text-slate-800">{comment.author_name}</span>
                          <span className="text-[10px] text-slate-400">{formatTimelineTimestamp(comment.created_at || '')}</span>
                          {comment.updated_at !== comment.created_at && (
                            <span className="text-[10px] text-slate-400 italic">(edited)</span>
                          )}
                        </div>
                        {editingCommentId === comment.comment_id ? (
                          <div className="space-y-2">
                            <textarea
                              value={editingText}
                              onChange={e => setEditingText(e.target.value)}
                              className="w-full px-2 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none"
                              rows={3}
                            />
                            <div className="flex gap-2">
                              <button
                                onClick={() => handleEditComment(comment.comment_id)}
                                className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                              >Save</button>
                              <button
                                onClick={() => setEditingCommentId(null)}
                                className="text-xs text-slate-400 hover:text-slate-600"
                              >Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <div className="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg px-3 py-2">
                            {renderCommentText(comment.comment_text)}
                          </div>
                        )}
                        {(comment.is_own || isAdmin) && editingCommentId !== comment.comment_id && (
                          <div className="flex gap-2 mt-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            {comment.is_own && (
                              <button
                                onClick={() => { setEditingCommentId(comment.comment_id); setEditingText(comment.comment_text); }}
                                className="text-[10px] text-slate-400 hover:text-indigo-600"
                              >{t.editComment}</button>
                            )}
                            <button
                              onClick={() => handleDeleteComment(comment.comment_id)}
                              className="text-[10px] text-slate-400 hover:text-red-500"
                            >{t.deleteComment}</button>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Add comment with @mention autocomplete */}
            <div className="border-t border-slate-200 pt-4 space-y-2 relative">
              <div className="relative">
                <textarea
                  value={newComment}
                  onChange={e => handleNewCommentChange(e.target.value)}
                  onKeyDown={handleCommentKeydown}
                  placeholder={t.addComment}
                  rows={3}
                  maxLength={2000}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none"
                />

                {/* @mention dropdown */}
                {showMentionDropdown && mentionMatches.length > 0 && (
                  <div className="absolute bottom-full mb-1 left-0 right-0 bg-white border border-slate-200 rounded-lg shadow-lg max-h-[180px] overflow-y-auto z-50">
                    {mentionMatches.map((user, idx) => (
                      <button
                        key={user.user_id}
                        onClick={() => insertMention(user)}
                        onMouseEnter={() => setSelectedMentionIdx(idx)}
                        className={`w-full text-left px-3 py-2 text-xs flex items-center gap-2 transition-colors ${
                          idx === selectedMentionIdx
                            ? 'bg-indigo-50 text-indigo-700'
                            : 'hover:bg-slate-50 text-slate-700'
                        }`}
                      >
                        <div className="w-6 h-6 rounded-full bg-slate-200 text-slate-600 text-[9px] font-bold flex items-center justify-center flex-shrink-0">
                          {user.full_name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="font-medium truncate">{user.full_name}</p>
                          <p className="text-slate-400 truncate text-[10px]">{user.email}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between">
                <span className="text-[10px] text-slate-400">{newComment.length}/2000 · Ctrl+Enter to post · @ for mentions</span>
                <button
                  onClick={handlePostComment}
                  disabled={!newComment.trim() || postingComment}
                  className="px-3 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40 transition-colors"
                >
                  {postingComment ? '…' : t.submitComment}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Interviews Tab */}
        {activeTab === 'interviews' && (
          <div className="px-6 py-4 space-y-4">
            {interviewsLoading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
                <span className="w-3.5 h-3.5 border-2 border-violet-300 border-t-transparent rounded-full animate-spin inline-block" />
                Loading interviews…
              </div>
            ) : (
              <>
                {interviews.length === 0 && !showInterviewForm && (
                  <p className="text-sm text-slate-400 italic py-2">{t.noInterviews}</p>
                )}
                {interviews.map(iv => (
                  <div key={iv.interview_id} className="border border-slate-200 rounded-xl overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-3 bg-slate-50">
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${
                          iv.status === 'completed' ? 'bg-emerald-100 text-emerald-700' :
                          iv.status === 'cancelled' ? 'bg-red-100 text-red-700' :
                          iv.status === 'no_show' ? 'bg-orange-100 text-orange-700' :
                          'bg-violet-100 text-violet-700'
                        }`}>
                          {t.interviewStatusLabels[iv.status] || iv.status}
                        </span>
                        <span className="text-xs font-semibold text-slate-700 truncate">
                          {t.interviewTypeLabels[iv.interview_type] || iv.interview_type}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => {
                            setEditingInterviewId(iv.interview_id);
                            setInterviewForm({
                              interview_type: iv.interview_type,
                              scheduled_at: iv.scheduled_at ? iv.scheduled_at.substring(0, 16) : '',
                              duration_min: iv.duration_min?.toString() || '',
                              location: iv.location || '',
                              interviewers: (iv.interviewers || []).join(', '),
                              status: iv.status,
                              notes: iv.notes || '',
                            });
                            setShowInterviewForm(true);
                          }}
                          className="text-slate-400 hover:text-slate-600 p-1"
                          title={t.editInterview}
                        >
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        {isAdmin && (
                          <button
                            onClick={() => handleDeleteInterview(iv.interview_id)}
                            className="text-slate-400 hover:text-red-500 p-1"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="px-4 py-3 space-y-1.5 text-xs text-slate-600">
                      {iv.scheduled_at && (
                        <div className="flex gap-2">
                          <span className="text-slate-400 w-20 flex-shrink-0">{t.scheduledAt}:</span>
                          <span>{new Date(iv.scheduled_at).toLocaleString()}</span>
                        </div>
                      )}
                      {iv.duration_min && (
                        <div className="flex gap-2">
                          <span className="text-slate-400 w-20 flex-shrink-0">{t.durationMin}:</span>
                          <span>{iv.duration_min} min</span>
                        </div>
                      )}
                      {iv.location && (
                        <div className="flex gap-2">
                          <span className="text-slate-400 w-20 flex-shrink-0">{t.location}:</span>
                          <span>{iv.location}</span>
                        </div>
                      )}
                      {iv.interviewers && iv.interviewers.length > 0 && (
                        <div className="flex gap-2">
                          <span className="text-slate-400 w-20 flex-shrink-0">{t.interviewers.split(' (')[0]}:</span>
                          <span>{iv.interviewers.join(', ')}</span>
                        </div>
                      )}
                      {iv.notes && (
                        <div className="flex gap-2">
                          <span className="text-slate-400 w-20 flex-shrink-0">{t.interviewNotes}:</span>
                          <span className="break-words">{iv.notes}</span>
                        </div>
                      )}
                      {iv.created_by_name && (
                        <div className="flex gap-2 pt-1 border-t border-slate-100">
                          <span className="text-slate-400 w-20 flex-shrink-0">By:</span>
                          <span className="text-slate-500">{iv.created_by_name}</span>
                        </div>
                      )}
                    </div>
                    {/* Feedback section */}
                    <div className="border-t border-slate-100">
                      <button
                        onClick={() => handleLoadFeedback(iv.interview_id)}
                        className="w-full px-4 py-2 text-xs text-slate-500 hover:text-violet-600 hover:bg-violet-50 transition-colors flex items-center gap-1.5"
                      >
                        <svg className={`w-3 h-3 transition-transform ${expandedFeedbackId === iv.interview_id ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                        </svg>
                        {t.addFeedback} ({iv.feedback_count})
                      </button>
                      {expandedFeedbackId === iv.interview_id && (
                        <div className="px-4 pb-3 space-y-3">
                          {(interviewFeedback[iv.interview_id] || []).map(fb => (
                            <div key={fb.feedback_id} className="bg-slate-50 rounded-lg p-3 text-xs">
                              <div className="flex items-center justify-between mb-1.5">
                                <span className="font-semibold text-slate-700">{fb.reviewer_name || 'Reviewer'}</span>
                                {fb.overall_rating && (
                                  <span className="flex items-center gap-0.5">
                                    {[1,2,3,4,5].map(s => (
                                      <span key={s} className={`w-2.5 h-2.5 rounded-full ${s <= fb.overall_rating! ? 'bg-amber-400' : 'bg-slate-200'}`} />
                                    ))}
                                  </span>
                                )}
                              </div>
                              {fb.recommendation && (
                                <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold ${
                                  fb.recommendation === 'strong_yes' ? 'bg-emerald-100 text-emerald-700' :
                                  fb.recommendation === 'yes' ? 'bg-green-100 text-green-700' :
                                  fb.recommendation === 'no' ? 'bg-red-100 text-red-700' :
                                  fb.recommendation === 'strong_no' ? 'bg-rose-100 text-rose-700' :
                                  'bg-slate-100 text-slate-600'
                                }`}>
                                  {t.recommendationLabels[fb.recommendation] || fb.recommendation}
                                </span>
                              )}
                              {fb.notes && <p className="mt-1.5 text-slate-600">{fb.notes}</p>}
                            </div>
                          ))}
                          {showFeedbackForm === iv.interview_id ? (
                            <div className="space-y-2">
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-500 w-24">{t.feedbackRating}:</span>
                                <div className="flex gap-1">
                                  {[1,2,3,4,5].map(s => (
                                    <button
                                      key={s}
                                      onClick={() => setFeedbackForm(f => ({ ...f, overall_rating: s.toString() }))}
                                      className={`w-6 h-6 rounded-full text-xs font-bold transition-colors ${
                                        parseInt(feedbackForm.overall_rating) >= s ? 'bg-amber-400 text-white' : 'bg-slate-200 text-slate-500 hover:bg-amber-200'
                                      }`}
                                    >
                                      {s}
                                    </button>
                                  ))}
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-slate-500 w-24">{t.feedbackRecommendation}:</span>
                                <select
                                  value={feedbackForm.recommendation}
                                  onChange={e => setFeedbackForm(f => ({ ...f, recommendation: e.target.value }))}
                                  className="flex-1 border border-slate-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                                >
                                  <option value="">—</option>
                                  {(['strong_yes','yes','neutral','no','strong_no'] as const).map(r => (
                                    <option key={r} value={r}>{t.recommendationLabels[r]}</option>
                                  ))}
                                </select>
                              </div>
                              <textarea
                                value={feedbackForm.notes}
                                onChange={e => setFeedbackForm(f => ({ ...f, notes: e.target.value }))}
                                placeholder={t.feedbackNotes}
                                rows={2}
                                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400 resize-none"
                              />
                              <div className="flex gap-2 justify-end">
                                <button onClick={() => setShowFeedbackForm(null)} className="px-3 py-1 text-xs text-slate-500 hover:text-slate-700">
                                  {t.cancelInterview}
                                </button>
                                <button
                                  onClick={() => handleSubmitFeedback(iv.interview_id)}
                                  disabled={savingFeedback}
                                  className="px-3 py-1.5 text-xs font-semibold bg-violet-600 text-white rounded-lg hover:bg-violet-700 disabled:opacity-40"
                                >
                                  {savingFeedback ? '…' : t.feedbackSubmitted.split(' ')[0]}
                                </button>
                              </div>
                            </div>
                          ) : (
                            <button
                              onClick={() => {
                                setShowFeedbackForm(iv.interview_id);
                                setFeedbackForm({ overall_rating: '', recommendation: '', notes: '' });
                              }}
                              className="text-xs text-violet-600 hover:text-violet-800 font-medium"
                            >
                              + {t.addFeedback}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}

                {/* Interview form */}
                {showInterviewForm && (
                  <div className="border border-violet-200 rounded-xl p-4 space-y-3 bg-violet-50">
                    <p className="text-xs font-semibold text-violet-700">
                      {editingInterviewId ? t.editInterview : t.scheduleInterview}
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.interviewType}</label>
                        <select
                          value={interviewForm.interview_type}
                          onChange={e => setInterviewForm(f => ({ ...f, interview_type: e.target.value }))}
                          className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                        >
                          {(['phone_screen','technical','hr','panel','final','other'] as const).map(it => (
                            <option key={it} value={it}>{t.interviewTypeLabels[it]}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.interviewStatus}</label>
                        <select
                          value={interviewForm.status}
                          onChange={e => setInterviewForm(f => ({ ...f, status: e.target.value }))}
                          className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                        >
                          {(['scheduled','completed','cancelled','no_show'] as const).map(s => (
                            <option key={s} value={s}>{t.interviewStatusLabels[s]}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.scheduledAt}</label>
                        <input
                          type="datetime-local"
                          value={interviewForm.scheduled_at}
                          onChange={e => setInterviewForm(f => ({ ...f, scheduled_at: e.target.value }))}
                          className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                        />
                      </div>
                      <div>
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.durationMin}</label>
                        <input
                          type="number"
                          value={interviewForm.duration_min}
                          onChange={e => setInterviewForm(f => ({ ...f, duration_min: e.target.value }))}
                          className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                          placeholder="60"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.location}</label>
                      <input
                        value={interviewForm.location}
                        onChange={e => setInterviewForm(f => ({ ...f, location: e.target.value }))}
                        className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                        placeholder="Zoom / Office / Phone"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.interviewers}</label>
                      <input
                        value={interviewForm.interviewers}
                        onChange={e => setInterviewForm(f => ({ ...f, interviewers: e.target.value }))}
                        className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400"
                        placeholder="Ali, Sarah"
                      />
                    </div>
                    <div>
                      <label className="text-[10px] text-slate-500 uppercase tracking-wide">{t.interviewNotes}</label>
                      <textarea
                        value={interviewForm.notes}
                        onChange={e => setInterviewForm(f => ({ ...f, notes: e.target.value }))}
                        rows={2}
                        className="w-full mt-0.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400 resize-none"
                      />
                    </div>
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => {
                          setShowInterviewForm(false);
                          setEditingInterviewId(null);
                        }}
                        className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700"
                      >
                        {t.cancelInterview}
                      </button>
                      <button
                        onClick={handleSaveInterview}
                        disabled={savingInterview}
                        className="px-4 py-1.5 text-xs font-semibold bg-violet-600 text-white rounded-lg hover:bg-violet-700 disabled:opacity-40"
                      >
                        {savingInterview ? '…' : t.saveInterview}
                      </button>
                    </div>
                  </div>
                )}

                {!showInterviewForm && (
                  <button
                    onClick={() => {
                      setEditingInterviewId(null);
                      setInterviewForm({ interview_type: 'phone_screen', scheduled_at: '', duration_min: '', location: '', interviewers: '', status: 'scheduled', notes: '' });
                      setShowInterviewForm(true);
                    }}
                    className="w-full py-2 text-xs font-semibold text-violet-600 hover:text-violet-800 border border-dashed border-violet-300 rounded-xl hover:border-violet-500 transition-colors"
                  >
                    + {t.scheduleInterview}
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {/* Approvals Tab */}
        {activeTab === 'approvals' && (
          <div className="px-6 py-4 space-y-3">
            {approvalsLoading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
                <span className="w-3.5 h-3.5 border-2 border-amber-300 border-t-transparent rounded-full animate-spin inline-block" />
                Loading approvals…
              </div>
            ) : (
              <>
                {approvals.length === 0 && (
                  <p className="text-sm text-slate-400 italic py-2">{t.noApprovals}</p>
                )}

                {/* Approval chain */}
                <div className="space-y-2">
                  {approvals.map((ap, idx) => (
                    <div key={ap.approval_id} className="border border-slate-200 rounded-xl overflow-hidden">
                      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-50">
                        <div className="flex items-center gap-2">
                          <span className="w-5 h-5 rounded-full bg-slate-200 text-slate-600 text-[10px] font-bold flex items-center justify-center flex-shrink-0">
                            {idx + 1}
                          </span>
                          <div>
                            <p className="text-xs font-semibold text-slate-700">{ap.approval_stage}</p>
                            {ap.approver_name && (
                              <p className="text-[10px] text-slate-400">{ap.approver_name}</p>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            ap.decision === 'approved' ? 'bg-emerald-100 text-emerald-700' :
                            ap.decision === 'rejected' ? 'bg-red-100 text-red-700' :
                            ap.decision === 'needs_revision' ? 'bg-amber-100 text-amber-700' :
                            'bg-slate-100 text-slate-500'
                          }`}>
                            {t.approvalLabels[ap.decision] || ap.decision}
                          </span>
                          {isAdmin && (
                            <button
                              onClick={() => handleDeleteApproval(ap.approval_id)}
                              className="text-slate-300 hover:text-red-500 p-0.5"
                            >
                              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                              </svg>
                            </button>
                          )}
                        </div>
                      </div>
                      {ap.comment && (
                        <div className="px-4 py-2 text-xs text-slate-500 italic border-t border-slate-100">
                          "{ap.comment}"
                        </div>
                      )}
                      {ap.decided_at && (
                        <div className="px-4 pb-2 text-[10px] text-slate-400">
                          Decided {new Date(ap.decided_at).toLocaleDateString()}
                          {ap.approver_name && ` by ${ap.approver_name}`}
                        </div>
                      )}
                      {/* Decision action for this stage */}
                      {decidingApprovalId === ap.approval_id ? (
                        <div className="px-4 pb-3 pt-1 space-y-2 border-t border-slate-100">
                          <select
                            value={approvalDecision.decision}
                            onChange={e => setApprovalDecision(d => ({ ...d, decision: e.target.value }))}
                            className="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                          >
                            <option value="">— Select decision —</option>
                            <option value="approved">{t.approve}</option>
                            <option value="rejected">{t.reject}</option>
                            <option value="needs_revision">{t.needsRevision}</option>
                          </select>
                          <input
                            value={approvalDecision.comment}
                            onChange={e => setApprovalDecision(d => ({ ...d, comment: e.target.value }))}
                            placeholder={t.approvalDecisionComment}
                            className="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                          />
                          <div className="flex gap-2 justify-end">
                            <button onClick={() => setDecidingApprovalId(null)} className="px-3 py-1 text-xs text-slate-500">
                              {t.cancelInterview}
                            </button>
                            <button
                              onClick={() => handleDecideApproval(ap.approval_id)}
                              disabled={!approvalDecision.decision}
                              className="px-3 py-1.5 text-xs font-semibold bg-amber-500 text-white rounded-lg hover:bg-amber-600 disabled:opacity-40"
                            >
                              Submit
                            </button>
                          </div>
                        </div>
                      ) : (
                        ap.decision === 'pending' && (
                          <button
                            onClick={() => {
                              setDecidingApprovalId(ap.approval_id);
                              setApprovalDecision({ decision: '', comment: '' });
                            }}
                            className="w-full px-4 py-2 text-xs text-amber-600 hover:text-amber-800 hover:bg-amber-50 transition-colors border-t border-slate-100 text-left"
                          >
                            + Decide on this stage
                          </button>
                        )
                      )}
                    </div>
                  ))}
                </div>

                {/* Admin: add stage manually */}
                {isAdmin && (
                  <>
                    {showAddApprovalForm ? (
                      <div className="flex gap-2 items-center">
                        <input
                          value={newApprovalStage}
                          onChange={e => setNewApprovalStage(e.target.value)}
                          placeholder="Stage name (e.g. Legal Review)"
                          className="flex-1 border border-slate-200 rounded-lg px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-amber-400"
                          onKeyDown={e => { if (e.key === 'Enter') handleAddApprovalStage(); }}
                        />
                        <button
                          onClick={handleAddApprovalStage}
                          disabled={addingApproval || !newApprovalStage.trim()}
                          className="px-3 py-1.5 text-xs font-semibold bg-amber-500 text-white rounded-lg hover:bg-amber-600 disabled:opacity-40"
                        >
                          {addingApproval ? '…' : 'Add'}
                        </button>
                        <button onClick={() => setShowAddApprovalForm(false)} className="text-slate-400 hover:text-slate-600 text-xs px-1">
                          ✕
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowAddApprovalForm(true)}
                        className="w-full py-2 text-xs font-semibold text-amber-600 hover:text-amber-800 border border-dashed border-amber-300 rounded-xl hover:border-amber-500 transition-colors"
                      >
                        + {t.addApprovalStage}
                      </button>
                    )}
                  </>
                )}

                {/* Initiate chain from policy */}
                <button
                  onClick={handleInitiateApprovals}
                  disabled={initiatingApprovals}
                  className="w-full py-2 text-xs font-semibold text-slate-600 hover:text-slate-800 border border-dashed border-slate-300 rounded-xl hover:border-slate-500 transition-colors disabled:opacity-40"
                >
                  {initiatingApprovals ? '…' : t.initiateApprovals}
                </button>
              </>
            )}
          </div>
        )}

        {/* Communication Tab */}
        {activeTab === 'communication' && (() => {
          // Resolve the best candidate email from detail when no preferred contact is manually set.
          // For forwarded/platform/manual intake the submission email may belong to a recruiter —
          // prefer the CV-extracted email instead. For public apply, submission email is the candidate.
          const forwarderIntake = (
            detail?.submission_source === 'email_forwarding' ||
            detail?.submission_source === 'platform_email' ||
            detail?.submission_source === 'manual_upload'
          );
          const resolvedFromDetail: { email: string | null; source: string | null } = (() => {
            if (!detail) return { email: null, source: null };
            if (forwarderIntake) {
              if (detail.candidate_email_from_cv)
                return { email: detail.candidate_email_from_cv, source: 'cv_extracted' };
              return { email: null, source: null };
            }
            // public_apply (or unknown): submission email is the candidate's own address
            if (detail.candidate_email)
              return { email: detail.candidate_email, source: detail.submission_source || null };
            if (detail.candidate_email_from_cv)
              return { email: detail.candidate_email_from_cv, source: 'cv_extracted' };
            return { email: null, source: null };
          })();

          // Effective preferred contact: local state → DB preferred → resolved from intake
          const effectivePreferred = localPreferred ?? {
            email: candidate.preferred_contact_email ?? resolvedFromDetail.email,
            source: candidate.preferred_contact_source ?? resolvedFromDetail.source,
          };
          const sourceLabels: Record<string, string> = {
            manual_upload: 'Manual Upload', email_forwarding: 'Email Fwd',
            platform_email: 'Platform Email', public_apply: 'Application',
            cv_extracted: 'CV Extracted', email_sender: 'Email Sender',
            manual_override: 'Manual Override',
          };
          return (
          <div className="px-6 py-4 space-y-4">
            {/* Preferred Contact Email block */}
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-bold text-slate-600 uppercase tracking-wide">Preferred Contact Email</p>
                <button
                  onClick={() => { setPrefContactEmailInput(effectivePreferred.email || ''); setShowPrefContactEdit(v => !v); }}
                  className="text-xs text-indigo-600 hover:underline"
                >
                  {showPrefContactEdit ? 'Cancel' : 'Edit'}
                </button>
              </div>
              {!showPrefContactEdit && (
                effectivePreferred.email ? (
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium text-slate-800 break-all">{effectivePreferred.email}</p>
                    {effectivePreferred.source && (
                      <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-indigo-100 text-indigo-700">
                        {sourceLabels[effectivePreferred.source] || effectivePreferred.source}
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="flex items-start gap-2">
                    <svg className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                    </svg>
                    <p className="text-xs text-amber-700">No candidate email available. Enter a recipient email manually before sending, or set a preferred contact email.</p>
                  </div>
                )
              )}
              {showPrefContactEdit && (
                <div className="space-y-2 mt-2">
                  <input
                    type="email"
                    value={prefContactEmailInput}
                    onChange={e => setPrefContactEmailInput(e.target.value)}
                    placeholder="email@example.com"
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={async () => {
                        if (savingPrefContact) return;
                        setSavingPrefContact(true);
                        try {
                          const api = new APIService(token);
                          const result = await api.updatePreferredContact(candidate.application_id, {
                            preferred_contact_email: prefContactEmailInput.trim() || null,
                          });
                          setLocalPreferred({ email: result.preferred_contact_email, source: result.preferred_contact_source });
                          setShowPrefContactEdit(false);
                          addToast('Preferred contact email updated.', 'success');
                        } catch (err: any) {
                          addToast(err.message || 'Failed to update contact email.', 'error');
                        } finally {
                          setSavingPrefContact(false);
                        }
                      }}
                      disabled={savingPrefContact}
                      className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg disabled:opacity-50"
                    >
                      {savingPrefContact ? '…' : 'Save'}
                    </button>
                    <button onClick={() => setShowPrefContactEdit(false)} className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700">
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* New communication button */}
            {!showCommForm && (
              <button
                onClick={() => {
                  setEditingCommId(null);
                  setCommForm({ subject: '', body: '', template_id: '' });
                  setShowCommForm(true);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                </svg>
                {t.newCommunication}
              </button>
            )}

            {/* Compose form */}
            {showCommForm && (
              <div className="bg-slate-50 rounded-xl p-4 border border-slate-200 space-y-3">
                <p className="text-xs font-bold text-slate-700">
                  {editingCommId ? 'Edit Draft' : t.newCommunication}
                </p>
                {/* Template chooser */}
                {commTemplates.length > 0 && (
                  <div>
                    <label className="block text-xs font-semibold text-slate-600 mb-1">{t.chooseTemplate}</label>
                    <select
                      value={commForm.template_id}
                      onChange={e => {
                        const tpl = commTemplates.find(t => t.template_id === e.target.value);
                        setCommForm(f => ({
                          ...f,
                          template_id: e.target.value,
                          subject: tpl ? tpl.subject : f.subject,
                          body: tpl ? tpl.body : f.body,
                        }));
                      }}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
                    >
                      <option value="">{t.chooseTemplate}</option>
                      {commTemplates.map(tpl => (
                        <option key={tpl.template_id} value={tpl.template_id}>
                          {tpl.name} ({(t.commCategories as Record<string, string>)[tpl.category] || tpl.category})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div>
                  <label className="block text-xs font-semibold text-slate-600 mb-1">{t.commSubject}</label>
                  <input
                    type="text"
                    value={commForm.subject}
                    onChange={e => setCommForm(f => ({ ...f, subject: e.target.value }))}
                    placeholder="Subject…"
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-600 mb-1">{t.commBody}</label>
                  <textarea
                    value={commForm.body}
                    onChange={e => setCommForm(f => ({ ...f, body: e.target.value }))}
                    rows={6}
                    placeholder="Message…"
                    className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-y"
                  />
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    onClick={() => { setSendDialogToEmail(effectivePreferred.email || ''); setSendEmailError(null); setShowSendConfirm(true); }}
                    disabled={savingComm || (!commForm.subject && !commForm.body) || !candidate}
                    className="px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-xs font-semibold rounded-lg transition-colors disabled:opacity-50"
                  >
                    Send Email
                  </button>
                  <button
                    onClick={async () => {
                      if (savingComm || !candidate) return;
                      setSavingComm(true);
                      const appId = candidate.application_id;
                      try {
                        const api = new APIService(token);
                        if (editingCommId) {
                          // Update existing draft in-place
                          await api.updateCommunication(appId, editingCommId, {
                            subject: commForm.subject || undefined,
                            body: commForm.body || undefined,
                            template_id: commForm.template_id || undefined,
                          });
                          setEditingCommId(null);
                        } else {
                          await api.logCommunication(appId, {
                            subject: commForm.subject || undefined,
                            body: commForm.body || undefined,
                            status: 'logged',
                            template_id: commForm.template_id || undefined,
                          });
                        }
                        setShowCommForm(false);
                        addToast(editingCommId ? 'Draft updated.' : t.commSaved, 'success');
                        await refreshCommunications(appId);
                      } catch (err: any) {
                        addToast(err.message || 'Failed to save.', 'error');
                      } finally {
                        setSavingComm(false);
                      }
                    }}
                    disabled={savingComm || (!commForm.subject && !commForm.body)}
                    className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors disabled:opacity-50"
                  >
                    {savingComm ? '…' : editingCommId ? 'Update Draft' : t.logCommunication}
                  </button>
                  <button
                    onClick={async () => {
                      if (savingComm || !candidate) return;
                      setSavingComm(true);
                      const appId = candidate.application_id;
                      try {
                        const api = new APIService(token);
                        if (editingCommId) {
                          await api.updateCommunication(appId, editingCommId, {
                            subject: commForm.subject || undefined,
                            body: commForm.body || undefined,
                            template_id: commForm.template_id || undefined,
                          });
                          setEditingCommId(null);
                        } else {
                          await api.logCommunication(appId, {
                            subject: commForm.subject || undefined,
                            body: commForm.body || undefined,
                            status: 'draft',
                            template_id: commForm.template_id || undefined,
                          });
                        }
                        setShowCommForm(false);
                        addToast('Draft saved.', 'success');
                        await refreshCommunications(appId);
                      } catch (err: any) {
                        addToast(err.message || 'Failed to save draft.', 'error');
                      } finally {
                        setSavingComm(false);
                      }
                    }}
                    disabled={savingComm || (!commForm.subject && !commForm.body)}
                    className="px-3 py-2 bg-slate-200 hover:bg-slate-300 text-slate-700 text-xs font-semibold rounded-lg transition-colors disabled:opacity-50"
                  >
                    {t.saveDraft}
                  </button>
                </div>
                <button
                  onClick={() => { setShowCommForm(false); setEditingCommId(null); }}
                  className="w-full px-3 py-2 text-xs text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Send confirmation dialog — handles both new compose and existing draft/failed send */}
            {showSendConfirm && candidate && (
              <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
                <div className="bg-white rounded-lg p-6 max-w-md w-full shadow-lg">
                  <h3 className="font-bold text-lg mb-4">Send Email to {candidate.candidate_name}</h3>
                  <div className="bg-slate-50 rounded-lg p-4 mb-4 space-y-3">
                    <div>
                      <label className="text-xs font-semibold text-slate-500 uppercase block mb-1">To</label>
                      <input
                        type="email"
                        value={sendDialogToEmail}
                        onChange={e => {
                          const val = e.target.value;
                          setSendDialogToEmail(val);
                          setSendEmailError(
                            val && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val.trim())
                              ? 'Invalid email address format.'
                              : null
                          );
                        }}
                        placeholder="recipient@example.com"
                        className={`w-full border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300 ${
                          sendEmailError ? 'border-red-400 bg-red-50' :
                          !sendDialogToEmail ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-white'
                        }`}
                      />
                      {sendEmailError && (
                        <p className="text-xs text-red-600 mt-1">{sendEmailError}</p>
                      )}
                      {!sendDialogToEmail && !sendEmailError && (
                        <p className="text-xs text-amber-600 mt-1">Enter a recipient email to send.</p>
                      )}
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-500 uppercase">Subject</p>
                      <p className="text-sm text-slate-800">{(sendingCommId ? communications.find(c => c.communication_id === sendingCommId)?.subject : commForm.subject) || '(no subject)'}</p>
                    </div>
                  </div>
                  <p className="text-xs text-slate-500 mb-4">This email will be sent immediately and recorded in the communication history.</p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => { setShowSendConfirm(false); setSendingCommId(null); setSendEmailError(null); }}
                      className="flex-1 px-4 py-2 rounded text-sm text-slate-700 bg-slate-100 hover:bg-slate-200"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={async () => {
                        if (savingComm || !candidate || !sendDialogToEmail.trim()) return;
                        const emailVal = sendDialogToEmail.trim();
                        if (!emailVal || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailVal)) {
                          setSendEmailError('Invalid recipient email address.');
                          return;
                        }
                        setSavingComm(true);
                        const appId = candidate.application_id;
                        try {
                          const api = new APIService(token);
                          if (sendingCommId) {
                            // Retry an existing draft/failed record
                            await api.sendExistingCommunication(appId, sendingCommId);
                          } else {
                            // New send from compose form
                            const toEmailOverride = emailVal !== (effectivePreferred.email || '') ? emailVal : undefined;
                            await api.sendCommunication(appId, {
                              subject: commForm.subject,
                              body: commForm.body,
                              to_email: toEmailOverride,
                              template_id: commForm.template_id || undefined,
                            });
                            if (editingCommId) setEditingCommId(null);
                          }
                          setShowCommForm(false);
                          setShowSendConfirm(false);
                          setSendingCommId(null);
                          setSendEmailError(null);
                          setCommForm({ subject: '', body: '', template_id: '' });
                          addToast('Email sent successfully.', 'success');
                          await refreshCommunications(appId);
                        } catch (err: any) {
                          addToast(err.message || 'Failed to send email.', 'error');
                          await refreshCommunications(appId);
                        } finally {
                          setSavingComm(false);
                        }
                      }}
                      disabled={savingComm || !sendDialogToEmail.trim() || !!sendEmailError}
                      className="flex-1 px-4 py-2 rounded text-sm text-white bg-green-600 hover:bg-green-700 disabled:opacity-50"
                    >
                      {savingComm ? 'Sending…' : 'Send'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Communication filters */}
            {!commsLoading && communications.length > 0 && (
              <div className="flex flex-wrap gap-2 pb-3 border-b border-slate-200">
                {(['all', 'draft', 'sent', 'failed', 'logged', 'automated', 'manual'] as const).map(f => (
                  <button
                    key={f}
                    onClick={() => setCommFilter(f)}
                    className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                      commFilter === f
                        ? 'bg-indigo-600 text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
                  </button>
                ))}
              </div>
            )}

            {/* History list */}
            {commsLoading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
                <span className="w-3.5 h-3.5 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin inline-block" />
                Loading…
              </div>
            ) : filterCommunications(communications, commFilter).length === 0 ? (
              <p className="text-sm text-slate-400 italic py-4">
                {communications.length === 0 ? t.noCommunications : 'No communications match the selected filter.'}
              </p>
            ) : (
              <div className="space-y-3">
                {filterCommunications(communications, commFilter).map(comm => {
                  const isDraft = comm.status === 'draft';
                  const isFailed = comm.status === 'failed';
                  const isActionable = isDraft || isFailed;
                  return (
                    <div key={comm.communication_id} className={`bg-white rounded-xl p-4 border ${isDraft ? 'border-amber-200' : isFailed ? 'border-red-200' : 'border-slate-200'}`}>
                      <div className="flex items-start justify-between mb-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          {comm.template_category && (
                            <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-indigo-100 text-indigo-700">
                              {(t.commCategories as Record<string, string>)[comm.template_category] || comm.template_category}
                            </span>
                          )}
                          {comm.is_automated && (
                            <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-violet-100 text-violet-700">
                              Auto-created
                            </span>
                          )}
                          <span className={`px-1.5 py-0.5 text-[10px] font-bold rounded-full ${
                            comm.status === 'sent' ? 'bg-green-100 text-green-700' :
                            comm.status === 'failed' ? 'bg-red-100 text-red-700' :
                            comm.status === 'draft' ? 'bg-amber-100 text-amber-700' :
                            'bg-slate-100 text-slate-600'
                          }`}>
                            {comm.status}
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-400 ml-2 flex-shrink-0">
                          {new Date(comm.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      {comm.subject && (
                        <p className="text-sm font-semibold text-slate-800 mb-1">{comm.subject}</p>
                      )}
                      {comm.body && (
                        <p className="text-xs text-slate-600 whitespace-pre-wrap line-clamp-3">{comm.body}</p>
                      )}
                      {comm.error_message && (
                        <p className="text-[10px] text-red-600 mt-2 bg-red-50 rounded px-2 py-1">{comm.error_message}</p>
                      )}
                      {comm.created_by_name && (
                        <p className="text-[10px] text-slate-400 mt-2">by {comm.created_by_name}</p>
                      )}
                      {/* Action buttons for draft and failed records */}
                      {isActionable && (
                        <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-100">
                          {/* Edit (draft only) */}
                          {isDraft && (
                            <button
                              onClick={() => {
                                setEditingCommId(comm.communication_id);
                                setCommForm({
                                  subject: comm.subject || '',
                                  body: comm.body || '',
                                  template_id: '',
                                });
                                setShowCommForm(true);
                              }}
                              className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                            >
                              Edit
                            </button>
                          )}
                          {/* Send / Retry */}
                          <button
                            onClick={() => {
                              setSendingCommId(comm.communication_id);
                              setSendDialogToEmail(comm.candidate_email || effectivePreferred.email || '');
                              setSendEmailError(null);
                              setShowSendConfirm(true);
                            }}
                            disabled={deletingCommId === comm.communication_id}
                            className="text-xs font-medium text-green-600 hover:text-green-800 disabled:opacity-40"
                          >
                            {isFailed ? 'Retry Send' : 'Send Email'}
                          </button>
                          {/* Delete (draft only) */}
                          {isDraft && (
                            <button
                              onClick={async () => {
                                if (deletingCommId || !candidate) return;
                                const appId = candidate.application_id;
                                setDeletingCommId(comm.communication_id);
                                try {
                                  const api = new APIService(token);
                                  await api.deleteCommunication(appId, comm.communication_id);
                                  addToast('Draft deleted.', 'success');
                                  await refreshCommunications(appId);
                                } catch (err: any) {
                                  addToast(err.message || 'Failed to delete draft.', 'error');
                                } finally {
                                  setDeletingCommId(null);
                                }
                              }}
                              disabled={deletingCommId === comm.communication_id}
                              className="text-xs font-medium text-red-500 hover:text-red-700 disabled:opacity-40 ml-auto"
                            >
                              {deletingCommId === comm.communication_id ? 'Deleting…' : 'Delete'}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          );
        })()}

        {/* Screening Tab */}
        {activeTab === 'screening' && (
          <div className="px-6 py-4">
            {detailLoading ? (
              <div className="flex items-center gap-2 text-sm text-slate-400 py-6">
                <span className="w-3.5 h-3.5 border-2 border-indigo-300 border-t-transparent rounded-full animate-spin inline-block" />
                Loading…
              </div>
            ) : (() => {
              const koAnswers = detail?.knockout_answers ?? [];
              if (koAnswers.length === 0) {
                return <p className="text-sm text-slate-400 italic py-4">{t.screeningNoQuestions}</p>;
              }

              type EvalResult = 'passed' | 'failed' | 'no_criteria' | 'not_evaluated' | 'not_answered';

              const evaluateAnswer = (qa: import('../types').KnockoutAnswerRecord, rawVal: string | null | undefined): EvalResult => {
                if (rawVal == null || rawVal === '') return 'not_answered';
                const pc = qa.passing_criteria;
                if (!pc) return 'no_criteria';
                if ((qa.question_type === 'yes_no' || qa.question_type === 'single_choice') && pc.passing_answers?.length) {
                  return pc.passing_answers.some(a => a.toLowerCase() === rawVal.toLowerCase()) ? 'passed' : 'failed';
                }
                if (qa.question_type === 'number' && pc.operator != null && pc.value != null) {
                  const num = parseFloat(rawVal);
                  if (isNaN(num)) return 'not_evaluated';
                  const ops: Record<string, boolean> = { '>=': num >= pc.value!, '>': num > pc.value!, '=': num === pc.value!, '<=': num <= pc.value!, '<': num < pc.value! };
                  const r = ops[pc.operator!];
                  return r === undefined ? 'not_evaluated' : (r ? 'passed' : 'failed');
                }
                return 'not_evaluated';
              };

              const evalBadge = (result: EvalResult) => {
                if (result === 'not_answered') return <span className="px-2 py-0.5 rounded-full text-[10px] font-black bg-slate-100 text-slate-400 italic">{t.knockoutNotAnswered}</span>;
                if (result === 'passed') return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-green-100 text-green-700"><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" /></svg>{t.knockoutPassed}</span>;
                if (result === 'failed') return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-red-100 text-red-700"><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12" /></svg>{t.knockoutFailed}</span>;
                if (result === 'no_criteria') return <span className="px-2 py-0.5 rounded-full text-[10px] font-black bg-slate-100 text-slate-500">{t.knockoutNoCriteria}</span>;
                return <span className="px-2 py-0.5 rounded-full text-[10px] font-black bg-amber-100 text-amber-700">{t.knockoutNotEvaluated}</span>;
              };

              const formatCriteria = (qa: import('../types').KnockoutAnswerRecord): string | null => {
                const pc = qa.passing_criteria;
                if (!pc) return null;
                if (qa.question_type === 'number' && pc.operator != null && pc.value != null) return t.knockoutCriteriaPass(pc.operator!, String(pc.value));
                if ((qa.question_type === 'yes_no' || qa.question_type === 'single_choice') && pc.passing_answers?.length) return t.knockoutCriteriaAnswers(pc.passing_answers);
                return null;
              };

              const sourceLabel = (src: string | null | undefined) => {
                if (!src || src === 'candidate_provided' || src === 'candidate_form') return t.knockoutSourceCandidate;
                if (src === 'candidate_email') return t.knockoutSourceCandidateEmail;
                if (src === 'recruiter_entered') return t.knockoutSourceRecruiter;
                if (src === 'ai_extracted' || src === 'ai_cv') return t.knockoutSourceAICV;
                if (src === 'ai_email') return t.knockoutSourceAIEmail;
                if (src === 'ai_cv_email') return t.knockoutSourceAICVEmail;
                return src;
              };

              const methodBadge = (method: string | null | undefined) => {
                if (!method || method === 'direct_statement') {
                  return (
                    <span className="inline-flex items-center px-1 py-0.5 rounded-full bg-green-50 text-green-700 text-[8px] font-black uppercase tracking-widest">
                      {t.knockoutMethodDirect}
                    </span>
                  );
                }
                if (method === 'ai_inference') {
                  return (
                    <span className="inline-flex items-center px-1 py-0.5 rounded-full bg-indigo-50 text-indigo-600 text-[8px] font-black uppercase tracking-widest">
                      {t.knockoutMethodInferred}
                    </span>
                  );
                }
                if (method === 'manual_entry') {
                  return (
                    <span className="inline-flex items-center px-1 py-0.5 rounded-full bg-slate-100 text-slate-500 text-[8px] font-black uppercase tracking-widest">
                      {t.knockoutMethodManual}
                    </span>
                  );
                }
                return null;
              };

              return (
                <div className="divide-y divide-slate-100 -mx-6 px-0">
                  {koAnswers.map((qa, idx) => {
                    const displayVal = qa.answer_value;
                    const evalResult = evaluateAnswer(qa, displayVal);
                    const criteriaLabel = formatCriteria(qa);
                    return (
                      <div key={qa.question_id} className="px-6 py-4 grid grid-cols-1 gap-y-1.5">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <p className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-0.5">{idx + 1}. {qa.question_text}
                              {qa.is_required && <span className="ml-1.5 px-1 py-0.5 bg-slate-100 text-slate-400 text-[9px] font-black uppercase rounded">{t.knockoutRequired}</span>}
                            </p>
                          </div>
                          <div className="flex-shrink-0">{evalBadge(evalResult)}</div>
                        </div>
                        <div className="flex items-center gap-4 flex-wrap">
                          <div>
                            <span className="text-[10px] font-semibold text-slate-400 uppercase mr-1">{t.knockoutAnswer}:</span>
                            {displayVal != null && displayVal !== '' ? (
                              <span className="text-sm font-semibold text-slate-800 capitalize">{displayVal}</span>
                            ) : (
                              <span className="text-sm text-slate-400 italic">{t.knockoutNotAnswered}</span>
                            )}
                          </div>
                          {criteriaLabel && (
                            <div>
                              <span className="text-[10px] font-semibold text-slate-400 uppercase mr-1">{t.knockoutPassingCriteria}:</span>
                              <span className="text-xs text-slate-600">{criteriaLabel}</span>
                            </div>
                          )}
                          {displayVal != null && displayVal !== '' && qa.answer_source && (
                            <span className="flex items-center gap-1 text-[10px] text-slate-400">
                              {t.knockoutSource}: {sourceLabel(qa.answer_source)}
                              {qa.answer_method && methodBadge(qa.answer_method)}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </div>
        )}

        {/* Overview Tab */}
        {activeTab === 'overview' && (
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

          {/* Assignment section */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="block text-xs font-medium text-slate-500 uppercase">{t.assignedTo}</label>
              <div className="relative">
                <button
                  onClick={() => setAssignDropdownOpen(v => !v)}
                  className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                >
                  {candidate.assigned_user_name ? 'Reassign' : t.assignTo}
                </button>
                {assignDropdownOpen && (
                  <div className="absolute right-0 top-6 z-50 bg-white border border-slate-200 rounded-lg shadow-lg py-1 min-w-[180px]">
                    {/* Unassign option */}
                    <button
                      onClick={async () => {
                        await onAssign(candidate.application_id, null);
                        setAssignDropdownOpen(false);
                      }}
                      className="w-full text-left px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 transition-colors"
                    >
                      {t.unassignOption}
                    </button>
                    {/* Self-assign shortcut */}
                    {currentUserId && (
                      <button
                        onClick={async () => {
                          await onAssign(candidate.application_id, currentUserId);
                          setAssignDropdownOpen(false);
                        }}
                        className="w-full text-left px-3 py-2 text-xs text-indigo-600 font-medium hover:bg-indigo-50 transition-colors"
                      >
                        {t.assignSelf}
                      </button>
                    )}
                    {/* Admin: list all assignable users */}
                    {isAdmin && assignableUsers.map(u => (
                      <button
                        key={u.user_id}
                        onClick={async () => {
                          await onAssign(candidate.application_id, u.user_id);
                          setAssignDropdownOpen(false);
                        }}
                        className="w-full text-left px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 transition-colors"
                      >
                        {u.full_name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {candidate.assigned_user_name ? (
              <div className="flex items-center gap-2">
                <div className="w-7 h-7 rounded-full bg-emerald-100 text-emerald-700 text-[10px] font-bold flex items-center justify-center">
                  {candidate.assigned_user_name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-800">{candidate.assigned_user_name}</p>
                  {candidate.assigned_at && (
                    <p className="text-xs text-slate-400">{formatDate(candidate.assigned_at)}</p>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-400 italic">No recruiter assigned.</p>
            )}
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

          {/* Tags Section */}
          <div className="mt-6 pt-6 border-t">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-gray-900 text-sm">Tags</h3>
              <button
                onClick={() => setShowTagCreator(!showTagCreator)}
                className="text-xs text-blue-600 hover:underline"
              >
                {showTagCreator ? 'Cancel' : '+ Add'}
              </button>
            </div>

            {/* Tag Chips */}
            {candidateTags.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-3">
                {candidateTags.map(tag => (
                  <TagChip
                    key={tag.tag_id}
                    tag={tag}
                    onRemove={onRemoveTag}
                  />
                ))}
              </div>
            )}

            {candidateTags.length === 0 && !showTagCreator && (
              <p className="text-xs text-gray-400 mb-3">No tags yet</p>
            )}

            {/* Tag Creator */}
            {showTagCreator && (
              <div className="p-3 bg-gray-50 rounded-lg mb-3">
                <input
                  type="text"
                  placeholder="Tag name…"
                  value={newTagName}
                  onChange={(e) => setNewTagName(e.target.value)}
                  className="w-full px-2 py-1 border rounded text-sm mb-2"
                />

                {/* Existing tags autocomplete */}
                {newTagName && (
                  <div className="mb-2 space-y-1 max-h-24 overflow-y-auto">
                    {allTags
                      .filter(tag =>
                        tag.tag_name.toLowerCase().includes(newTagName.toLowerCase()) &&
                        !candidateTags.some(t => t.tag_id === tag.tag_id)
                      )
                      .map(tag => (
                        <button
                          key={tag.tag_id}
                          onClick={() => onAddTag(tag.tag_id)}
                          className="block w-full text-left px-2 py-1 text-xs rounded hover:bg-gray-100"
                        >
                          {tag.tag_name}
                        </button>
                      ))}
                  </div>
                )}

                <div className="flex gap-2">
                  {(() => {
                    const trimmed = newTagName.trim();
                    const existingTag = allTags.find(t => t.tag_name.toLowerCase() === trimmed.toLowerCase());
                    const isAlreadyAdded = existingTag && candidateTags.some(t => t.tag_id === existingTag.tag_id);

                    return (
                      <button
                        onClick={() => onCreateAndAddTag(newTagName).then(() => { setNewTagName(''); setShowTagCreator(false); })}
                        disabled={!trimmed || isAlreadyAdded}
                        className="flex-1 px-2 py-1 bg-blue-600 text-white text-xs rounded disabled:opacity-50"
                      >
                        {existingTag ? 'Add Tag' : 'Create & Add'}
                      </button>
                    );
                  })()}
                  <button
                    onClick={() => {
                      setShowTagCreator(false);
                      setNewTagName('');
                    }}
                    className="flex-1 px-2 py-1 bg-gray-200 text-gray-700 text-xs rounded"
                  >
                    Done
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Talent Pool Section */}
          <div className="mt-6 pt-6 border-t">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-gray-900 text-sm">Talent Pool</h3>
              <button
                onClick={onToggleTalentPool}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  candidate.is_talent_pool
                    ? 'bg-green-100 text-green-800 hover:bg-green-200'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {candidate.is_talent_pool ? '✓ In Pool' : 'Add to Pool'}
              </button>
            </div>

            {candidate.is_talent_pool && (
              <button
                onClick={() => setShowReuseModal(true)}
                className="mt-3 text-xs text-blue-600 hover:underline font-medium"
              >
                → Add to another job
              </button>
            )}
          </div>

          {/* Reuse Candidate Modal */}
          {showReuseModal && (
            <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg p-6 max-w-md w-full">
                <h3 className="font-bold text-lg mb-4">
                  Add {candidate.candidate_name} to Another Job
                </h3>

                <div className="mb-4">
                  <label className="text-xs font-semibold text-gray-600 mb-2 block">
                    Target Job
                  </label>
                  <select
                    value={reuseTargetJob}
                    onChange={(e) => setReuseTargetJob(e.target.value)}
                    className="w-full px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Select a job…</option>
                    {availableJobs.map(job => (
                      <option key={job.job_id} value={job.job_id}>
                        {job.title}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      setShowReuseModal(false);
                      setReuseTargetJob('');
                    }}
                    className="flex-1 px-4 py-2 rounded text-sm text-slate-700 bg-slate-100 hover:bg-slate-200"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => onReuseCandidate(reuseTargetJob).then(() => { setShowReuseModal(false); setReuseTargetJob(''); })}
                    disabled={!reuseTargetJob}
                    className="flex-1 px-4 py-2 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
                  >
                    Add
                  </button>
                </div>
              </div>
            </div>
          )}

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
                          {event.type === 'communication' && event.communicationData ? (
                            <div className={`px-3 py-2 rounded border bg-purple-50 border-purple-200`}>
                              {/* Communication headers with badges */}
                              <div className="flex items-start justify-between gap-2 mb-1.5">
                                <div className="flex items-center gap-1.5 flex-wrap max-w-[calc(100%-60px)]">
                                  <span className="font-medium text-purple-900 truncate">
                                    {event.communicationData.subject || '(no subject)'}
                                  </span>
                                  {event.communicationData.template_category && (
                                    <span className="px-1 py-0.5 text-[9px] font-bold rounded bg-indigo-200 text-indigo-700 flex-shrink-0">
                                      {event.communicationData.template_category}
                                    </span>
                                  )}
                                  <span className={`px-1 py-0.5 text-[9px] font-bold rounded flex-shrink-0 ${
                                    event.communicationData.status === 'sent' ? 'bg-green-200 text-green-700' :
                                    event.communicationData.status === 'failed' ? 'bg-red-200 text-red-700' :
                                    event.communicationData.status === 'draft' ? 'bg-amber-200 text-amber-700' :
                                    'bg-slate-200 text-slate-700'
                                  }`}>
                                    {event.communicationData.status}
                                  </span>
                                  {event.communicationData.is_automated && (
                                    <span className="px-1 py-0.5 text-[9px] font-bold rounded bg-violet-200 text-violet-700 flex-shrink-0">
                                      Automated
                                    </span>
                                  )}
                                </div>
                                <span className="text-[10px] text-slate-500 flex-shrink-0">
                                  {formatTimelineTimestamp(event.timestamp)}
                                </span>
                              </div>

                              {/* Sender and recipient */}
                              <div className="text-[10px] text-purple-700 mb-1">
                                from {event.actor}
                                {event.communicationData.candidate_email && (
                                  <> to <span className="font-medium">{event.communicationData.candidate_email}</span></>
                                )}
                              </div>

                              {/* Message preview */}
                              {event.communicationData.body && (
                                <div className="text-[11px] text-slate-600 bg-white rounded px-2 py-1.5 mt-1.5 border border-purple-100 whitespace-pre-wrap line-clamp-3">
                                  {event.communicationData.body}
                                </div>
                              )}

                              {/* Error message for failed emails */}
                              {event.communicationData.error_message && (
                                <div className="text-[10px] text-red-700 bg-red-50 rounded px-2 py-1 mt-1.5 border border-red-200">
                                  {event.communicationData.error_message}
                                </div>
                              )}

                              {/* Automation info */}
                              {event.communicationData.is_automated && event.communicationData.event_type && (
                                <div className="text-[10px] text-violet-700 mt-1.5 bg-violet-50 rounded px-2 py-1 border border-violet-100">
                                  Triggered by: {event.communicationData.event_type}
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className={`px-2 py-1.5 rounded border ${event.isAdvancedMove ? 'bg-amber-50 text-amber-800 border-amber-200' : timelineEventColor(event.type)}`}>
                              <div className="flex items-start justify-between gap-1">
                                <span className="font-medium">
                                  {event.action}
                                  {event.isAdvancedMove && (
                                    <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide bg-amber-200 text-amber-800 px-1 py-0.5 rounded">
                                      Exceptional
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
                          )}
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
            {(candidate.preferred_contact_email || detail?.preferred_contact_email) && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Preferred Contact</label>
                <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
                  <p className="text-slate-700 break-all">{candidate.preferred_contact_email || detail?.preferred_contact_email}</p>
                  {(candidate.preferred_contact_source || detail?.preferred_contact_source) && (
                    <span className="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-indigo-100 text-indigo-700">
                      {{ manual_upload: 'Manual Upload', email_forwarding: 'Email Fwd', platform_email: 'Platform Email', public_apply: 'Application', cv_extracted: 'CV Extracted', email_sender: 'Email Sender', manual_override: 'Manual Override' }[candidate.preferred_contact_source || detail?.preferred_contact_source || ''] || (candidate.preferred_contact_source || detail?.preferred_contact_source)}
                    </span>
                  )}
                </div>
              </div>
            )}
            {detail?.candidate_email && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">Application Email</label>
                <p className="text-slate-700 mt-0.5 break-all">{detail.candidate_email}</p>
              </div>
            )}
            {detail?.candidate_email_from_cv && (
              <div>
                <label className="block text-xs font-medium text-slate-500 uppercase">CV Extracted Email</label>
                <p className="text-slate-700 mt-0.5 break-all">{detail.candidate_email_from_cv}</p>
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
        )} {/* end Overview tab */}
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
  jobFilter: string,
  search: string,
  assignedFilter: string,
  page: number,
  tagFilter: string[] = [],
  talentPoolOnly: boolean = false,
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
  if (aiResultFilter)   p.ai_decision = aiResultFilter === 'rejected_low_match' ? 'rejected' : aiResultFilter;
  if (jobFilter)        p.job_id = jobFilter;
  if (campaignFilter)   p.campaign_id = campaignFilter;
  if (clientFilter)     p.client_organization_id = clientFilter;
  if (search.trim())    p.search = search.trim();
  if (assignedFilter)   p.assigned_to = assignedFilter;
  if (tagFilter.length > 0) p.tag_ids = tagFilter.join(',');
  if (talentPoolOnly)   p.talent_pool_only = 'true';

  console.debug('[buildApiParams]', { tagFilter, talentPoolOnly, params: p });
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
  const initialJob = searchParams.get('job_id') || '';
  const initialSearch = searchParams.get('search') || '';
  const initialPage = parseInt(searchParams.get('page') || '1', 10);
  const initialAppId = searchParams.get('app_id') || '';
  const initialTagFilter = searchParams.get('tags')?.split(',').filter(Boolean) || [];
  const initialTalentPoolOnly = searchParams.get('talent_pool') === 'true';

  const [activeView, setActiveView] = useState<QuickView>(initialView);
  const [workflowFilter, setWorkflowFilter] = useState(initialWorkflow);
  const [processingFilter, setProcessingFilter] = useState(initialProcessing);
  const [aiResultFilter, setAiResultFilter] = useState(initialAiResult);
  const [campaignFilter, setCampaignFilter] = useState(initialCampaign);
  const [clientFilter, setClientFilter] = useState(initialClient);
  const [jobFilter, setJobFilter] = useState(initialJob);
  const [search, setSearch] = useState(initialSearch);
  const [assignedFilter, setAssignedFilter] = useState(searchParams.get('assigned_to') || '');
  const [page, setPage] = useState(initialPage);
  const [selectedAppId, setSelectedAppId] = useState(initialAppId);

  // Initialize tagFilter and talentPoolOnly from URL params
  const [tagFilter, setTagFilter] = useState<string[]>(initialTagFilter);
  const [talentPoolOnly, setTalentPoolOnly] = useState(initialTalentPoolOnly);

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

  // Saved views
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveViewName, setSaveViewName] = useState('');
  const [savingView, setSavingView] = useState(false);
  const [makeViewShared, setMakeViewShared] = useState(false);

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkTargetStatus, setBulkTargetStatus] = useState('');
  const [showBulkConfirm, setShowBulkConfirm] = useState(false);
  const [bulkNote, setBulkNote] = useState('');
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [bulkResult, setBulkResult] = useState<any>(null);
  const [bulkResultOpen, setBulkResultOpen] = useState(false);
  // Bulk assign
  const [bulkAssignTarget, setBulkAssignTarget] = useState('');
  const [showBulkAssignConfirm, setShowBulkAssignConfirm] = useState(false);
  const [bulkAssignProcessing, setBulkAssignProcessing] = useState(false);

  // Assignable users (fetched once on mount for assignment dropdowns)
  const [assignableUsers, setAssignableUsers] = useState<AssignableUser[]>([]);

  // Tags
  const [allTags, setAllTags] = useState<CandidateTag[]>([]);
  const [candidateTags, setCandidateTags] = useState<CandidateTag[]>([]);
  const [bulkTagMode, setBulkTagMode] = useState<'add' | 'remove' | null>(null);
  const [bulkTagSelection, setBulkTagSelection] = useState<Set<string>>(new Set());

  // Talent pool
  const [availableJobs, setAvailableJobs] = useState<any[]>([]);

  // Check if user is agency/freelancer
  const isAgency = auth.user?.tenant_type === 'agency' || auth.user?.tenant_type === 'individual_recruiter';
  const isAdmin = auth.user?.role === 'admin' || auth.user?.role === 'super_admin';

  useEffect(() => {
    setPageTitle(t.pageTitle);
    return () => { setPageTitle(null); };
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

  // Fetch saved views once on mount
  useEffect(() => {
    if (!auth.token) return;
    apiService.get(WEBHOOK_CONFIG.CANDIDATE_SAVED_VIEWS_URL, {}, auth.token)
      .then((data: any) => {
        if (data && Array.isArray(data.saved_views)) setSavedViews(data.saved_views);
      })
      .catch(() => {}); // non-critical — silent fail
  }, [auth.token]);

  // Fetch tags once on mount
  useEffect(() => {
    if (!auth.token) return;
    const api = new APIService(auth.token);
    api.listTags()
      .then((data: any) => {
        if (data && Array.isArray(data.tags)) setAllTags(data.tags);
      })
      .catch(() => {}); // non-critical
  }, [auth.token]);

  // Load tags for selected candidate when drawer opens
  useEffect(() => {
    if (!selectedAppId || !auth.token) {
      setCandidateTags([]);
      return;
    }
    loadCandidateTags(selectedAppId);
  }, [selectedAppId, auth.token]);

  // Fetch assignable users once on mount
  useEffect(() => {
    if (!auth.token) return;
    apiService.get(WEBHOOK_CONFIG.ASSIGNABLE_USERS_URL, {}, auth.token)
      .then((data: any) => {
        if (data && Array.isArray(data.users)) setAssignableUsers(data.users);
      })
      .catch(() => {}); // non-critical
  }, [auth.token]);

  // Load available jobs (reuse modal + Job filter dropdown)
  useEffect(() => {
    if (!auth.token) return;
    apiService.get(WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL, {}, auth.token)
      .then((data: any) => {
        if (Array.isArray(data)) setAvailableJobs(data);
        else if (data?.jobs) setAvailableJobs(data.jobs);
      })
      .catch(() => {});
  }, [auth.token]);

  // Job options filtered by currently selected campaign/client
  const jobOptions = useMemo(() => {
    return availableJobs.filter((j: any) =>
      (!clientFilter || j.client_organization_id === clientFilter) &&
      (!campaignFilter || j.campaign_id === campaignFilter)
    );
  }, [availableJobs, clientFilter, campaignFilter]);

  // Reset job filter when it no longer belongs to the selected campaign/client
  useEffect(() => {
    if (jobFilter && !jobOptions.some((j: any) => j.job_id === jobFilter)) {
      setJobFilter('');
    }
  }, [jobOptions]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleApplySavedView = (view: SavedView) => {
    const f = view.filters;
    setActiveView((f.activeView as QuickView) || 'all');
    setWorkflowFilter(f.workflowFilter || '');
    setProcessingFilter(f.processingFilter || '');
    setAiResultFilter(f.aiResultFilter || '');
    setCampaignFilter(f.campaignFilter || '');
    setClientFilter(f.clientFilter || '');
    setJobFilter(f.jobFilter || '');
    setSearch(f.search || '');
    setDebouncedSearch(f.search || '');
    setAssignedFilter(f.assignedFilter || '');
    setTagFilter(f.tagFilter || []);
    setTalentPoolOnly(f.talentPoolOnly || false);
    setPage(1);
    clearSelection();
    addToastRef.current(t.savedViewApplied, 'success');
  };

  const handleSaveView = async () => {
    const name = saveViewName.trim();
    if (!name) return;
    setSavingView(true);
    try {
      const filters: SavedViewFilters = {};
      if (activeView && activeView !== 'all') filters.activeView = activeView;
      if (workflowFilter)   filters.workflowFilter   = workflowFilter;
      if (processingFilter) filters.processingFilter = processingFilter;
      if (aiResultFilter)   filters.aiResultFilter   = aiResultFilter;
      if (campaignFilter)   filters.campaignFilter   = campaignFilter;
      if (clientFilter)     filters.clientFilter     = clientFilter;
      if (jobFilter)        filters.jobFilter        = jobFilter;
      if (debouncedSearch)  filters.search           = debouncedSearch;
      if (assignedFilter)   filters.assignedFilter   = assignedFilter;
      if (tagFilter.length > 0) filters.tagFilter = tagFilter;
      if (talentPoolOnly) filters.talentPoolOnly = talentPoolOnly;

      const data: any = await apiService.post(
        WEBHOOK_CONFIG.CANDIDATE_SAVED_VIEWS_URL,
        { name, filters, visibility_type: makeViewShared ? 'tenant_shared' : 'private' },
        auth.token!,
      );
      if (data?.saved_view) {
        setSavedViews(prev => [...prev, data.saved_view]);
      }
      setSaveViewName('');
      setShowSaveDialog(false);
      setMakeViewShared(false);
      addToastRef.current(makeViewShared ? t.savedViewShared : t.savedViewSaved, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to save view', 'error');
    } finally {
      setSavingView(false);
    }
  };

  const handleDeleteSavedView = async (viewId: string) => {
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.CANDIDATE_SAVED_VIEWS_URL}/${viewId}`,
        auth.token!,
      );
      setSavedViews(prev => prev.filter(v => v.saved_view_id !== viewId));
      addToastRef.current(t.savedViewDeleted, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to delete view', 'error');
    }
  };

  // Bulk selection handlers
  const toggleSelected = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const eligible = candidates.filter(c => c.processing_status === 'ai_scored');
    if (selectedIds.size === eligible.length && eligible.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(eligible.map(c => c.application_id)));
    }
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setBulkTargetStatus('');
    setBulkNote('');
    setBulkAssignTarget('');
  };

  const handleBulkAssign = async () => {
    if (selectedIds.size === 0) return;
    setBulkAssignProcessing(true);
    try {
      await apiService.patch(
        WEBHOOK_CONFIG.BULK_ASSIGNMENT_URL,
        {
          application_ids: Array.from(selectedIds),
          assigned_user_id: bulkAssignTarget || null,
        },
        auth.token!,
      );
      addToastRef.current(t.assignmentUpdated, 'success');
      setShowBulkAssignConfirm(false);
      clearSelection();
      fetchCandidates();
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to update assignment', 'error');
    } finally {
      setBulkAssignProcessing(false);
    }
  };

  const handleAssignCandidate = async (applicationId: string, userId: string | null) => {
    try {
      const data: any = await apiService.patch(
        `${WEBHOOK_CONFIG.APPLICATION_ASSIGNMENT_URL}/${applicationId}/assignment`,
        { assigned_user_id: userId },
        auth.token!,
      );

      // Update candidates list with new assignment
      let updatedCandidates = candidates.map(c =>
        c.application_id === applicationId
          ? { ...c, assigned_user_id: data.assigned_user_id, assigned_user_name: data.assigned_user_name, assigned_at: data.assigned_at }
          : c
      );

      // Apply active filter to remove rows that no longer match
      const currentUserId = auth.user?.user_id || auth.user?.sub;
      if (assignedFilter === 'me') {
        updatedCandidates = updatedCandidates.filter(c => c.assigned_user_id === currentUserId);
      } else if (assignedFilter === 'unassigned') {
        updatedCandidates = updatedCandidates.filter(c => !c.assigned_user_id);
      } else if (assignedFilter) {
        // Specific user filter
        updatedCandidates = updatedCandidates.filter(c => c.assigned_user_id === assignedFilter);
      }

      setCandidates(updatedCandidates);

      // If drawer is open on this candidate and it's no longer visible, close it
      if (selectedAppId === applicationId && !updatedCandidates.find(c => c.application_id === applicationId)) {
        closeCandidate();
      }

      addToastRef.current(t.assignmentUpdated, 'success');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to update assignment', 'error');
    }
  };

  const getNoteRequirement = (status: string): 'required' | 'recommended' | 'none' => {
    if (status === 'rejected' || status === 'withdrawn') return 'required';
    if (status === 'on_hold') return 'recommended';
    return 'none';
  };

  const handleBulkMove = async () => {
    if (!bulkTargetStatus || selectedIds.size === 0) return;

    const noteReq = getNoteRequirement(bulkTargetStatus);
    if (noteReq === 'required' && !bulkNote.trim()) {
      addToastRef.current(`Note required for ${bulkTargetStatus}`, 'error');
      return;
    }

    setBulkProcessing(true);
    try {
      const data: any = await apiService.patch(
        `${WEBHOOK_CONFIG.CANDIDATES_SEARCH_URL}/bulk-workflow-status`,
        {
          workflow_status: bulkTargetStatus,
          note: bulkNote || null,
          advanced_move: false,
          application_ids: Array.from(selectedIds),
        },
        auth.token!,
      );

      if (data?.updated_count >= 0) {
        // Store result and show modal
        setBulkResult({
          ...data,
          targetStatus: bulkTargetStatus,
        });
        setBulkResultOpen(true);

        // Still show lightweight toast
        const msg = t.bulkUpdated
          .replace('{updated}', String(data.updated_count))
          .replace('{skipped}', String(data.skipped_count));
        addToastRef.current(msg, data.skipped_count > 0 ? 'warning' : 'success');
      }

      setShowBulkConfirm(false);
      clearSelection();
      fetchCandidates(); // refresh list
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to update candidates', 'error');
    } finally {
      setBulkProcessing(false);
    }
  };

  // ── Tag operations ─────────────────────────────────────────────────────────

  const handleBulkAddTags = async () => {
    if (bulkTagSelection.size === 0 || selectedIds.size === 0) return;

    let successful = 0, failed = 0;
    const api = new APIService(auth.token!);

    setBulkProcessing(true);
    try {
      for (const appId of Array.from(selectedIds)) {
        try {
          await api.addTagsToApplication(appId, Array.from(bulkTagSelection));
          successful++;
        } catch (error) {
          failed++;
        }
      }

      clearSelection();
      setBulkTagMode(null);
      setBulkTagSelection(new Set());
      fetchCandidates(); // refresh list
      const msg = `${successful} tagged${failed > 0 ? `, ${failed} failed` : ''}`;
      addToastRef.current(msg, failed === 0 ? 'success' : 'warning');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to add tags', 'error');
    } finally {
      setBulkProcessing(false);
    }
  };

  const handleBulkRemoveTags = async () => {
    if (bulkTagSelection.size === 0 || selectedIds.size === 0) return;

    let successful = 0, failed = 0;
    const api = new APIService(auth.token!);

    setBulkProcessing(true);
    try {
      for (const appId of Array.from(selectedIds)) {
        for (const tagId of Array.from(bulkTagSelection)) {
          try {
            await api.removeTagFromApplication(appId, tagId);
            successful++;
          } catch (error) {
            failed++;
          }
        }
      }

      clearSelection();
      setBulkTagMode(null);
      setBulkTagSelection(new Set());
      fetchCandidates(); // refresh list
      const msg = `${successful} tags removed${failed > 0 ? `, ${failed} failed` : ''}`;
      addToastRef.current(msg, failed === 0 ? 'success' : 'warning');
    } catch (err: any) {
      addToastRef.current(err.message || 'Failed to remove tags', 'error');
    } finally {
      setBulkProcessing(false);
    }
  };

  const loadCandidateTags = async (appId: string) => {
    const api = new APIService(auth.token!);
    try {
      const response: any = await api.getApplicationTags(appId);
      if (response.tags && Array.isArray(response.tags)) {
        setCandidateTags(response.tags);
      }
    } catch (error) {
      setCandidateTags([]);
    }
  };

  const handleAddTagToCandidate = async (tagId: string) => {
    if (!selectedCandidate) return;

    const api = new APIService(auth.token!);
    try {
      await api.addTagsToApplication(selectedCandidate.application_id, [tagId]);
      await loadCandidateTags(selectedCandidate.application_id);
      addToastRef.current('Tag added', 'success');
    } catch (error: any) {
      addToastRef.current(error.message || 'Failed to add tag', 'error');
    }
  };

  const handleRemoveTagFromCandidate = async (tagId: string) => {
    if (!selectedCandidate) return;

    const api = new APIService(auth.token!);
    try {
      await api.removeTagFromApplication(selectedCandidate.application_id, tagId);
      await loadCandidateTags(selectedCandidate.application_id);
      addToastRef.current('Tag removed', 'success');
    } catch (error: any) {
      addToastRef.current(error.message || 'Failed to remove tag', 'error');
    }
  };

  const handleCreateAndAddTag = async (name: string) => {
    if (!selectedCandidate || !name.trim()) return;

    const trimmedName = name.trim();
    const api = new APIService(auth.token!);

    // Check if exact match exists (case-insensitive)
    const existingTag = allTags.find(t => t.tag_name.toLowerCase() === trimmedName.toLowerCase());

    if (existingTag) {
      // Tag already exists, just add it (unless already on candidate)
      if (candidateTags.some(t => t.tag_id === existingTag.tag_id)) {
        addToastRef.current('Tag already added to candidate', 'error');
        return;
      }

      try {
        await api.addTagsToApplication(selectedCandidate.application_id, [existingTag.tag_id]);
        await loadCandidateTags(selectedCandidate.application_id);
        addToastRef.current('Tag already exists, added existing tag', 'success');
      } catch (error: any) {
        addToastRef.current(error.message || 'Failed to add tag', 'error');
      }
      return;
    }

    // Tag doesn't exist, try to create it
    try {
      const newTag: any = await api.createTag(trimmedName);
      await api.addTagsToApplication(selectedCandidate.application_id, [newTag.tag_id]);
      setAllTags(prev => [...prev, newTag]);
      await loadCandidateTags(selectedCandidate.application_id);
      addToastRef.current('Tag created and added', 'success');
    } catch (error: any) {
      // If 409 (conflict), the tag was created between our check and the POST
      if (error?.status === 409 || error?.message?.includes('409')) {
        // Refetch tags to find the one that was created
        try {
          const data: any = await api.listTags();
          const freshTags = data?.tags || [];
          setAllTags(freshTags);

          const createdTag = freshTags.find(t => t.tag_name.toLowerCase() === trimmedName.toLowerCase());
          if (createdTag && !candidateTags.some(t => t.tag_id === createdTag.tag_id)) {
            await api.addTagsToApplication(selectedCandidate.application_id, [createdTag.tag_id]);
            await loadCandidateTags(selectedCandidate.application_id);
            addToastRef.current('Tag already exists, added existing tag', 'success');
          } else if (createdTag) {
            addToastRef.current('Tag already added to candidate', 'error');
          } else {
            addToastRef.current('Failed to find or add tag', 'error');
          }
        } catch (refetchError: any) {
          addToastRef.current(refetchError.message || 'Failed to add tag', 'error');
        }
      } else {
        addToastRef.current(error.message || 'Failed to create tag', 'error');
      }
    }
  };

  const handleToggleTalentPool = async () => {
    if (!selectedCandidate) return;

    const api = new APIService(auth.token!);
    const newStatus = !selectedCandidate.is_talent_pool;
    try {
      await api.toggleTalentPool(selectedCandidate.application_id, newStatus);
      setCandidates(prev =>
        prev.map(c =>
          c.application_id === selectedCandidate.application_id
            ? { ...c, is_talent_pool: newStatus }
            : c
        )
      );
      addToastRef.current(
        newStatus ? 'Added to talent pool' : 'Removed from talent pool',
        'success'
      );
    } catch (error: any) {
      addToastRef.current(error.message || 'Failed to update talent pool', 'error');
    }
  };

  const handleReuseCandidate = async (targetJobId: string) => {
    if (!selectedCandidate || !targetJobId) return;

    const api = new APIService(auth.token!);
    try {
      const response: any = await api.reuseCandidate(
        selectedCandidate.application_id,
        targetJobId
      );
      addToastRef.current(
        `${response.candidate_name} added to target job`,
        'success'
      );
    } catch (error: any) {
      addToastRef.current(error.message || 'Failed to reuse candidate', 'error');
    }
  };

  // Sync URL when filters change
  useEffect(() => {
    const p: Record<string, string> = {};
    if (activeView !== 'all')     p.view = activeView;
    if (workflowFilter)           p.workflow_status = workflowFilter;
    if (processingFilter)         p.processing_status = processingFilter;
    if (aiResultFilter)           p.ai_decision = aiResultFilter;
    if (campaignFilter)           p.campaign_id = campaignFilter;
    if (clientFilter)             p.client_organization_id = clientFilter;
    if (jobFilter)                p.job_id = jobFilter;
    if (debouncedSearch)          p.search = debouncedSearch;
    if (assignedFilter)           p.assigned_to = assignedFilter;
    if (page > 1)                 p.page = String(page);
    if (selectedAppId)            p.app_id = selectedAppId;
    if (tagFilter.length > 0)     p.tags = tagFilter.join(',');
    if (talentPoolOnly)           p.talent_pool = 'true';
    setSearchParams(p, { replace: true });
  }, [activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, jobFilter, debouncedSearch, assignedFilter, page, selectedAppId, tagFilter, talentPoolOnly, setSearchParams]);

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
      const params = buildApiParams(activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, jobFilter, debouncedSearch, assignedFilter, page, tagFilter, talentPoolOnly);
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
  }, [auth.token, activeView, workflowFilter, processingFilter, aiResultFilter, campaignFilter, clientFilter, jobFilter, debouncedSearch, assignedFilter, page, tagFilter, talentPoolOnly]); // eslint-disable-line react-hooks/exhaustive-deps

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
    setAssignedFilter('');
    setTagFilter([]);
    setTalentPoolOnly(false);
    setPage(1);
    clearSelection();
  };

  // Clear all manual filters
  const handleClearFilters = () => {
    setWorkflowFilter('');
    setProcessingFilter('');
    setAiResultFilter('');
    setCampaignFilter('');
    setClientFilter('');
    setJobFilter('');
    setSearch('');
    setDebouncedSearch('');
    setAssignedFilter('');
    setTagFilter([]);
    setTalentPoolOnly(false);
    setPage(1);
    clearSelection();
  };

  const hasManualFilters = workflowFilter || processingFilter || aiResultFilter || campaignFilter || clientFilter || jobFilter || debouncedSearch || assignedFilter || tagFilter.length > 0 || talentPoolOnly;

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
        isAdvancedMove ? `Exceptional Move → ${label}` : `Moved to ${label}`,
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

      {/* Saved Views Row */}
      {(savedViews.length > 0 || hasManualFilters || activeView !== 'all') && (
        <div className="flex flex-wrap gap-2 items-center">
          {/* User-saved view chips */}
          {savedViews.map(view => {
            const isShared = view.visibility_type === 'tenant_shared';
            const canDelete = view.is_own || isAdmin;
            return (
              <div
                key={view.saved_view_id}
                className={`group flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                  isShared
                    ? 'bg-emerald-50 border border-emerald-200 text-emerald-700 hover:bg-emerald-100'
                    : 'bg-indigo-50 border border-indigo-200 text-indigo-700 hover:bg-indigo-100'
                }`}
              >
                {isShared && (
                  <span className="text-emerald-500" title={t.sharedView}>
                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M15 8a3 3 0 10-2.977-2.63l-4.94 2.47a3 3 0 100 4.319l4.94 2.47a3 3 0 10.895-1.789l-4.94-2.47a3.027 3.027 0 000-.74l4.94-2.47C13.456 7.68 14.19 8 15 8z"/></svg>
                  </span>
                )}
                <button
                  onClick={() => handleApplySavedView(view)}
                  className="max-w-[140px] truncate"
                  title={`${view.name}${isShared ? ` (${t.sharedView})` : ''}`}
                >
                  {view.name}
                </button>
                {canDelete && (
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      if (window.confirm(t.savedViewDeleteConfirm)) handleDeleteSavedView(view.saved_view_id);
                    }}
                    className={`ml-0.5 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100 shrink-0 ${
                      isShared ? 'text-emerald-400 hover:text-red-500' : 'text-indigo-400 hover:text-red-500'
                    }`}
                    title="Delete"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12"/></svg>
                  </button>
                )}
              </div>
            );
          })}

          {/* Save Current View */}
          {(hasManualFilters || activeView !== 'all') && !showSaveDialog && (
            <button
              onClick={() => setShowSaveDialog(true)}
              className="flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium border border-dashed border-slate-300 text-slate-500 hover:border-indigo-400 hover:text-indigo-600 transition-colors bg-white"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4"/></svg>
              {t.saveView}
            </button>
          )}

          {/* Inline save dialog */}
          {showSaveDialog && (
            <div className="flex items-center gap-2 bg-white border border-indigo-300 rounded-full px-3 py-1 shadow-sm">
              <input
                autoFocus
                type="text"
                value={saveViewName}
                onChange={e => setSaveViewName(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') handleSaveView();
                  if (e.key === 'Escape') { setShowSaveDialog(false); setSaveViewName(''); setMakeViewShared(false); }
                }}
                placeholder={t.saveViewPlaceholder}
                className="text-xs outline-none w-36 text-slate-700 placeholder:text-slate-400 bg-transparent"
                maxLength={100}
              />
              {isAdmin && (
                <label className="flex items-center gap-1 text-xs text-slate-500 cursor-pointer select-none" title={t.makeShared}>
                  <input
                    type="checkbox"
                    checked={makeViewShared}
                    onChange={e => setMakeViewShared(e.target.checked)}
                    className="w-3 h-3 text-emerald-600 rounded"
                  />
                  <span className="text-emerald-600">Shared</span>
                </label>
              )}
              <button
                onClick={handleSaveView}
                disabled={savingView || !saveViewName.trim()}
                className="text-xs font-semibold text-indigo-600 hover:text-indigo-800 disabled:opacity-40 transition-colors"
              >
                {savingView ? '…' : t.saveViewConfirm}
              </button>
              <button
                onClick={() => { setShowSaveDialog(false); setSaveViewName(''); setMakeViewShared(false); }}
                className="text-xs text-slate-400 hover:text-slate-600 transition-colors"
              >
                {t.saveViewCancel}
              </button>
            </div>
          )}
        </div>
      )}

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
          onChange={e => {
            const newProcessing = e.target.value;
            // If changing to pending, in_progress, or stopped_before_ai, reset AI Result filter
            // (AI Result only applies to ai_scored apps)
            if (newProcessing && newProcessing !== 'ai_scored') {
              setAiResultFilter('');
            }
            setProcessingFilter(newProcessing);
            setPage(1);
          }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
        >
          <option value="">{t.filterProcessing}</option>
          <option value="pending">{t.procPending}</option>
          <option value="in_progress">{t.procInProgress}</option>
          <option value="ai_scored">{t.procAiScored}</option>
          <option value="stopped_before_ai">{t.procStoppedBeforeAi}</option>
        </select>

        {/* AI result filter */}
        <select
          value={aiResultFilter}
          onChange={e => { setAiResultFilter(e.target.value); setPage(1); }}
          disabled={processingFilter !== '' && processingFilter !== 'ai_scored'}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:opacity-50 disabled:cursor-not-allowed"
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

        {/* Job filter */}
        <select
          value={jobFilter}
          onChange={e => { setJobFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:opacity-50"
          disabled={availableJobs.length === 0}
        >
          <option value="">{lang === 'ar' ? 'كل الوظائف' : 'All Jobs'}</option>
          {jobOptions.map((j: any) => (
            <option key={j.job_id} value={j.job_id}>
              {j.job_title}{j.job_code ? ` (${j.job_code})` : ''}
            </option>
          ))}
        </select>

        {/* Assigned filter */}
        <select
          value={assignedFilter}
          onChange={e => { setAssignedFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
        >
          <option value="">{t.filterAssigned}</option>
          <option value="me">{t.assignedToMe}</option>
          <option value="unassigned">{t.unassigned}</option>
          {isAdmin && assignableUsers.map(u => (
            <option key={u.user_id} value={u.user_id}>{u.full_name}</option>
          ))}
        </select>

        {/* Tag Filter */}
        <div className="border-l pl-3">
          <label className="text-xs font-semibold text-gray-600 mb-2 block">Tags</label>
          <div className="flex flex-wrap gap-1 mb-2 max-h-24 overflow-y-auto">
            {allTags.map(tag => (
              <button
                key={tag.tag_id}
                onClick={() => {
                  setTagFilter(prev =>
                    prev.includes(tag.tag_id)
                      ? prev.filter(id => id !== tag.tag_id)
                      : [...prev, tag.tag_id]
                  );
                  setPage(1);
                }}
                className={`px-2 py-1 text-xs rounded-full transition-all ${
                  tagFilter.includes(tag.tag_id)
                    ? 'ring-2 ring-offset-1'
                    : 'opacity-60 hover:opacity-80'
                }`}
                style={{ backgroundColor: tag.color || '#3b82f6', color: '#fff' }}
              >
                {tag.tag_name}
              </button>
            ))}
          </div>
          {tagFilter.length > 0 && (
            <button
              onClick={() => {
                setTagFilter([]);
                setPage(1);
              }}
              className="text-xs text-blue-600 hover:underline"
            >
              Clear tags
            </button>
          )}
        </div>

        {/* Talent Pool Filter */}
        <label className="flex items-center gap-2 mt-3 cursor-pointer">
          <input
            type="checkbox"
            checked={talentPoolOnly}
            onChange={(e) => {
              setTalentPoolOnly(e.target.checked);
              setPage(1);
            }}
            className="w-4 h-4 rounded"
          />
          <span className="text-xs font-medium text-gray-700">Talent Pool Only</span>
        </label>

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

      {/* Bulk Action Bar */}
      {selectedIds.size > 0 && (
        <div className="sticky top-0 z-20 bg-indigo-50 border border-indigo-200 rounded-xl px-4 py-3 flex flex-wrap gap-3 items-center shadow-sm">
          <span className="text-sm font-semibold text-indigo-900">
            {t.bulkSelect.replace('{count}', String(selectedIds.size))}
          </span>

          {/* Bulk workflow move */}
          <select
            value={bulkTargetStatus}
            onChange={e => { setBulkTargetStatus(e.target.value); setBulkAssignTarget(''); }}
            className="text-sm border border-indigo-300 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
          >
            <option value="">{t.bulkMove}…</option>
            {(Object.keys(WORKFLOW_STATUS_LABELS_EN) as WorkflowStatus[]).map(s => (
              <option key={s} value={s}>{wfLabels[s]}</option>
            ))}
          </select>

          {bulkTargetStatus && (
            <button
              onClick={() => setShowBulkConfirm(true)}
              disabled={bulkProcessing}
              className="px-4 py-1.5 bg-indigo-600 text-white text-sm font-semibold rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {bulkProcessing ? '…' : t.bulkConfirm}
            </button>
          )}

          {/* Divider */}
          <span className="text-indigo-300 select-none">|</span>

          {/* Bulk assign */}
          <select
            value={bulkAssignTarget}
            onChange={e => { setBulkAssignTarget(e.target.value); setBulkTargetStatus(''); }}
            className="text-sm border border-indigo-300 rounded-lg px-3 py-1.5 text-slate-700 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-300"
          >
            <option value="">{t.bulkAssign}…</option>
            <option value="__unassign__">{t.unassignOption}</option>
            {assignableUsers.map(u => (
              <option key={u.user_id} value={u.user_id}>{u.full_name}</option>
            ))}
          </select>

          {bulkAssignTarget && (
            <button
              onClick={() => setShowBulkAssignConfirm(true)}
              disabled={bulkAssignProcessing}
              className="px-4 py-1.5 bg-emerald-600 text-white text-sm font-semibold rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {bulkAssignProcessing ? '…' : t.bulkAssign}
            </button>
          )}

          {/* Divider */}
          <span className="text-indigo-300 select-none">|</span>

          {/* Bulk Tag Actions */}
          <button
            onClick={() => {
              setBulkTagMode('add');
              setBulkTagSelection(new Set());
            }}
            className="px-4 py-1.5 bg-violet-600 text-white text-sm font-semibold rounded-lg hover:bg-violet-700 transition-colors"
          >
            + Add Tags
          </button>
          <button
            onClick={() => {
              setBulkTagMode('remove');
              setBulkTagSelection(new Set());
            }}
            className="px-4 py-1.5 bg-violet-600 text-white text-sm font-semibold rounded-lg hover:bg-violet-700 transition-colors"
          >
            - Remove Tags
          </button>

          <button
            onClick={clearSelection}
            disabled={bulkProcessing || bulkAssignProcessing}
            className="ml-auto text-sm text-indigo-600 hover:text-indigo-800 font-semibold disabled:opacity-50 transition-colors"
          >
            {t.bulkClear}
          </button>
        </div>
      )}

      {/* Bulk Assign Confirmation Modal */}
      {showBulkAssignConfirm && selectedIds.size > 0 && bulkAssignTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-lg max-w-sm w-full mx-4 overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-emerald-50 to-emerald-100">
              <h2 className="text-lg font-bold text-emerald-900">{t.bulkAssignTitle}</h2>
            </div>
            <div className="px-6 py-4">
              <p className="text-sm text-slate-700">
                {t.bulkAssignMessage.replace('{count}', String(selectedIds.size))}{' '}
                <strong>
                  {bulkAssignTarget === '__unassign__'
                    ? t.unassignOption
                    : (assignableUsers.find(u => u.user_id === bulkAssignTarget)?.full_name || bulkAssignTarget)}
                </strong>
              </p>
            </div>
            <div className="px-6 py-3 bg-slate-50 border-t border-slate-200 flex gap-3 justify-end">
              <button
                onClick={() => setShowBulkAssignConfirm(false)}
                disabled={bulkAssignProcessing}
                className="px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 rounded-lg disabled:opacity-50 transition-colors"
              >
                {t.bulkCancel}
              </button>
              <button
                onClick={() => {
                  const finalTarget = bulkAssignTarget === '__unassign__' ? null : bulkAssignTarget;
                  setBulkAssignTarget(finalTarget || '__unassign__');
                  handleBulkAssign();
                }}
                disabled={bulkAssignProcessing}
                className="px-4 py-2 text-sm font-semibold text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg disabled:opacity-50 transition-colors"
              >
                {bulkAssignProcessing ? '…' : t.bulkAssign}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Confirmation Modal */}
      {showBulkConfirm && selectedIds.size > 0 && bulkTargetStatus && (() => {
        // Detect mixed workflow states across selected candidates
        const selectedCandidates = candidates.filter(c => selectedIds.has(c.application_id));
        const statusCounts = selectedCandidates.reduce<Record<string, number>>((acc, c) => {
          acc[c.workflow_status] = (acc[c.workflow_status] || 0) + 1;
          return acc;
        }, {});
        const isMixed = Object.keys(statusCounts).length > 1;

        return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-lg max-w-sm w-full mx-4 overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-indigo-50 to-indigo-100">
              <h2 className="text-lg font-bold text-indigo-900">{t.bulkConfirmTitle}</h2>
            </div>
            <div className="px-6 py-4 space-y-4">
              <p className="text-sm text-slate-700">
                {t.bulkConfirmMessage.replace('{count}', String(selectedIds.size))}{' '}
                <strong>{wfLabels[bulkTargetStatus as WorkflowStatus]}</strong>
              </p>

              {/* Mixed workflow state warning */}
              {isMixed && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 space-y-2">
                  <div className="flex items-start gap-2">
                    <svg className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <p className="text-xs text-amber-800 font-medium">{t.bulkMixedStateWarning}</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(statusCounts).map(([st, count]) => (
                      <span key={st} className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">
                        {wfLabels[st as WorkflowStatus] || st}: {count}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {(() => {
                const noteReq = getNoteRequirement(bulkTargetStatus);
                if (noteReq === 'none') return null;
                return (
                  <div className="space-y-2">
                    <label className={`text-xs font-semibold ${noteReq === 'required' ? 'text-red-600' : 'text-amber-600'}`}>
                      {noteReq === 'required'
                        ? t.bulkNoteRequired.replace('{status}', bulkTargetStatus)
                        : t.bulkNoteRecommended.replace('{status}', bulkTargetStatus)}
                    </label>
                    <textarea
                      value={bulkNote}
                      onChange={e => setBulkNote(e.target.value)}
                      placeholder={t.bulkNotePlaceholder}
                      rows={3}
                      className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none"
                    />
                  </div>
                );
              })()}
            </div>
            <div className="px-6 py-3 bg-slate-50 border-t border-slate-200 flex gap-3 justify-end">
              <button
                onClick={() => setShowBulkConfirm(false)}
                disabled={bulkProcessing}
                className="px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 rounded-lg disabled:opacity-50 transition-colors"
              >
                {t.bulkCancel}
              </button>
              <button
                onClick={handleBulkMove}
                disabled={bulkProcessing || (getNoteRequirement(bulkTargetStatus) === 'required' && !bulkNote.trim())}
                className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50 transition-colors"
              >
                {bulkProcessing ? '…' : t.bulkConfirm}
              </button>
            </div>
          </div>
        </div>
        );
      })()}

      {/* Bulk Result Modal */}
      {bulkResultOpen && bulkResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
          <div className="bg-white rounded-xl shadow-lg max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto">
            {/* Header */}
            <div className="sticky top-0 px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-slate-50 to-slate-100 flex items-center justify-between">
              <h2 className="text-lg font-bold text-slate-900">Bulk Action Results</h2>
              <button
                onClick={() => setBulkResultOpen(false)}
                className="text-slate-400 hover:text-slate-600 transition-colors"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Body */}
            <div className="px-6 py-4 space-y-6">
              {/* Summary */}
              <div className="grid grid-cols-2 gap-4">
                <div className="rounded-lg bg-green-50 border border-green-200 p-4">
                  <p className="text-xs font-semibold text-green-700 uppercase mb-1">Updated Successfully</p>
                  <p className="text-2xl font-bold text-green-700">{bulkResult.updated_count}</p>
                </div>
                {bulkResult.skipped_count > 0 && (
                  <div className="rounded-lg bg-amber-50 border border-amber-200 p-4">
                    <p className="text-xs font-semibold text-amber-700 uppercase mb-1">Skipped</p>
                    <p className="text-2xl font-bold text-amber-700">{bulkResult.skipped_count}</p>
                  </div>
                )}
              </div>

              {/* Updated candidates section */}
              {bulkResult.updated_count > 0 && (
                <div>
                  <h3 className="text-sm font-bold text-slate-900 mb-3">Candidates Updated to {wfLabels[bulkResult.targetStatus as WorkflowStatus]}</h3>
                  <div className="space-y-2 max-h-[200px] overflow-y-auto bg-slate-50 rounded-lg p-3">
                    {(bulkResult.updated_candidates || []).map((c: any) => (
                      <div key={c.application_id} className="text-sm text-slate-700 flex items-start gap-2">
                        <span className="text-green-600 font-bold mt-0.5">✓</span>
                        <div className="flex-1">
                          <p className="font-medium">{c.candidate_name}</p>
                          <p className="text-xs text-slate-500">{c.application_id}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Skipped candidates section */}
              {bulkResult.skipped_count > 0 && (
                <div>
                  <h3 className="text-sm font-bold text-slate-900 mb-3">Candidates Skipped</h3>
                  <div className="space-y-2 max-h-[300px] overflow-y-auto bg-amber-50 rounded-lg p-3 border border-amber-200">
                    {(bulkResult.skipped_candidates || []).map((c: any) => (
                      <div
                        key={c.application_id}
                        onClick={() => {
                          const candidate = candidates.find(ca => ca.application_id === c.application_id);
                          if (candidate) {
                            openCandidate(candidate);
                            setBulkResultOpen(false);
                          }
                        }}
                        className="text-sm text-slate-700 flex items-start gap-2 p-2 hover:bg-amber-100 rounded cursor-pointer transition-colors"
                      >
                        <span className="text-amber-600 font-bold mt-0.5">—</span>
                        <div className="flex-1">
                          <p className="font-medium">{c.candidate_name}</p>
                          <p className="text-xs text-amber-700">{c.reason}</p>
                          <p className="text-xs text-slate-500 mt-0.5">{c.application_id}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="sticky bottom-0 px-6 py-4 border-t border-slate-200 bg-slate-50 flex justify-end gap-3">
              <button
                onClick={() => setBulkResultOpen(false)}
                className="px-4 py-2 text-sm font-semibold bg-white border border-slate-300 text-slate-700 rounded-lg hover:bg-slate-50 transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk Tag Modal */}
      {bulkTagMode && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md w-full">
            <h3 className="font-bold text-lg mb-4">
              {bulkTagMode === 'add' ? 'Add Tags to' : 'Remove Tags from'} {selectedIds.size} Candidates
            </h3>

            <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
              {allTags.map(tag => (
                <label key={tag.tag_id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={bulkTagSelection.has(tag.tag_id)}
                    onChange={(e) => {
                      const newSelection = new Set(bulkTagSelection);
                      if (e.target.checked) {
                        newSelection.add(tag.tag_id);
                      } else {
                        newSelection.delete(tag.tag_id);
                      }
                      setBulkTagSelection(newSelection);
                    }}
                    className="w-4 h-4"
                  />
                  <span
                    className="px-2 py-1 rounded-full text-xs text-white"
                    style={{ backgroundColor: tag.color || '#3b82f6' }}
                  >
                    {tag.tag_name}
                  </span>
                </label>
              ))}
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => {
                  setBulkTagMode(null);
                  setBulkTagSelection(new Set());
                }}
                className="btn-secondary flex-1 px-4 py-2 rounded text-sm text-slate-700 bg-slate-100 hover:bg-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (bulkTagMode === 'add') {
                    handleBulkAddTags();
                  } else {
                    handleBulkRemoveTags();
                  }
                }}
                disabled={bulkTagSelection.size === 0}
                className="btn-primary flex-1 px-4 py-2 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50"
              >
                {bulkTagMode === 'add' ? 'Add' : 'Remove'}
              </button>
            </div>
          </div>
        </div>
      )}

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
                <th className="px-3 py-3 text-center w-10">
                  {(() => {
                    const eligibleCount = candidates.filter(c => c.processing_status === 'ai_scored').length;
                    return (
                      <input
                        type="checkbox"
                        checked={eligibleCount > 0 && selectedIds.size === eligibleCount}
                        onChange={toggleSelectAll}
                        disabled={eligibleCount === 0}
                        title={eligibleCount === 0 ? 'No eligible candidates on this page' : 'Select all eligible candidates'}
                        className="w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-2 focus:ring-indigo-300 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                      />
                    );
                  })()}
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colCandidate}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colJob}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colAiMatch}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colAiResult}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide">{t.colWorkflow}</th>
                <th className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wide hidden xl:table-cell">{t.colAssigned}</th>
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
                const isBulkEligible = c.processing_status === 'ai_scored';
                return (
                  <tr
                    key={c.application_id}
                    className={`hover:bg-slate-50 transition-colors ${isUpdating ? 'opacity-70' : ''} ${selectedIds.has(c.application_id) ? 'bg-indigo-50' : ''}`}
                  >
                    {/* Checkbox — disabled for system-managed (non-ai_scored) rows */}
                    <td className="px-3 py-3 text-center" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(c.application_id)}
                        onChange={() => isBulkEligible && toggleSelected(c.application_id)}
                        disabled={!isBulkEligible}
                        title={!isBulkEligible ? 'System-managed candidates cannot be selected for bulk workflow actions' : undefined}
                        className="w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-2 focus:ring-indigo-300 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
                      />
                    </td>
                    {/* Candidate name — click navigates to detail */}
                    <td className="px-4 py-3 cursor-pointer" onClick={() => openCandidate(c)}>
                      <div className="font-medium text-slate-900 leading-tight">{c.candidate_name || '—'}</div>
                      {c.client_org_name && (
                        <div className="text-xs text-slate-400 mt-0.5">{c.client_org_name}</div>
                      )}
                      {(c as any).is_talent_pool && (
                        <span className="inline-block mt-1 px-2 py-1 text-xs bg-green-100 text-green-700 rounded-full">
                          Talent Pool
                        </span>
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

                    {/* Assigned to */}
                    <td className="px-4 py-3 hidden xl:table-cell" onClick={e => e.stopPropagation()}>
                      {c.assigned_user_name ? (
                        <div className="flex items-center gap-1.5">
                          <div className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 text-[10px] font-bold flex items-center justify-center flex-shrink-0">
                            {c.assigned_user_name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                          </div>
                          <span className="text-xs text-slate-600 truncate max-w-[80px]">{c.assigned_user_name}</span>
                        </div>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
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
                          advancedMoveEnabled={auth.user?.allow_advanced_workflow_move && c.job_allow_advanced_workflow_move}
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
        currentUserId={auth.user?.user_id || auth.user?.sub}
        advancedMoveEnabled={auth.user?.allow_advanced_workflow_move && selectedCandidate?.job_allow_advanced_workflow_move}
        assignableUsers={assignableUsers}
        isAdmin={isAdmin}
        candidateTags={candidateTags}
        allTags={allTags}
        availableJobs={availableJobs}
        onAddTag={handleAddTagToCandidate}
        onRemoveTag={handleRemoveTagFromCandidate}
        onCreateAndAddTag={handleCreateAndAddTag}
        onToggleTalentPool={handleToggleTalentPool}
        onReuseCandidate={handleReuseCandidate}
        onClose={closeCandidate}
        onWorkflowUpdate={handleWorkflowUpdate}
        onNotesUpdate={handleNotesUpdate}
        onAssign={handleAssignCandidate}
        addToast={addToast}
      />
    </div>
  );
};
