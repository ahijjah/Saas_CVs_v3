import React from 'react';
import type { RequirementsItem, RequirementsLogicInput } from '../services/requirementsApi';
import { hasLogic, logicCopy, logicProblem, Lang, toInput } from '../utils/requirementLogic';

interface Props {
  item: RequirementsItem;
  lang: Lang;
  /** Present only while editing: the draft correction and its setter. */
  draft?: RequirementsLogicInput | null;
  onDraft?: (next: RequirementsLogicInput | null) => void;
}

/** How this criterion's wording combines, with the source wording behind it, so the recruiter can verify and correct the AI's interpretation. */
export default function RequirementLogic({ item, lang, draft, onDraft }: Props) {
  const copy = logicCopy(lang);
  const editing = !!onDraft;
  const logic = editing ? (draft === undefined ? toInput(item.logic) : draft) : toInput(item.logic);
  if (!editing && !hasLogic(item)) return null;
  const options = logic?.options ?? null;
  const problem = editing ? logicProblem(logic ?? null, lang) : null;
  const set = (patch: Partial<RequirementsLogicInput>) => onDraft?.({ options: null, examples: null, ambiguity: null, ...(logic ?? {}), ...patch });

  return (
    <div data-testid={`logic-${item.id}`} className="mt-2 space-y-2 text-xs">
      {options && (
        <div data-testid={`logic-options-${item.id}`}>
          <p className="font-semibold text-slate-700">{copy.anyOf}:</p>
          <ul className="mt-1 space-y-1">
            {options.map((o, index) => (
              <li key={index} data-testid={`logic-option-${item.id}-${index}`} data-acceptance={o.acceptance}
                className={`rounded border px-2 py-1 ${o.acceptance === 'review' ? 'border-amber-300 bg-amber-50' : 'border-slate-200 bg-slate-50'}`}>
                <span dir="auto" className="text-slate-900">{o.text}</span>
                {o.all_of && (
                  <p dir="auto" className="text-slate-700" data-testid={`logic-parts-${item.id}-${index}`}>{copy.allOf}: {o.all_of.join(' + ')}</p>
                )}
                <span className={`ms-2 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${o.acceptance === 'review' ? 'bg-amber-200 text-amber-900' : 'bg-emerald-100 text-emerald-800'}`}>
                  {o.acceptance === 'review' ? copy.review : copy.direct}
                </span>
                {o.acceptance === 'review' && !editing && <p className="text-amber-900">{copy.reviewHelp}</p>}
                {editing && (
                  <span className="ms-2 inline-flex gap-2">
                    <button type="button" className="underline" data-testid={`logic-toggle-${item.id}-${index}`}
                      onClick={() => set({ options: options.map((x, i) => (i === index ? { ...x, acceptance: x.acceptance === 'direct' ? 'review' : 'direct' } : x)) })}>
                      {o.acceptance === 'direct' ? copy.makeReview : copy.makeDirect}
                    </button>
                    <button type="button" className="underline text-red-700" onClick={() => set({ options: options.filter((_, i) => i !== index) })}>{copy.removeOption}</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
          {problem && <p role="alert" className="mt-1 text-red-700">{problem}</p>}
        </div>
      )}
      {(editing || (logic?.examples?.length ?? 0) > 0) && (logic?.examples?.length || editing) ? (
        <div data-testid={`logic-examples-${item.id}`}>
          {editing ? (
            <label className="block">
              <span className="font-semibold text-slate-700">{copy.examplesField}</span>
              <textarea dir="auto" rows={2} className="mt-1 w-full border border-slate-300 rounded px-2 py-1" value={(logic?.examples ?? []).join('\n')}
                onChange={(e) => set({ examples: e.target.value.split('\n') })} />
            </label>
          ) : (
            <p dir="auto" className="text-slate-700"><span className="font-semibold">{copy.examples}:</span> {logic!.examples!.join(' · ')}</p>
          )}
        </div>
      ) : null}
      {logic?.ambiguity && (
        <div data-testid={`logic-ambiguity-${item.id}`} role="note" className="rounded border border-violet-300 bg-violet-50 px-2 py-1 text-violet-900">
          <p className="font-semibold">{copy.ambiguity}</p>
          <p dir="auto">{logic.ambiguity.note}</p>
          <p className="text-violet-800">{copy.ambiguityHelp}</p>
          {editing && (
            <button type="button" className="mt-1 underline" data-testid={`logic-clear-ambiguity-${item.id}`} onClick={() => set({ ambiguity: null })}>{copy.clearAmbiguity}</button>
          )}
        </div>
      )}
      {item.also_source_texts && item.also_source_texts.length > 0 && (
        <p dir="auto" data-testid={`logic-also-${item.id}`} className="text-slate-500">
          {copy.alsoStated}: {item.also_source_texts.map((t) => `“${t}”`).join(' · ')}
        </p>
      )}
    </div>
  );
}
