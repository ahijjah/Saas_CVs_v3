import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  canEditRequirements, getRequirements, ImportanceLevel, RequirementsApiError, RequirementsCategory, RequirementsOperation,
  RequirementsView, saveRequirementEdits,
} from '../services/requirementsApi';
import {
  acknowledgeWarning, buildFindingOperation, buildOperations, buildReorderOperation, CONFIRM_NO_NUMERIC_SCORE, confirmStructure,
  currentOrder, FindingDecision, FindingDraft, findingProblem, findItem, FINDING_DECISIONS, ItemDraft, moveItem, NewItemDraft,
} from '../services/requirementsEdits';

const CATEGORY_LABELS: Record<string, string> = {
  skills: 'Skills',
  experience: 'Experience',
  education: 'Education',
  certifications: 'Certifications',
  soft_skills: 'Soft skills',
  domain_knowledge: 'Domain knowledge',
  other_requirements: 'Other requirements',
};

const READINESS_LABELS: Record<string, string> = {
  ready: 'Ready for scoring',
  needs_items: 'Needs requirements',
  needs_confirmation: 'Needs confirmation',
  needs_review: 'Needs review',
  needs_classification_review: 'Needs classification review',
  needs_structure_review: 'Needs structure review',
  needs_structural_review: 'Needs structural review',
};

const DECISION_LABELS: Record<FindingDecision, string> = {
  fixed: 'Fixed: the requirements now have the correct structure',
  accepted_as_is: 'Accepted as is',
  dismissed_false_positive: 'Not a real issue',
};

type Notice = { kind: 'ok' | 'error'; text: string } | null;

// The approved API reads requirements for administrators and recruiters only. Any other role is refused, and sees this fixed text.
const REFUSED_TEXT = 'The requirements for this job are available to administrators and recruiters only.';

interface Props {
  jobId: string;
  userRole: string | null | undefined;
  onReadinessChange?: (canProceed: boolean) => void;
  // The job's requirements extraction status. The panel reloads on every change, so a completed or retried extraction
  // replaces the waiting state without a browser refresh.
  extractionStatus?: string | null;
}

const EXTRACTION_IN_PROGRESS = new Set(['pending', 'processing']);

let newItemCounter = 0;

export default function RequirementsPanel({ jobId, userRole, onReadinessChange, extractionStatus }: Props) {
  const canEdit = canEditRequirements(userRole);
  const [view, setView] = useState<RequirementsView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [itemDrafts, setItemDrafts] = useState<Record<string, ItemDraft>>({});
  const [categoryDrafts, setCategoryDrafts] = useState<Record<string, number>>({});
  const [orderDrafts, setOrderDrafts] = useState<Record<string, string[]>>({});
  const [findingDrafts, setFindingDrafts] = useState<Record<string, FindingDraft>>({});
  const [newItems, setNewItems] = useState<NewItemDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [conflict, setConflict] = useState<{ currentRevision: number | null } | null>(null);

  const load = useCallback(async (): Promise<RequirementsView | null> => {
    try {
      const next = await getRequirements(jobId);
      setView(next);
      setLoadError(null);
      onReadinessChange?.(next.review.readiness.can_proceed);
      return next;
    } catch (err) {
      setLoadError(err instanceof RequirementsApiError && err.status === 403 ? REFUSED_TEXT
        : err instanceof Error ? err.message : 'The requirements could not be loaded.');
      onReadinessChange?.(false);
      return null;
    }
  }, [jobId, onReadinessChange]);

  // Reload when the extraction status changes (pending -> completed, failed -> pending on retry, and so on).
  const lastStatusRef = useRef(extractionStatus);
  useEffect(() => {
    if (lastStatusRef.current === extractionStatus) return;
    lastStatusRef.current = extractionStatus;
    void load();
  }, [extractionStatus, load]);

  useEffect(() => {
    lastStatusRef.current = extractionStatus;
    setView(null);
    setItemDrafts({});
    setCategoryDrafts({});
    setOrderDrafts({});
    setFindingDrafts({});
    setNewItems([]);
    setNotice(null);
    setConflict(null);
    void load();
  }, [jobId, load]);

  const pendingOps = useMemo(
    () => (view ? buildOperations(view, itemDrafts, categoryDrafts, orderDrafts, newItems) : []),
    [view, itemDrafts, categoryDrafts, orderDrafts, newItems],
  );

  const editItem = (itemId: string, patch: ItemDraft) =>
    setItemDrafts((prev) => ({ ...prev, [itemId]: { ...prev[itemId], ...patch } }));

  /** One save with the given operations at the loaded revision. Success clears the drafts it sent; a conflict keeps everything. */
  const submit = async (ops: RequirementsOperation[], successText: string, sentDrafts: 'all' | 'none' = 'all') => {
    if (!view || ops.length === 0 || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await saveRequirementEdits(jobId, view.requirements_revision, ops);
      if (sentDrafts === 'all') {
        setItemDrafts({});
        setCategoryDrafts({});
        setOrderDrafts({});
        setNewItems([]);
      }
      setNotice({ kind: 'ok', text: `${successText} The requirements are now at revision ${result.requirements_revision}.` });
      await load();
    } catch (err) {
      if (err instanceof RequirementsApiError && err.code === 'revision_conflict') {
        // The document changed under these edits. Nothing is applied and nothing local is dropped: the user chooses what happens next.
        setConflict({ currentRevision: err.currentRevision });
        setNotice(null);
      } else {
        setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'The requirements could not be saved.' });
      }
    } finally {
      setBusy(false);
    }
  };

  /** After a conflict: reapply the same local edits on the current revision, reading that revision first. */
  const reapplyOnCurrent = async () => {
    const latest = await load();
    if (!latest) return;
    setConflict(null);
    const ops = buildOperations(latest, itemDrafts, categoryDrafts, orderDrafts, newItems);
    if (ops.length === 0) {
      setNotice({ kind: 'ok', text: 'The requirements changed and there was nothing left to apply.' });
      return;
    }
    await submitOn(latest, ops);
  };

  const submitOn = async (current: RequirementsView, ops: RequirementsOperation[]) => {
    setBusy(true);
    try {
      const result = await saveRequirementEdits(jobId, current.requirements_revision, ops);
      setItemDrafts({});
      setCategoryDrafts({});
      setOrderDrafts({});
      setNewItems([]);
      setNotice({ kind: 'ok', text: `Your edits were applied to the current requirements. Revision ${result.requirements_revision}.` });
      await load();
    } catch (err) {
      setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'The requirements could not be saved.' });
    } finally {
      setBusy(false);
    }
  };

  if (loadError && !view) {
    const waiting = !!extractionStatus && EXTRACTION_IN_PROGRESS.has(extractionStatus);
    return (
      <section className="bg-white rounded-xl border border-slate-200 p-6" data-testid="requirements-unavailable">
        <p className="text-sm text-slate-700" role="status">
          {waiting ? 'The requirements are being generated. They appear here when extraction completes.' : loadError}
        </p>
        {!waiting && (
          <button type="button" onClick={() => void load()} className="mt-3 text-sm font-bold text-primary underline">Reload requirements</button>
        )}
      </section>
    );
  }
  if (!view) {
    return (
      <section className="bg-white rounded-xl border border-slate-200 p-6">
        <p className="text-sm text-slate-500">Loading requirements…</p>
      </section>
    );
  }

  const { readiness, classification, structural, structure } = view.review;
  const readinessLabel = READINESS_LABELS[readiness.state] ?? 'Not ready';
  const openWarnings = classification.open_warnings.filter((w) => !w.acknowledged);

  return (
    <section className="bg-white rounded-xl border border-slate-200 p-4 sm:p-6 space-y-6" aria-labelledby="requirements-heading">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="requirements-heading" className="text-lg font-semibold text-slate-900">Requirements</h2>
          <p className="text-xs text-slate-500">Revision {view.requirements_revision}</p>
        </div>
        <span
          className={`text-sm font-medium px-3 py-1 rounded-full ${readiness.can_proceed ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800'}`}
          data-testid="readiness-state"
        >
          {readinessLabel}
        </span>
      </header>

      {!readiness.can_proceed && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-900" role="status" data-testid="scoring-blocked">
          <p className="font-medium">Candidate scoring is blocked until the requirements are ready.</p>
          {readiness.reasons.length > 0 && (
            <ul className="list-disc ps-5 mt-2 space-y-1">
              {readiness.reasons.map((reason, index) => (
                <li key={`${reason.code}-${index}`}>{reason.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {conflict && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 space-y-3" role="alert">
          <p className="font-medium">The requirements changed while you were editing.</p>
          <p>
            Your unsaved edits are still here. The current revision is{' '}
            {conflict.currentRevision ?? 'newer than the one you loaded'}.
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="px-3 py-1.5 rounded-lg border border-amber-400 bg-white text-sm"
              onClick={() => { setConflict(null); void load(); }}>
              Reload the current requirements
            </button>
            <button type="button" className="px-3 py-1.5 rounded-lg bg-amber-700 text-white text-sm disabled:opacity-50"
              disabled={busy} onClick={() => void reapplyOnCurrent()}>
              Apply my edits to the current requirements
            </button>
          </div>
        </div>
      )}

      {notice && (
        <p role={notice.kind === 'ok' ? 'status' : 'alert'}
          className={`text-sm rounded-lg p-3 ${notice.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>
          {notice.text}
        </p>
      )}

      {canEdit && readiness.state === 'needs_confirmation' && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm space-y-2">
          <p className="font-medium text-slate-900">No numeric score</p>
          <p className="text-slate-700">
            Every requirement is preferred, so candidates cannot receive a numeric score. Confirm that this is intended.
          </p>
          <button type="button" className="px-3 py-1.5 rounded-lg bg-blue-700 text-white text-sm disabled:opacity-50"
            disabled={busy} onClick={() => void submit([CONFIRM_NO_NUMERIC_SCORE], 'Confirmed: no numeric score.')}>
            Confirm: no numeric score
          </button>
        </div>
      )}

      {openWarnings.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm space-y-3">
          <p className="font-medium text-slate-900">Classification to confirm</p>
          <ul className="space-y-2">
            {openWarnings.map((w) => {
              const found = findItem(view, w.item_id);
              return (
                <li key={w.id} className="flex flex-wrap items-center gap-2 justify-between">
                  <span className="text-slate-700">
                    <span dir="auto">{found ? found.item.text : CATEGORY_LABELS[w.category] ?? w.category}</span>
                    {' '}is marked Preferred, but the job description does not clearly say so.
                  </span>
                  {canEdit && (
                    <button type="button" className="px-3 py-1 rounded-lg border border-slate-300 text-sm disabled:opacity-50"
                      disabled={busy} onClick={() => void submit([acknowledgeWarning(w.id)], 'Classification accepted.')}>
                      Accept this classification
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {structural.open_findings.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm space-y-4">
          <p className="font-medium text-slate-900">Structure to resolve</p>
          {structural.open_findings.map((f) => {
            const draft = findingDrafts[f.id] ?? { decision: 'fixed' as FindingDecision, note: '', evidence: [] };
            const problem = findingProblem(draft);
            return (
              <div key={f.id} className="space-y-2">
                <p className="text-slate-700">{f.message}</p>
                <p dir="auto" className="text-xs text-slate-500">From the job description: “{f.source_text}”</p>
                {canEdit && (
                  <div className="space-y-2">
                    <label className="block">
                      <span className="sr-only">Decision for this finding</span>
                      <select aria-label="Decision for this finding" className="border border-slate-300 rounded px-2 py-1 w-full sm:w-auto"
                        value={draft.decision}
                        onChange={(e) => setFindingDrafts((prev) => ({ ...prev, [f.id]: { ...draft, decision: e.target.value as FindingDecision } }))}>
                        {FINDING_DECISIONS.map((d) => <option key={d} value={d}>{DECISION_LABELS[d]}</option>)}
                      </select>
                    </label>
                    {draft.decision === 'fixed' ? (
                      <fieldset className="space-y-1">
                        <legend className="text-xs text-slate-600">Requirements that now carry the correct structure</legend>
                        {f.item_ids.map((itemId) => {
                          const found = findItem(view, itemId);
                          if (!found) return null;
                          return (
                            <label key={itemId} className="flex items-center gap-2 text-slate-700">
                              <input type="checkbox" checked={draft.evidence.includes(itemId)}
                                onChange={(e) => setFindingDrafts((prev) => ({
                                  ...prev,
                                  [f.id]: { ...draft, evidence: e.target.checked ? [...draft.evidence, itemId] : draft.evidence.filter((x) => x !== itemId) },
                                }))} />
                              <span dir="auto">{found.item.text}</span>
                            </label>
                          );
                        })}
                      </fieldset>
                    ) : (
                      <label className="block text-xs text-slate-600">
                        Note for the record
                        <textarea dir="auto" className="mt-1 w-full border border-slate-300 rounded px-2 py-1" rows={2}
                          value={draft.note}
                          onChange={(e) => setFindingDrafts((prev) => ({ ...prev, [f.id]: { ...draft, note: e.target.value } }))} />
                      </label>
                    )}
                    {problem && <p className="text-xs text-amber-700">{problem}</p>}
                    <button type="button" className="px-3 py-1.5 rounded-lg bg-blue-700 text-white text-sm disabled:opacity-50"
                      disabled={busy || problem !== null}
                      onClick={() => void submit([buildFindingOperation(f.id, draft)], 'Finding resolved.')}>
                      Resolve this finding
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {structure.pending_item_ids.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm space-y-2">
          <p className="font-medium text-slate-900">Structure to confirm</p>
          <ul className="space-y-2">
            {structure.pending_item_ids.map((itemId) => {
              const found = findItem(view, itemId);
              if (!found) return null;
              return (
                <li key={itemId} className="flex flex-wrap items-center gap-2 justify-between">
                  <span dir="auto" className="text-slate-700">{found.item.text}</span>
                  {canEdit && (
                    <button type="button" className="px-3 py-1 rounded-lg border border-slate-300 text-sm disabled:opacity-50"
                      disabled={busy} onClick={() => void submit([confirmStructure(itemId)], 'Structure confirmed.')}>
                      Confirm this structure
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(Object.entries(view.document.categories) as [string, RequirementsCategory][]).map(([category, block]) => {
        const order = orderDrafts[category] ?? currentOrder(view, category);
        const itemsById = new Map(block.items.map((i) => [i.id, i]));
        return (
          <div key={category} className="border border-slate-200 rounded-lg">
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 bg-slate-50 rounded-t-lg">
              <h3 className="font-medium text-slate-900">{CATEGORY_LABELS[category] ?? category}</h3>
              <label className="text-sm text-slate-600 flex items-center gap-2">
                Category weight
                <input type="number" min={0} max={100} aria-label={`${CATEGORY_LABELS[category] ?? category} category weight`}
                  className="w-20 border border-slate-300 rounded px-2 py-1" disabled={!canEdit}
                  value={categoryDrafts[category] ?? block.weight}
                  onChange={(e) => setCategoryDrafts((prev) => ({ ...prev, [category]: Number(e.target.value) }))} />
              </label>
            </div>
            <ul className="divide-y divide-slate-100">
              {block.items.length === 0 && <li className="px-4 py-3 text-sm text-slate-500">No requirements in this category.</li>}
              {order.map((itemId, index) => {
                const item = itemsById.get(itemId);
                if (!item) return null;
                const draft = itemDrafts[item.id] ?? {};
                if (draft.removed) {
                  return (
                    <li key={item.id} className="px-4 py-3 text-sm text-slate-500 flex justify-between">
                      <span>Marked for removal</span>
                      <button type="button" className="text-blue-700 underline" onClick={() => editItem(item.id, { removed: false })}>Undo</button>
                    </li>
                  );
                }
                const importance: ImportanceLevel = draft.importance ?? item.importance;
                const weightValue = draft.weight !== undefined ? draft.weight : item.weight;
                return (
                  <li key={item.id} className="px-4 py-3 space-y-2">
                    <div className="flex flex-wrap gap-2 items-start">
                      {canEdit && (
                        <div className="flex flex-col">
                          <button type="button" aria-label={`Move ${item.text} up`} className="text-slate-600 disabled:opacity-30"
                            disabled={index === 0}
                            onClick={() => setOrderDrafts((prev) => ({ ...prev, [category]: moveItem(order, index, -1) }))}>▲</button>
                          <button type="button" aria-label={`Move ${item.text} down`} className="text-slate-600 disabled:opacity-30"
                            disabled={index === order.length - 1}
                            onClick={() => setOrderDrafts((prev) => ({ ...prev, [category]: moveItem(order, index, 1) }))}>▼</button>
                        </div>
                      )}
                      <input dir="auto" aria-label="Requirement text"
                        className="flex-1 min-w-[12rem] border border-slate-300 rounded px-2 py-1" disabled={!canEdit}
                        value={draft.text ?? item.text} onChange={(e) => editItem(item.id, { text: e.target.value })} />
                      <select aria-label="Importance" className="border border-slate-300 rounded px-2 py-1" disabled={!canEdit}
                        value={importance} onChange={(e) => editItem(item.id, { importance: e.target.value as ImportanceLevel })}>
                        <option value="required">Required</option>
                        <option value="preferred">Preferred</option>
                      </select>
                      {importance === 'required' && (
                        <input type="number" min={1} max={100} aria-label="Item weight"
                          className="w-20 border border-slate-300 rounded px-2 py-1" disabled={!canEdit}
                          value={weightValue ?? ''}
                          onChange={(e) => editItem(item.id, { weight: e.target.value === '' ? null : Number(e.target.value) })} />
                      )}
                      {canEdit && (
                        <button type="button" className="text-red-700 underline text-sm" onClick={() => editItem(item.id, { removed: true })}>Remove</button>
                      )}
                    </div>
                    {item.alternatives && item.alternatives.length > 0 && (
                      <p dir="auto" className="text-xs text-slate-600">One of: {item.alternatives.join(' / ')}</p>
                    )}
                    {item.experience && (item.experience.subject || item.experience.min_years !== null) && (
                      <p dir="auto" className="text-xs text-slate-600">
                        Experience{item.experience.min_years !== null ? `: ${item.experience.min_years} years` : ''}
                        {item.experience.subject ? ` of ${item.experience.subject}` : ''}
                      </p>
                    )}
                    {item.source_text && <p dir="auto" className="text-xs text-slate-500">From the job description: “{item.source_text}”</p>}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}

      {canEdit && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-slate-900">Add a requirement</p>
          {newItems.map((added) => (
            <div key={added.key} className="flex flex-wrap gap-2 items-center">
              <select aria-label="Category for the new requirement" className="border border-slate-300 rounded px-2 py-1" value={added.category}
                onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, category: e.target.value } : n)))}>
                {Object.keys(CATEGORY_LABELS).map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
              </select>
              <input dir="auto" aria-label="New requirement text" className="flex-1 min-w-[12rem] border border-slate-300 rounded px-2 py-1"
                value={added.text} onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, text: e.target.value } : n)))} />
              <select aria-label="New requirement importance" className="border border-slate-300 rounded px-2 py-1" value={added.importance}
                onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, importance: e.target.value as ImportanceLevel } : n)))}>
                <option value="required">Required</option>
                <option value="preferred">Preferred</option>
              </select>
              <button type="button" className="text-slate-600 underline text-sm" onClick={() => setNewItems((prev) => prev.filter((n) => n.key !== added.key))}>Discard</button>
            </div>
          ))}
          <button type="button" className="text-sm text-blue-700 underline"
            onClick={() => {
              newItemCounter += 1;
              setNewItems((prev) => [...prev, { key: `new-${newItemCounter}`, category: 'skills', text: '', importance: 'required' }]);
            }}>
            Add requirement
          </button>
        </div>
      )}

      {canEdit ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
          <p className="text-xs text-slate-500">{pendingOps.length === 0 ? 'No unsaved changes.' : `${pendingOps.length} unsaved change(s).`}</p>
          <button type="button" className="px-4 py-2 rounded-lg bg-blue-700 text-white text-sm disabled:opacity-50"
            disabled={pendingOps.length === 0 || busy || conflict !== null}
            onClick={() => void submit(pendingOps, 'Saved.')}>
            {busy ? 'Saving…' : 'Save requirements'}
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-500 border-t border-slate-200 pt-4">Only administrators and recruiters can edit requirements.</p>
      )}
    </section>
  );
}
