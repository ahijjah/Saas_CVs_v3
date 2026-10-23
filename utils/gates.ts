// Gates / knockout criteria: the vocabulary, the on-screen wording (English and Arabic) and the pure rules the screens share.
// The server decides every rule (backend/services/requirements_v2/gates.py and services/eligibility_service.py); what is here only
// keeps an obviously invalid choice from being sent and says the same thing in both languages.

import type { GateMethod, RequirementsItem } from '../services/requirementsApi';
import type { KnockoutQuestion } from '../types';

export type GateResult = 'passed' | 'failed' | 'needs_verification' | 'not_assessed' | 'overridden';
export type EligibilityStatus = 'eligible' | 'not_eligible' | 'review_required' | 'not_applicable';
export type Lang = 'en' | 'ar';

export const GATE_METHODS: readonly GateMethod[] = ['cv_evidence', 'candidate_question', 'assessment', 'manual'];
/** Requirement gates are verified from CV evidence. Their outcome is decided from the evidence alone: there is no failure action to choose. */
export const DEFAULT_GATE_METHOD: GateMethod = 'cv_evidence';

export interface GateDraft {
  mandatory: boolean;
  verification_method: GateMethod | null;
  linked_question_id: string | null;
}

export const GATE_COPY = {
  en: {
    mandatoryGate: 'Mandatory Gate',
    mandatoryGateControl: 'Mandatory Gate (eligibility)',
    mandatoryGateHelp: 'The CV must clearly meet this. Clear evidence that it is not met makes the candidate Not Eligible, whatever the score is; missing or unclear evidence sends the candidate to Review Required. The requirement still counts in the normal score.',
    preferredCannotBeGate: 'Preferred requirements cannot be gates.',
    badgeRequired: 'Required',
    badgePreferred: 'Preferred',
    badgeCandidateQuestion: 'Candidate Question',
    badgeAiSuggested: 'AI Suggested',
    aiSuggestedHelp: (cue: string) => `The job description says “${cue}”. This suggestion is not enforced until an admin or HR manager confirms it.`,
    confirmGate: 'Confirm as Mandatory Gate',
    removeGate: 'Remove gate',
    keepSuggestionOff: 'Not a gate',
    verificationMethod: 'Verification method',
    linkedQuestion: 'Knockout question',
    noLinkedQuestion: 'Choose a question',
    linkedQuestionHelp: 'The question must be an eligibility gate with passing criteria, on this job.',
    methods: { cv_evidence: 'CV evidence', candidate_question: 'Candidate question', assessment: 'Assessment result', manual: 'Manual verification' },
    problems: {
      method: 'Choose how this gate is verified.',
      link: 'Choose the knockout question that verifies this gate.',
      contradiction: 'A question is linked, but the method is not “Candidate question”.',
      duplicate: 'That question already verifies another requirement.',
    },
    unsaved: 'Unsaved gate change',
    // Create Job / knockout questions
    responseRequired: 'Response Required',
    responseRequiredHelp: 'The candidate must answer this question to submit. This is not an eligibility rule.',
    useAsEligibilityGate: 'Use as Eligibility Gate',
    useAsEligibilityGateHelp: 'A candidate who gives a failing answer is not eligible. Without this, the question is advisory.',
    advisoryQuestion: 'Advisory',
    gateQuestion: 'Eligibility Gate',
    gateNeedsCriteria: 'Set the passing criteria before using this question as a gate.',
    linkedTo: (text: string) => `Verifies: ${text}`,
    manageLinkInAnalysis: 'The link is set in Job Analysis.',
    // Gate Summary
    summaryTitle: 'Eligibility Gates',
    overall: 'Overall status',
    status: { eligible: 'Eligible', not_eligible: 'Not Eligible', review_required: 'Review Required', not_applicable: 'No gates' },
    statusExplain: {
      eligible: 'Every enforced gate is satisfied.',
      not_eligible: 'A mandatory gate failed. The score does not change this.',
      review_required: 'At least one gate needs a person to verify it.',
      not_applicable: 'This job has no enforced gates.',
    },
    scoreNote: 'The numeric score below cannot override a failed gate.',
    result: { passed: 'Passed', failed: 'Failed', needs_verification: 'Needs verification', not_assessed: 'Not assessed', overridden: 'Overridden' },
    evidence: { cv: 'CV evidence', candidate_answer: 'Candidate answer', assessment: 'Assessment', manual: 'Manual check', none: 'No evidence yet' },
    method: 'Verified by',
    evidenceHeading: 'Evidence',
    history: 'History',
    overrideAction: 'Override',
    overrideReason: 'Reason (required)',
    overrideConfirm: 'Confirm override',
    cancel: 'Cancel',
    clearOverride: 'Withdraw override',
    overriddenBy: (name: string, at: string) => `Overridden by ${name} on ${at}`,
    events: { evaluated: 'Evaluated', recorded: 'Result recorded', overridden: 'Overridden', override_cleared: 'Override withdrawn' },
    recordResult: 'Record result',
    recordPassed: 'Passed',
    recordFailed: 'Failed',
    recordNeedsVerification: 'Needs verification',
    summaryNote: 'Note (optional)',
    evaluateAgain: 'Check again',
    processingError: 'Processing error',
    processingErrorHelp: 'The check could not be completed. This says nothing about the candidate. Check again to retry.',
    loading: 'Loading eligibility…',
    loadFailed: 'The eligibility gates could not be loaded.',
    viewOnly: 'You can view the gates. Only administrators and HR managers can record or override results.',
    originalResult: 'Original result',
  },
  ar: {
    mandatoryGate: 'بوابة إلزامية',
    mandatoryGateControl: 'بوابة إلزامية (الأهلية)',
    mandatoryGateHelp: 'يجب أن تُظهر السيرة الذاتية استيفاء هذا الشرط بوضوح. الدليل الواضح على عدم الاستيفاء يجعل المرشح غير مؤهل مهما كانت درجته، أما غياب الدليل أو عدم وضوحه فيحيل المرشح إلى المراجعة. ويبقى الشرط محسوبًا في الدرجة العادية.',
    preferredCannotBeGate: 'لا يمكن أن تكون المتطلبات المفضلة بوابات.',
    badgeRequired: 'مطلوب',
    badgePreferred: 'مفضل',
    badgeCandidateQuestion: 'سؤال للمرشح',
    badgeAiSuggested: 'مقترح من الذكاء الاصطناعي',
    aiSuggestedHelp: (cue: string) => `يذكر الوصف الوظيفي «${cue}». لا يُطبَّق هذا الاقتراح حتى يؤكده المسؤول أو مدير الموارد البشرية.`,
    confirmGate: 'تأكيد كبوابة إلزامية',
    removeGate: 'إزالة البوابة',
    keepSuggestionOff: 'ليست بوابة',
    verificationMethod: 'طريقة التحقق',
    linkedQuestion: 'سؤال الإقصاء',
    noLinkedQuestion: 'اختر سؤالًا',
    linkedQuestionHelp: 'يجب أن يكون السؤال بوابة أهلية لها معايير اجتياز، ضمن هذه الوظيفة.',
    methods: { cv_evidence: 'دليل من السيرة الذاتية', candidate_question: 'سؤال للمرشح', assessment: 'نتيجة تقييم', manual: 'تحقق يدوي' },
    problems: {
      method: 'اختر طريقة التحقق من هذه البوابة.',
      link: 'اختر سؤال الإقصاء الذي يتحقق من هذه البوابة.',
      contradiction: 'تم ربط سؤال لكن طريقة التحقق ليست «سؤال للمرشح».',
      duplicate: 'هذا السؤال يتحقق بالفعل من متطلب آخر.',
    },
    unsaved: 'تغيير غير محفوظ في البوابة',
    responseRequired: 'الرد إلزامي',
    responseRequiredHelp: 'يجب أن يجيب المرشح عن هذا السؤال ليتمكن من التقديم. هذا ليس شرط أهلية.',
    useAsEligibilityGate: 'استخدام كبوابة أهلية',
    useAsEligibilityGateHelp: 'المرشح الذي يقدّم إجابة غير مجتازة لا يكون مؤهلًا. وبدون ذلك يبقى السؤال استرشاديًا.',
    advisoryQuestion: 'استرشادي',
    gateQuestion: 'بوابة أهلية',
    gateNeedsCriteria: 'حدّد معايير الاجتياز قبل استخدام هذا السؤال كبوابة.',
    linkedTo: (text: string) => `يتحقق من: ${text}`,
    manageLinkInAnalysis: 'يتم الربط من تحليل الوظيفة.',
    summaryTitle: 'بوابات الأهلية',
    overall: 'الحالة العامة',
    status: { eligible: 'مؤهل', not_eligible: 'غير مؤهل', review_required: 'يتطلب مراجعة', not_applicable: 'لا توجد بوابات' },
    statusExplain: {
      eligible: 'جميع البوابات المطبّقة مستوفاة.',
      not_eligible: 'فشلت بوابة إلزامية. لا تغيّر الدرجة ذلك.',
      review_required: 'بوابة واحدة على الأقل تحتاج إلى تحقق من شخص.',
      not_applicable: 'لا توجد بوابات مطبّقة لهذه الوظيفة.',
    },
    scoreNote: 'لا يمكن للدرجة الرقمية أدناه أن تتجاوز بوابة فاشلة.',
    result: { passed: 'مجتاز', failed: 'غير مجتاز', needs_verification: 'يحتاج إلى تحقق', not_assessed: 'لم يُقيَّم', overridden: 'تم التجاوز' },
    evidence: { cv: 'السيرة الذاتية', candidate_answer: 'إجابة المرشح', assessment: 'التقييم', manual: 'تحقق يدوي', none: 'لا يوجد دليل بعد' },
    method: 'التحقق عبر',
    evidenceHeading: 'الدليل',
    history: 'السجل',
    overrideAction: 'تجاوز',
    overrideReason: 'السبب (مطلوب)',
    overrideConfirm: 'تأكيد التجاوز',
    cancel: 'إلغاء',
    clearOverride: 'سحب التجاوز',
    overriddenBy: (name: string, at: string) => `تم التجاوز بواسطة ${name} في ${at}`,
    events: { evaluated: 'تم التقييم', recorded: 'تم تسجيل النتيجة', overridden: 'تم التجاوز', override_cleared: 'تم سحب التجاوز' },
    recordResult: 'تسجيل النتيجة',
    recordPassed: 'مجتاز',
    recordFailed: 'غير مجتاز',
    recordNeedsVerification: 'يحتاج إلى تحقق',
    summaryNote: 'ملاحظة (اختياري)',
    evaluateAgain: 'إعادة الفحص',
    processingError: 'خطأ في المعالجة',
    processingErrorHelp: 'تعذّر إكمال الفحص. هذا لا يعني شيئًا عن المرشح. أعد الفحص للمحاولة مرة أخرى.',
    loading: 'جارٍ تحميل الأهلية…',
    loadFailed: 'تعذّر تحميل بوابات الأهلية.',
    viewOnly: 'يمكنك عرض البوابات. وحده المسؤول أو مدير الموارد البشرية يمكنه تسجيل النتائج أو تجاوزها.',
    originalResult: 'النتيجة الأصلية',
  },
} as const;

export type GateCopy = (typeof GATE_COPY)[Lang];

export function gateCopy(lang: Lang): GateCopy {
  return GATE_COPY[lang] ?? GATE_COPY.en;
}

/** A gate draft from a requirement: its confirmed gate, or (when it only has a suggestion) none. A suggestion is never a draft. */
export function draftFromItem(item: RequirementsItem): GateDraft | null {
  const gate = item.gate;
  if (!gate) return null;
  return {
    mandatory: true, verification_method: gate.verification_method,
    linked_question_id: gate.linked_question_id,
  };
}

/** The gate a person would confirm when accepting the suggestion: the safe defaults, nothing enforced yet. */
export function suggestedDraft(): GateDraft {
  return { mandatory: true, verification_method: DEFAULT_GATE_METHOD, linked_question_id: null };
}

export function sameGate(a: GateDraft | null | undefined, b: GateDraft | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  return a.verification_method === b.verification_method
    && (a.linked_question_id ?? null) === (b.linked_question_id ?? null);
}

/** Mirrors the server's gate rules for one draft, so an invalid gate is not sent. The server still decides. */
export function gateProblem(
  draft: GateDraft | null, importance: 'required' | 'preferred', otherLinkedQuestionIds: readonly string[], lang: Lang,
): string | null {
  if (!draft || !draft.mandatory) return null;
  const copy = gateCopy(lang);
  if (importance !== 'required') return copy.preferredCannotBeGate;
  if (!draft.verification_method) return copy.problems.method;
  if (draft.verification_method === 'candidate_question') {
    if (!draft.linked_question_id) return copy.problems.link;
    if (otherLinkedQuestionIds.includes(draft.linked_question_id)) return copy.problems.duplicate;
  } else if (draft.linked_question_id) {
    return copy.problems.contradiction;
  }
  return null;
}

/** The knockout questions a candidate-question gate may name: eligibility gates only. */
export function linkableQuestions(questions: readonly KnockoutQuestion[] | null | undefined): KnockoutQuestion[] {
  return (questions ?? []).filter((q) => q.is_eligibility_gate === true && !!q.question_id);
}

export type RequirementBadge = { key: string; label: string; tone: 'neutral' | 'amber' | 'red' | 'blue' | 'violet' };

/** The badges of one requirement: "Required · Mandatory Gate · Candidate Question", plus "AI Suggested" until a person confirms. */
export function requirementBadges(item: RequirementsItem, lang: Lang): RequirementBadge[] {
  const copy = gateCopy(lang);
  const out: RequirementBadge[] = [{
    key: 'importance', label: item.importance === 'required' ? copy.badgeRequired : copy.badgePreferred,
    tone: item.importance === 'required' ? 'neutral' : 'amber',
  }];
  if (item.gate && item.importance === 'required') {
    out.push({ key: 'gate', label: copy.mandatoryGate, tone: 'red' });
    if (item.gate.verification_method === 'candidate_question') {
      out.push({ key: 'question', label: copy.badgeCandidateQuestion, tone: 'blue' });
    }
  } else if (item.gate_suggestion && item.importance === 'required') {
    out.push({ key: 'suggested', label: copy.badgeAiSuggested, tone: 'violet' });
  }
  return out;
}

export function statusTone(status: EligibilityStatus | string | null | undefined): 'green' | 'red' | 'amber' | 'slate' {
  switch (status) {
    case 'eligible': return 'green';
    case 'not_eligible': return 'red';
    case 'review_required': return 'amber';
    default: return 'slate';
  }
}

export function resultTone(result: GateResult | string): 'green' | 'red' | 'amber' | 'slate' | 'blue' {
  switch (result) {
    case 'passed': return 'green';
    case 'failed': return 'red';
    case 'needs_verification': return 'amber';
    case 'overridden': return 'blue';
    default: return 'slate';
  }
}

export function statusLabel(status: string | null | undefined, lang: Lang): string {
  const copy = gateCopy(lang);
  return (copy.status as Record<string, string>)[status ?? ''] ?? copy.status.not_applicable;
}

/** Whether the outcome shown next to a score should be the gate status instead of the score's decision. */
export function overridesScoreDecision(status: string | null | undefined): boolean {
  return status === 'not_eligible' || status === 'review_required';
}

/** The part of a knockout-question editor row that decides whether it can be an Eligibility Gate. */
export interface QuestionGateFields {
  question_type?: 'yes_no' | 'single_choice' | 'number';
  passingAnswers?: string[];
  passingValue?: string;
  optionsText?: string;
}

/** Whether the row has passing criteria a gate could be judged against. A gate without them is refused by the server too. */
export function hasPassingCriteria(q: QuestionGateFields): boolean {
  const type = q.question_type ?? 'yes_no';
  if (type === 'number') return (q.passingValue ?? '').trim() !== '' && !Number.isNaN(parseFloat(q.passingValue ?? ''));
  const answers = q.passingAnswers ?? [];
  if (type === 'single_choice') {
    const options = (q.optionsText ?? '').split('\n').map((s) => s.trim()).filter(Boolean);
    return answers.some((a) => options.includes(a));
  }
  return answers.length > 0;
}

/** The gate fields sent with a saved question. A question that is not a gate stays advisory; the failure action is retired and never sent. */
export function questionGatePayload(
  q: { is_eligibility_gate?: boolean; question_id?: string },
): { question_id?: string; is_eligibility_gate: boolean } {
  const out: { question_id?: string; is_eligibility_gate: boolean } = { is_eligibility_gate: q.is_eligibility_gate === true };
  if (q.question_id) out.question_id = q.question_id;
  return out;
}

/** A message when the row cannot be saved as it is set (a gate with no passing criteria), else null. */
export function questionGateProblem(q: QuestionGateFields & { is_eligibility_gate?: boolean }, lang: Lang): string | null {
  if (q.is_eligibility_gate === true && !hasPassingCriteria(q)) return gateCopy(lang).gateNeedsCriteria;
  return null;
}
