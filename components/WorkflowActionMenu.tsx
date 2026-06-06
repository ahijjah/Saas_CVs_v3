/**
 * WorkflowActionMenu — shared grouped workflow transition dropdown.
 *
 * Dropdowns are rendered via React portal into document.body with fixed
 * coordinates derived from getBoundingClientRect(), so they are never
 * clipped by any parent overflow or z-index constraint.
 *
 * Displays allowed transitions for a candidate, grouped into:
 *   Forward      — candidate moves to a later recruitment stage
 *   Back/Reopen  — candidate moves back to an earlier stage
 *   Pause/Close  — on_hold, rejected, withdrawn
 *   Other        — statuses outside the standard progression
 *
 * Note requirement rules (normal Move):
 *   Required    — rejected, withdrawn (confirm disabled until note entered)
 *   Recommended — on_hold, Back/Reopen moves (textarea shown; can proceed without)
 *   Optional    — normal forward progression (no textarea shown)
 *
 * Exceptional Move (admin/super_admin only):
 *   - Bypasses the VALID_WORKFLOW_TRANSITIONS graph
 *   - For exceptional operational corrections only — not routine workflow
 *   - Shows all statuses except current, grouped identically
 *   - Note/reason is always required
 *   - Visually separated from Standard Move: amber button with divider
 *   - Rendered only when advancedMoveEnabled && userRole is in ADVANCED_MOVE_ALLOWED_ROLES
 *   - Backend independently enforces permission + required note
 *
 * Processing gate: only processing_status === 'ai_scored' candidates are
 * recruiter-actionable. All others render a muted "System Managed" badge.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { WorkflowStatus } from '../types';
import {
  VALID_WORKFLOW_TRANSITIONS,
  WORKFLOW_STATUS_STYLES,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
  ADVANCED_MOVE_ALLOWED_ROLES,
  ALL_WORKFLOW_STATUSES,
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
type NoteRequirement = 'required' | 'recommended' | 'optional';

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
    if (t === current) continue;
    groups[getTransitionGroup(current, t)].push(t);
  }
  return groups;
}

function getNoteRequirement(toStatus: WorkflowStatus, current: WorkflowStatus): NoteRequirement {
  if (toStatus === 'rejected' || toStatus === 'withdrawn') return 'required';
  if (toStatus === 'on_hold') return 'recommended';
  if (getTransitionGroup(current, toStatus) === 'back_reopen') return 'recommended';
  return 'optional';
}

// ── Portal position calculator ────────────────────────────────────────────────

interface MenuPos {
  top: number;
  left: number;
  minWidth: number;
  maxHeight: number;
}

function calcMenuPos(btn: HTMLElement, menuMinWidth: number): MenuPos {
  const rect = btn.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // Align right edge of menu with right edge of button by default
  let left = rect.right - menuMinWidth;
  const top = rect.bottom + 4;

  // Keep inside viewport horizontally
  if (left < 8) left = 8;
  if (left + menuMinWidth > vw - 8) left = vw - 8 - menuMinWidth;

  // Available height below the button (leave 16px breathing room)
  const maxHeight = Math.max(150, vh - top - 16);

  return { top, left, minWidth: menuMinWidth, maxHeight };
}

// ── Component ─────────────────────────────────────────────────────────────────

export interface WorkflowActionMenuProps {
  applicationId: string;
  /** Nullable at runtime: DB column has been nullable since migration 077. Component applies safe fallback. */
  currentStatus: WorkflowStatus | null | undefined;
  candidateName?: string;
  /**
   * Only 'ai_scored' applications are recruiter-actionable.
   * Any other value renders a "System Managed" badge.
   */
  processingStatus: string;
  isUpdating: boolean;
  lang?: 'en' | 'ar';
  /**
   * Role of the current user. Used to conditionally show the Advanced Move button.
   * Backend independently enforces the permission check.
   */
  userRole?: string;
  /**
   * When true and userRole is in ADVANCED_MOVE_ALLOWED_ROLES, the Advanced Move
   * dropdown is rendered. Set false to disable the feature at the tenant/page level.
   */
  advancedMoveEnabled?: boolean;
  /**
   * Called after the recruiter confirms a transition.
   * - note: present when the recruiter filled the optional/required note field.
   * - isAdvancedMove: true when the transition bypassed normal stage progression.
   */
  onTransition: (
    applicationId: string,
    toStatus: WorkflowStatus,
    note?: string,
    isAdvancedMove?: boolean,
  ) => void;
}

export const WorkflowActionMenu: React.FC<WorkflowActionMenuProps> = ({
  applicationId,
  currentStatus,
  candidateName,
  processingStatus,
  isUpdating,
  lang = 'en',
  userRole,
  advancedMoveEnabled,
  onTransition,
}) => {
  const [open, setOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [pendingStatus, setPendingStatus] = useState<WorkflowStatus | null>(null);
  const [pendingIsAdvanced, setPendingIsAdvanced] = useState(false);
  const [noteRequirement, setNoteRequirement] = useState<NoteRequirement>('optional');
  const [noteText, setNoteText] = useState('');
  const [showConfirmationModal, setShowConfirmationModal] = useState(false);
  const [menuPos, setMenuPos] = useState<MenuPos | null>(null);
  const [advancedMenuPos, setAdvancedMenuPos] = useState<MenuPos | null>(null);

  // Refs for the trigger buttons and the portal menu divs
  const wrapperRef = useRef<HTMLDivElement>(null);
  const normalBtnRef = useRef<HTMLButtonElement>(null);
  const advancedBtnRef = useRef<HTMLButtonElement>(null);
  const menuDropRef = useRef<HTMLDivElement>(null);
  const advancedMenuDropRef = useRef<HTMLDivElement>(null);

  const wfLabels = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;

  const canDoAdvancedMove =
    !!advancedMoveEnabled && !!userRole && ADVANCED_MOVE_ALLOWED_ROLES.has(userRole);

  // Close dropdowns when clicking outside — must check both trigger and portal divs
  useEffect(() => {
    if (!open && !advancedOpen) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const inWrapper = wrapperRef.current?.contains(target);
      const inMenu = menuDropRef.current?.contains(target);
      const inAdvancedMenu = advancedMenuDropRef.current?.contains(target);
      if (!inWrapper && !inMenu && !inAdvancedMenu) {
        setOpen(false);
        setAdvancedOpen(false);
        resetPending();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, advancedOpen]);

  // Reposition on scroll/resize while a menu is open
  useEffect(() => {
    if (!open && !advancedOpen) return;
    const reposition = () => {
      if (open && normalBtnRef.current) {
        setMenuPos(calcMenuPos(normalBtnRef.current, 200));
      }
      if (advancedOpen && advancedBtnRef.current) {
        setAdvancedMenuPos(calcMenuPos(advancedBtnRef.current, 240));
      }
    };
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open, advancedOpen]);

  // ── Processing gate ──────────────────────────────────────────────────────
  if (processingStatus !== 'ai_scored') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs text-slate-400 bg-slate-50 border border-slate-200">
        System Managed
      </span>
    );
  }

  // Runtime null guard: DB column is nullable since migration 077.
  // For ai_scored candidates, null/unknown status defaults to 'awaiting_review',
  // matching the backend's own fallback rule in applications.py.
  const safeStatus: WorkflowStatus =
    currentStatus != null && (currentStatus as string) in VALID_WORKFLOW_TRANSITIONS
      ? currentStatus
      : 'awaiting_review';

  const transitions = VALID_WORKFLOW_TRANSITIONS[safeStatus] ?? [];
  const groups = groupTransitions(safeStatus, transitions);
  const hasNormalOptions = Object.values(groups).some(g => g.length > 0);

  if (!hasNormalOptions && !canDoAdvancedMove) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs text-slate-400 bg-slate-50 border border-slate-200">
        No standard moves available
      </span>
    );
  }

  // ── Handlers ─────────────────────────────────────────────────────────────

  const resetPending = () => {
    setPendingStatus(null);
    setPendingIsAdvanced(false);
    setNoteText('');
    setNoteRequirement('optional');
  };

  const openNormal = () => {
    if (open) {
      setOpen(false);
      setMenuPos(null);
      resetPending();
      return;
    }
    if (normalBtnRef.current) {
      setMenuPos(calcMenuPos(normalBtnRef.current, 200));
    }
    setOpen(true);
    setAdvancedOpen(false);
    setAdvancedMenuPos(null);
    resetPending();
  };

  const openAdvanced = () => {
    if (advancedOpen) {
      setAdvancedOpen(false);
      setAdvancedMenuPos(null);
      resetPending();
      return;
    }
    if (advancedBtnRef.current) {
      setAdvancedMenuPos(calcMenuPos(advancedBtnRef.current, 240));
    }
    setAdvancedOpen(true);
    setOpen(false);
    setMenuPos(null);
    resetPending();
  };

  const selectStatus = (toStatus: WorkflowStatus) => {
    const requirement = getNoteRequirement(toStatus, safeStatus);
    if (requirement === 'required' || requirement === 'recommended') {
      setPendingStatus(toStatus);
      setPendingIsAdvanced(false);
      setNoteRequirement(requirement);
      setNoteText('');
    } else {
      setOpen(false);
      setMenuPos(null);
      onTransition(applicationId, toStatus);
    }
  };

  const selectAdvancedStatus = (toStatus: WorkflowStatus) => {
    setPendingStatus(toStatus);
    setPendingIsAdvanced(true);
    setNoteRequirement('required');
    setNoteText('');
  };

  const confirmPending = () => {
    if (!pendingStatus) return;
    if (noteRequirement === 'required' && !noteText.trim()) return;

    if (pendingIsAdvanced) {
      setShowConfirmationModal(true);
      return;
    }

    setOpen(false);
    setMenuPos(null);
    setAdvancedOpen(false);
    setAdvancedMenuPos(null);
    onTransition(applicationId, pendingStatus, noteText.trim() || undefined, false);
    resetPending();
  };

  const confirmAdvancedMove = () => {
    if (!pendingStatus) return;
    setShowConfirmationModal(false);
    setOpen(false);
    setMenuPos(null);
    setAdvancedOpen(false);
    setAdvancedMenuPos(null);
    onTransition(applicationId, pendingStatus, noteText.trim() || undefined, true);
    resetPending();
  };

  const cancelAdvancedMove = () => {
    setShowConfirmationModal(false);
  };

  const cancelPending = () => {
    resetPending();
  };

  // Advanced targets: all statuses except current
  const advancedTargets = ALL_WORKFLOW_STATUSES.filter(s => s !== safeStatus);
  const advancedGroups = groupTransitions(safeStatus, advancedTargets);

  // ── Shared note confirmation panel ────────────────────────────────────────

  const NotePanel = () => (
    <div className="px-3 py-2 space-y-2">
      <p className="text-xs font-semibold text-slate-700">
        {pendingStatus && wfLabels[pendingStatus]}
        <span className="text-slate-400 font-normal ml-1">
          {noteRequirement === 'required' ? '— note required' : '— note optional'}
        </span>
      </p>

      {pendingIsAdvanced && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 font-medium">
          ⚠ Exceptional move — bypasses standard workflow. A reason is required.
        </p>
      )}

      {!pendingIsAdvanced && noteRequirement === 'required' && (
        <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          A note is required for this action.
        </p>
      )}
      {!pendingIsAdvanced && noteRequirement === 'recommended' && (
        <p className="text-xs text-blue-600 bg-blue-50 border border-blue-200 rounded px-2 py-1">
          Adding a note is recommended for this action.
        </p>
      )}

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
          disabled={noteRequirement === 'required' && !noteText.trim()}
          className={`flex-1 py-1 rounded-lg text-xs font-semibold transition-colors ${
            noteRequirement === 'required' && !noteText.trim()
              ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
              : pendingIsAdvanced
                ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
                : pendingStatus
                  ? (WORKFLOW_STATUS_STYLES[pendingStatus] ?? 'bg-slate-100 text-slate-700')
                  : 'bg-slate-100 text-slate-700'
          } disabled:hover:opacity-100`}
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
  );

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div ref={wrapperRef} className="flex items-center gap-1.5" onClick={e => e.stopPropagation()}>

      {/* ── Normal Move button ── */}
      {hasNormalOptions ? (
        <button
          ref={normalBtnRef}
          disabled={isUpdating}
          onClick={openNormal}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-indigo-200 text-xs font-semibold text-indigo-600 bg-indigo-50 hover:bg-indigo-100 hover:border-indigo-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isUpdating ? (
            <span className="w-3 h-3 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin inline-block" />
          ) : (
            <>
              Move to...
              <svg className="w-3 h-3" viewBox="0 0 12 12" fill="none">
                <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </>
          )}
        </button>
      ) : (
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs text-slate-400 bg-slate-50 border border-slate-200">
          No standard moves
        </span>
      )}

      {/* ── Separator between standard and exceptional ── */}
      {canDoAdvancedMove && <span className="w-px h-4 bg-slate-200 flex-shrink-0" />}

      {/* ── Exceptional Move trigger ── */}
      {canDoAdvancedMove && (
        <button
          ref={advancedBtnRef}
          disabled={isUpdating}
          onClick={openAdvanced}
          title="Exceptional Move — use only for exceptional operational corrections. Bypasses standard recruitment workflow."
          className="inline-flex items-center gap-0.5 px-2 py-1 rounded-lg border border-amber-200 text-[10px] font-semibold text-amber-700 bg-amber-50 hover:bg-amber-100 hover:border-amber-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Exceptional Move
          <svg className="w-2.5 h-2.5" viewBox="0 0 12 12" fill="none">
            <path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}

      {/* ── Normal Move dropdown portal ── */}
      {open && menuPos && createPortal(
        <div
          ref={menuDropRef}
          style={{
            position: 'fixed',
            top: menuPos.top,
            left: menuPos.left,
            minWidth: menuPos.minWidth,
            maxHeight: menuPos.maxHeight,
            zIndex: 9999,
          }}
          className="bg-white border border-slate-200 rounded-xl shadow-lg py-1 overflow-y-auto"
        >
          {pendingStatus && !pendingIsAdvanced ? (
            <NotePanel />
          ) : (
            <>
              <div className="px-3 py-2 bg-indigo-50 border-b border-indigo-100 rounded-t-xl">
                <p className="text-[10px] font-bold text-indigo-800 uppercase tracking-wider">Standard Move</p>
                <p className="text-[10px] text-indigo-600 mt-0.5">Allowed transitions from current stage</p>
              </div>
              <GroupSection label="Forward" statuses={groups.forward} wfLabels={wfLabels} onSelect={selectStatus} prev={false} />
              <GroupSection label="Back / Reopen" statuses={groups.back_reopen} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0} />
              <GroupSection label="Pause / Close" statuses={groups.pause_close} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0 || groups.back_reopen.length > 0} />
              <GroupSection label="Other" statuses={groups.other} wfLabels={wfLabels} onSelect={selectStatus} prev={groups.forward.length > 0 || groups.back_reopen.length > 0 || groups.pause_close.length > 0} />
            </>
          )}
        </div>,
        document.body,
      )}

      {/* ── Exceptional Move dropdown portal ── */}
      {advancedOpen && advancedMenuPos && createPortal(
        <div
          ref={advancedMenuDropRef}
          style={{
            position: 'fixed',
            top: advancedMenuPos.top,
            left: advancedMenuPos.left,
            minWidth: advancedMenuPos.minWidth,
            maxHeight: advancedMenuPos.maxHeight,
            zIndex: 9999,
          }}
          className="bg-white border border-amber-200 rounded-xl shadow-lg py-1 overflow-y-auto"
        >
          {!(pendingStatus && pendingIsAdvanced) && (
            <div className="px-3 py-2 bg-amber-50 border-b border-amber-100 rounded-t-xl">
              <p className="text-[10px] font-bold text-amber-800 uppercase tracking-wider">Exceptional Move</p>
              <p className="text-[10px] text-amber-600 mt-0.5">Use only for exceptional corrections · Bypasses standard workflow</p>
            </div>
          )}

          {pendingStatus && pendingIsAdvanced ? (
            <NotePanel />
          ) : (
            <>
              <GroupSection label="Forward" statuses={advancedGroups.forward} wfLabels={wfLabels} onSelect={selectAdvancedStatus} prev={false} />
              <GroupSection label="Back / Reopen" statuses={advancedGroups.back_reopen} wfLabels={wfLabels} onSelect={selectAdvancedStatus} prev={advancedGroups.forward.length > 0} />
              <GroupSection label="Pause / Close" statuses={advancedGroups.pause_close} wfLabels={wfLabels} onSelect={selectAdvancedStatus} prev={advancedGroups.forward.length > 0 || advancedGroups.back_reopen.length > 0} />
              <GroupSection label="Other" statuses={advancedGroups.other} wfLabels={wfLabels} onSelect={selectAdvancedStatus} prev={advancedGroups.forward.length > 0 || advancedGroups.back_reopen.length > 0 || advancedGroups.pause_close.length > 0} />
            </>
          )}
        </div>,
        document.body,
      )}

      {/* ── Exceptional Move Confirmation Modal ── */}
      {showConfirmationModal && pendingStatus && createPortal(
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/30">
          <div className="bg-white rounded-xl shadow-2xl max-w-sm w-full mx-4 overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-amber-50 to-orange-50">
              <h2 className="text-lg font-bold text-slate-900 flex items-center gap-2">
                <svg className="w-5 h-5 text-amber-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
                Exceptional Workflow Move
              </h2>
            </div>

            <div className="px-6 py-4 space-y-4">
              <p className="text-sm text-slate-700">
                This action bypasses the standard recruitment workflow and should only be used for exceptional operational corrections.
              </p>

              {candidateName && (
                <div className="rounded-lg bg-slate-50 p-3 text-sm">
                  <p className="text-slate-600">
                    <span className="font-semibold">Candidate:</span> {candidateName}
                  </p>
                </div>
              )}

              <div className="grid grid-cols-3 gap-2 items-center text-sm">
                <div>
                  <p className="text-xs font-semibold text-slate-600 uppercase mb-1">From</p>
                  <div className={`inline-block px-2.5 py-1 rounded-full text-xs font-semibold ${WORKFLOW_STATUS_STYLES[safeStatus] ?? 'bg-slate-100 text-slate-700'}`}>
                    {wfLabels[safeStatus]}
                  </div>
                </div>
                <div className="flex justify-center">
                  <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-600 uppercase mb-1">To</p>
                  <div className={`inline-block px-2.5 py-1 rounded-full text-xs font-semibold ${WORKFLOW_STATUS_STYLES[pendingStatus] ?? 'bg-slate-100 text-slate-700'}`}>
                    {wfLabels[pendingStatus]}
                  </div>
                </div>
              </div>

              {noteText && (
                <div className="rounded-lg bg-blue-50 p-3 text-sm border border-blue-200">
                  <p className="text-xs font-semibold text-blue-700 mb-1">Reason:</p>
                  <p className="text-blue-800">{noteText}</p>
                </div>
              )}
            </div>

            <div className="px-6 py-4 border-t border-slate-200 flex gap-3">
              <button
                onClick={cancelAdvancedMove}
                className="flex-1 py-2 rounded-lg text-sm font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmAdvancedMove}
                disabled={isUpdating}
                className="flex-1 py-2 rounded-lg text-sm font-semibold bg-amber-600 text-white hover:bg-amber-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {isUpdating ? (
                  <>
                    <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin inline-block" />
                    Confirming...
                  </>
                ) : (
                  <>
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    Confirm Exceptional Move
                  </>
                )}
              </button>
            </div>
          </div>
        </div>,
        document.body,
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
  prev: boolean;
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
