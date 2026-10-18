import { describe, expect, it } from 'vitest';
import { scoringBlockedByRequirements, buildFindingOperation, buildOperations, buildReorderOperation, findingProblem, moveItem, acknowledgeWarning, confirmStructure, CONFIRM_NO_NUMERIC_SCORE } from '../../services/requirementsEdits';
import { ids, view } from './fixtures';

const none = {};

describe('operation payloads', () => {
  it('sends nothing for an unchanged document', () => {
    expect(buildOperations(view(), none, {}, {}, [])).toEqual([]);
  });

  it('sends a text change only when the trimmed text differs and is not empty', () => {
    const v = view();
    expect(buildOperations(v, { [ids.py]: { text: '  Python 3  ' } }, {}, {}, [])).toEqual([
      { op: 'set_text', item_id: ids.py, text: 'Python 3' }]);
    expect(buildOperations(v, { [ids.py]: { text: '   ' } }, {}, {}, [])).toEqual([]);
  });

  it('sends a weight only for a required item', () => {
    const v = view();
    expect(buildOperations(v, { [ids.py]: { weight: 60 } }, {}, {}, [])).toEqual([
      { op: 'set_item_weight', item_id: ids.py, weight: 60 }]);
    expect(buildOperations(v, { [ids.py]: { importance: 'preferred', weight: 60 } }, {}, {}, [])).toEqual([
      { op: 'set_importance', item_id: ids.py, importance: 'preferred' }]);
  });

  it('drops a draft for an item that is no longer in the document', () => {
    expect(buildOperations(view(), { req_gone: { removed: true } }, {}, {}, [])).toEqual([]);
  });

  it('sends a category weight and a reorder only when they differ', () => {
    const v = view();
    expect(buildOperations(v, none, { skills: 90 }, {}, [])).toEqual([{ op: 'set_category_weight', category: 'skills', weight: 90 }]);
    expect(buildOperations(v, none, {}, { skills: [ids.sql, ids.py] }, [])).toEqual([
      { op: 'reorder_items', category: 'skills', item_ids: [ids.sql, ids.py] }]);
    expect(buildOperations(v, none, {}, { skills: [ids.py, ids.sql] }, [])).toEqual([]);
  });

  it('never sends a hash, timestamp, identity or server-owned block in any payload', () => {
    const v = view();
    const ops = [
      ...buildOperations(v, { [ids.py]: { text: 'x' } }, { skills: 90 }, { skills: [ids.sql, ids.py] },
        [{ key: 'n', category: 'skills', text: 'Docker', importance: 'required' }]),
      CONFIRM_NO_NUMERIC_SCORE,
      acknowledgeWarning('w-1'),
      confirmStructure(ids.py),
      buildFindingOperation('f-1', { decision: 'accepted_as_is', note: 'ok', evidence: [] }),
    ];
    const text = JSON.stringify(ops);
    for (const forbidden of ['basis_hash', 'confirmed_at', 'user_id', 'reviewer', 'acknowledged_at', 'resolved_at', 'recorded_at', 'structure_review', 'classification_review']) {
      expect(text).not.toContain(forbidden);
    }
  });
});

describe('reordering', () => {
  it('moves one item and keeps the order a permutation', () => {
    expect(moveItem([ids.py, ids.sql], 0, 1)).toEqual([ids.sql, ids.py]);
    expect(moveItem([ids.py, ids.sql], 0, -1)).toEqual([ids.py, ids.sql]);
    expect(moveItem([ids.py, ids.sql], 1, 1)).toEqual([ids.py, ids.sql]);
  });

  it('refuses an order that omits, duplicates or adds an id', () => {
    const v = view();
    expect(() => buildReorderOperation(v, 'skills', [ids.py])).toThrow();
    expect(() => buildReorderOperation(v, 'skills', [ids.py, ids.py])).toThrow();
    expect(() => buildReorderOperation(v, 'skills', [ids.py, 'req_other000000'])).toThrow();
  });
});

describe('review decisions', () => {
  it('requires evidence for a fixed finding and a note for any other decision', () => {
    expect(findingProblem({ decision: 'fixed', note: '', evidence: [] })).not.toBeNull();
    expect(findingProblem({ decision: 'fixed', note: '', evidence: [ids.py] })).toBeNull();
    expect(findingProblem({ decision: 'accepted_as_is', note: '  ', evidence: [] })).not.toBeNull();
    expect(findingProblem({ decision: 'accepted_as_is', note: 'Both tools', evidence: [] })).toBeNull();
  });

  it('builds a fixed resolution with evidence and no note unless one was written', () => {
    expect(buildFindingOperation('f-1', { decision: 'fixed', note: '', evidence: [ids.py] })).toEqual({
      op: 'resolve_structural_finding', finding_id: 'f-1', decision: 'fixed', note: null, evidence_item_ids: [ids.py] });
    expect(buildFindingOperation('f-1', { decision: 'dismissed_false_positive', note: 'Not an issue', evidence: [ids.py] })).toEqual({
      op: 'resolve_structural_finding', finding_id: 'f-1', decision: 'dismissed_false_positive', note: 'Not an issue',
      evidence_item_ids: null });
  });
});

describe('scoring gate', () => {
  it('blocks scoring of a requirements-v2 job until its document is ready, and never blocks a legacy job', () => {
    expect(scoringBlockedByRequirements(true, false)).toBe(true);
    expect(scoringBlockedByRequirements(true, true)).toBe(false);
    expect(scoringBlockedByRequirements(false, false)).toBe(false);
  });
});
