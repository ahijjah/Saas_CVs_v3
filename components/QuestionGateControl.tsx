// The "Use as Eligibility Gate" control of one knockout question, shared by Create Job and the Job page editor. A question is advisory
// unless this is switched on explicitly. When a Job Analysis requirement already names the question, that requirement owns the failure
// action, so it is shown here and not editable.

import React from 'react';
import type { KnockoutQuestion } from '../types';
import type { GateAction } from '../services/requirementsApi';
import { GATE_ACTIONS, Lang, QuestionGateFields, gateCopy, hasPassingCriteria } from '../utils/gates';

interface Props {
  lang: Lang;
  question: QuestionGateFields & {
    is_eligibility_gate?: boolean;
    gate_failure_action?: GateAction;
    linked_requirement?: KnockoutQuestion['linked_requirement'];
  };
  onChange: (field: 'is_eligibility_gate' | 'gate_failure_action', value: boolean | GateAction) => void;
}

const QuestionGateControl: React.FC<Props> = ({ lang, question, onChange }) => {
  const copy = gateCopy(lang);
  const isGate = question.is_eligibility_gate === true;
  const linked = question.linked_requirement ?? null;
  const missingCriteria = isGate && !hasPassingCriteria(question);
  return (
    <div className="space-y-1.5 pt-1" data-testid="question-gate-control">
      <label className="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" checked={isGate} className="rounded border-border"
               onChange={(e) => onChange('is_eligibility_gate', e.target.checked)} />
        <span className="text-xs text-textMain font-semibold">{copy.useAsEligibilityGate}</span>
      </label>
      <p className="text-[11px] text-textMuted">{copy.useAsEligibilityGateHelp}</p>
      {isGate && (
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-textMuted font-semibold">{copy.failureAction}</span>
          <select aria-label={copy.failureAction} value={linked ? linked.failure_action : (question.gate_failure_action ?? 'hold_for_review')} disabled={!!linked}
                  onChange={(e) => onChange('gate_failure_action', e.target.value as GateAction)}
                  className="px-2 py-1 border border-border rounded-lg text-xs bg-white">
            {GATE_ACTIONS.map((a) => <option key={a} value={a}>{copy.actions[a]}</option>)}
          </select>
        </div>
      )}
      {linked && (
        <p className="text-[11px] text-textMuted" data-testid="question-linked-requirement">
          {copy.linkedTo(linked.text)} <span className="italic">{copy.linkedGovernsAction}</span>
        </p>
      )}
      {missingCriteria && <p className="text-[11px] text-red-600 font-semibold" role="alert">{copy.gateNeedsCriteria}</p>}
    </div>
  );
};

export default QuestionGateControl;
