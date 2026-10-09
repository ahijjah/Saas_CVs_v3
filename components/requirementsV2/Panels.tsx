import React from 'react';
import { CATEGORIES, CategoryComparison, CategoryKey, DraftItem, ServerDocument } from '../../utils/requirementsV2';
import type { Choice, MergeAnalysis, MergeConflict } from '../../utils/requirementsMerge';
import type { RequirementsView } from '../../services/requirementsV2Api';
import { fmt, Strings } from './i18n';
import { CheckIcon, InfoIcon, WarnIcon } from './icons';

const btn = 'px-3 py-2 text-xs font-black rounded-xl border focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed';
export const btnPrimary = `${btn} bg-primary text-white border-primary hover:bg-primaryDark`;
export const btnSecondary = `${btn} bg-white text-textMain border-border hover:bg-slate-50`;

export const EvaluationUnavailable: React.FC<{ s: Strings }> = ({ s }) => (
  <div role="note" className="rounded-xl border border-amber-300 bg-amber-50 p-4 flex gap-3" data-testid="evaluation-unavailable">
    <InfoIcon className="mt-0.5 shrink-0 text-amber-700" />
    <div><p className="text-sm font-black text-amber-900">{s.evalUnavailableTitle}</p><p className="text-xs text-amber-900 mt-0.5">{s.evalUnavailableBody}</p></div>
  </div>
);

export interface ReadinessProps {
  s: Strings; view: RequirementsView; goToCategory(c: string): void; goToItem(id: string): void;
  dirty?: boolean;                                   // the draft differs from the saved version
}

export const ReadinessCard: React.FC<ReadinessProps> = ({ s, view, goToCategory, goToItem, dirty }) => {
  const r = view.readiness;
  const ready = r.state === 'ready';
  const title = (s as any)[`state_${r.state}`] || r.state;
  const reasonText = (code: string, fallback: string) => (s as any)[`reason_${code}`] || fallback;
  return (
    <section aria-labelledby="req-readiness-h" className={`rounded-xl border p-4 ${ready ? 'border-green-300 bg-green-50' : 'border-border bg-white'}`} data-testid="readiness">
      <div className="flex items-start gap-3">
        {ready ? <CheckIcon className="mt-0.5 text-success shrink-0" /> : <WarnIcon className="mt-0.5 text-warning shrink-0" />}
        <div className="min-w-0">
          <h4 id="req-readiness-h" className="text-sm font-black text-textMain">{dirty ? s.readinessSavedTitle : s.readiness}: <span data-testid="readiness-state">{title}</span></h4>
          {dirty && (
            <p className="mt-1 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="readiness-stale">
              {s.readinessSavedNote}
            </p>
          )}
          {ready && <p className="text-xs text-textMuted mt-0.5">{r.scoring_mode === 'none' ? s.hint_ready_none : s.hint_ready_weighted}</p>}
          {!ready && r.reasons.length > 0 && (
            <>
              <p className="text-[10px] font-black uppercase tracking-widest text-textMuted mt-2">{s.blockers}</p>
              <ul className="mt-1 space-y-1 text-xs text-textMain list-disc ps-4">
                {dedupe(r.reasons).map((x, n) => (
                  <li key={n}>
                    {reasonText(x.code, x.message)}{' '}
                    {x.item_id ? <button type="button" className="underline text-primary font-bold" onClick={() => goToItem(x.item_id as string)}>{s.goToItem}</button>
                      : x.category ? <button type="button" className="underline text-primary font-bold" onClick={() => goToCategory(x.category as string)}>{s.categories[x.category as CategoryKey]}</button> : null}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="text-[11px] text-textMuted mt-2">{s.draftNotReady} {s.saveVsReady}</p>
        </div>
      </div>
    </section>
  );
};

type Reason = RequirementsView['readiness']['reasons'][number];
function dedupe(xs: Reason[]): Reason[] {
  const seen = new Set<string>();
  return xs.filter(x => { const k = `${x.code}|${x.item_id}|${x.category}`; if (seen.has(k)) return false; seen.add(k); return true; });
}

export const IssuesBox: React.FC<{ title: string; hint?: string; items: { text: string; onGo?: () => void }[]; s: Strings; tone: 'amber' | 'red'; testId: string }> = ({ title, hint, items, s, tone, testId }) => (
  items.length === 0 ? null : (
    <div role={tone === 'red' ? 'alert' : 'region'} aria-label={title} data-testid={testId}
         className={`rounded-xl border p-3 ${tone === 'red' ? 'border-red-300 bg-red-50' : 'border-amber-300 bg-amber-50'}`}>
      <p className={`text-xs font-black ${tone === 'red' ? 'text-red-800' : 'text-amber-900'}`}>{title}</p>
      {hint && <p className="text-[11px] text-textMuted mt-0.5">{hint}</p>}
      <ul className="mt-1 text-xs space-y-0.5 list-disc ps-4">
        {items.map((i, n) => <li key={n}>{i.text} {i.onGo && <button type="button" className="underline text-primary font-bold" onClick={i.onGo}>{s.goToItem}</button>}</li>)}
      </ul>
    </div>
  )
);

export interface ClassificationProps {
  dirty?: boolean; changedIds?: Set<string>;         // draft differs from saved / ids of items the draft changed
  s: Strings; view: RequirementsView; canEdit: boolean; canAct: boolean; busy: boolean; textOf(id: string): string;
  fmtDate(d: string): string; who(id: string): string; onAck(id: string): void; goToItem(id: string): void;
}

export const ClassificationPanel: React.FC<ClassificationProps> = ({ s, view, canEdit, canAct, busy, textOf, fmtDate, who, onAck, goToItem, dirty, changedIds }) => {
  const list = view.classification_warnings;
  if (list.length === 0) return null;
  const required = view.classification_policy.require_acknowledgment;
  const stateLabel = (st: string) => ({ unresolved: s.stateUnresolved, acknowledged: s.stateAcknowledged, inactive: s.stateInactive, resolved: s.stateResolved } as any)[st] || st;
  return (
    <section aria-labelledby="req-class-h" className="rounded-xl border border-border bg-white p-4" data-testid="classification">
      <h4 id="req-class-h" className="text-sm font-black text-textMain">{s.classificationTitle}</h4>
      <p className="text-xs text-textMuted mt-0.5" data-testid="classification-policy">{required ? s.classificationPolicyYes : s.classificationPolicyNo}</p>
      {dirty && <p className="mt-2 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="classification-saved-note">{s.savedNoteWarnings}</p>}
      <ul className="mt-3 space-y-2">
        {list.map(w => (
          <li key={w.id} className="rounded-lg border border-border p-3 text-xs" data-state={w.state}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`font-black ${w.state === 'unresolved' ? 'text-warning' : 'text-textMuted'}`}>{stateLabel(w.state)}</span>
              <button type="button" className="font-bold text-primary underline" onClick={() => goToItem(w.item_id)}>{textOf(w.item_id) || s.goToItem}</button>
            </div>
            <p className="mt-1 text-textMain">{s.classificationWhy}</p>
            {w.evidence.cue && <p className="mt-0.5 text-textMuted">{s.cue}: <span dir="auto">“{w.evidence.cue}”</span></p>}
            {changedIds?.has(w.item_id) && <p className="mt-1 text-amber-900 font-bold" data-testid="warning-item-edited">{s.savedBadge}: {s.itemEditedSaved}</p>}
            {w.state === 'acknowledged' && w.acknowledgment && <p className="mt-1 text-textMuted">{fmt(s.acknowledged, { name: who(w.acknowledgment.user_id), date: fmtDate(w.acknowledgment.acknowledged_at) })}{changedIds?.has(w.item_id) ? ` — ${s.ackSavedOnly}` : ''}</p>}
            {w.state === 'unresolved' && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {canEdit && <button type="button" className={btnSecondary} disabled={!canAct || busy} onClick={() => onAck(w.id)}>{s.acknowledge}</button>}
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

export interface SimilarityProps { s: Strings; view: RequirementsView; textOf(id: string): string; goToItem(id: string): void; dirty?: boolean; changedIds?: Set<string> }

export const SimilarityPanel: React.FC<SimilarityProps> = ({ s, view, textOf, goToItem, dirty, changedIds }) => {
  const list = view.similarity_warnings;
  if (list.length === 0) return null;
  return (
    <section aria-labelledby="req-sim-h" className="rounded-xl border border-border bg-white p-4" data-testid="similarity">
      <h4 id="req-sim-h" className="text-sm font-black text-textMain">{s.similarityTitle}</h4>
      <p className="text-xs text-textMuted mt-0.5">{s.similarityBody}</p>
      {dirty && <p className="mt-2 text-xs font-bold text-amber-900 bg-amber-50 border border-amber-300 rounded px-2 py-1" data-testid="similarity-saved-note">{s.savedNoteWarnings}</p>}
      <ul className="mt-3 space-y-2">
        {list.map(w => (
          <li key={w.id} className="rounded-lg border border-border p-3 text-xs" data-kind={w.kind} data-saved-only={w.items.some(i => changedIds?.has(i.item_id)) ? 'true' : undefined}>
            <p className="font-black text-textMain">{w.kind === 'possible_duplicate' ? s.possibleDuplicate : s.similarRequirement}
              {w.items.some(i => changedIds?.has(i.item_id)) && <span className="ms-2 font-bold text-amber-900">({s.similarSaved}: {s.itemEditedSaved})</span>}
              <span className="font-normal text-textMuted"> · {w.same_category ? s.sameCategory : s.acrossCategories}</span></p>
            <ul className="mt-1 space-y-1">
              {w.items.map(it => (
                <li key={it.item_id} className="flex flex-wrap gap-2 items-center">
                  <span className="text-textMuted">{s.categories[it.category as CategoryKey]} · {it.importance === 'required' ? s.reqShort : s.prefShort}</span>
                  <button type="button" className="font-bold text-primary underline text-start" onClick={() => goToItem(it.item_id)} dir="auto">{textOf(it.item_id) || s.goToItem}</button>
                </li>
              ))}
            </ul>
            {w.differences.length > 0 && <p className="mt-1 text-textMuted">{fmt(s.differsIn, { list: w.differences.map(d => (s as any)[`diff_${d}`] || d).join(', ') })}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
};

export interface PreferredOnlyProps {
  dirty?: boolean;
  s: Strings; view: RequirementsView; canEdit: boolean; canAct: boolean; busy: boolean; onConfirm(): void; fmtDate(d: string): string; who(id: string): string;
}

export const PreferredOnlyCard: React.FC<PreferredOnlyProps> = ({ s, view, canEdit, canAct, busy, onConfirm, fmtDate, who, dirty }) => {
  const conf = view.preferred_only_confirmation;
  const needs = view.readiness.state === 'needs_confirmation';
  if (!needs && !conf.confirmed) return null;
  return (
    <section aria-labelledby="req-po-h" className="rounded-xl border border-border bg-white p-4" data-testid="preferred-only">
      <h4 id="req-po-h" className="text-sm font-black text-textMain">{s.preferredOnlyTitle}</h4>
      {conf.confirmed
        ? <p className="text-xs text-textMuted mt-1">{fmt(s.preferredOnlyDone, { name: who(conf.user_id || ''), date: fmtDate(conf.confirmed_at || '') })}{dirty && <span className="block font-bold text-amber-900 mt-1" data-testid="preferred-only-saved-note">{s.preferredOnlySavedOnly}</span>}</p>
        : (
          <>
            <p className="text-xs text-textMuted mt-1">{s.preferredOnlyBody}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {canEdit && <button type="button" className={btnPrimary} disabled={!canAct || busy} onClick={onConfirm}>{s.preferredOnlyConfirm}</button>}
              {canEdit && !canAct && <span className="text-xs text-amber-800">{s.needsSavedState}</span>}
            </div>
          </>
        )}
    </section>
  );
};

export interface ConflictProps {
  s: Strings; latestRevision: number; analysis: MergeAnalysis; choices: Record<string, Choice | undefined>;
  onChoose(key: string, c: Choice): void; onApply(): void; onDiscard(): void;
}

const fmtVal = (s: Strings, c: MergeConflict, v: unknown, deleted?: boolean): string => {
  if (deleted) return s.deletedValue;
  if (c.kind === 'category_weight') return v === null || v === undefined ? s.noneValue : `${v}%`;
  if (c.kind === 'deleted_by_me' || c.kind === 'deleted_in_latest') {
    const it = v as DraftItem | null; return it ? `${it.text} (${it.importance === 'required' ? s.reqShort + (it.weight !== null ? ` ${it.weight}%` : '') : s.prefShort})` : s.deletedValue;
  }
  switch (c.field) {
    case 'importance': return v === 'required' ? s.reqShort : s.prefShort;
    case 'weight': return v === null || v === undefined ? s.noneValue : `${v}%`;
    case 'alternatives': return Array.isArray(v) && v.length ? (v as string[]).join(' / ') : s.noneValue;
    case 'experience': { const e = v as { subject: string | null; min_years: number | null } | null; return e ? [e.subject, e.min_years !== null ? `${e.min_years}+` : null].filter(Boolean).join(' · ') : s.noneValue; }
    default: return String(v ?? '');
  }
};

export const ConflictResolver: React.FC<ConflictProps> = ({ s, latestRevision, analysis, choices, onChoose, onApply, onDiscard }) => {
  const { conflicts, keptFromMine, takenFromLatest } = analysis;
  const unresolved = conflicts.filter(c => !choices[c.key]).length;
  const whatLabel = (w: string) => (s as any)[`what_${w}`] || w;
  const choiceLabels = (c: MergeConflict): [string, string] =>
    c.kind === 'deleted_by_me' ? [s.choiceDeleteIt, s.choiceKeepLatest]
      : c.kind === 'deleted_in_latest' ? [s.choiceKeepMine, s.choiceAcceptDeletion] : [s.useMine, s.useLatest];
  const heading = (c: MergeConflict) => c.kind === 'category_weight' ? `${s.categories[c.category]} · ${s.conflictFieldCw}`
    : c.kind === 'field' ? `${s.categories[c.category]} · ${c.itemLabel} · ${whatLabel(c.field as string)}` : `${s.categories[c.category]} · ${c.itemLabel}`;
  return (
    <section aria-labelledby="req-conflict-h" className="rounded-xl border border-red-300 bg-red-50 p-4" data-testid="conflict">
      <h4 id="req-conflict-h" role="alert" className="text-sm font-black text-red-800">{s.conflictTitle}</h4>
      <p className="text-xs text-red-900 mt-1">{s.conflictBody} ({fmt(s.conflictLatest, { n: latestRevision })})</p>
      <ul className="mt-2 flex flex-wrap gap-2 text-xs font-bold" data-testid="merge-summary">
        <li className="px-2 py-0.5 rounded-full bg-white border border-border">{fmt(s.keptFromYou, { n: keptFromMine.length })}</li>
        <li className="px-2 py-0.5 rounded-full bg-white border border-border">{fmt(s.takenFromLatest, { n: takenFromLatest.length })}</li>
        <li className="px-2 py-0.5 rounded-full bg-white border border-border">{fmt(s.conflictsToResolve, { n: conflicts.length })}</li>
      </ul>
      {(keptFromMine.length > 0 || takenFromLatest.length > 0) && (
        <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
          {[[s.keptFromYou, keptFromMine], [s.takenFromLatest, takenFromLatest]].map(([title, list]: any, n) => list.length > 0 && (
            <details key={n} className="rounded-lg bg-white border border-border p-2">
              <summary className="cursor-pointer font-bold">{fmt(title, { n: list.length })}</summary>
              <ul className="mt-1 list-disc ps-4 space-y-0.5">{list.map((c: any, i: number) => <li key={i} dir="auto">{s.categories[c.category as CategoryKey]}{c.itemLabel ? ` · ${c.itemLabel}` : ''} — {whatLabel(c.what)}</li>)}</ul>
            </details>
          ))}
        </div>
      )}
      {conflicts.length === 0 && <p className="mt-2 text-xs font-bold text-green-800">{s.noClashes}</p>}
      <ul className="mt-3 space-y-3">
        {conflicts.map(c => {
          const [mineLabel, latestLabel] = choiceLabels(c);
          const note = c.kind === 'deleted_by_me' ? s.conflictDeletedByMe : c.kind === 'deleted_in_latest' ? s.conflictDeletedInLatest : null;
          return (
            <li key={c.key} data-conflict={c.key}>
              <fieldset className="rounded-lg bg-white border border-border p-3">
                <legend className="px-1 text-xs font-black text-textMain" dir="auto">{heading(c)}</legend>
                {note && <p className="text-xs text-red-900 mb-2">{note}</p>}
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead><tr className="text-start text-textMuted">
                      <th scope="col" className="text-start p-1 font-bold">{s.colOriginal}</th>
                      <th scope="col" className="text-start p-1 font-bold">{s.colMine}</th>
                      <th scope="col" className="text-start p-1 font-bold">{s.colLatest}</th></tr></thead>
                    <tbody><tr className="align-top">
                      <td className="p-1" dir="auto">{fmtVal(s, c, c.base)}</td>
                      <td className="p-1" dir="auto">{fmtVal(s, c, c.mine, c.mineDeleted)}</td>
                      <td className="p-1" dir="auto">{fmtVal(s, c, c.latest, c.latestDeleted)}</td></tr></tbody>
                  </table>
                </div>
                <div className="mt-2 flex flex-wrap gap-4 text-xs">
                  {([['mine', mineLabel], ['latest', latestLabel]] as [Choice, string][]).map(([v, label]) => (
                    <label key={v} className="inline-flex items-center gap-1.5 cursor-pointer">
                      <input type="radio" name={`conflict-${c.key}`} value={v} checked={choices[c.key] === v} onChange={() => onChoose(c.key, v)}
                             className="focus:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className={btnPrimary} disabled={unresolved > 0} onClick={onApply} data-testid="apply-merge">{s.applyMerge}</button>
        <button type="button" className={`${btnSecondary} text-error`} onClick={onDiscard} data-testid="discard-for-latest">{s.discardForLatest}</button>
        {unresolved > 0 && <span className="text-xs text-red-900" role="status">{s.chooseAll} ({unresolved})</span>}
      </div>
      <p className="text-[11px] text-red-900 mt-2">{s.applyHint}</p>
    </section>
  );
};

export interface ComparisonProps { s: Strings; original: ServerDocument | null; comparison: Record<CategoryKey, CategoryComparison> | null }

export const ComparisonView: React.FC<ComparisonProps> = ({ s, original, comparison }) => {
  if (!original || !comparison) return <p className="text-xs text-textMuted">{s.compareNone}</p>;
  return (
    <section aria-labelledby="req-cmp-h" className="rounded-xl border border-border bg-white p-4" data-testid="comparison">
      <h4 id="req-cmp-h" className="text-sm font-black text-textMain">{s.compareTitle}</h4>
      <div className="mt-3 grid grid-cols-1 lg:grid-cols-2 gap-3">
        {CATEGORIES.map(c => {
          const o = original.categories[c], cmp = comparison[c];
          return (
            <div key={c} className="rounded-lg border border-border p-3">
              <p className="text-xs font-black">{s.categories[c]} <span className="font-normal text-textMuted">· {o.weight}%</span>
                {cmp.weightChanged && <span className="ms-2 text-amber-800 font-bold">{fmt(s.cmpWeight, { n: cmp.originalWeight })}</span>}</p>
              <ul className="mt-2 space-y-1 text-xs">
                {o.items.map(i => {
                  const ch = cmp.current.find(x => x.kind !== 'added' && (x as any).item.id === i.id) as any;
                  const removed = cmp.removed.some(r => r.id === i.id);
                  const tag = removed ? s.cmpRemoved : ch && ch.kind === 'changed' ? s.cmpChanged : s.cmpSame;
                  return (
                    <li key={i.id} className="flex flex-wrap gap-1 items-baseline">
                      <span className={`text-[10px] font-black uppercase px-1 rounded ${removed ? 'bg-red-100 text-red-700' : ch && ch.kind === 'changed' ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}>{tag}</span>
                      <span className="text-textMuted">{i.importance === 'required' ? `${s.reqShort} ${i.weight ?? ''}%` : s.prefShort}</span>
                      <span dir="auto">{i.text}</span>
                      {ch && ch.kind === 'changed' && <span className="text-textMuted">({fmt(s.cmpFields, { list: ch.fields.map((f: string) => (s as any)[`f_${f}`] || f).join(', ') })})</span>}
                    </li>
                  );
                })}
                {cmp.current.filter(x => x.kind === 'added').map((x: any) => (
                  <li key={x.item.key} className="flex flex-wrap gap-1 items-baseline">
                    <span className="text-[10px] font-black uppercase px-1 rounded bg-green-100 text-green-800">{s.cmpAdded}</span>
                    <span dir="auto">{x.item.text || '…'}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
};
