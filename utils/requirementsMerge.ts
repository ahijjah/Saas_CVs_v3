// Three-way merge of requirements drafts (pure; no React, no network).
//
//   base    the version the recruiter's draft was started from ("original loaded version")
//   mine    the recruiter's current draft
//   theirs  the latest SAVED version on the server (what someone else saved meanwhile)
//
// Rule per value (a field of an item, a category weight, the presence of an item):
//   changed only by me      -> keep mine            (kept from the draft)
//   changed only by them    -> take theirs          (taken from the latest)
//   changed to the same     -> that value
//   changed differently     -> CONFLICT: the recruiter chooses; nothing is decided automatically
// Items are matched by id. An item the recruiter added (no id) is kept; an item only the latest has is taken.
// A deletion on one side against an edit on the other is a conflict. Nothing here saves or resubmits anything, and it
// never redistributes weights (the merged draft goes through the normal validation and the server's revision check).
import {
  CATEGORIES, CategoryKey, Draft, DraftItem, ExperienceStructure, newKey,
} from './requirementsV2';

export type ItemField = 'text' | 'importance' | 'weight' | 'alternatives' | 'experience';
export const ITEM_FIELDS: ItemField[] = ['text', 'importance', 'weight', 'alternatives', 'experience'];

export type Choice = 'mine' | 'latest';

export interface MergeConflict {
  key: string;                                   // stable id used for the recruiter's choice
  kind: 'category_weight' | 'field' | 'deleted_by_me' | 'deleted_in_latest';
  category: CategoryKey;
  itemId: string | null;
  itemLabel: string;                             // wording to identify the item
  field: ItemField | null;
  base: unknown; mine: unknown; latest: unknown; // raw values (null + `*Deleted` flags for deletions)
  mineDeleted?: boolean; latestDeleted?: boolean;
}

export interface MergeChange { category: CategoryKey; itemLabel: string; what: string; from: 'mine' | 'latest' }

export interface MergeAnalysis {
  conflicts: MergeConflict[];
  keptFromMine: MergeChange[];
  takenFromLatest: MergeChange[];
  /** Build the merged draft. Throws if a conflict has no choice (callers keep Apply disabled until all are chosen). */
  build(choices: Record<string, Choice | undefined>): Draft;
}

const eq = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
const idOf = (i: DraftItem) => i.id as string;

const fieldOf = (i: DraftItem, f: ItemField): unknown =>
  f === 'weight' ? (i.importance === 'preferred' ? null : i.weight) : (i as any)[f];

function byId(d: Draft): Map<string, { category: CategoryKey; item: DraftItem }> {
  const m = new Map<string, { category: CategoryKey; item: DraftItem }>();
  for (const c of CATEGORIES) for (const item of d[c].items) if (item.id) m.set(item.id, { category: c, item });
  return m;
}

const clone = (i: DraftItem): DraftItem => ({
  ...i, alternatives: i.alternatives ? [...i.alternatives] : null,
  experience: i.experience ? ({ ...i.experience } as ExperienceStructure) : null,
});

export function threeWayMerge(base: Draft, mine: Draft, theirs: Draft): MergeAnalysis {
  const B = byId(base), M = byId(mine), T = byId(theirs);
  const conflicts: MergeConflict[] = [];
  const keptFromMine: MergeChange[] = [];
  const takenFromLatest: MergeChange[] = [];
  const label = (i: DraftItem | undefined, fallback = '') => (i && i.text.trim()) || fallback || '…';

  // category weights
  const cwPick: Partial<Record<CategoryKey, { kind: 'mine' | 'latest' | 'same' | 'conflict' | 'none' }>> = {};
  for (const c of CATEGORIES) {
    const b = base[c].weight, m = mine[c].weight, t = theirs[c].weight;
    if (eq(m, t)) cwPick[c] = { kind: eq(m, b) ? 'none' : 'same' };
    else if (eq(m, b)) { cwPick[c] = { kind: 'latest' }; takenFromLatest.push({ category: c, itemLabel: '', what: 'category_weight', from: 'latest' }); }
    else if (eq(t, b)) { cwPick[c] = { kind: 'mine' }; keptFromMine.push({ category: c, itemLabel: '', what: 'category_weight', from: 'mine' }); }
    else {
      cwPick[c] = { kind: 'conflict' };
      conflicts.push({ key: `cw:${c}`, kind: 'category_weight', category: c, itemId: null, itemLabel: '', field: null, base: b, mine: m, latest: t });
    }
  }

  // per existing item: decide presence and per-field values
  type Decision = { present: 'mine' | 'latest' | 'conflict' | 'gone'; fields: Partial<Record<ItemField, 'mine' | 'latest' | 'conflict'>>; conflictKey?: string };
  const decisions = new Map<string, Decision>();
  const allIds = new Set<string>([...B.keys(), ...T.keys(), ...M.keys()].filter(id => B.has(id)));   // items that existed in the base
  for (const id of allIds) {
    const b = B.get(id)!, m = M.get(id), t = T.get(id);
    const itemLabel = label(m?.item ?? t?.item ?? b.item, b.item.text);
    const category = b.category;
    const changedBy = (x: { item: DraftItem } | undefined) => !!x && ITEM_FIELDS.some(f => !eq(fieldOf(x.item, f), fieldOf(b.item, f)));
    if (!m && !t) { decisions.set(id, { present: 'gone', fields: {} }); continue; }
    if (!m && t) {                                  // I deleted it
      if (!changedBy(t)) { decisions.set(id, { present: 'gone', fields: {} }); keptFromMine.push({ category, itemLabel, what: 'deleted', from: 'mine' }); }
      else {
        const key = `item:${id}:deleted_by_me`;
        conflicts.push({ key, kind: 'deleted_by_me', category, itemId: id, itemLabel, field: null, base: b.item, mine: null, latest: t.item, mineDeleted: true });
        decisions.set(id, { present: 'conflict', fields: {}, conflictKey: key });
      }
      continue;
    }
    if (m && !t) {                                  // someone else deleted it
      if (!changedBy(m)) { decisions.set(id, { present: 'gone', fields: {} }); takenFromLatest.push({ category, itemLabel, what: 'deleted', from: 'latest' }); }
      else {
        const key = `item:${id}:deleted_in_latest`;
        conflicts.push({ key, kind: 'deleted_in_latest', category, itemId: id, itemLabel, field: null, base: b.item, mine: m.item, latest: null, latestDeleted: true });
        decisions.set(id, { present: 'conflict', fields: {}, conflictKey: key });
      }
      continue;
    }
    // present on both sides: field-wise
    const fields: Decision['fields'] = {};
    for (const f of ITEM_FIELDS) {
      const bv = fieldOf(b.item, f), mv = fieldOf(m!.item, f), tv = fieldOf(t!.item, f);
      if (eq(mv, tv)) fields[f] = 'mine';
      else if (eq(mv, bv)) { fields[f] = 'latest'; takenFromLatest.push({ category, itemLabel, what: f, from: 'latest' }); }
      else if (eq(tv, bv)) { fields[f] = 'mine'; keptFromMine.push({ category, itemLabel, what: f, from: 'mine' }); }
      else {
        fields[f] = 'conflict';
        conflicts.push({ key: `item:${id}:${f}`, kind: 'field', category, itemId: id, itemLabel, field: f, base: bv, mine: mv, latest: tv });
      }
    }
    decisions.set(id, { present: 'mine', fields });
  }
  for (const [id, t] of T) if (!B.has(id) && !M.has(id))
    takenFromLatest.push({ category: t.category, itemLabel: label(t.item), what: 'added', from: 'latest' });
  for (const c of CATEGORIES) for (const i of mine[c].items) if (!i.id) keptFromMine.push({ category: c, itemLabel: label(i), what: 'added', from: 'mine' });

  // stable order: category order, then the order conflicts were found
  const order = new Map(CATEGORIES.map((c, n) => [c, n]));
  conflicts.sort((a, b) => (order.get(a.category)! - order.get(b.category)!));

  const build = (choices: Record<string, Choice | undefined>): Draft => {
    const need = (key: string): Choice => {
      const c = choices[key];
      if (!c) throw new Error(`conflict ${key} has no choice`);
      return c;
    };
    const out = {} as Draft;
    for (const c of CATEGORIES) {
      const pick = cwPick[c]!;
      const weight = pick.kind === 'conflict' ? (need(`cw:${c}`) === 'mine' ? mine[c].weight : theirs[c].weight)
        : pick.kind === 'latest' ? theirs[c].weight : mine[c].weight;
      const items: DraftItem[] = [];
      // existing items follow the latest version's order (what everyone else sees), then new latest-only items
      for (const t of theirs[c].items) {
        const id = idOf(t);
        const d = decisions.get(id);
        if (!d) { items.push(clone(t)); continue; }                           // added by someone else (not in my base)
        if (d.present === 'gone') continue;
        if (d.present === 'conflict') {
          const ch = need(d.conflictKey!);
          if (d.conflictKey!.endsWith('deleted_by_me')) { if (ch === 'latest') items.push(clone(t)); }
          else items.push(ch === 'mine' ? reAdd(M.get(id)!.item) : null as any);
          continue;
        }
        const mi = M.get(id)!.item, item = clone(t);
        for (const f of ITEM_FIELDS) {
          const how = d.fields[f]!;
          const useMine = how === 'mine' || (how === 'conflict' && need(`item:${id}:${f}`) === 'mine');
          if (useMine && f !== 'weight') (item as any)[f] = f === 'alternatives' ? (mi.alternatives ? [...mi.alternatives] : null)
            : f === 'experience' ? (mi.experience ? { ...mi.experience } : null) : (mi as any)[f];
          if (f === 'weight') item.weight = useMine ? (mi.importance === 'preferred' ? null : mi.weight) : (t.importance === 'preferred' ? null : t.weight);
        }
        if (item.importance === 'preferred') item.weight = null;
        items.push(item);
      }
      // items the recruiter deleted-in-latest-but-edited choose "mine": they were pushed above as re-added only if still in theirs;
      // for deleted_in_latest the id is NOT in theirs, so add them here
      for (const mi of mine[c].items) {
        if (!mi.id) { items.push(clone(mi)); continue; }                       // new in my draft: keep
        const d = decisions.get(mi.id);
        if (d && d.present === 'conflict' && d.conflictKey!.endsWith('deleted_in_latest') && !theirs[c].items.some(x => x.id === mi.id)
            && need(d.conflictKey!) === 'mine') items.push(reAdd(mi));
      }
      out[c] = { weight, items: items.filter(Boolean) };
    }
    return out;
  };

  return { conflicts, keptFromMine, takenFromLatest, build };
}

/** Keeping my edited version of an item that someone else deleted: the server will not accept the deleted id again, so it
 *  is re-created as a new item with my wording and structure (provenance becomes "recruiter added"). */
function reAdd(item: DraftItem): DraftItem {
  return { ...clone(item), key: newKey(), id: null, origin: 'recruiter_added', source_text: null };
}
