import React, { useCallback, useEffect, useState } from 'react';
import { getItemScoring, ItemScoringApiError, rescoreApplication, resolveItem } from '../services/itemScoringApi';
import {
  CategoryView, isItemLevel, ItemScoringResponse, ItemScoringView, ItemView, itemScoringCopy, outcomeLabel, outcomeTone, rangeText,
  RESOLVABLE, resultTone, scoreText,
} from '../utils/itemScoring';
import { canEditRequirements } from '../utils/permissions';
import { Lang, statusLabel } from '../utils/gates';

const CHIP = {
  green: 'bg-emerald-100 text-emerald-800', amber: 'bg-amber-100 text-amber-800', red: 'bg-red-100 text-red-800', slate: 'bg-slate-100 text-slate-700',
} as const;

interface Props {
  applicationId: string;
  userRole: string | null | undefined;
  lang: Lang;
  /** Called with the server's effective outcome whenever the view is loaded or changed, so the page header can stay in step. */
  onOutcome?: (view: ItemScoringResponse) => void;
  /** Test seam: an already-loaded view replaces the first request. */
  initial?: ItemScoringResponse | null;
}

/** The item-level scoring of one application: per category the weight, score and contribution, every Required item with its evidence,
 *  the Preferred results, and the overall panel. All numbers come from the server; nothing here calculates a score. */
export default function ItemScoring({ applicationId, userRole, lang, onOutcome, initial = null }: Props) {
  const copy = itemScoringCopy(lang);
  const canAct = canEditRequirements(userRole);
  const [data, setData] = useState<ItemScoringResponse | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);
  const [choice, setChoice] = useState<(typeof RESOLVABLE)[number]>('met');
  const [reason, setReason] = useState('');
  const [queued, setQueued] = useState(false);

  const apply = useCallback((next: ItemScoringResponse) => { setData(next); onOutcome?.(next); }, [onOutcome]);

  useEffect(() => {
    if (initial) { onOutcome?.(initial); return; }
    let live = true;
    setData(null);
    getItemScoring(applicationId).then((next) => { if (live) { apply(next); setError(null); } })
      .catch((err) => { if (live) setError(err instanceof Error ? err.message : copy.loadFailed); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  if (error && !data) {
    return <section data-testid="item-scoring-error" role="status" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-700">{copy.loadFailed}</section>;
  }
  if (!data) {
    return <section data-testid="item-scoring-loading" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-500">{copy.loading}</section>;
  }
  if (!isItemLevel(data)) {
    if (data.scoring_method === 'none') return null;
    return (
      <section data-testid="item-scoring-legacy" className="rounded-2xl border border-slate-200 bg-white p-4 space-y-1">
        <h3 className="text-sm font-bold text-slate-800">{copy.legacyTitle}</h3>
        <p className="text-xs text-slate-600">{copy.legacyNote}</p>
        {data.stale && data.requirements_revision_used != null && (
          <p data-testid="item-scoring-stale" className="text-xs font-semibold text-amber-800">{copy.stale} {copy.revisionUsed(data.requirements_revision_used, data.current_revision)}</p>
        )}
      </section>
    );
  }

  const view: ItemScoringView = data;
  const tone = CHIP[outcomeTone(view.effective_decision)];

  const run = async (work: () => Promise<ItemScoringResponse>) => {
    setBusy(true);
    setError(null);
    try { apply(await work()); setResolving(null); setReason(''); }
    catch (err) { setError(err instanceof ItemScoringApiError || err instanceof Error ? err.message : copy.loadFailed); }
    finally { setBusy(false); }
  };

  const rescore = async () => {
    setBusy(true);
    setError(null);
    try { await rescoreApplication(applicationId); setQueued(true); }
    catch (err) { setError(err instanceof Error ? err.message : copy.loadFailed); }
    finally { setBusy(false); }
  };

  const renderRequired = (item: ItemView) => (
    <li key={item.item_id} data-testid={`item-${item.item_id}`} data-result={item.result} className="rounded-lg border border-slate-200 bg-white p-3 space-y-2">
      <div className="flex flex-wrap items-start gap-2">
        <p dir="auto" className="text-sm font-semibold text-slate-900 flex-1 min-w-[10rem]">{item.text}</p>
        <span className="text-xs text-slate-500">{copy.weight}: {item.weight}%</span>
        <span data-testid={`result-${item.item_id}`} className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${CHIP[resultTone(item.result)]}`}>{copy.result[item.result]}</span>
        <span data-testid={`earned-${item.item_id}`} className="text-xs font-semibold text-slate-700">
          {item.result === 'unclear' ? copy.reviewNeeded : `${copy.earned}: ${item.earned} ${copy.pts}`}
        </span>
      </div>
      {item.evidence.length > 0
        ? <ul className="space-y-1">{item.evidence.map((q, i) => <li key={i} dir="auto" className="text-xs text-slate-700 border-s-2 border-slate-300 ps-2">“{q}”</li>)}</ul>
        : <p className="text-xs text-slate-500">{copy.noEvidence}</p>}
      {item.explanation && <p dir="auto" className="text-xs text-slate-600">{item.explanation}</p>}
      {item.resolution && (
        <p data-testid={`resolution-${item.item_id}`} className="text-xs text-blue-800">
          {copy.resolvedBy(item.resolution.by)} · {copy.aiSaid}: {copy.result[item.resolution.previous_result] ?? item.resolution.previous_result}
          {item.resolution.reason ? <> · <span dir="auto">{item.resolution.reason}</span></> : null}
        </p>
      )}
      {item.result === 'unclear' && canAct && resolving !== item.item_id && (
        <button type="button" disabled={busy} data-testid={`resolve-${item.item_id}`} onClick={() => { setResolving(item.item_id); setChoice('met'); setReason(''); }}
          className="px-3 py-1 rounded bg-amber-600 text-white text-xs font-semibold disabled:opacity-50">{copy.resolve}</button>
      )}
      {resolving === item.item_id && (
        <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-2">
          <p className="text-xs font-semibold text-amber-900">{copy.resolveTitle}</p>
          <div role="radiogroup" className="flex flex-wrap gap-3">
            {RESOLVABLE.map((r) => (
              <label key={r} className="text-xs flex items-center gap-1">
                <input type="radio" name={`resolve-${item.item_id}`} checked={choice === r} onChange={() => setChoice(r)} /> {copy.result[r]}
              </label>
            ))}
          </div>
          <textarea dir="auto" aria-label={copy.resolveReason} placeholder={copy.resolveReason} rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
            className="w-full border border-slate-300 rounded px-2 py-1 text-sm" />
          <div className="flex gap-2">
            <button type="button" disabled={busy || !reason.trim()} data-testid={`resolve-save-${item.item_id}`}
              onClick={() => run(() => resolveItem(applicationId, item.item_id, choice, reason.trim()))}
              className="px-3 py-1 rounded bg-slate-900 text-white text-xs font-semibold disabled:opacity-50">{copy.save}</button>
            <button type="button" disabled={busy} onClick={() => setResolving(null)} className="px-3 py-1 rounded border border-slate-300 text-xs">{copy.cancel}</button>
          </div>
        </div>
      )}
    </li>
  );

  const renderCategory = (c: CategoryView) => {
    const open = view.status === 'review_required' && c.score_min !== c.score_max;
    return (
      <div key={c.category} data-testid={`category-${c.category}`} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
        <header className="flex flex-wrap items-center gap-x-6 gap-y-1">
          <h4 className="text-sm font-bold text-slate-900 flex-1">{copy.category[c.category] ?? c.category}</h4>
          <span className="text-xs text-slate-600">{copy.weight}: <b>{c.weight}%</b></span>
          <span data-testid={`score-${c.category}`} className="text-xs text-slate-600">
            {copy.score}: <b>{open ? `${c.score_min} – ${c.score_max}` : c.score}</b>
          </span>
          <span data-testid={`contribution-${c.category}`} className="text-xs text-slate-600">
            {copy.contribution}: <b>{open ? `${c.contribution_min} – ${c.contribution_max}` : c.contribution}</b>
          </span>
        </header>
        <ul className="space-y-2">{c.items.map(renderRequired)}</ul>
      </div>
    );
  };

  return (
    <section data-testid="item-scoring" data-status={view.status} aria-label={copy.title} className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-base font-black text-slate-900">{copy.title}</h3>
        <span data-testid="item-scoring-outcome" className={`px-3 py-1 rounded-full text-xs font-black uppercase tracking-wide ${tone}`}>{outcomeLabel(view.effective_decision, lang)}</span>
      </header>

      <div data-testid="item-scoring-overall" className="rounded-xl border border-slate-200 p-4 grid gap-2 sm:grid-cols-2">
        {view.status === 'scored' && <p className="text-sm">{copy.finalScore}: <b data-testid="final-score">{scoreText(view.final_score)}</b></p>}
        {view.status === 'review_required' && (
          <div className="sm:col-span-2 space-y-1">
            <p className="text-sm">{copy.provisionalRange}: <b data-testid="provisional-range">{rangeText(view.range)}</b> · {copy.unclearCount(view.unclear_count)}</p>
            <p className="text-xs text-amber-800">{copy.provisionalNote}</p>
          </div>
        )}
        {view.status === 'not_scored' && (
          <div className="sm:col-span-2 space-y-1">
            <p className="text-sm font-bold" data-testid="not-scored">{copy.notScored}</p>
            <p className="text-xs text-slate-600">{copy.notScoredNote}</p>
          </div>
        )}
        <p className="text-xs text-slate-600">{copy.scoringDecision}: <b data-testid="stored-decision">{view.status === 'review_required' ? outcomeLabel('review_required', lang) : outcomeLabel(view.decision ?? (view.status === 'not_scored' ? 'not_scored' : null), lang)}</b></p>
        <p className="text-xs text-slate-600">{copy.eligibility}: <b data-testid="eligibility">{statusLabel(view.eligibility_status, lang)}</b></p>
        {view.status === 'scored' && <p className="text-xs text-slate-500">{copy.thresholds(view.qualified_threshold, view.partial_threshold)}</p>}
        <p data-testid="revision-line" className="text-xs text-slate-500 sm:col-span-2">{copy.revisionUsed(view.requirements_revision_used, view.current_revision)}</p>
      </div>

      {view.stale && (
        <div data-testid="item-scoring-stale" role="alert" className="rounded-lg border border-amber-400 bg-amber-50 p-3 text-xs text-amber-900 flex flex-wrap items-center gap-3">
          <span className="flex-1">{copy.stale}</span>
          {canAct && !queued && <button type="button" disabled={busy} data-testid="rescore" onClick={rescore} className="px-3 py-1 rounded bg-amber-700 text-white font-semibold disabled:opacity-50">{copy.rescore}</button>}
          {queued && <span data-testid="rescore-queued" className="font-semibold">{copy.rescoreQueued}</span>}
        </div>
      )}
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}

      <div className="space-y-3">{view.categories.map(renderCategory)}</div>

      {view.preferred.items.length > 0 && (
        <div data-testid="preferred-section" className="rounded-xl border border-slate-200 p-4 space-y-2">
          <header className="flex flex-wrap items-center gap-3">
            <h4 className="text-sm font-bold text-slate-900">{copy.preferred}</h4>
            <span data-testid="preferred-summary" className="text-xs font-semibold text-slate-700">{copy.preferredSummary(view.preferred.summary)}</span>
          </header>
          <p className="text-xs text-slate-500">{copy.preferredNote}</p>
          <ul className="space-y-2">
            {view.preferred.items.map((item) => (
              <li key={item.item_id} data-testid={`item-${item.item_id}`} data-result={item.result} className="flex flex-wrap items-start gap-2 text-sm">
                <span dir="auto" className="flex-1 min-w-[10rem]">{item.text}</span>
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${CHIP[resultTone(item.result)]}`}>{copy.result[item.result]}</span>
                {item.evidence.map((q, i) => <span key={i} dir="auto" className="basis-full text-xs text-slate-600 border-s-2 border-slate-300 ps-2">“{q}”</span>)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
