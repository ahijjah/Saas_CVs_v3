import React from 'react';
import type { RequirementsItem } from '../services/requirementsApi';
import type { KnockoutQuestion } from '../types';
import {
  GateDraft, Lang, draftFromItem, gateCopy, gateProblem, suggestedDraft,
} from '../utils/gates';

interface Props {
  item: RequirementsItem;
  /** The draft, or undefined when the person has not touched the gate (the item's confirmed gate, if any, is shown). null: removed. */
  draft: GateDraft | null | undefined;
  importance: 'required' | 'preferred';
  questions: readonly KnockoutQuestion[] | null | undefined;
  /** Question ids that other requirements' gates already name. */
  otherLinkedQuestionIds: readonly string[];
  lang: Lang;
  disabled?: boolean;
  onChange: (next: GateDraft | null) => void;
}

/** The Mandatory Gate control of one Required item, shown while editing. Preferred items show no control, only the reason. */
export default function RequirementGateControls({ item, draft, importance, lang, disabled, onChange }: Props) {
  const copy = gateCopy(lang);
  if (importance !== 'required') {
    return item.gate || item.gate_suggestion || draft ? (
      <p data-testid="gate-preferred-note" className="text-xs text-slate-500">{copy.preferredCannotBeGate}</p>
    ) : null;
  }
  const effective: GateDraft | null = draft === undefined ? draftFromItem(item) : draft;
  const problem = gateProblem(effective, importance, [], lang);

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2" data-testid={`gate-controls-${item.id}`}>
      <label className="flex items-start gap-2 text-sm font-medium text-slate-900">
        <input type="checkbox" aria-label={copy.mandatoryGateControl} disabled={disabled} checked={!!effective}
          onChange={(e) => onChange(e.target.checked ? (effective ?? suggestedDraft()) : null)} />
        <span>
          {copy.mandatoryGateControl}
          <span className="block text-xs font-normal text-slate-600">{copy.mandatoryGateHelp}</span>
        </span>
      </label>
      {!effective && item.gate_suggestion && (
        <div className="rounded-md border border-violet-200 bg-violet-50 p-2 text-xs text-violet-900" data-testid="gate-suggestion">
          <span className="font-semibold">{copy.badgeAiSuggested}.</span>{' '}
          <span dir="auto">{copy.aiSuggestedHelp(item.gate_suggestion.cue)}</span>
          {!disabled && (
            <button type="button" className="ms-2 underline font-medium" onClick={() => onChange(suggestedDraft())}>{copy.confirmGate}</button>
          )}
        </div>
      )}
      {problem && <p role="alert" className="text-xs text-amber-700">{problem}</p>}
    </div>
  );
}
