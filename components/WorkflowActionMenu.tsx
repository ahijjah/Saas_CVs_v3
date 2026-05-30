/**
 * WorkflowActionMenu — shared grouped workflow transition dropdown.
 *
 * Displays allowed transitions for a candidate, grouped into:
 *   Forward      — candidate moves to a later recruitment stage
 *   Back/Reopen  — candidate moves back to an earlier stage
 *   Pause/Close  — on_hold, rejected, withdrawn
 *   Other        — statuses outside the standard progression
 *
 * For Pause/Close transitions an optional inline note input is shown
 * before the recruiter confirms, allowing context to be stored in
 * application_workflow_history.note.
 *
 * Processing gate: only processing_status === 'ai_scored' candidates are
 * recruiter-actionable. All others render a muted "System Managed" badge.
 *
 * TODO (future): tenant/platform setting allow_workflow_stage_jumping
 *   - When enabled, show an "Advanced Move" section that lists all statuses
 *     (not just backend-allowed transitions) in a separate group
 *   - Require role permission can_jump_workflow_stages
 *   - Show confirmation dialog with mandatory reason field
 *   - Write audit/timeline entry with actor, reason, and bypassed steps
 */

import React, { useState, useEffect, useRef } from 'react';
import { WorkflowStatus } from '../types';
import {
  VALID_WORKFLOW_TRANSITIONS,
  WORKFLOW_STATUS_STYLES,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
} from '../constants/workflow';

// ── Grouping helpers ─────────────────────────────────────────────────────────

/** Canonical recruiter pipeline order, used to classify forward vs backward moves. */
const WORKFLOW_PROGRESSION_ORDER: WorkflowStatus[] = [
  'awaiting_review',
  'under_review',
  'shortlisted',
  'interviewing',
  'offer_made',
  'hired',
];

/** Terminal / close-out statuses — always classified as Pause/Close regardless of position. */
const TERMINAL_STATUSES = new Set<WorkflowStatus>(['on_hold', 'rejected', 'withdrawn']);

type TransitionGroup = 'forward' | 'back_reopen' | 'pause_close' | 'other';

function getTransitionGroup(current: WorkflowStatus, target: WorkflowStatus): TransitionGroup {
  if (TERMINAL_STATUSES.has(target)) return 'pause_close';
  const ci = WORKFLOW_PROGRESSION_ORDER.indexOf(current);
  const ti = WORKFLOW_PROGRESSION_ORDER.indexOf(target);
  if (ci === -1 || ti === -1) return 'other';
  if (ti > ci) return 'forward';
  if (ti < ci) return 'back_reopen';
  return 'other';
}

function groupTransitions(
  current: WorkflowStatus,
  transitions: WorkflowStatus[],
): Record<TransitionGroup, WorkflowStatus[]> {
  const groups: Record<TransitionGroup, WorkflowStatus[]> = {
    forward: [], back_reopen: [], pause_close: [], other: [],
  };
  for (const t of transitions) {
    if (t === current) continue; // never show current status as an option
    groups[getTransitionGroup(current, t)].push(t);
  }
  return groups;
}

// ── Component ─────────────────────────────────────────────────────────────────

export interface WorkflowActionMenuProps {
  applicationId: string;
  currentStatus: WorkflowStatus;
  /**
   * Only 'ai_scored' applications are recruiter-actionable.
   * Any other value renders a "System Managed" badge.
   */
  processingStatus: string;
  isUpdating: boolean;
  lang?: 'en' | 'ar';
  /**
   * Called after the recruiter selects a target status (and confirms note if prompted).
   * `note` is provided when the recruiter filled in the optional note field.
   */
  onTransition: (applicationId: string, toStatus: WorkflowStatus, note?: string) => void;
}

export const WorkflowActionMenu: React.FC<WorkflowActionMenuProps> = ({
  applicationId, currentStatus, processingStatus, isUpdating, lang = 'en', onTransition,
}) => {
  const [open, setOpen] = useState(false);
  const [pendingStatus, setPendingStatus] = useState<WorkflowStatus | null>(null);
  const [noteText, setNoteText] = useState('');
  const menuRef = useRef<HTMLDivElement>(null);
  const wfLabels = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
        setPendingStatus(null);
        setNoteText('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // ── Processing gate ──────────────────────────────────────────────────────
  if (processingStatus !== 'ai_scored') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs text-slate-400 bg-slate-50 border border-slate-200">
        System Managed
      </span>
    );
  }

  const transitions = VALID_WORKFLOW_TRANSITIONS[currentStatus] ?? [];
  const groups = groupTransitions(currentStatus, transitions);
  const hasOptions = Object.values(groups).some(g => g.length > 0);

  // Terminal state (e.g. hired) or all transitions filtered — no actions available
  if (!hasOptions) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs text-slate-400 bg-slate-50 border border-slate-200">
        —
      </span>
    );
  }

  const selectStatus = (toStatus: WorkflowStatus) => {
    // Pause/Close statuses prompt for an optional note before confirming
    if (TERMINAL_STATUSES.has(toStatus)) {
      setPendingStatus(toStatus);
      setNoteText('');
    } else {
      setOpen(false);
      onTransition(applicationId, toStatus);
    }
  };

  const confirmPending = () => {
    if (!pendingStatus) return;
    setOpen(false);
    onTransition(applicationId, pendingStatus, noteText.trim() || undefined);
    setPendingStatus(null);
    setNoteText('');
  };

  const cancelPending = () => {
    setPendingStatus(null);
    setNoteText('');
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div ref={menuRef} className="relative" onClick={e => e.stopPropagation()}>
      {/* Trigger button */}
      <button
        disabled={isUpdating}
        onClick={() => { setOpen(v => !v); setPendingStatus(null); setNoteText(''); }}
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-slate-200 text-xs font-medium text-slate-600 bg-white hover:border-indigo-300 hover:text-indigo-600 hover:bg-indigo-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isUpdating ? (
          <span className="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin inline-block" />
        ) : (
          <>
            Move
            <svg className="w-3 h-3" viewBox="0 0 12 12" fill="none">
              <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-xl shadow-lg py-1 min-w-[190px]">

          {/* ── Note confirmation panel for Pause/Close ── */}
          {pendingStatus ? (
            <div className="px-3 py-2 space-y-2">
              <p className="text-xs font-semibold text-slate-700">
                {wfLabels[pendingStatus]}
                <span className="text-slate-400 font-normal ml-1">— add a note (optional)</span>
              </p>
              <textarea
                autoFocus
                value={noteText}
                onChange={e => setNoteText(e.target.value)}
                placeholder="Reason, context, or next step…"
                rows={2}
                className="w-full text-xs border border-slate-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none placeholder-slate-400"
              />
              <div className="flex gap-1.5">
                <button
                  onClick={confirmPending}
                  className={`flex-1 py-1 rounded-lg text-xs font-semibold transition-colors ${WORKFLOW_STATUS_STYLES[pendingStatus] ?? 'bg-slate-100 text-slate-700'} hover:opacity-80`}
                >
                  Confirm
                </button>
                <button
                  onClick={cancelPending}
                  className="flex-1 py-1 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <>
              <GroupSection label="Forward" statuses={groups.forward} wfLabels={wfLabels} onSelect={selectStatus} prev={false} />
              <GroupSection label="Back / Reopen" statuses={groups.back_reopen} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0} />
              <GroupSection label="Pause / Close" statuses={groups.pause_close} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0 || groups.back_reopen.length > 0} />
              <GroupSection label="Other" statuses={groups.other} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0 || groups.back_reopen.length > 0 || groups.pause_close.length > 0} />
            </>
          )}
        </div>
      )}
    </div>
  );
};

// ── GroupSection sub-component ────────────────────────────────────────────────

interface GroupSectionProps {
  label: string;
  statuses: WorkflowStatus[];
  wfLabels: Record<WorkflowStatus, string>;
  onSelect: (status: WorkflowStatus) => void;
  prev: boolean; // whether a previous group was rendered (controls divider)
}

const GroupSection: React.FC<GroupSectionProps> = ({ label, statuses, wfLabels, onSelect, prev }) => {
  if (statuses.length === 0) return null;
  return (
    <>
      {prev && <div className="h-px bg-slate-100 my-1" />}
      <div className="px-3 py-1 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{label}</div>
      {statuses.map(toStatus => {
        const style = WORKFLOW_STATUS_STYLES[toStatus] ?? 'bg-slate-100 text-slate-600';
        return (
          <button
            key={toStatus}
            onClick={() => onSelect(toStatus)}
            className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-slate-50 transition-colors text-left"
          >
            <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${style.split(' ')[0]}`} />
            {wfLabels[toStatus]}
          </button>
        );
      })}
    </>
  );
};
