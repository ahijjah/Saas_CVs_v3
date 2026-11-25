// How a criterion's wording combines (any one of / all of / examples / accepted only after review / flagged ambiguity): the on-screen words
// (English and Arabic) and the pure helpers for correcting it. The server validates and stores it (backend/services/requirements_v2/logic.py);
// nothing here interprets a job description or decides an outcome.

import type { RequirementsItem, RequirementsLogic, RequirementsLogicInput, RequirementsOperation } from '../services/requirementsApi';

export type Lang = 'en' | 'ar';

const COPY = {
  en: {
    anyOf: 'Any one of', allOf: 'All of', direct: 'Accepted', review: 'Accepted only after review',
    reviewHelp: 'A candidate who meets this is not accepted automatically: a person decides.',
    examples: 'Examples (illustrations, not requirements)', ambiguity: 'Needs your review',
    ambiguityHelp: 'The AI could read this wording more than one way. Confirm or correct it before the requirements are approved.',
    alsoStated: 'Also stated in the job description as', source: 'From the job description',
    edit: 'Edit interpretation', makeDirect: 'Accept directly', makeReview: 'Accept only after review', removeOption: 'Remove option',
    examplesField: 'Examples (one per line)', clearAmbiguity: 'The wording is clear: clear the flag', optionText: 'Option',
    minTwo: 'Keep at least two options, or clear them.', oneDirect: 'At least one option must be accepted directly.',
  },
  ar: {
    anyOf: 'أيٌّ من', allOf: 'جميع', direct: 'مقبول', review: 'مقبول بعد المراجعة فقط',
    reviewHelp: 'من يستوفي هذا الخيار لا يُقبل تلقائيًا: يقرر شخص ذلك.',
    examples: 'أمثلة (للتوضيح وليست متطلبات)', ambiguity: 'يحتاج إلى مراجعتك',
    ambiguityHelp: 'يمكن قراءة هذه الصياغة بأكثر من معنى. أكّدها أو صحّحها قبل اعتماد المتطلبات.',
    alsoStated: 'ورد أيضًا في الوصف الوظيفي بصيغة', source: 'من الوصف الوظيفي',
    edit: 'تعديل التفسير', makeDirect: 'قبول مباشر', makeReview: 'قبول بعد المراجعة فقط', removeOption: 'إزالة الخيار',
    examplesField: 'أمثلة (مثال في كل سطر)', clearAmbiguity: 'الصياغة واضحة: إزالة التنبيه', optionText: 'الخيار',
    minTwo: 'أبقِ خيارين على الأقل، أو أزلها.', oneDirect: 'يجب قبول خيار واحد على الأقل مباشرة.',
  },
} as const;

export type LogicCopy = typeof COPY['en'] | typeof COPY['ar'];
export function logicCopy(lang: Lang): LogicCopy { return lang === 'ar' ? COPY.ar : COPY.en; }

export function hasLogic(item: Pick<RequirementsItem, 'logic' | 'also_source_texts'>): boolean {
  const l = item.logic;
  return !!(l && (l.options?.length || l.examples?.length || l.ambiguity)) || !!item.also_source_texts?.length;
}

export function hasAmbiguity(item: Pick<RequirementsItem, 'logic'>): boolean { return !!item.logic?.ambiguity; }

/** The editable form of a stored logic block (option ids are dropped: the server assigns them). */
export function toInput(logic: RequirementsLogic | null | undefined): RequirementsLogicInput | null {
  if (!logic) return null;
  return {
    options: logic.options ? logic.options.map((o) => ({ text: o.text, acceptance: o.acceptance, all_of: o.all_of ? [...o.all_of] : null })) : null,
    examples: logic.examples ? [...logic.examples] : null,
    ambiguity: logic.ambiguity ? { note: logic.ambiguity.note } : null,
  };
}

/** Why a draft cannot be sent, or null. Mirrors the server's rules so an obviously invalid correction is not sent; the server still decides. */
export function logicProblem(draft: RequirementsLogicInput | null, lang: Lang): string | null {
  if (!draft) return null;
  const copy = logicCopy(lang);
  if (draft.options) {
    if (draft.options.length < 2) return copy.minTwo;
    if (!draft.options.some((o) => o.acceptance === 'direct')) return copy.oneDirect;
  }
  return null;
}

export function normalizeInput(draft: RequirementsLogicInput | null): RequirementsLogicInput | null {
  if (!draft) return null;
  const examples = (draft.examples ?? []).map((e) => e.trim()).filter(Boolean);
  const out: RequirementsLogicInput = {
    options: draft.options && draft.options.length ? draft.options : null,
    examples: examples.length ? examples : null,
    ambiguity: draft.ambiguity && draft.ambiguity.note.trim() ? { note: draft.ambiguity.note.trim() } : null,
  };
  return out.options || out.examples || out.ambiguity ? out : null;
}

export function sameInput(a: RequirementsLogicInput | null, b: RequirementsLogicInput | null): boolean {
  return JSON.stringify(normalizeInput(a)) === JSON.stringify(normalizeInput(b));
}

/** A set_logic operation, only when the draft differs from what the item holds. */
export function buildLogicOperation(item: RequirementsItem, draft: RequirementsLogicInput | null | undefined): RequirementsOperation | null {
  if (draft === undefined) return null;
  const next = normalizeInput(draft);
  if (sameInput(toInput(item.logic), next)) return null;
  return { op: 'set_logic', item_id: item.id, logic: next };
}
