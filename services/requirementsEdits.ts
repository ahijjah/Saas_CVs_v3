// Pure request composition for the requirements panel. Every operation the panel sends is built here, so the payloads, the reorder
// rule and the decision rules are testable without rendering. Nothing built here carries a hash, a timestamp or an identity: the
// server supplies those, and the parser refuses them.

import type { ImportanceLevel, RequirementsItem, RequirementsOperation, RequirementsView } from './requirementsApi';
import { draftFromItem, sameGate } from '../utils/gates';
import type { GateDraft } from '../utils/gates';

export const FINDING_DECISIONS = ['fixed', 'accepted_as_is', 'dismissed_false_positive'] as const;
export type FindingDecision = (typeof FINDING_DECISIONS)[number];

export interface ItemDraft {
  text?: string;
  importance?: ImportanceLevel;
  weight?: number | null;
  removed?: boolean;
  /** undefined: no change. null: remove the gate. A draft: confirm (or change) the Mandatory Gate. */
  gate?: GateDraft | null;
}

export interface NewItemDraft {
  key: string;
  category: string;
  text: string;
  importance: ImportanceLevel;
}

export interface FindingDraft {
  decision: FindingDecision;
  note: string;
  evidence: string[];
}

export function findItem(view: RequirementsView, itemId: string): { category: string; item: RequirementsItem } | null {
  for (const [category, block] of Object.entries(view.document.categories)) {
    const item = block.items.find((i) => i.id === itemId);
    if (item) return { category, item };
  }
  return null;
}

/** The category's item ids in the order the document currently holds them. */
export function currentOrder(view: RequirementsView, category: string): string[] {
  return (view.document.categories[category]?.items ?? []).map((i) => i.id);
}

/** Move one item one place up or down. The result is always a permutation of the input. Out-of-range moves return the input. */
export function moveItem(ids: string[], index: number, direction: -1 | 1): string[] {
  const target = index + direction;
  if (index < 0 || index >= ids.length || target < 0 || target >= ids.length) return [...ids];
  const next = [...ids];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** A reorder operation, only when the new order differs from the document's. The order must name every current item exactly once. */
export function buildReorderOperation(view: RequirementsView, category: string, orderedIds: string[]): RequirementsOperation | null {
  const current = currentOrder(view, category);
  if (orderedIds.length !== current.length || new Set(orderedIds).size !== orderedIds.length
      || !orderedIds.every((id) => current.includes(id))) {
    throw new Error('The new order must name every requirement in the category exactly once.');
  }
  if (orderedIds.every((id, index) => id === current[index])) return null;
  return { op: 'reorder_items', category, item_ids: orderedIds };
}

export function buildOperations(
  view: RequirementsView,
  itemDrafts: Record<string, ItemDraft>,
  categoryDrafts: Record<string, number>,
  orderDrafts: Record<string, string[]>,
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
    if (draft.text !== undefined && draft.text.trim() !== item.text && draft.text.trim() !== '') {
      ops.push({ op: 'set_text', item_id: itemId, text: draft.text.trim() });
    }
    if (draft.importance !== undefined && draft.importance !== item.importance) {
      ops.push({ op: 'set_importance', item_id: itemId, importance: draft.importance });
    }
    const effectiveImportance = draft.importance ?? item.importance;
    if (draft.weight !== undefined && effectiveImportance === 'required' && draft.weight !== item.weight) {
      ops.push({ op: 'set_item_weight', item_id: itemId, weight: draft.weight });
    }
    // A Mandatory Gate belongs to Required items only. When the item becomes Preferred the server removes the gate itself, so nothing is sent.
    if (draft.gate !== undefined && effectiveImportance === 'required') {
      const current = draftFromItem(item);
      if (draft.gate === null) {
        if (current) ops.push({ op: 'clear_gate', item_id: itemId });
      } else if (!current || !sameGate(draft.gate, current)) {
        ops.push({ op: 'set_gate', item_id: itemId, verification_method: draft.gate.verification_method,
                   linked_question_id: draft.gate.verification_method === 'candidate_question' ? draft.gate.linked_question_id : null });
      }
    }
  }

  for (const [category, weight] of Object.entries(categoryDrafts)) {
    const current = view.document.categories[category]?.weight;
    if (current !== undefined && weight !== current) {
      ops.push({ op: 'set_category_weight', category, weight });
    }
  }

  for (const [category, ordered] of Object.entries(orderDrafts)) {
    const reorder = buildReorderOperation(view, category, ordered);
    if (reorder) ops.push(reorder);
  }

  for (const added of newItems) {
    const text = added.text.trim();
    if (!text) continue;                                    // an empty new item is not sent
    ops.push({
      op: 'add_item', category: added.category, text, importance: added.importance,
      weight: null, alternatives: null, experience: null,
    });
  }

  return ops;
}

/** Client-side checks that mirror the server's rules, so an obviously invalid decision is not sent. The server still decides. */
export function findingProblem(finding: FindingDraft): string | null {
  if (finding.decision === 'fixed') {
    return finding.evidence.length > 0 ? null : 'Choose the requirement(s) that now carry the correct structure.';
  }
  return finding.note.trim() ? null : 'Write a short note explaining this decision.';
}

export function buildFindingOperation(findingId: string, finding: FindingDraft): RequirementsOperation {
  const note = finding.note.trim();
  return {
    op: 'resolve_structural_finding',
    finding_id: findingId,
    decision: finding.decision,
    note: finding.decision === 'fixed' ? (note || null) : note,
    evidence_item_ids: finding.decision === 'fixed' ? [...finding.evidence] : null,
  };
}

export const CONFIRM_NO_NUMERIC_SCORE: RequirementsOperation = { op: 'confirm_no_numeric_score' };

export function acknowledgeWarning(warningId: string): RequirementsOperation {
  return { op: 'acknowledge_classification_warning', warning_id: warningId };
}

export function confirmStructure(itemId: string): RequirementsOperation {
  return { op: 'confirm_structure', item_id: itemId };
}

/** Whether the candidate-scoring action must stay disabled. A requirements-v2 job cannot be scored until its document is ready. */
export function scoringBlockedByRequirements(isRequirementsV2: boolean, requirementsCanProceed: boolean): boolean {
  return isRequirementsV2 && !requirementsCanProceed;
}
