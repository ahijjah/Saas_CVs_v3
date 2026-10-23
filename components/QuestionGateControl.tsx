// The "Use as Eligibility Gate" control of one knockout question, shared by Create Job and the Job page editor. A question is advisory
// unless this is switched on explicitly. There is no failure action to choose: a clearly failing answer is not eligible, and an answer
// that cannot be read goes to review.

import React from 'react';
import type { KnockoutQuestion } from '../types';
import { Lang, QuestionGateFields, gateCopy, hasPassingCriteria } from '../utils/gates';

interface Props {
  lang: Lang;
  question: QuestionGateFields & {
    is_eligibility_gate?: boolean;
    linked_requirement?: KnockoutQuestion['linked_requirement'];
  };
  onChange: (field: 'is_eligibility_gate', value: boolean) => void;
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
      {linked && (
        <p className="text-[11px] text-textMuted" data-testid="question-linked-requirement">
          {copy.linkedTo(linked.text)}
        </p>
      )}
      {missingCriteria && <p className="text-[11px] text-red-600 font-semibold" role="alert">{copy.gateNeedsCriteria}</p>}
    </div>
  );
};

export default QuestionGateControl;
