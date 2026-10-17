// Pure edit planning for the requirements panel: turns the recruiter's local drafts into the typed operations the API accepts.
// Nothing is sent unless it differs from the loaded document, and a weight is only ever sent for a required item, because preferred
// items carry no weight.

import type { ImportanceLevel, RequirementsItem, RequirementsOperation, RequirementsView } from './requirementsApi';

export interface ItemDraft {
  text?: string;
  importance?: ImportanceLevel;
  weight?: number | null;
  removed?: boolean;
}

export interface NewItemDraft {
  key: string;
  category: string;
  text: string;
  importance: ImportanceLevel;
  weight: number | null;
}

export type CategoryWeightDrafts = Record<string, number>;

export function findItem(view: RequirementsView, itemId: string): { category: string; item: RequirementsItem } | null {
  for (const [category, block] of Object.entries(view.document.categories)) {
    const item = block.items.find((i) => i.id === itemId);
    if (item) return { category, item };
  }
  return null;
}

export function buildOperations(
  view: RequirementsView,
  itemDrafts: Record<string, ItemDraft>,
  categoryDrafts: CategoryWeightDrafts,
  newItems: NewItemDraft[],
): RequirementsOperation[] {
  const ops: RequirementsOperation[] = [];

  for (const [itemId, draft] of Object.entries(itemDrafts)) {
    const found = findItem(view, itemId);
    if (!found) continue;                                   // a draft for an item that is no longer there is dropped, never sent
    const { item } = found;
    if (draft.removed) {
      ops.push({ op: 'remove_item', item_id: itemId });
      continue;
    }
    if (draft.text !== undefined && draft.text.trim() !== item.text) {
      ops.push({ op: 'set_text', item_id: itemId, text: draft.text.trim() });
    }
    if (draft.importance !== undefined && draft.importance !== item.importance) {
      ops.push({ op: 'set_importance', item_id: itemId, importance: draft.importance });
    }
    const effectiveImportance = draft.importance ?? item.importance;
    if (draft.weight !== undefined && effectiveImportance === 'required' && draft.weight !== item.weight) {
      ops.push({ op: 'set_item_weight', item_id: itemId, weight: draft.weight });
    }
  }

  for (const [category, weight] of Object.entries(categoryDrafts)) {
    const current = view.document.categories[category]?.weight;
    if (current !== undefined && weight !== current) {
      ops.push({ op: 'set_category_weight', category, weight });
    }
  }

  for (const added of newItems) {
    const text = added.text.trim();
    if (!text) continue;                                    // an empty new item is not sent
    ops.push({
      op: 'add_item',
      category: added.category,
      text,
      importance: added.importance,
      weight: added.importance === 'required' ? added.weight : null,
      alternatives: null,
      experience: null,
    });
  }

  return ops;
}

export function hasChanges(ops: RequirementsOperation[]): boolean {
  return ops.length > 0;
}
