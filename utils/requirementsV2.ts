// Requirements-v2 draft model: pure functions (no React, no network). They mirror the BACKEND rules so that the
// editor behaves predictably, but the backend stays the authority: every save is validated again on the server and
// its answer is what the UI shows. Nothing here redistributes weights on its own; Equalize and Normalize are explicit
// actions chosen by the recruiter.

export const CATEGORIES = [
  'skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other_requirements',
] as const;
export type CategoryKey = typeof CATEGORIES[number];
export type Importance = 'required' | 'preferred';
export const MAX_REQUIRED_PER_CATEGORY = 100;

export interface ExperienceStructure { subject: string | null; min_years: number | null }

/** An item as the server sends it (public part). */
export interface ServerItem {
  id: string;
  text: string;
  importance: Importance;
  weight: number | null;
  origin: 'stated' | 'from_responsibilities' | 'recruiter_added';
  source_text: string | null;
  alternatives: string[] | null;
  experience: ExperienceStructure | null;
}

/** An item in the recruiter's draft. `key` is stable for React; `id` is null until the server assigns one. */
export interface DraftItem {
  key: string;
  id: string | null;
  text: string;
  importance: Importance;
  weight: number | null;
  origin: ServerItem['origin'];
  source_text: string | null;
  alternatives: string[] | null;
  experience: ExperienceStructure | null;
}

export interface DraftCategory { weight: number | null; items: DraftItem[] }
export type Draft = Record<CategoryKey, DraftCategory>;

export interface ServerDocument {
  schema_version: 2;
  categories: Record<CategoryKey, { weight: number; items: ServerItem[] }>;
}

// ── conversion ───────────────────────────────────────────────────────────────

export function draftFromServer(doc: ServerDocument): Draft {
  const out = {} as Draft;
  for (const c of CATEGORIES) {
    const cat = doc.categories[c];
    out[c] = {
      weight: cat.weight,
      items: cat.items.map(i => ({
        key: i.id, id: i.id, text: i.text, importance: i.importance, weight: i.weight, origin: i.origin,
        source_text: i.source_text, alternatives: i.alternatives ? [...i.alternatives] : null,
        experience: i.experience ? { ...i.experience } : null,
      })),
    };
  }
  return out;
}

/** The body of PUT /jobs/{id}/requirements (`requirements`). Existing items keep their id; new items have none.
 *  Provenance (origin, source wording) is never sent: the server owns it. */
export function toPayload(draft: Draft) {
  const categories: Record<string, unknown> = {};
  for (const c of CATEGORIES) {
    categories[c] = {
      weight: draft[c].weight,
      items: draft[c].items.map(i => ({
        ...(i.id ? { id: i.id } : {}),
        text: i.text, importance: i.importance, weight: i.importance === 'preferred' ? null : i.weight,
        alternatives: i.alternatives, experience: i.experience,
      })),
    };
  }
  return { schema_version: 2, categories };
}

export function isDirty(draft: Draft, base: Draft): boolean {
  return JSON.stringify(toPayload(draft)) !== JSON.stringify(toPayload(base));
}

let counter = 0;
export function newKey(): string { counter += 1; return `new_${counter}`; }

// ── queries ──────────────────────────────────────────────────────────────────

export const requiredItems = (cat: DraftCategory) => cat.items.filter(i => i.importance === 'required');
export const preferredItems = (cat: DraftCategory) => cat.items.filter(i => i.importance === 'preferred');
const num = (v: number | null) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

export function requiredTotal(cat: DraftCategory): number {
  return requiredItems(cat).reduce((s, i) => s + num(i.weight), 0);
}
export function categoryTotal(draft: Draft): number {
  return CATEGORIES.reduce((s, c) => s + num(draft[c].weight), 0);
}
export function hasAnyRequired(draft: Draft): boolean {
  return CATEGORIES.some(c => requiredItems(draft[c]).length > 0);
}
export function countItems(draft: Draft): { required: number; preferred: number } {
  let required = 0, preferred = 0;
  for (const c of CATEGORIES) { required += requiredItems(draft[c]).length; preferred += preferredItems(draft[c]).length; }
  return { required, preferred };
}
export function findItem(draft: Draft, key: string): { category: CategoryKey; item: DraftItem } | null {
  for (const c of CATEGORIES) {
    const item = draft[c].items.find(i => i.key === key || i.id === key);
    if (item) return { category: c, item };
  }
  return null;
}

// ── edits (immutable) ────────────────────────────────────────────────────────

function mapItem(draft: Draft, key: string, fn: (i: DraftItem) => DraftItem): Draft {
  const found = findItem(draft, key);
  if (!found) return draft;
  const cat = draft[found.category];
  return { ...draft, [found.category]: { ...cat, items: cat.items.map(i => (i.key === found.item.key ? fn(i) : i)) } };
}

/** When a category loses its last required item its own weight becomes 0 (backend rule); nothing else moves. */
function zeroIfNoRequired(draft: Draft, category: CategoryKey): Draft {
  return requiredItems(draft[category]).length === 0 ? { ...draft, [category]: { ...draft[category], weight: 0 } } : draft;
}

export function addItem(draft: Draft, category: CategoryKey, importance: Importance): { draft: Draft; key: string } {
  const key = newKey();
  const item: DraftItem = { key, id: null, text: '', importance, weight: null, origin: 'recruiter_added',
    source_text: null, alternatives: null, experience: null };
  const cat = draft[category];
  return { draft: { ...draft, [category]: { ...cat, items: [...cat.items, item] } }, key };
}

export function removeItem(draft: Draft, key: string): Draft {
  const found = findItem(draft, key);
  if (!found) return draft;
  const cat = draft[found.category];
  const next = { ...draft, [found.category]: { ...cat, items: cat.items.filter(i => i.key !== found.item.key) } };
  return zeroIfNoRequired(next, found.category);
}

export function setImportance(draft: Draft, key: string, importance: Importance): Draft {
  const found = findItem(draft, key);
  if (!found || found.item.importance === importance) return draft;
  const next = mapItem(draft, key, i => ({ ...i, importance, weight: null }));   // required starts unweighted; preferred has none
  return zeroIfNoRequired(next, found.category);
}

export const setText = (d: Draft, key: string, text: string) => mapItem(d, key, i => ({ ...i, text }));
export const setItemWeight = (d: Draft, key: string, weight: number | null) =>
  mapItem(d, key, i => (i.importance === 'preferred' ? i : { ...i, weight }));
export const setAlternatives = (d: Draft, key: string, alternatives: string[] | null) =>
  mapItem(d, key, i => ({ ...i, alternatives }));
export const setExperience = (d: Draft, key: string, experience: ExperienceStructure | null) =>
  mapItem(d, key, i => ({ ...i, experience }));
export const setCategoryWeight = (d: Draft, category: CategoryKey, weight: number | null): Draft =>
  ({ ...d, [category]: { ...d[category], weight } });

// ── explicit weight actions ──────────────────────────────────────────────────

/** 100 // n each; the remainder one point at a time in list order (3 -> 34/33/33). */
export function equalizeWeights(n: number): number[] {
  if (n <= 0) return [];
  const base = Math.floor(100 / n), rem = 100 % n;
  return Array.from({ length: n }, (_, i) => (i < rem ? base + 1 : base));
}

export function equalizeCategory(draft: Draft, category: CategoryKey): Draft {
  const req = requiredItems(draft[category]);
  if (req.length === 0 || req.length > MAX_REQUIRED_PER_CATEGORY) return draft;
  const weights = equalizeWeights(req.length);
  const byKey = new Map(req.map((i, n) => [i.key, weights[n]]));
  const cat = draft[category];
  return { ...draft, [category]: { ...cat, items: cat.items.map(i => (byKey.has(i.key) ? { ...i, weight: byKey.get(i.key)! } : i)) } };
}

export type NormalizeOutcome =
  | { status: 'ok'; weights: Record<CategoryKey, number>; raised: CategoryKey[] }
  | { status: 'no_eligible' }
  | { status: 'fallback_required'; suggested: Record<CategoryKey, number> };

export function equalCategorySplit(eligible: CategoryKey[]): Record<CategoryKey, number> {
  const out = Object.fromEntries(CATEGORIES.map(c => [c, 0])) as Record<CategoryKey, number>;
  const elig = CATEGORIES.filter(c => eligible.includes(c));
  equalizeWeights(elig.length).forEach((w, n) => { out[elig[n]] = w; });
  return out;
}

/** Proportional rescaling of the category weights to whole percentages totalling 100 (largest remainder, ties in
 *  category order; categories with required items stay >= 1; categories without required items become 0). */
export function computeNormalize(draft: Draft): NormalizeOutcome {
  const eligible = CATEGORIES.filter(c => requiredItems(draft[c]).length > 0);
  if (eligible.length === 0) return { status: 'no_eligible' };
  const vals = Object.fromEntries(eligible.map(c => {
    const v = draft[c].weight;
    return [c, typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0];
  })) as Record<string, number>;
  const total = eligible.reduce((s, c) => s + vals[c], 0);
  if (total <= 0) return { status: 'fallback_required', suggested: equalCategorySplit(eligible) };
  const weights = Object.fromEntries(CATEGORIES.map(c => [c, 0])) as Record<CategoryKey, number>;
  const rema: Record<string, number> = {};
  for (const c of eligible) { weights[c] = Math.floor((vals[c] * 100) / total); rema[c] = (vals[c] * 100) % total; }
  let missing = 100 - eligible.reduce((s, c) => s + weights[c], 0);
  const order = [...eligible].sort((a, b) => rema[b] - rema[a] || CATEGORIES.indexOf(a) - CATEGORIES.indexOf(b));
  for (const c of order.slice(0, missing)) weights[c] += 1;
  const raised: CategoryKey[] = [];
  for (const c of eligible) {
    if (weights[c] >= 1) continue;
    const donors = eligible.filter(d => weights[d] > 1);
    if (donors.length === 0) break;
    donors.sort((a, b) => weights[b] - weights[a] || CATEGORIES.indexOf(a) - CATEGORIES.indexOf(b));
    weights[donors[0]] -= 1; weights[c] = 1; raised.push(c);
  }
  return { status: 'ok', weights, raised };
}

export function applyCategoryWeights(draft: Draft, weights: Record<CategoryKey, number>): Draft {
  const out = { ...draft };
  for (const c of CATEGORIES) out[c] = { ...draft[c], weight: weights[c] };
  return out;
}

// ── local validation (mirrors the backend; the server's answer is final) ─────

export interface LocalIssue {
  code: string;
  category?: CategoryKey;
  itemKey?: string;
  params?: Record<string, string | number>;
}

export function validateDraft(draft: Draft): LocalIssue[] {
  const issues: LocalIssue[] = [];
  let anyRequired = false;
  for (const c of CATEGORIES) {
    const cat = draft[c];
    const req = requiredItems(cat);
    if (!Number.isInteger(cat.weight) || (cat.weight as number) < 0 || (cat.weight as number) > 100)
      issues.push({ code: 'bad_category_weight', category: c });
    if (req.length) {
      anyRequired = true;
      if (num(cat.weight) < 1) issues.push({ code: 'category_weight_not_positive', category: c });
      if (req.length > MAX_REQUIRED_PER_CATEGORY) issues.push({ code: 'too_many_required_items', category: c, params: { n: req.length } });
      let allValid = true;
      for (const i of req) {
        if (!Number.isInteger(i.weight) || (i.weight as number) < 1 || (i.weight as number) > 100) {
          allValid = false;
          issues.push({ code: 'required_weight_invalid', category: c, itemKey: i.key });
        }
      }
      if (allValid && requiredTotal(cat) !== 100)
        issues.push({ code: 'required_weights_total', category: c, params: { total: requiredTotal(cat) } });
    } else if (num(cat.weight) !== 0) {
      issues.push({ code: 'category_weight_without_required_items', category: c });
    }
    for (const i of cat.items) {
      if (!i.text.trim()) issues.push({ code: 'empty_text', category: c, itemKey: i.key });
      if (i.alternatives && (i.alternatives.length < 2 || i.alternatives.some(a => !a.trim())))
        issues.push({ code: 'bad_alternatives', category: c, itemKey: i.key });
      if (i.experience) {
        const { subject, min_years } = i.experience;
        const subjOk = subject === null || subject.trim() !== '';
        const yrsOk = min_years === null || (Number.isInteger(min_years) && min_years >= 0);
        if (!subjOk || !yrsOk || (subject === null && min_years === null))
          issues.push({ code: 'bad_experience', category: c, itemKey: i.key });
      }
    }
  }
  if (anyRequired && categoryTotal(draft) !== 100)
    issues.push({ code: 'category_weights_total', params: { total: categoryTotal(draft) } });
  return issues;
}

/** Parse a typed whole number. '' -> null (empty); anything that is not a plain non-negative integer -> undefined. */
export function parseWhole(s: string): number | null | undefined {
  const t = s.trim();
  if (t === '') return null;
  return /^\d{1,4}$/.test(t) ? Number(t) : undefined;
}

// ── comparison with the original AI analysis ─────────────────────────────────

export type ItemChange =
  | { kind: 'same'; item: DraftItem }
  | { kind: 'changed'; item: DraftItem; original: ServerItem; fields: string[] }
  | { kind: 'added'; item: DraftItem };

export interface CategoryComparison {
  weightChanged: boolean;
  originalWeight: number;
  current: ItemChange[];
  removed: ServerItem[];
}

export function compareWithOriginal(draft: Draft, original: ServerDocument): Record<CategoryKey, CategoryComparison> {
  const out = {} as Record<CategoryKey, CategoryComparison>;
  const origById = new Map<string, ServerItem>();
  for (const c of CATEGORIES) for (const i of original.categories[c].items) origById.set(i.id, i);
  const keptIds = new Set(CATEGORIES.flatMap(c => draft[c].items.map(i => i.id).filter(Boolean) as string[]));
  for (const c of CATEGORIES) {
    out[c] = {
      weightChanged: draft[c].weight !== original.categories[c].weight,
      originalWeight: original.categories[c].weight,
      current: draft[c].items.map((item): ItemChange => {
        const o = item.id ? origById.get(item.id) : undefined;
        if (!o) return { kind: 'added', item };
        const fields: string[] = [];
        if (o.text !== item.text) fields.push('text');
        if (o.importance !== item.importance) fields.push('importance');
        if (o.weight !== item.weight) fields.push('weight');
        if (JSON.stringify(o.alternatives) !== JSON.stringify(item.alternatives)) fields.push('alternatives');
        if (JSON.stringify(o.experience) !== JSON.stringify(item.experience)) fields.push('experience');
        return fields.length ? { kind: 'changed', item, original: o, fields } : { kind: 'same', item };
      }),
      removed: original.categories[c].items.filter(i => !keptIds.has(i.id)),
    };
  }
  return out;
}

// ── backend errors ───────────────────────────────────────────────────────────

export interface ApiProblem {
  status: number | null;
  code: string | null;
  message: string;
  issues: { code: string; message: string; category: CategoryKey | null; item_id: string | null }[];
  current: any | null;
}

export function describeApiError(err: any): ApiProblem {
  const detail = err?.data?.detail;
  const d = detail && typeof detail === 'object' && !Array.isArray(detail) ? detail : {};
  return {
    status: typeof err?.status === 'number' ? err.status : null,
    code: typeof d.code === 'string' ? d.code : null,
    message: typeof d.message === 'string' ? d.message : String(err?.message ?? err ?? ''),
    issues: Array.isArray(d.issues) ? d.issues : [],
    current: d.current ?? null,
  };
}
