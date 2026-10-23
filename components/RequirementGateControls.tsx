import React from 'react';
import type { RequirementsItem } from '../services/requirementsApi';
import type { KnockoutQuestion } from '../types';
import {
  DEFAULT_GATE_ACTION, DEFAULT_GATE_METHOD, GATE_ACTIONS, GateDraft, Lang, draftFromItem, gateCopy, gateProblem,
  suggestedDraft,
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
  const patch = (changes: Partial<GateDraft>) => onChange({
    ...(effective ?? suggestedDraft()),
    ...changes,
    // Requirement gates are always verified from CV evidence. Candidate answers belong to Knockout Questions.
    verification_method: DEFAULT_GATE_METHOD,
    linked_question_id: null,
  });

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
      {effective && (
        <div>
          <label className="text-xs text-slate-700">
            {copy.failureAction}
            <select aria-label={copy.failureAction} disabled={disabled} className="mt-1 w-full border border-slate-300 rounded px-2 py-1 text-sm"
              value={effective.failure_action ?? DEFAULT_GATE_ACTION}
              onChange={(e) => patch({ failure_action: e.target.value as GateDraft['failure_action'] })}>
              {GATE_ACTIONS.map((a) => <option key={a} value={a}>{copy.actions[a]}</option>)}
            </select>
          </label>
        </div>
      )}
      {problem && <p role="alert" className="text-xs text-amber-700">{problem}</p>}
    </div>
  );
}
