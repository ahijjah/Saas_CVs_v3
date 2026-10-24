import React, { useEffect, useRef, useState } from 'react';
import { getGates, GateSummaryData } from '../services/gatesApi';
import { getItemScoring } from '../services/itemScoringApi';
import { isItemLevel, ItemScoringView, ItemView } from '../utils/itemScoring';
import { Lang, matrixCopy, statusLabel, statusTone, TONE_CLASS } from '../utils/matrix';

export interface DrawerFocus {
  /** Item ids and gate keys to show first (a flag's targets or one clicked result); empty = everything. */
  itemIds: string[];
  gateKeys: string[];
}
interface Props {
  applicationId: string;
  candidateName: string;
  focus: DrawerFocus;
  lang: Lang;
  onClose: () => void;
  onOpenCandidate: () => void;
  /** Test seam: already-loaded data replaces the two requests. */
  initial?: { scoring: ItemScoringView | null; gates: GateSummaryData | null };
}

const shown = (item: ItemView) => (item.result === 'not_clear' ? 'unclear' : item.result);       // a Preferred item has no partial result

/** Evidence, confidence and human resolution for the clicked result, summary or flag. Reads the stored item evaluations and gate results; changes nothing. */
export default function MatrixEvidenceDrawer({ applicationId, candidateName, focus, lang, onClose, onOpenCandidate, initial }: Props) {
  const copy = matrixCopy(lang);
  const [scoring, setScoring] = useState<ItemScoringView | null>(initial?.scoring ?? null);
  const [gates, setGates] = useState<GateSummaryData | null>(initial?.gates ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!initial);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (initial) return;
    let live = true;
    setLoading(true);
    Promise.all([getItemScoring(applicationId), getGates(applicationId).catch(() => null)])
      .then(([s, g]) => { if (live) { setScoring(isItemLevel(s) ? s : null); setGates(g); setError(null); } })
      .catch((err) => { if (live) setError(err instanceof Error ? err.message : copy.loadFailed); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  const allItems: ItemView[] = scoring ? [...scoring.categories.flatMap((c) => c.items), ...scoring.preferred.items] : [];
  const focused = focus.itemIds.length > 0 || focus.gateKeys.length > 0;
  const items = focused ? allItems.filter((i) => focus.itemIds.includes(i.item_id)) : allItems;
  const gateList = (gates?.gates ?? []).filter((g) => !focused || focus.gateKeys.includes(g.gate_key));

  return (
    <aside role="dialog" aria-modal="true" aria-label={copy.drawerTitle} data-testid="matrix-drawer"
      className="fixed inset-y-0 end-0 z-50 w-full max-w-md bg-white shadow-2xl border-s border-slate-200 overflow-y-auto p-5 space-y-4">
      <header className="flex items-start gap-3">
        <div className="flex-1">
          <h2 className="text-base font-black text-slate-900">{copy.drawerTitle}</h2>
          <p dir="auto" className="text-sm text-slate-600">{candidateName}</p>
          {focused && <p className="text-xs text-slate-500">{copy.flaggedTargets}</p>}
        </div>
        <button ref={closeRef} type="button" onClick={onClose} data-testid="drawer-close" className="px-3 py-1 rounded border border-slate-300 text-sm">{copy.drawerClose}</button>
      </header>
      {loading && <p className="text-sm text-slate-500">{copy.loadingEvidence}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}

      {items.map((item) => (
        <article key={item.item_id} data-testid={`drawer-item-${item.item_id}`} className="rounded-lg border border-slate-200 p-3 space-y-2">
          <div className="flex flex-wrap items-start gap-2">
            <p dir="auto" className="flex-1 text-sm font-semibold text-slate-900">{item.text}</p>
            {item.weight != null && <span className="text-xs text-slate-500">{copy.weight}: {item.weight}%</span>}
            <span data-testid={`drawer-result-${item.item_id}`} className={`px-2 py-0.5 rounded-full text-xs font-bold ${TONE_CLASS[statusTone(shown(item))]}`}>{statusLabel(shown(item), lang)}</span>
          </div>
          {item.evidence.length > 0
            ? <ul className="space-y-1">{item.evidence.map((q, i) => <li key={i} dir="auto" className="text-xs text-slate-700 border-s-2 border-slate-300 ps-2">“{q}”</li>)}</ul>
            : <p className="text-xs text-slate-500">{copy.noEvidence}</p>}
          {item.explanation && <p dir="auto" className="text-xs text-slate-600"><b>{copy.explanation}:</b> {item.explanation}</p>}
          {item.confidence != null && <p className="text-xs text-slate-600"><b>{copy.confidence}:</b> {Math.round(Number(item.confidence) * 100)}%</p>}
          {item.resolution && (
            <p data-testid={`drawer-resolution-${item.item_id}`} className="text-xs text-blue-800">
              <b>{copy.humanResolution}:</b> {copy.resolvedBy} {item.resolution.by ?? '—'} · {copy.aiResult}: {statusLabel(item.resolution.previous_result === 'not_clear' ? 'unclear' : item.resolution.previous_result, lang)}
              {item.resolution.reason ? <> · {copy.reason}: <span dir="auto">{item.resolution.reason}</span></> : null}
            </p>
          )}
        </article>
      ))}

      {gateList.map((gate) => (
        <article key={gate.gate_key} data-testid={`drawer-gate-${gate.gate_key}`} className="rounded-lg border border-slate-200 p-3 space-y-2">
          <div className="flex flex-wrap items-start gap-2">
            <p dir="auto" className="flex-1 text-sm font-semibold text-slate-900">{gate.label}</p>
            <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${TONE_CLASS[statusTone(gate.result === 'overridden' ? 'passed' : gate.result === 'needs_verification' ? 'review_required' : gate.result)]}`}>
              {statusLabel(gate.result === 'overridden' ? 'passed' : gate.result === 'needs_verification' ? 'review_required' : gate.result, lang)}
            </span>
          </div>
          {gate.evidence_summary && <p dir="auto" className="text-xs text-slate-700"><b>{copy.gateEvidence}:</b> {gate.evidence_summary}</p>}
          {gate.confidence != null && <p className="text-xs text-slate-600"><b>{copy.confidence}:</b> {Math.round(gate.confidence * 100)}%</p>}
          {gate.result === 'overridden' && <p className="text-xs text-blue-800"><b>{copy.gateOverridden}:</b> <span dir="auto">{gate.override_reason}</span></p>}
        </article>
      ))}
      {!loading && !error && items.length === 0 && gateList.length === 0 && <p className="text-sm text-slate-500">{copy.noEvidence}</p>}

      <button type="button" onClick={onOpenCandidate} data-testid="drawer-open-candidate" className="w-full px-3 py-2 rounded bg-slate-900 text-white text-sm font-semibold">{copy.openFull}</button>
    </aside>
  );
}
