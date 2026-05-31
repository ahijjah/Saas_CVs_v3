
import { WorkflowStatus } from '../types';

// ── Labels ────────────────────────────────────────────────────────────────────

export const WORKFLOW_STATUS_LABELS_EN: Record<WorkflowStatus, string> = {
  awaiting_review: 'Awaiting Review',
  under_review:    'Under Review',
  shortlisted:     'Shortlisted',
  interviewing:    'Interviewing',
  offer_made:      'Offer Made',
  hired:           'Hired',
  rejected:        'Rejected',
  withdrawn:       'Withdrawn',
  on_hold:         'On Hold',
};

export const WORKFLOW_STATUS_LABELS_AR: Record<WorkflowStatus, string> = {
  awaiting_review: 'في انتظار المراجعة',
  under_review:    'قيد المراجعة',
  shortlisted:     'مختار',
  interviewing:    'مقابلة',
  offer_made:      'عرض مقدم',
  hired:           'تم التعيين',
  rejected:        'مرفوض',
  withdrawn:       'منسحب',
  on_hold:         'متوقف مؤقتاً',
};

export function getWorkflowLabels(lang: 'en' | 'ar'): Record<WorkflowStatus, string> {
  return lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;
}

// ── Styles ────────────────────────────────────────────────────────────────────

export const WORKFLOW_STATUS_STYLES: Record<WorkflowStatus, string> = {
  awaiting_review: 'bg-sky-100 text-sky-700',
  under_review:    'bg-blue-100 text-blue-700',
  shortlisted:     'bg-indigo-100 text-indigo-700',
  interviewing:    'bg-purple-100 text-purple-700',
  offer_made:      'bg-amber-100 text-amber-700',
  hired:           'bg-green-100 text-green-800',
  rejected:        'bg-red-100 text-red-700',
  withdrawn:       'bg-orange-100 text-orange-700',
  on_hold:         'bg-yellow-100 text-yellow-700',
};

export const WORKFLOW_STATUS_STYLES_BORDERED: Record<WorkflowStatus, string> = {
  awaiting_review: 'bg-sky-100 text-sky-700 border-sky-200',
  under_review:    'bg-blue-100 text-blue-700 border-blue-200',
  shortlisted:     'bg-indigo-100 text-indigo-700 border-indigo-200',
  interviewing:    'bg-purple-100 text-purple-700 border-purple-200',
  offer_made:      'bg-amber-100 text-amber-700 border-amber-200',
  hired:           'bg-green-100 text-green-800 border-green-200',
  rejected:        'bg-red-100 text-red-700 border-red-200',
  withdrawn:       'bg-orange-100 text-orange-700 border-orange-200',
  on_hold:         'bg-yellow-100 text-yellow-700 border-yellow-200',
};

// ── Transition state machine ──────────────────────────────────────────────────

export const VALID_WORKFLOW_TRANSITIONS: Record<WorkflowStatus, WorkflowStatus[]> = {
  awaiting_review: ['under_review', 'on_hold', 'rejected', 'withdrawn'],
  under_review:    ['shortlisted', 'on_hold', 'rejected', 'withdrawn'],
  shortlisted:     ['interviewing', 'under_review', 'on_hold', 'rejected', 'withdrawn'],
  interviewing:    ['offer_made', 'shortlisted', 'on_hold', 'rejected', 'withdrawn'],
  offer_made:      ['hired', 'interviewing', 'on_hold', 'rejected', 'withdrawn'],
  hired:           [],
  rejected:        ['awaiting_review', 'under_review'],
  withdrawn:       ['awaiting_review', 'under_review'],
  on_hold:         ['awaiting_review', 'under_review', 'shortlisted', 'interviewing', 'offer_made', 'rejected', 'withdrawn'],
};

// ── Action button labels ──────────────────────────────────────────────────────

// ── Exceptional Move ──────────────────────────────────────────────────────────

/**
 * Roles permitted to perform exceptional (stage-jumping) workflow moves.
 * For exceptional operational corrections only — not routine workflow.
 * Backend independently enforces this — frontend uses it only to conditionally render the UI.
 */
export const ADVANCED_MOVE_ALLOWED_ROLES = new Set<string>(['admin', 'super_admin']);

/**
 * All recruiter-facing workflow statuses in pipeline order, used by the
 * Advanced Move dropdown to populate every reachable target status.
 */
export const ALL_WORKFLOW_STATUSES: WorkflowStatus[] = [
  'awaiting_review',
  'under_review',
  'shortlisted',
  'interviewing',
  'offer_made',
  'hired',
  'on_hold',
  'rejected',
  'withdrawn',
];

// ── Action button labels ──────────────────────────────────────────────────────

export const WORKFLOW_ACTION_LABELS_EN: Record<WorkflowStatus, string> = {
  awaiting_review: 'Awaiting Review',
  under_review:    'Start Review',
  shortlisted:     'Shortlist',
  interviewing:    'Move to Interview',
  offer_made:      'Make Offer',
  hired:           'Mark Hired',
  rejected:        'Reject',
  withdrawn:       'Mark Withdrawn',
  on_hold:         'Put On Hold',
};
