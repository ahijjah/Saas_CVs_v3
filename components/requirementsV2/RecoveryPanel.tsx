import React from 'react';
import type { MergePreview, RecoveryVersion, RecoveryView } from '../../services/requirementsV2Api';
import { fmt, type Strings } from './i18n';
import { btnPrimary, btnSecondary } from './Panels';

// Re-analysis: a new analysis written beside the draft. Shown only when the platform switch is on (the server says so); nothing here
// changes the draft until the recruiter chooses keep, take or merge. The server decides every rule; this card only presents its answer.

export interface MergeState {
  version: RecoveryVersion;
  preview: MergePreview;
  decisions: Record<string, string>;
}

export interface RecoveryCardProps {
  s: Strings;
  recovery: RecoveryView | null | undefined;
  canEdit: boolean;                   // admin / HR manager on this page; the server checks again
  dirty: boolean;                     // unsaved edits: every decision waits until they are saved or discarded
  busy: boolean;
  merge: MergeState | null;
  onRequest: () => void;
  onKeep: (versionId: string) => void;
  onTake: (versionId: string) => void;
  onDiscard: (versionId: string) => void;
  onOpenMerge: (versionId: string) => void;
  onDecide: (key: string, choice: string) => void;
  onApplyMerge: () => void;
  onCloseMerge: () => void;
}

const statusLabel = (s: Strings, status: string): string => (s as unknown as Record<string, string>)[`recoveryStatus_${status}`] ?? status;

const reasonText = (s: Strings, reason: RecoveryView['reason']): string | null =>
  reason === 'bound' ? s.recoveryReasonBound : reason === 'candidate_open' ? s.recoveryReasonCandidateOpen : reason === 'no_analysis' ? s.recoveryReasonNoAnalysis : null;

export const RecoveryCard: React.FC<RecoveryCardProps> = (p) => {
  const { s, recovery, canEdit, dirty, busy, merge } = p;
  if (!recovery || !recovery.enabled) return null;                 // off by default: no control and no hint
  const open = recovery.open_candidate;
  const last = recovery.versions[recovery.versions.length - 1];
  const lastRejected = !open && last && last.status === 'rejected' ? last : null;
  const reason = reasonText(s, recovery.reason);
  const locked = dirty || busy;                                    // a decision needs a clean draft and no request in flight
  const rejectedText = lastRejected?.rejection_code === 'inputs_changed_during_call' ? s.recoveryRejectedInputs : s.recoveryRejectedOther;

  return (
    <section aria-labelledby="req-recovery-h" className="rounded-xl border border-slate-200 bg-white p-4 space-y-3" data-testid="recovery">
      <h4 id="req-recovery-h" className="text-sm font-black text-textMain">{s.recoveryTitle}</h4>
      <p className="text-xs text-textMuted">{s.recoveryIntro}</p>
      {reason && <p className="text-xs text-textMuted" data-testid="recovery-reason">{reason}</p>}
      {!canEdit && <p className="text-xs text-textMuted" data-testid="recovery-viewer">{s.recoveryViewerOnly}</p>}
      {canEdit && dirty && <p className="text-xs text-amber-900" data-testid="recovery-needs-save">{s.recoveryNeedsSave}</p>}
      {lastRejected && <p className="text-xs text-red-900" role="alert" data-testid="recovery-rejected">{rejectedText}</p>}

      {canEdit && !open && recovery.can_request && (
        <button type="button" className={btnPrimary} onClick={p.onRequest} disabled={locked} data-testid="recovery-request">{s.recoveryRequest}</button>
      )}

      {open && open.status === 'requested' && (
        <p className="text-xs text-textMain" role="status" data-testid="recovery-status" data-status="requested">{s.recoveryRunning}</p>
      )}

      {open && open.status === 'completed' && (
        <div className="rounded-lg border border-primary/40 bg-slate-50 p-3 space-y-2" data-testid="recovery-candidate" data-status="completed">
          <p className="text-xs font-bold text-textMain" role="status" data-testid="recovery-status">
            {s.recoveryReady} <span className="text-textMuted font-normal">{fmt(s.recoveryVersion, { n: open.version_no })}</span>
          </p>
          {open.input_sha256 && (
            <p className="text-[11px] text-textMuted font-mono break-all">{s.recoveryFingerprint}: {open.input_sha256}</p>
          )}
          {canEdit && (
            <ul className="space-y-2 text-xs">
              <li className="flex flex-col sm:flex-row sm:items-center gap-2">
                <button type="button" className={btnSecondary} onClick={() => p.onKeep(open.version_id)} disabled={locked} data-testid="recovery-keep">{s.recoveryKeep}</button>
                <span className="text-textMuted">{s.recoveryKeepHint}</span>
              </li>
              <li className="flex flex-col sm:flex-row sm:items-center gap-2">
                <button type="button" className={btnPrimary} onClick={() => p.onTake(open.version_id)} disabled={locked} data-testid="recovery-take">{s.recoveryTake}</button>
                <span className="text-textMuted">{s.recoveryTakeHint}</span>
              </li>
              <li className="flex flex-col sm:flex-row sm:items-center gap-2">
                <button type="button" className={btnSecondary} onClick={() => p.onOpenMerge(open.version_id)} disabled={locked || merge !== null} data-testid="recovery-merge">{s.recoveryMerge}</button>
                <span className="text-textMuted">{s.recoveryMergeHint}</span>
              </li>
              <li className="flex flex-col sm:flex-row sm:items-center gap-2">
                <button type="button" className={btnSecondary} onClick={() => p.onDiscard(open.version_id)} disabled={locked} data-testid="recovery-discard">{s.recoveryDiscard}</button>
              </li>
            </ul>
          )}
          <p className="text-[11px] text-textMuted">{s.recoveryNoClear}</p>
        </div>
      )}

      {merge && canEdit && <MergeForm s={s} merge={merge} busy={busy} dirty={dirty} onDecide={p.onDecide} onApply={p.onApplyMerge} onCancel={p.onCloseMerge} />}

      {recovery.versions.length > 0 && (
        <details className="text-xs" data-testid="recovery-history">
          <summary className="cursor-pointer font-bold text-textMain">{s.recoveryHistory}</summary>
          <ol className="mt-2 space-y-1">
            {recovery.versions.map(v => (
              <li key={v.version_id} data-testid="recovery-version" data-status={v.status} className="flex flex-wrap gap-2 text-textMuted">
                <span className="font-bold text-textMain">{fmt(s.recoveryVersion, { n: v.version_no })}</span>
                <span>{statusLabel(s, v.status)}</span>
                {v.applied_choice && <span>{fmt(s.recoveryAppliedAs, { choice: v.applied_choice })}</span>}
                {v.completed_at && <time dateTime={v.completed_at}>{v.completed_at.slice(0, 16).replace('T', ' ')}</time>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
};

const MergeForm: React.FC<{ s: Strings; merge: MergeState; busy: boolean; dirty: boolean; onDecide: (k: string, c: string) => void; onApply: () => void; onCancel: () => void }> = ({ s, merge, busy, dirty, onDecide, onApply, onCancel }) => {
  const { preview, decisions } = merge;
  const keys = [
    ...preview.conflicts.map(x => x.key), ...preview.draft_only.map(x => x.key), ...preview.candidate_only.map(x => x.key), ...preview.weights.map(x => x.key),
  ];
  const decided = keys.filter(k => decisions[k] !== undefined).length;
  const complete = decided === keys.length;
  const choose = (key: string, choice: string, label: string, name: string) => (
    <label className="inline-flex items-center gap-1.5 mr-4">
      <input type="radio" name={name} value={choice} checked={decisions[key] === choice} onChange={() => onDecide(key, choice)} aria-label={label} />
      <span>{label}</span>
    </label>
  );
  const fieldName = (f: string) => (s as unknown as Record<string, string>)[`mergeField_${f}`] ?? f;

  return (
    <div className="rounded-lg border border-slate-300 bg-white p-3 space-y-3" data-testid="recovery-merge-form">
      <h5 className="text-xs font-black text-textMain">{s.mergeTitle}</h5>
      <p className="text-xs text-textMuted" role="status" data-testid="merge-count">{fmt(s.mergeCount, { done: decided, total: keys.length })}</p>
      <p className="text-xs text-amber-900" data-testid="merge-unverified-note">{s.mergeUnverifiedNote}</p>
      {keys.length === 0 && <p className="text-xs text-textMuted">{s.mergeNothingToDecide}</p>}

      {preview.conflicts.map(x => (
        <fieldset key={x.key} className="space-y-1 text-xs" data-testid="merge-conflict">
          <legend className="font-bold text-textMain">{s.mergeConflictTitle}: {x.text ?? ''}</legend>
          <p className="text-textMuted">{fmt(s.mergeDifferences, { fields: x.fields.map(fieldName).join(', ') })}</p>
          {choose(x.key, 'draft', s.mergeUseDraft, `merge-${x.key}`)}
          {choose(x.key, 'candidate', s.mergeUseCandidate, `merge-${x.key}`)}
        </fieldset>
      ))}

      {preview.draft_only.map(x => (
        <fieldset key={x.key} className="space-y-1 text-xs" data-testid="merge-draft-only">
          <legend className="font-bold text-textMain">{s.mergeDraftOnlyTitle}: {x.text ?? ''}</legend>
          {choose(x.key, 'keep', s.mergeKeep, `merge-${x.key}`)}
          {choose(x.key, 'drop', s.mergeDrop, `merge-${x.key}`)}
        </fieldset>
      ))}

      {preview.candidate_only.map(x => (
        <fieldset key={x.key} className="space-y-1 text-xs" data-testid="merge-candidate-only">
          <legend className="font-bold text-textMain">{s.mergeCandidateOnlyTitle}: {x.text ?? ''}</legend>
          {choose(x.key, 'add', s.mergeAdd, `merge-${x.key}`)}
          {choose(x.key, 'skip', s.mergeSkip, `merge-${x.key}`)}
        </fieldset>
      ))}

      {preview.weights.map(x => (
        <fieldset key={x.key} className="space-y-1 text-xs" data-testid="merge-weight">
          <legend className="font-bold text-textMain">{s.mergeWeightTitle}: {x.category}</legend>
          {choose(x.key, 'draft', fmt(s.mergeKeepWeight, { value: x.draft ?? 0 }), `merge-${x.key}`)}
          {choose(x.key, 'candidate', fmt(s.mergeUseWeight, { value: x.candidate ?? 0 }), `merge-${x.key}`)}
        </fieldset>
      ))}

      <div className="flex flex-wrap gap-2">
        <button type="button" className={btnPrimary} onClick={onApply} disabled={!complete || busy || dirty} data-testid="merge-apply">{s.mergeApply}</button>
        <button type="button" className={btnSecondary} onClick={onCancel} disabled={busy} data-testid="merge-cancel">{s.mergeCancel}</button>
      </div>
    </div>
  );
};

