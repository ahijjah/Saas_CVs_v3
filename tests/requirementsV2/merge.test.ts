import { describe, expect, it } from 'vitest';
import { doc, item, weightedDoc } from './fixtures';
import { CATEGORIES, Draft, draftFromServer, removeItem, setCategoryWeight, setImportance, setItemWeight, setText, setAlternatives, addItem } from '../../utils/requirementsV2';
import { threeWayMerge } from '../../utils/requirementsMerge';

const base = (): Draft => draftFromServer(weightedDoc());
const texts = (d: Draft, c: any = 'skills') => d[c].items.map((i: any) => i.text);

describe('three-way merge', () => {
  it('keeps both sides when different fields / items changed; nothing to decide', () => {
    const mine = setText(base(), 'req_py', 'Python (mine)');
    const theirs = setText(base(), 'req_docker', 'Docker (theirs)');
    const m = threeWayMerge(base(), mine, theirs);
    expect(m.conflicts).toEqual([]);
    expect(m.keptFromMine.map(c => c.what)).toEqual(['text']); expect(m.takenFromLatest.map(c => c.what)).toEqual(['text']);
    expect(texts(m.build({}))).toEqual(['Python (mine)', 'SQL or PostgreSQL', 'Docker (theirs)']);
  });
  it('different fields of the SAME item merge field-wise', () => {
    const mine = setText(base(), 'req_py', 'Python 3');
    const theirs = setItemWeight(setItemWeight(base(), 'req_py', 70), 'req_sql', 30);
    const out = threeWayMerge(base(), mine, theirs).build({});
    expect(out.skills.items.slice(0, 2).map(i => [i.text, i.weight])).toEqual([['Python 3', 70], ['SQL or PostgreSQL', 30]]);
  });
  it('the same field changed differently is a conflict that needs a choice; build refuses to guess', () => {
    const m = threeWayMerge(base(), setText(base(), 'req_py', 'mine'), setText(base(), 'req_py', 'theirs'));
    expect(m.conflicts).toHaveLength(1);
    expect(m.conflicts[0]).toMatchObject({ kind: 'field', field: 'text', base: 'Python', mine: 'mine', latest: 'theirs', itemId: 'req_py' });
    expect(() => m.build({})).toThrow();
    expect(texts(m.build({ [m.conflicts[0].key]: 'mine' }))[0]).toBe('mine');
    expect(texts(m.build({ [m.conflicts[0].key]: 'latest' }))[0]).toBe('theirs');
  });
  it('the same change on both sides is not a conflict', () => {
    const m = threeWayMerge(base(), setText(base(), 'req_py', 'same'), setText(base(), 'req_py', 'same'));
    expect(m.conflicts).toEqual([]); expect(texts(m.build({}))[0]).toBe('same');
  });
  it('category weights: conflict only when both changed differently', () => {
    const both = threeWayMerge(base(), setCategoryWeight(base(), 'skills', 55), setCategoryWeight(base(), 'skills', 70));
    expect(both.conflicts.map(c => c.key)).toEqual(['cw:skills']);
    expect(both.build({ 'cw:skills': 'latest' }).skills.weight).toBe(70);
    const one = threeWayMerge(base(), setCategoryWeight(base(), 'skills', 55), setCategoryWeight(base(), 'experience', 30));
    expect(one.conflicts).toEqual([]);
    const out = one.build({}); expect([out.skills.weight, out.experience.weight]).toEqual([55, 30]);
  });
  it('importance conflicts and the no-weight rule for preferred items', () => {
    const mine = setImportance(base(), 'req_py', 'preferred');
    const theirs = setItemWeight(base(), 'req_py', 20);
    const m = threeWayMerge(base(), mine, theirs);
    expect(m.conflicts.map(c => c.field)).toEqual(['weight']);                                                // importance changed only by me
    const out = m.build({ [m.conflicts[0].key]: 'latest' });
    expect(out.skills.items[0]).toMatchObject({ importance: 'preferred', weight: null });                    // preferred never keeps a weight
    const both = threeWayMerge(base(), mine, setImportance(base(), 'req_py', 'preferred'));                   // same change on both sides
    expect(both.conflicts).toEqual([]);
    const clash = threeWayMerge(base(), setImportance(setImportance(base(), 'req_docker', 'required'), 'req_docker', 'required'), setImportance(base(), 'req_docker', 'required'));
    expect(clash.conflicts).toEqual([]);
  });
  it('items added on either side are kept; deletions without a competing edit apply', () => {
    let mine = addItem(base(), 'education', 'preferred').draft; mine = removeItem(mine, 'req_docker');
    let theirs = base(); theirs = { ...theirs, certifications: { ...theirs.certifications, items: [{ ...addItem(base(), 'certifications', 'preferred').draft.certifications.items[0], id: 'req_new_theirs', key: 'req_new_theirs', text: 'PMP' }] } };
    theirs = removeItem(theirs, 'req_resp');
    const m = threeWayMerge(base(), mine, theirs);
    expect(m.conflicts).toEqual([]);
    const out = m.build({});
    expect(texts(out)).toEqual(['Python', 'SQL or PostgreSQL']);                                             // my deletion
    expect(texts(out, 'experience')).toEqual(['4 years as a Maintenance Planner']);                          // their deletion
    expect(texts(out, 'certifications')).toEqual(['PMP']); expect(out.education.items).toHaveLength(1);
    expect(out.education.items[0].id).toBeNull();
  });
  it('I deleted, they edited: conflict; "delete" removes it, "latest" keeps their version', () => {
    const m = threeWayMerge(base(), removeItem(base(), 'req_docker'), setText(base(), 'req_docker', 'Docker upstream'));
    expect(m.conflicts).toHaveLength(1); expect(m.conflicts[0]).toMatchObject({ kind: 'deleted_by_me', mineDeleted: true });
    expect(texts(m.build({ [m.conflicts[0].key]: 'mine' }))).toEqual(['Python', 'SQL or PostgreSQL']);
    expect(texts(m.build({ [m.conflicts[0].key]: 'latest' }))).toEqual(['Python', 'SQL or PostgreSQL', 'Docker upstream']);
  });
  it('they deleted, I edited: conflict; "mine" re-adds my version as a NEW item (the deleted id is never reused)', () => {
    const m = threeWayMerge(base(), setText(base(), 'req_docker', 'Docker mine'), removeItem(base(), 'req_docker'));
    expect(m.conflicts[0]).toMatchObject({ kind: 'deleted_in_latest', latestDeleted: true });
    const kept = m.build({ [m.conflicts[0].key]: 'mine' });
    const re = kept.skills.items.find(i => i.text === 'Docker mine')!;
    expect(re).toMatchObject({ id: null, origin: 'recruiter_added', source_text: null, importance: 'preferred', weight: null });
    expect(re.key).not.toBe('req_docker');
    expect(texts(m.build({ [m.conflicts[0].key]: 'latest' }))).toEqual(['Python', 'SQL or PostgreSQL']);
  });
  it('both deleted → gone; edit vs delete of an item I only structurally changed counts as an edit', () => {
    expect(texts(threeWayMerge(base(), removeItem(base(), 'req_docker'), removeItem(base(), 'req_docker')).build({}))).toEqual(['Python', 'SQL or PostgreSQL']);
    const m = threeWayMerge(base(), setAlternatives(base(), 'req_sql', ['SQL', 'PostgreSQL', 'MySQL']), removeItem(base(), 'req_sql'));
    expect(m.conflicts[0].kind).toBe('deleted_in_latest');
  });
  it('never mutates its inputs and never touches weights on its own', () => {
    const b = base(), mine = setText(b, 'req_py', 'x'), theirs = setItemWeight(b, 'req_sql', 40);
    const snap = JSON.stringify([b, mine, theirs]);
    const out = threeWayMerge(b, mine, theirs).build({});
    expect(JSON.stringify([b, mine, theirs])).toBe(snap);
    expect(out.skills.items.map(i => i.weight)).toEqual([50, 40, null]);                                   // no redistribution
    expect(CATEGORIES.map(c => out[c].weight)).toEqual([60, 40, 0, 0, 0, 0, 0]);
  });
  it('an unchanged draft merges to the latest version exactly', () => {
    const theirs = setText(base(), 'req_py', 'Python upstream');
    expect(JSON.stringify(threeWayMerge(base(), base(), theirs).build({}))).toBe(JSON.stringify(theirs));
  });
  it('empty documents merge', () => {
    const e = draftFromServer(doc({}));
    expect(threeWayMerge(e, e, e).conflicts).toEqual([]);
    expect(item('a', 'b', 'required', 1).id).toBe('a');
  });
});
