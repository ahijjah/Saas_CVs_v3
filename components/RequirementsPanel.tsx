import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  canEditRequirements, getRequirements, RequirementsApiError, RequirementsCategory, RequirementsView, saveRequirementEdits,
  ImportanceLevel,
} from '../services/requirementsApi';
import {
  buildOperations, CategoryWeightDrafts, ItemDraft, NewItemDraft,
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

let newItemCounter = 0;

interface Props {
  jobId: string;
  userRole: string | null | undefined;
  onReadinessChange?: (canProceed: boolean) => void;
}

export default function RequirementsPanel({ jobId, userRole, onReadinessChange }: Props) {
  const canEdit = canEditRequirements(userRole);
  const [view, setView] = useState<RequirementsView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [itemDrafts, setItemDrafts] = useState<Record<string, ItemDraft>>({});
  const [categoryDrafts, setCategoryDrafts] = useState<CategoryWeightDrafts>({});
  const [newItems, setNewItems] = useState<NewItemDraft[]>([]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error' | 'conflict'; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await getRequirements(jobId);
      setView(next);
      setLoadError(null);
      onReadinessChange?.(next.review.readiness.can_proceed);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'The requirements could not be loaded.');
      onReadinessChange?.(false);
    }
  }, [jobId, onReadinessChange]);

  useEffect(() => {
    setView(null);
    setItemDrafts({});
    setCategoryDrafts({});
    setNewItems([]);
    setNotice(null);
    void load();
  }, [jobId, load]);

  const pendingOps = useMemo(
    () => (view ? buildOperations(view, itemDrafts, categoryDrafts, newItems) : []),
    [view, itemDrafts, categoryDrafts, newItems],
  );

  const editItem = (itemId: string, patch: ItemDraft) =>
    setItemDrafts((prev) => ({ ...prev, [itemId]: { ...prev[itemId], ...patch } }));

  const save = async () => {
    if (!view || pendingOps.length === 0 || saving) return;
    setSaving(true);
    setNotice(null);
    try {
      const result = await saveRequirementEdits(jobId, view.requirements_revision, pendingOps);
      setItemDrafts({});
      setCategoryDrafts({});
      setNewItems([]);
      setNotice({ kind: 'ok', text: `Saved. The requirements are now at revision ${result.requirements_revision}.` });
      await load();
    } catch (err) {
      if (err instanceof RequirementsApiError && err.code === 'revision_conflict') {
        // The document changed under this edit. The local edits are kept for review; nothing is applied silently.
        setNotice({
          kind: 'conflict',
          text: 'The requirements changed while you were editing. Your edits are still here: review them, then save again.',
        });
        await load();
      } else {
        setNotice({ kind: 'error', text: err instanceof Error ? err.message : 'The requirements could not be saved.' });
      }
    } finally {
      setSaving(false);
    }
  };

  if (loadError && !view) {
    return (
      <section className="bg-white rounded-xl border border-slate-200 p-6">
        <p className="text-sm text-red-600" role="alert">{loadError}</p>
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

  return (
    <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-6" aria-labelledby="requirements-heading">
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
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-900" role="status">
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

      {classification.open_warnings.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm">
          <p className="font-medium text-slate-900">Classification warnings</p>
          <ul className="mt-2 space-y-1 text-slate-700">
            {classification.open_warnings.map((w) => (
              <li key={w.id}>
                {w.category ? `${CATEGORY_LABELS[w.category] ?? w.category}: ` : ''}Preferred classification is not supported by the job description
                {w.acknowledged ? ' (acknowledged)' : ''}.
              </li>
            ))}
          </ul>
        </div>
      )}

      {structural.open_findings.length > 0 && (
        <div className="rounded-lg border border-slate-200 p-4 text-sm">
          <p className="font-medium text-slate-900">Structural findings</p>
          <ul className="mt-2 space-y-2 text-slate-700">
            {structural.open_findings.map((f) => (
              <li key={f.id}>
                <p>{f.message}</p>
                <p dir="auto" className="text-xs text-slate-500 mt-1">“{f.source_text}”</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {structure.pending_item_ids.length > 0 && (
        <p className="text-sm text-slate-700">
          {structure.pending_item_ids.length} requirement(s) changed their wording and need their structure reviewed.
        </p>
      )}

      {notice && (
        <p
          role={notice.kind === 'ok' ? 'status' : 'alert'}
          className={`text-sm rounded-lg p-3 ${notice.kind === 'ok' ? 'bg-emerald-50 text-emerald-800' : notice.kind === 'conflict' ? 'bg-amber-50 text-amber-900' : 'bg-red-50 text-red-700'}`}
        >
          {notice.text}
        </p>
      )}

      {(Object.entries(view.document.categories) as [string, RequirementsCategory][]).map(([category, block]) => (
        <div key={category} className="border border-slate-200 rounded-lg">
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 bg-slate-50 rounded-t-lg">
            <h3 className="font-medium text-slate-900">{CATEGORY_LABELS[category] ?? category}</h3>
            <label className="text-sm text-slate-600 flex items-center gap-2">
              Category weight
              <input
                type="number"
                min={0}
                max={100}
                aria-label={`${CATEGORY_LABELS[category] ?? category} category weight`}
                className="w-20 border border-slate-300 rounded px-2 py-1"
                disabled={!canEdit}
                value={categoryDrafts[category] ?? block.weight}
                onChange={(e) => setCategoryDrafts((prev) => ({ ...prev, [category]: Number(e.target.value) }))}
              />
            </label>
          </div>
          <ul className="divide-y divide-slate-100">
            {block.items.length === 0 && <li className="px-4 py-3 text-sm text-slate-500">No requirements in this category.</li>}
            {block.items.map((item) => {
              const draft = itemDrafts[item.id] ?? {};
              if (draft.removed) {
                return (
                  <li key={item.id} className="px-4 py-3 text-sm text-slate-500 flex justify-between">
                    <span>Marked for removal</span>
                    <button type="button" className="text-blue-700 underline" onClick={() => editItem(item.id, { removed: false })}>
                      Undo
                    </button>
                  </li>
                );
              }
              const importance: ImportanceLevel = draft.importance ?? item.importance;
              const weightValue = draft.weight !== undefined ? draft.weight : item.weight;
              return (
                <li key={item.id} className="px-4 py-3 space-y-2">
                  <div className="flex flex-wrap gap-2 items-start">
                    <input
                      dir="auto"
                      aria-label="Requirement text"
                      className="flex-1 min-w-[12rem] border border-slate-300 rounded px-2 py-1"
                      disabled={!canEdit}
                      value={draft.text ?? item.text}
                      onChange={(e) => editItem(item.id, { text: e.target.value })}
                    />
                    <select
                      aria-label="Importance"
                      className="border border-slate-300 rounded px-2 py-1"
                      disabled={!canEdit}
                      value={importance}
                      onChange={(e) => editItem(item.id, { importance: e.target.value as ImportanceLevel })}
                    >
                      <option value="required">Required</option>
                      <option value="preferred">Preferred</option>
                    </select>
                    {importance === 'required' && (
                      <input
                        type="number"
                        min={1}
                        max={100}
                        aria-label="Item weight"
                        className="w-20 border border-slate-300 rounded px-2 py-1"
                        disabled={!canEdit}
                        value={weightValue ?? ''}
                        onChange={(e) => editItem(item.id, { weight: e.target.value === '' ? null : Number(e.target.value) })}
                      />
                    )}
                    {canEdit && (
                      <button type="button" className="text-red-700 underline text-sm" onClick={() => editItem(item.id, { removed: true })}>
                        Remove
                      </button>
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
                  {item.source_text && (
                    <p dir="auto" className="text-xs text-slate-500">From the job description: “{item.source_text}”</p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      {canEdit && (
        <div className="space-y-3">
          <p className="text-sm font-medium text-slate-900">Add a requirement</p>
          {newItems.map((added) => (
            <div key={added.key} className="flex flex-wrap gap-2 items-center">
              <select
                aria-label="Category for the new requirement"
                className="border border-slate-300 rounded px-2 py-1"
                value={added.category}
                onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, category: e.target.value } : n)))}
              >
                {Object.keys(CATEGORY_LABELS).map((c) => (
                  <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>
                ))}
              </select>
              <input
                dir="auto"
                aria-label="New requirement text"
                className="flex-1 min-w-[12rem] border border-slate-300 rounded px-2 py-1"
                value={added.text}
                onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, text: e.target.value } : n)))}
              />
              <select
                aria-label="New requirement importance"
                className="border border-slate-300 rounded px-2 py-1"
                value={added.importance}
                onChange={(e) => setNewItems((prev) => prev.map((n) => (n.key === added.key ? { ...n, importance: e.target.value as ImportanceLevel } : n)))}
              >
                <option value="required">Required</option>
                <option value="preferred">Preferred</option>
              </select>
              <button type="button" className="text-slate-600 underline text-sm"
                onClick={() => setNewItems((prev) => prev.filter((n) => n.key !== added.key))}>
                Discard
              </button>
            </div>
          ))}
          <button
            type="button"
            className="text-sm text-blue-700 underline"
            onClick={() => {
              newItemCounter += 1;
              setNewItems((prev) => [...prev, { key: `new-${newItemCounter}`, category: 'skills', text: '', importance: 'required', weight: null }]);
            }}
          >
            Add requirement
          </button>
        </div>
      )}

      {canEdit ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
          <p className="text-xs text-slate-500">{pendingOps.length === 0 ? 'No unsaved changes.' : `${pendingOps.length} unsaved change(s).`}</p>
          <button
            type="button"
            className="px-4 py-2 rounded-lg bg-blue-700 text-white text-sm disabled:opacity-50"
            disabled={pendingOps.length === 0 || saving}
            onClick={save}
          >
            {saving ? 'Saving…' : 'Save requirements'}
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-500 border-t border-slate-200 pt-4">Only administrators and recruiters can edit requirements.</p>
      )}
    </section>
  );
}
