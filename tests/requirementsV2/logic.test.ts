import { describe, expect, it } from 'vitest';
import parity from './parity.json';
import { doc, item, weightedDoc } from './fixtures';
import {
  CATEGORIES, addItem, computeNormalize, draftFromServer, equalizeCategory, equalizeWeights, isDirty, removeItem,
  setCategoryWeight, setImportance, setItemWeight, setText, toPayload, validateDraft, categoryTotal, requiredTotal, parseWhole,
  compareWithOriginal, describeApiError, applyCategoryWeights,
} from '../../utils/requirementsV2';

const d0 = () => draftFromServer(weightedDoc());

describe('weights: parity with the backend', () => {
  it('equalize matches backend equalize_weights', () => {
    for (const [n, expected] of Object.entries(parity.equalize)) expect(equalizeWeights(Number(n))).toEqual(expected);
  });
  it('normalize matches backend normalize_category_weights on every vector', () => {
    for (const v of parity.normalize) {
      const items: any = {};
      for (const c of v.eligible) items[c] = { weight: (v.proposed as any)[c] ?? 0, items: [item(`req_${c}`, c, 'required', 100)] };
      for (const c of Object.keys(v.proposed)) if (!v.eligible.includes(c)) items[c] = { weight: (v.proposed as any)[c], items: [] };
      const out = computeNormalize(draftFromServer(doc(items)));
      expect(out.status).toBe(v.status);
      if (v.status === 'ok') { expect((out as any).weights).toEqual(v.weights); expect((out as any).raised).toEqual(v.raised); }
      if (v.status === 'fallback_required') expect((out as any).suggested).toEqual(v.fallback);
    }
  });
});

describe('edit rules', () => {
  it('never redistributes: changing one weight leaves every other weight alone', () => {
    const d = setItemWeight(d0(), 'req_py', 10);
    expect(d.skills.items.map(i => i.weight)).toEqual([10, 50, null]);
    expect(d.skills.weight).toBe(60); expect(d.experience.weight).toBe(40);
  });
  it('adding an item changes no weight; a new required item is unweighted until the recruiter sets it', () => {
    const { draft } = addItem(d0(), 'skills', 'required');
    expect(draft.skills.items.at(-1)).toMatchObject({ id: null, text: '', importance: 'required', weight: null, origin: 'recruiter_added' });
    expect(draft.skills.items.slice(0, 3).map(i => i.weight)).toEqual([50, 50, null]);
  });
  it('removing the last required item sets the category weight to 0 and moves nothing else', () => {
    let d = removeItem(d0(), 'req_exp');
    expect(d.experience.weight).toBe(0); expect(d.skills.weight).toBe(60);
    expect(d.experience.items.map(i => i.key)).toEqual(['req_resp']);
    d = removeItem(d0(), 'req_py');
    expect(d.skills.weight).toBe(60);                                   // another required item remains
  });
  it('reclassifying clears the weight; reclassifying the last required item zeroes the category', () => {
    const d = setImportance(d0(), 'req_exp', 'preferred');
    expect(d.experience.items[0]).toMatchObject({ importance: 'preferred', weight: null });
    expect(d.experience.weight).toBe(0);
    const r = setImportance(d0(), 'req_docker', 'required');
    expect(r.skills.items[2]).toMatchObject({ importance: 'required', weight: null });
    expect(r.skills.weight).toBe(60);
  });
  it('a preferred item never takes a weight', () => {
    const d = setItemWeight(d0(), 'req_docker', 20);
    expect(d.skills.items[2].weight).toBeNull();
    expect(toPayload(setImportance(d0(), 'req_py', 'preferred')).categories.skills).toMatchObject({ items: [{ weight: null }, { weight: 50 }, { weight: null }] });
  });
  it('equalize is explicit, per category and leaves preferred items and the category weight alone', () => {
    let d = setImportance(d0(), 'req_docker', 'required');
    d = equalizeCategory(d, 'skills');
    expect(d.skills.items.map(i => i.weight)).toEqual([34, 33, 33]); expect(d.skills.weight).toBe(60);
    expect(d.experience.items[0].weight).toBe(100);
  });
  it('normalize applies only through applyCategoryWeights', () => {
    const d = setCategoryWeight(d0(), 'skills', 30);
    expect(categoryTotal(d)).toBe(70);
    const out = computeNormalize(d);
    expect(out.status).toBe('ok');
    expect(categoryTotal(applyCategoryWeights(d, (out as any).weights))).toBe(100);
  });
  it('payload keeps ids of existing items, omits ids of new ones and never sends provenance', () => {
    const { draft } = addItem(setText(d0(), 'req_py', 'Python 3'), 'skills', 'preferred');
    const items = (toPayload(draft).categories as any).skills.items;
    expect(items[0]).toEqual({ id: 'req_py', text: 'Python 3', importance: 'required', weight: 50, alternatives: null, experience: null });
    expect('id' in items[3]).toBe(false);
    expect(JSON.stringify(toPayload(draft))).not.toMatch(/origin|source_text|scoring_confirmation|classification_review|structure_review/);
    expect(isDirty(draft, d0())).toBe(true); expect(isDirty(d0(), d0())).toBe(false);
  });
  it('parseWhole accepts plain whole numbers only', () => {
    expect([parseWhole(''), parseWhole(' 12 '), parseWhole('1.5'), parseWhole('-3'), parseWhole('abc'), parseWhole('12345')]).toEqual([null, 12, undefined, undefined, undefined, undefined]);
  });
});

describe('local validation mirrors the backend rules', () => {
  const codes = (d: any) => validateDraft(d).map(i => i.code);
  it('a valid draft has no issues; totals are reported', () => {
    expect(codes(d0())).toEqual([]);
    expect(requiredTotal(d0().skills)).toBe(100); expect(categoryTotal(d0())).toBe(100);
  });
  it('unbalanced item weights, category totals, missing weights, empty text, structure shapes', () => {
    expect(codes(setItemWeight(d0(), 'req_py', 30))).toContain('required_weights_total');
    expect(codes(setCategoryWeight(d0(), 'skills', 50))).toContain('category_weights_total');
    expect(codes(setItemWeight(d0(), 'req_py', null))).toContain('required_weight_invalid');
    expect(codes(setText(d0(), 'req_py', '  '))).toContain('empty_text');
    expect(codes(setCategoryWeight(d0(), 'education', 5))).toContain('category_weight_without_required_items');
    expect(codes(setCategoryWeight(d0(), 'skills', 0))).toContain('category_weight_not_positive');
  });
  it('an empty or preferred-only draft needs no weights', () => {
    expect(codes(draftFromServer(doc({})))).toEqual([]);
  });
  it('alternatives need two non-empty entries; experience needs a subject or a whole-number duration', () => {
    const base = d0();
    const withAlt = (alts: string[] | null) => ({ ...base, skills: { ...base.skills, items: base.skills.items.map(i => (i.id === 'req_sql' ? { ...i, alternatives: alts } : i)) } });
    expect(codes(withAlt(['a']))).toContain('bad_alternatives');
    expect(codes(withAlt(['a', '']))).toContain('bad_alternatives');
    expect(codes(withAlt(['a', 'b']))).toEqual([]);
    const withExp = (e: any) => ({ ...base, experience: { ...base.experience, items: base.experience.items.map(i => (i.id === 'req_exp' ? { ...i, experience: e } : i)) } });
    expect(codes(withExp({ subject: null, min_years: null }))).toContain('bad_experience');
    expect(codes(withExp({ subject: '', min_years: 2 }))).toContain('bad_experience');
    expect(codes(withExp({ subject: 'x', min_years: null }))).toEqual([]);
  });
});

describe('comparison with the original and API errors', () => {
  it('marks changed, added and removed items', () => {
    let d = setText(d0(), 'req_py', 'Python 3');
    d = removeItem(d, 'req_docker');
    d = addItem(d, 'education', 'preferred').draft;
    const cmp = compareWithOriginal(d, weightedDoc());
    expect(cmp.skills.current.find(x => x.item.key === 'req_py')).toMatchObject({ kind: 'changed', fields: ['text'] });
    expect(cmp.skills.removed.map(r => r.id)).toEqual(['req_docker']);
    expect(cmp.education.current[0].kind).toBe('added');
    expect(compareWithOriginal(d0(), weightedDoc()).skills.current.every(x => x.kind === 'same')).toBe(true);
  });
  it('describeApiError reads the structured detail the backend sends', () => {
    const p = describeApiError(Object.assign(new Error('m'), { status: 422, data: { detail: { code: 'invalid_requirements', message: 'x', issues: [{ code: 'a', message: 'b', category: 'skills', item_id: null }] } } }));
    expect(p).toMatchObject({ status: 422, code: 'invalid_requirements', message: 'x' }); expect(p.issues).toHaveLength(1);
    expect(describeApiError(new Error('boom'))).toMatchObject({ status: null, code: null, message: 'boom', issues: [] });
  });
  it('covers all seven categories', () => { expect(CATEGORIES).toHaveLength(7); });
});
