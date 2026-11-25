import React from 'react';
import { ItemView, itemScoringCopy, resultTone } from '../utils/itemScoring';
import type { Lang } from '../utils/gates';

const CHIP = {
  green: 'bg-emerald-100 text-emerald-800', amber: 'bg-amber-100 text-amber-800', red: 'bg-red-100 text-red-800', slate: 'bg-slate-100 text-slate-700',
} as const;

/** Which alternative was met, how every option was assessed, and why a case needs review. Shared by the scoring view and the matrix drawer. */
export default function OptionResults({ item, lang }: { item: ItemView; lang: Lang }) {
  const copy = itemScoringCopy(lang);
  const options = item.option_results ?? null;
  const reason = item.review_reason && item.result === item.ai_result ? copy.reasons[item.review_reason] : null;
  if (!options && !reason) return null;
  return (
    <div data-testid={`options-${item.item_id}`} className="space-y-1">
      {item.matched_option && (
        <p data-testid={`matched-${item.item_id}`} className="text-xs text-slate-800"><b>{copy.metThrough}:</b> <span dir="auto">{item.matched_option}</span></p>
      )}
      {reason && <p data-testid={`reason-${item.item_id}`} className="text-xs text-amber-900">{reason}</p>}
      {options && (
        <ul className="space-y-0.5" aria-label={copy.options}>
          {options.map((o) => (
            <li key={o.option_id} data-testid={`option-${item.item_id}-${o.option_id}`} data-result={o.result} className="flex flex-wrap items-center gap-2 text-xs">
              <span className={`px-1.5 py-0.5 rounded-full font-semibold ${CHIP[resultTone(o.result)]}`}>{copy.result[o.result]}</span>
              <span dir="auto" className="text-slate-800">{o.text}</span>
              {o.acceptance === 'review' && <span className="text-amber-800">({copy.afterReview})</span>}
              {o.parts && <span dir="auto" className="text-slate-500">{copy.parts}: {o.parts.map((p) => `${p.text} — ${copy.result[p.result]}`).join(' · ')}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
