import React from 'react';
import { CategoryKey } from '../../utils/requirementsV2';
import type { ModelConflict, PipelineIssue, RequirementsView } from '../../services/requirementsV2Api';
import { fmt, Strings } from './i18n';
import { InfoIcon, WarnIcon } from './icons';
import { btnSecondary } from './Panels';

// Panels for the server-side extraction checks (injection, split-OR, model conflicts). Everything shown here was DERIVED BY THE BACKEND from
// the stored records and the saved document; this file only presents it and offers corrections that change the recruiter's DRAFT. Nothing
// here detects, recomputes readiness, saves, acknowledges on its own or redistributes weights.

const quote = 'rounded border border-border bg-slate-50 px-2 py-1 text-xs text-textMain';

export const pipelineUnguarded = (view: RequirementsView) => !!view.pipeline && view.pipeline.status !== 'ok';

// ── availability, provenance and (editors only) the raw AI output ─────────────────────────────────────────────────────────────────────
export const PipelineStatusCard: React.FC<{ s: Strings; view: RequirementsView; canEdit: boolean }> = ({ s, view, canEdit }) => {
  const p = view.pipeline;
  if (!p) return null;
  if (p.status === 'unavailable') {
    return (
      <section role="note" aria-labelledby="req-pipe-h" className="rounded-xl border border-amber-300 bg-amber-50 p-4 flex gap-3" data-testid="pipeline-unavailable">
        <InfoIcon className="mt-0.5 shrink-0 text-amber-700" />
        <div><h4 id="req-pipe-h" className="text-sm font-black text-amber-900">{s.pipelineUnavailable}</h4><p className="text-xs text-amber-900 mt-0.5">{s.pipelineUnavailableBody}</p></div>
      </section>
    );
  }
  if (p.status === 'invalid_record') {
    return (
      <section role="alert" aria-labelledby="req-pipe-h" className="rounded-xl border-2 border-red-400 bg-red-50 p-4 flex gap-3" data-testid="pipeline-invalid">
        <WarnIcon className="mt-0.5 shrink-0 text-red-700" />
        <div>
          <h4 id="req-pipe-h" className="text-sm font-black text-red-800">{s.pipelineInvalid}</h4>
          <p className="text-xs text-red-900 mt-0.5">{s.pipelineInvalidBody}</p>
          {p.errors.length > 0 && <ul className="mt-1 list-disc ps-4 text-[11px] text-red-900" dir="ltr">{p.errors.map((e, n) => <li key={n}>{e}</li>)}</ul>}
        </div>
      </section>
    );
  }
  const cv = p.component_versions || {};
  const prompt = p.provenance?.extraction_prompt || cv.extraction_prompt || null;
  const model = p.provenance?.model || null;
  const raw = p.raw_response;
  const showRaw = canEdit && raw && (typeof raw.text === 'string' || p.raw_ai_output !== undefined);
  return (
    <section aria-labelledby="req-pipe-h" className="rounded-xl border border-border bg-white p-4" data-testid="pipeline-ok">
      <h4 id="req-pipe-h" className="text-sm font-black text-textMain">{s.pipelineTitle}: <span className="text-success">{s.pipelineActive}</span></h4>
      <details className="mt-2 text-xs" data-testid="provenance">
        <summary className="cursor-pointer font-bold">{s.pipelineProvenance}</summary>
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5" dir="ltr">
          <dt className="text-textMuted">{s.provPrompt}</dt>
          <dd data-testid="prov-prompt">{prompt?.version ? `${prompt.version}${prompt.sha256 ? ` (${prompt.sha256.slice(0, 8)})` : ''}` : s.provUnknown}</dd>
          <dt className="text-textMuted">{s.provModel}</dt><dd data-testid="prov-model">{model || s.provUnknown}</dd>
          <dt className="text-textMuted">{s.provPipeline}</dt><dd>{cv.pipeline || s.provUnknown}</dd>
          <dt className="text-textMuted">{s.provGuards}</dt><dd>{[cv.injection_guard, cv.split_or_guard, cv.warning_adapter].filter(Boolean).join(' · ') || s.provUnknown}</dd>
        </dl>
      </details>
      {showRaw && (
        <details className="mt-2 text-xs" data-testid="raw-output">
          <summary className="cursor-pointer font-bold">{s.rawOutput}</summary>
          <p className="text-textMuted mt-1">{s.rawOutputHint}</p>
          {typeof raw!.text === 'string' && (
            <><p className="mt-1 font-bold">{s.rawResponseText}</p><pre dir="ltr" className="mt-0.5 max-h-64 overflow-auto rounded border border-border bg-slate-50 p-2 text-[11px] whitespace-pre-wrap break-words">{raw!.text}</pre></>
          )}
          {p.raw_ai_output !== undefined && (
            <><p className="mt-2 font-bold">{s.rawParsedOutput}</p><pre dir="ltr" className="mt-0.5 max-h-64 overflow-auto rounded border border-border bg-slate-50 p-2 text-[11px] whitespace-pre-wrap break-words">{JSON.stringify(p.raw_ai_output, null, 2)}</pre></>
          )}
        </details>
      )}
    </section>
  );
};

// ── corrections (draft only) ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
export interface CorrectionActions {
  removeItem(id: string): void;
  replaceItem(id: string): void;
  editCategoryWeight(category: string): void;
  keepOne(issue: PipelineIssue, keepId: string): void;
  editStructure(id: string): void;
  goToItem(id: string): void;
}

interface IssueListProps {
  s: Strings; view: RequirementsView; canEdit: boolean; dirty: boolean; busy: boolean;
  textOf(id: string): string; pending(issue: PipelineIssue): boolean; actions: CorrectionActions;
}

const policyNote = (s: Strings, i: PipelineIssue, requireAck: boolean) => {
  if (i.gate === 'injection' || i.gate === 'split_or') return s.blocksAlways;
  const blocks = requireAck ? i.blocks_when_ack_required : i.blocks_when_ack_not_required;
  return blocks ? s.blocksByPolicy : s.infoByPolicy;
};

const Pending: React.FC<{ s: Strings }> = ({ s }) => <p className="mt-1 text-xs font-bold text-green-900 bg-green-50 border border-green-300 rounded px-2 py-1" data-testid="issue-pending">{s.pendingInDraft}</p>;

/** Injection and split-OR: always blocking, never acknowledgeable, shown prominently. */
export const BlockerPanel: React.FC<IssueListProps> = ({ s, view, canEdit, dirty, busy, textOf, pending, actions }) => {
  const issues = (view.unresolved_issues || []).filter(i => i.gate === 'injection' || i.gate === 'split_or');
  if (issues.length === 0) return null;
  return (
    <section role="alert" aria-labelledby="req-blockers-h" className="rounded-xl border-2 border-red-400 bg-red-50 p-4" data-testid="blockers">
      <div className="flex items-start gap-2">
        <WarnIcon className="mt-0.5 shrink-0 text-red-700" />
        <div className="min-w-0 flex-1">
          <h4 id="req-blockers-h" className="text-sm font-black text-red-800">{s.gate_injection} / {s.gate_split_or}</h4>
          <p className="text-xs text-red-900 mt-0.5">{s.pendingHint}</p>
          {dirty && <p className="mt-1 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="blockers-saved-note">{s.issuesSavedNote}</p>}
          <ul className="mt-3 space-y-3">
            {issues.map(i => {
              const d = i.details || {};
              const pend = pending(i);
              return (
                <li key={i.id} data-issue={i.id} data-gate={i.gate} data-kind={i.kind} className="rounded-lg border border-red-300 bg-white p-3 text-xs">
                  <p className="font-black text-red-800">{i.kind === 'injection_requirement' ? s.injectionRequirementTitle : i.kind === 'injection_weights' ? s.injectionWeightsTitle : s.splitOrTitle}</p>
                  <p className="text-textMuted mt-0.5">{policyNote(s, i, true)}</p>
                  <p className="mt-1" dir="auto">{(s as any)[`reason_${i.kind}`] || i.message}</p>

                  {i.kind === 'injection_requirement' && (
                    <>
                      <p className="mt-2 font-bold">{s.affectedItem}</p>
                      {i.item_ids.map(id => <p key={id} className={quote} dir="auto" data-testid="affected-item">{textOf(id) || d.item_text}</p>)}
                      {d.instruction_text && (<><p className="mt-2 font-bold">{s.instructionText}</p><blockquote className={quote} dir="auto" data-testid="instruction-text">{d.instruction_text}</blockquote></>)}
                      {canEdit && i.item_ids.map(id => (
                        <div key={id} className="mt-2 flex flex-wrap gap-2">
                          <button type="button" className={btnSecondary} disabled={busy} onClick={() => actions.removeItem(id)}>{s.removeItem}</button>
                          <button type="button" className={btnSecondary} disabled={busy} onClick={() => actions.replaceItem(id)}>{s.replaceItem}</button>
                        </div>
                      ))}
                    </>
                  )}

                  {i.kind === 'injection_weights' && (
                    <>
                      <p className="mt-2 font-bold">{s.categories[i.category as CategoryKey] || i.category}</p>
                      <ul className="mt-0.5 list-disc ps-4">
                        {d.contaminated_applied_weight != null && <li>{fmt(s.weightApplied, { n: d.contaminated_applied_weight })}</li>}
                        {d.proposed_weight != null && <li>{fmt(s.weightProposed, { n: d.proposed_weight })}</li>}
                        {d.clause && <li dir="auto">{fmt(s.weightInstruction, { clause: d.clause })}</li>}
                      </ul>
                      {d.instruction_text && (<><p className="mt-2 font-bold">{s.instructionText}</p><blockquote className={quote} dir="auto" data-testid="instruction-text">{d.instruction_text}</blockquote></>)}
                      {canEdit && i.category && (
                        <div className="mt-2"><button type="button" className={btnSecondary} onClick={() => actions.editCategoryWeight(i.category as string)}>{fmt(s.editCategoryWeight, { name: s.categories[i.category as CategoryKey] || i.category })}</button></div>
                      )}
                    </>
                  )}

                  {i.gate === 'split_or' && (
                    <>
                      {d.options && <p className="mt-2"><span className="font-bold">{s.optionsLabel}: </span><span dir="auto" data-testid="split-options">{d.options.join(' / ')}</span></p>}
                      {d.shared_evidence && (<><p className="mt-2 font-bold">{s.sharedSentence}</p><blockquote className={quote} dir="auto">{d.shared_evidence}</blockquote></>)}
                      <ul className="mt-2 space-y-2">
                        {i.item_ids.map(id => (
                          <li key={id} className="rounded border border-border p-2" data-split-item={id}>
                            <button type="button" className="font-bold text-primary underline text-start" dir="auto" onClick={() => actions.goToItem(id)}>{textOf(id) || d.item_texts?.[id] || s.goToItem}</button>
                            {canEdit && (
                              <div className="mt-1 flex flex-wrap gap-2">
                                <button type="button" className={btnSecondary} disabled={busy} onClick={() => actions.keepOne(i, id)} data-testid="keep-one">{s.keepThisOne}</button>
                                <button type="button" className={btnSecondary} disabled={busy} onClick={() => actions.editStructure(id)}>{s.editStructure}</button>
                                <button type="button" className={btnSecondary} disabled={busy} onClick={() => actions.removeItem(id)}>{s.removeItem}</button>
                              </div>
                            )}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {pend && <Pending s={s} />}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
};

/** Every other open issue, grouped by gate. Injection / split-OR are listed in the blocker panel above; classification and conflict actions live in their panels. */
export const IssuesPanel: React.FC<Pick<IssueListProps, 's' | 'view' | 'dirty' | 'textOf' | 'pending'> & { goToItem(id: string): void; goToCategory(c: string): void }> = ({ s, view, dirty, textOf, pending, goToItem, goToCategory }) => {
  const all = view.unresolved_issues;
  if (!all) return null;
  const rest = all.filter(i => i.gate !== 'injection' && i.gate !== 'split_or');
  const requireAck = view.classification_policy.require_acknowledgment;
  const groups: [string, PipelineIssue[]][] = [];
  for (const i of rest) { const g = groups.find(x => x[0] === i.gate); if (g) g[1].push(i); else groups.push([i.gate, [i]]); }
  return (
    <section aria-labelledby="req-issues-h" className="rounded-xl border border-border bg-white p-4" data-testid="issues">
      <h4 id="req-issues-h" className="text-sm font-black text-textMain">{s.issuesTitle}</h4>
      {dirty && <p className="mt-1 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="issues-saved-note">{s.issuesSavedNote}</p>}
      {groups.length === 0 && all.length === 0 && <p className="text-xs text-textMuted mt-1">{s.issuesNone}</p>}
      {groups.map(([gate, list]) => (
        <div key={gate} className="mt-3" data-gate-group={gate}>
          <h5 className="text-xs font-black text-textMain">{(s as any)[`gate_${gate}`] || gate} <span className="font-normal text-textMuted">({list.length})</span></h5>
          <ul className="mt-1 space-y-1 text-xs">
            {list.map(i => (
              <li key={i.id} className="rounded border border-border p-2" data-issue={i.id}>
                <p dir="auto">{(s as any)[`reason_${i.kind}`] || i.message}</p>
                <p className="text-textMuted">{policyNote(s, i, requireAck)}</p>
                {pending(i) && <Pending s={s} />}
                {i.item_ids.map(id => <button key={id} type="button" className="me-2 font-bold text-primary underline" dir="auto" onClick={() => goToItem(id)}>{textOf(id) || s.goToItem}</button>)}
                {i.item_ids.length === 0 && i.category && <button type="button" className="font-bold text-primary underline" onClick={() => goToCategory(i.category as string)}>{s.categories[i.category as CategoryKey] || i.category}</button>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
};

// ── item-specific Required/Preferred conflicts reported by the model, with the job description's own statements ───────────────────
export interface ConflictPanelProps {
  s: Strings; view: RequirementsView; canEdit: boolean; canAct: boolean; busy: boolean; dirty: boolean; changedIds?: Set<string>;
  textOf(id: string): string; fmtDate(d: string): string; who(id: string): string; onAck(id: string): void; goToItem(id: string): void;
}

export const ConflictPanel: React.FC<ConflictPanelProps> = ({ s, view, canEdit, canAct, busy, dirty, changedIds, textOf, fmtDate, who, onAck, goToItem }) => {
  const list = view.model_conflicts;
  if (!list || list.length === 0) return null;
  const required = view.classification_policy.require_acknowledgment;
  const label = (c: ModelConflict) => ({ unresolved: s.stateUnresolved, acknowledged: s.stateAcknowledged, inactive: s.conflictStateInactive, resolved: s.conflictStateResolved } as any)[c.state];
  return (
    <section aria-labelledby="req-conf-h" className="rounded-xl border border-border bg-white p-4" data-testid="conflicts">
      <h4 id="req-conf-h" className="text-sm font-black text-textMain">{s.conflictsTitle}</h4>
      <p className="text-xs text-textMuted mt-0.5" data-testid="conflict-policy">{required ? s.classificationPolicyYes : s.classificationPolicyNo}</p>
      {dirty && <p className="mt-2 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="conflicts-saved-note">{s.savedNoteWarnings}</p>}
      <ul className="mt-3 space-y-2">
        {list.map(c => (
          <li key={c.id} className="rounded-lg border border-border p-3 text-xs" data-conflict-id={c.id} data-state={c.state}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`font-black ${c.state === 'unresolved' ? 'text-warning' : 'text-textMuted'}`}>{label(c)}</span>
              <button type="button" className="font-bold text-primary underline" dir="auto" onClick={() => goToItem(c.item_id)}>{textOf(c.item_id) || s.goToItem}</button>
            </div>
            <p className="mt-1">{s.conflictWhy}</p>
            {c.jd_statements.length > 0 && (
              <ul className="mt-1 space-y-1">
                {c.jd_statements.map((st, n) => (
                  <li key={n}><span className="font-bold">{st.class === 'required' ? s.jdSaysRequired : s.jdSaysPreferred}: </span><span className={quote} dir="auto" data-testid="jd-evidence">{st.text}</span></li>
                ))}
              </ul>
            )}
            {c.model_warnings.map((m, n) => <p key={n} className="mt-1 text-textMuted"><span className="font-bold">{s.modelSaid}: </span><span dir="auto">{m}</span></p>)}
            {changedIds?.has(c.item_id) && <p className="mt-1 text-amber-900 font-bold">{s.savedBadge}: {s.itemEditedSaved}</p>}
            {c.state === 'acknowledged' && c.acknowledgment && <p className="mt-1 text-textMuted">{fmt(s.acknowledged, { name: who(c.acknowledgment.user_id), date: fmtDate(c.acknowledgment.acknowledged_at) })}</p>}
            {c.state === 'unresolved' && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {canEdit && <button type="button" className={btnSecondary} disabled={!canAct || busy} onClick={() => onAck(c.id)} data-testid="ack-conflict">{s.acknowledgeConflict}</button>}
                <span className="text-textMuted">{s.correctInstead}</span>
                {canEdit && !canAct && <span className="text-amber-800">{s.needsSavedState}</span>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
};

// ── informational: model notes and parser notes (ordinary similarity warnings keep their own panel) ───────────────────────────────
export const InformationalPanel: React.FC<{ s: Strings; view: RequirementsView }> = ({ s, view }) => {
  const info = view.informational;
  if (!info || (info.generic_model_notes.length === 0 && info.parser_review.length === 0)) return null;
  return (
    <section aria-labelledby="req-info-h" className="rounded-xl border border-border bg-white p-4" data-testid="informational">
      <h4 id="req-info-h" className="text-sm font-black text-textMain">{s.infoTitle}</h4>
      <p className="text-xs text-textMuted mt-0.5">{s.infoBody}</p>
      {info.generic_model_notes.length > 0 && (<><p className="mt-2 text-xs font-bold">{s.modelNotes}</p><ul className="list-disc ps-4 text-xs" data-testid="model-notes">{info.generic_model_notes.map((n, k) => <li key={k} dir="auto">{n.text}</li>)}</ul></>)}
      {info.parser_review.length > 0 && (<><p className="mt-2 text-xs font-bold">{s.parserNotes}</p><ul className="list-disc ps-4 text-xs">{info.parser_review.map((n, k) => <li key={k} dir="auto">{n.message}</li>)}</ul></>)}
    </section>
  );
};

