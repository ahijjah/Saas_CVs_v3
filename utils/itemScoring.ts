// Item-level scoring (Requirements V2): the vocabulary, the on-screen wording (English and Arabic) and the small pure helpers the screens share.
// Every number is computed by the server from the stored item results (backend/services/item_scoring/calc.py); nothing here calculates a score.

import type { Lang } from './gates';

export type RequiredResult = 'met' | 'partially_met' | 'not_met' | 'unclear';
export type PreferredResult = 'yes' | 'no' | 'not_clear';
export type ScoringStatus = 'scored' | 'review_required' | 'not_scored';

export interface ItemHistoryEvent {
  event_type: string;
  previous_result: string | null;
  new_result: string | null;
  reason: string | null;
  at: string | null;
  by: string | null;
}

export interface ItemView {
  item_id: string;
  text: string;
  weight: number | null;
  result: RequiredResult | PreferredResult;
  ai_result: RequiredResult | PreferredResult;
  earned: string | null;
  evidence: string[];
  explanation: string | null;
  confidence: string | null;
  source: string | null;
  evaluated_at: string | null;
  resolution: { by: string | null; at: string; reason: string | null; previous_result: string } | null;
  history: ItemHistoryEvent[];
}

export interface CategoryView {
  category: string;
  weight: number;
  score: string | null;
  score_min: string;
  score_max: string;
  contribution: string | null;
  contribution_min: string;
  contribution_max: string;
  items: ItemView[];
}

export interface ItemScoringView {
  application_id: string;
  scoring_method: 'item_level_v2';
  status: ScoringStatus;
  requirements_revision_used: number;
  current_revision: number | null;
  stale: boolean;
  final_score: number | null;
  final_score_raw: string | null;
  range: { min: string; max: string } | null;
  unclear_count: number;
  decision: string | null;
  eligibility_status: string | null;
  effective_decision: string | null;
  qualified_threshold: number | null;
  partial_threshold: number | null;
  categories: CategoryView[];
  preferred: { summary: { yes: number; no: number; not_clear: number }; items: ItemView[] };
  narrative: Record<string, unknown>;
  model: string | null;
  scored_at: string | null;
}

/** What the server returns for an application without item-level scoring. */
export interface NoItemScoring {
  application_id: string;
  scoring_method: 'legacy_category' | 'none';
  requirements_revision_used: number | null;
  current_revision: number | null;
  stale: boolean;
  decision: string | null;
  eligibility_status: string | null;
  effective_decision: string | null;
}

export type ItemScoringResponse = ItemScoringView | NoItemScoring;

export function isItemLevel(view: ItemScoringResponse | null | undefined): view is ItemScoringView {
  return !!view && view.scoring_method === 'item_level_v2';
}

export const RESOLVABLE: readonly Exclude<RequiredResult, 'unclear'>[] = ['met', 'partially_met', 'not_met'];

const COPY = {
  en: {
    title: 'Requirement scoring',
    loading: 'Loading the requirement scoring…',
    loadFailed: 'The requirement scoring could not be loaded.',
    category: {
      skills: 'Skills', experience: 'Experience', education: 'Education', certifications: 'Certifications',
      soft_skills: 'Soft skills', domain_knowledge: 'Domain knowledge', other_requirements: 'Other requirements',
    } as Record<string, string>,
    weight: 'Weight', score: 'Score', contribution: 'Contribution', earned: 'Earned', range: 'Range',
    required: 'Required', preferred: 'Preferred',
    result: {
      met: 'Met', partially_met: 'Partially Met', not_met: 'Not Met', unclear: 'Unclear',
      yes: 'Yes', no: 'No', not_clear: 'Not Clear',
    } as Record<string, string>,
    evidence: 'Evidence', noEvidence: 'No supporting quote in the CV', explanation: 'Explanation', confidence: 'Confidence',
    evaluatedBy: 'Evaluated by AI', resolvedBy: (who: string | null) => `Resolved by ${who ?? 'a reviewer'}`, aiSaid: 'AI result',
    reason: 'Reason',
    finalScore: 'Final score', provisionalRange: 'Provisional range', provisionalNote: 'Review Required: the score is final only after every unclear Required item is resolved.',
    notScored: 'Not scored', notScoredNote: 'This job has no Required items, so there is no numeric score. The Preferred results are shown for information only.',
    scoringDecision: 'Scoring decision', eligibility: 'Eligibility', effectiveDecision: 'Overall outcome',
    revisionUsed: (used: number, current: number | null) => `Scored against revision ${used}${current != null ? `; current revision is ${current}` : ''}.`,
    stale: 'These results are out of date: the requirements changed after this CV was scored.',
    rescore: 'Rescore against current requirements', rescoreQueued: 'Rescoring was queued. Earlier results stay as history.',
    resolve: 'Resolve', resolveTitle: 'Resolve this unclear item', resolveReason: 'Reason (required)', save: 'Save', cancel: 'Cancel',
    reviewNeeded: 'Needs a reviewer',
    preferredSummary: (c: { yes: number; no: number; not_clear: number }) => `${c.yes} Yes · ${c.no} No · ${c.not_clear} Not Clear`,
    preferredNote: 'Preferred items never change the score or eligibility.',
    legacyTitle: 'Earlier scoring method', legacyNote: 'This score was produced by the earlier category-level method. It is shown as it was and is not an item-level result.',
    decisions: { qualified: 'Qualified', partial: 'Partial', rejected: 'Rejected', low_match: 'Low match', not_eligible: 'Not Eligible', review_required: 'Review Required', not_scored: 'Not scored' } as Record<string, string>,
    noDecision: 'No decision yet', thresholds: (q: number | null, p: number | null) => `Thresholds ${q ?? '–'} / ${p ?? '–'}`,
    history: 'History', unclearCount: (n: number) => `${n} unclear Required item${n === 1 ? '' : 's'}`,
    pts: 'pts',
  },
  ar: {
    title: 'تقييم المتطلبات',
    loading: 'جارٍ تحميل تقييم المتطلبات…',
    loadFailed: 'تعذّر تحميل تقييم المتطلبات.',
    category: {
      skills: 'المهارات', experience: 'الخبرة', education: 'التعليم', certifications: 'الشهادات',
      soft_skills: 'المهارات الشخصية', domain_knowledge: 'المعرفة المجالية', other_requirements: 'متطلبات أخرى',
    } as Record<string, string>,
    weight: 'الوزن', score: 'الدرجة', contribution: 'المساهمة', earned: 'المكتسب', range: 'النطاق',
    required: 'مطلوب', preferred: 'مفضّل',
    result: {
      met: 'مستوفى', partially_met: 'مستوفى جزئيًا', not_met: 'غير مستوفى', unclear: 'غير واضح',
      yes: 'نعم', no: 'لا', not_clear: 'غير واضح',
    } as Record<string, string>,
    evidence: 'الدليل', noEvidence: 'لا يوجد اقتباس داعم في السيرة الذاتية', explanation: 'التفسير', confidence: 'الثقة',
    evaluatedBy: 'تقييم الذكاء الاصطناعي', resolvedBy: (who: string | null) => `حسمه ${who ?? 'مراجع'}`, aiSaid: 'نتيجة الذكاء الاصطناعي',
    reason: 'السبب',
    finalScore: 'الدرجة النهائية', provisionalRange: 'النطاق المؤقت', provisionalNote: 'مطلوب مراجعة: تصبح الدرجة نهائية بعد حسم كل بند مطلوب غير واضح.',
    notScored: 'بدون درجة', notScoredNote: 'لا توجد بنود مطلوبة في هذه الوظيفة، لذلك لا توجد درجة رقمية. نتائج البنود المفضّلة معروضة للاطلاع فقط.',
    scoringDecision: 'قرار التقييم', eligibility: 'الأهلية', effectiveDecision: 'النتيجة العامة',
    revisionUsed: (used: number, current: number | null) => `تم التقييم وفق النسخة ${used}${current != null ? `؛ النسخة الحالية ${current}` : ''}.`,
    stale: 'هذه النتائج قديمة: تغيّرت المتطلبات بعد تقييم هذه السيرة الذاتية.',
    rescore: 'إعادة التقييم وفق المتطلبات الحالية', rescoreQueued: 'تمت جدولة إعادة التقييم. تبقى النتائج السابقة في السجل.',
    resolve: 'حسم', resolveTitle: 'حسم هذا البند غير الواضح', resolveReason: 'السبب (مطلوب)', save: 'حفظ', cancel: 'إلغاء',
    reviewNeeded: 'يحتاج إلى مراجع',
    preferredSummary: (c: { yes: number; no: number; not_clear: number }) => `${c.yes} نعم · ${c.no} لا · ${c.not_clear} غير واضح`,
    preferredNote: 'البنود المفضّلة لا تغيّر الدرجة ولا الأهلية.',
    legacyTitle: 'طريقة التقييم السابقة', legacyNote: 'أُنتجت هذه الدرجة بطريقة التقييم السابقة على مستوى الفئات. تُعرض كما كانت وليست نتيجة على مستوى البنود.',
    decisions: { qualified: 'مؤهل', partial: 'جزئي', rejected: 'مرفوض', low_match: 'تطابق منخفض', not_eligible: 'غير مؤهل', review_required: 'مطلوب مراجعة', not_scored: 'بدون درجة' } as Record<string, string>,
    noDecision: 'لا قرار بعد', thresholds: (q: number | null, p: number | null) => `الحدود ${q ?? '–'} / ${p ?? '–'}`,
    history: 'السجل', unclearCount: (n: number) => `${n} بنود مطلوبة غير واضحة`,
    pts: 'نقطة',
  },
} as const;

export type ItemScoringCopy = typeof COPY['en'] | typeof COPY['ar'];
export function itemScoringCopy(lang: Lang): ItemScoringCopy { return lang === 'ar' ? COPY.ar : COPY.en; }

export function resultTone(result: string): 'green' | 'amber' | 'red' | 'slate' {
  switch (result) {
    case 'met': case 'yes': return 'green';
    case 'partially_met': case 'unclear': case 'not_clear': return 'amber';
    case 'not_met': case 'no': return 'red';
    default: return 'slate';
  }
}

/** The label of an outcome (a stored decision, Not Eligible, Review Required or Not scored). Unknown values are shown as they are. */
export function outcomeLabel(outcome: string | null | undefined, lang: Lang): string {
  const copy = itemScoringCopy(lang);
  if (!outcome) return copy.noDecision;
  return copy.decisions[outcome] ?? outcome;
}

export function outcomeTone(outcome: string | null | undefined): 'green' | 'amber' | 'red' | 'slate' {
  switch (outcome) {
    case 'qualified': return 'green';
    case 'partial': case 'review_required': return 'amber';
    case 'rejected': case 'not_eligible': return 'red';
    default: return 'slate';
  }
}

/** The text of the score box: the number only when the server stored a final score, a dash otherwise (null is not zero). */
export function scoreText(score: number | null | undefined): string {
  return score == null ? '—' : String(score);
}

/** The provisional range as one string, e.g. "73.5 – 84.1". */
export function rangeText(range: { min: string; max: string } | null | undefined): string {
  return range ? `${range.min} – ${range.max}` : '';
}
