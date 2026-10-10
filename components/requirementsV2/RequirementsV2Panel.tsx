import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CATEGORIES, CategoryKey, Draft, DraftItem, addItem, applyCategoryWeights, compareWithOriginal, computeNormalize,
  describeApiError, draftFromServer, equalizeCategory, findItem, isDirty, isEqualSplit, removeItem, setAlternatives, setCategoryWeight,
  setExperience, setImportance, setItemWeight, setText, toPayload, validateDraft, categoryTotal, ApiProblem, keepOneOfSplit, replaceWithBlank,
  parseWhole,
} from '../../utils/requirementsV2';
import { Choice, threeWayMerge } from '../../utils/requirementsMerge';
import type { PipelineIssue, RequirementsApi, RequirementsView } from '../../services/requirementsV2Api';
import { fmt, STRINGS, type Strings } from './i18n';
import { issueText as describeIssue } from './issues';
import { CategoryCard, CategoryEditState, EditingState, ItemInfo, RowActions } from './CategoryCard';
import type { EditValues } from './ItemEditor';
import { BlockerPanel, ConflictPanel, CorrectionActions, InformationalPanel, IssuesPanel, PipelineStatusCard } from './PipelinePanels';
import {
  ClassificationPanel, ComparisonView, ConflictResolver, EvaluationUnavailable, PreferredOnlyCard, ReadinessCard,
  SimilarityPanel, btnPrimary, btnSecondary,
} from './Panels';

// Recruiter editor for requirements-v2 jobs. The BACKEND is the authority: this component sends the draft with the
// revision it was based on, shows the server's answer (readiness, warnings, validation issues) and never overwrites or
// discards the recruiter's draft without an explicit choice. It never creates a job and never starts evaluation.

export interface RequirementsV2PanelProps {
  jobId: string;
  api: RequirementsApi;
  isAr: boolean;
  canEdit: boolean;                                  // page-level permission (admin / hr_manager); the server decides too
  currentUserId?: string | null;
  resolveUser?: (id: string) => string | null;
  addToast?: (msg: string, type: 'success' | 'error' | 'info') => void;
  /** Test hook: replaces window.confirm. */
  confirmFn?: (message: string) => boolean;
}

type Busy = null | 'load' | 'save' | 'action';

export const RequirementsV2Panel: React.FC<RequirementsV2PanelProps> = ({
  jobId, api, isAr, canEdit: pageCanEdit, currentUserId, resolveUser, addToast, confirmFn,
}) => {
  const s = STRINGS[isAr ? 'ar' : 'en'];
  const [view, setView] = useState<RequirementsView | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [base, setBase] = useState<Draft | null>(null);            // what the draft is compared with for "unsaved"
  const [baseRevision, setBaseRevision] = useState(0);              // the revision sent as expected_revision
  const [busy, setBusy] = useState<Busy>('load');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);  // last refused save / action
  const [conflict, setConflict] = useState<{ current: RequirementsView } | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice | undefined>>({});
  const [mergedNotice, setMergedNotice] = useState(false);
  const [showCompare, setShowCompare] = useState(false);
  const [status, setStatus] = useState('');
  const [normalizeNote, setNormalizeNote] = useState<string | null>(null);
  const [fallback, setFallback] = useState<Record<CategoryKey, number> | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);   // the one item whose editor is open (nothing changes until Apply)
  const [catEdit, setCatEdit] = useState<{ category: CategoryKey } & CategoryEditState | null>(null);
  const alive = useRef(true);
  const apiRef = useRef(api);
  apiRef.current = api;                                            // callers may pass a new object each render

  const toast = useCallback((m: string, t: 'success' | 'error' | 'info') => { if (addToast) addToast(m, t); }, [addToast]);
  const say = (m: string) => setStatus(m);

  const apply = useCallback((v: RequirementsView) => {
    const d = draftFromServer(v.requirements);
    setView(v); setDraft(d); setBase(d); setBaseRevision(v.revision);
    setConflict(null); setChoices({}); setMergedNotice(false); setProblem(null); setFallback(null); setNormalizeNote(null);
    setEditing(null); setCatEdit(null);
  }, []);

  const load = useCallback(async () => {
    setBusy('load'); setLoadError(null);
    try { apply(await apiRef.current.get(jobId)); }
    catch (e) { const p = describeApiError(e); setLoadError((s as any)[`err_${p.code}`] || p.message || s.loadFailed); }
    finally { if (alive.current) setBusy(null); }
  }, [jobId, apply, s]);

  useEffect(() => { alive.current = true; load(); return () => { alive.current = false; }; }, [load]);

  // While the extraction is queued or processing, the status is refreshed by itself; when it completes the reloaded view is the editor.
  const extracting = view?.readiness.basis === 'extraction' && (view.extraction?.status === 'pending' || view.extraction?.status === 'processing');
  useEffect(() => {
    if (!extracting) return;
    const id = window.setTimeout(() => { load(); }, 3000);
    return () => window.clearTimeout(id);
  }, [extracting, view, load]);

  const dirty = !!(draft && base && isDirty(draft, base));
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

  const canEdit = pageCanEdit && !!view?.can_edit;
  const canAct = canEdit && !dirty && !conflict;                  // server-side actions need a clean, current draft

  // ── helpers ────────────────────────────────────────────────────────────────
  const fmtDate = (d: string) => { const x = new Date(d); return isNaN(x.getTime()) ? '—' : x.toLocaleDateString(isAr ? 'ar' : 'en-GB'); };
  const who = (id: string) => (resolveUser && resolveUser(id)) || (currentUserId && id === currentUserId ? (isAr ? 'أنت' : 'you') : (isAr ? 'مستخدم آخر' : 'another user'));
  // The warning lists describe the SAVED version, so they name items by their saved wording (not the unsaved draft's).
  const textOf = (id: string) => (base ? findItem(base, id)?.item.text : undefined) || (draft ? findItem(draft, id)?.item.text || '' : '');
  const goTo = (elId: string) => {
    const el = document.getElementById(elId);
    if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); (el as HTMLElement).focus({ preventScroll: true }); }
  };
  const goToItem = (id: string) => goTo(`req-item-${id}`);
  const goToCategory = (c: string) => goTo(`req-cat-${c}`);

  const localIssues = useMemo(() => (draft ? validateDraft(draft) : []), [draft]);

  // Every problem with the draft in one list, each with the words that say what is wrong (actual and expected values included): the local
  // rules, and the server's answer to the last save.
  type Problem = { code: string; text: string; itemKey?: string; category?: CategoryKey };
  const issueCtx = { s, categoryName: (c: string) => s.categories[c as CategoryKey] ?? c };
  const itemNameOf = (key: string) => (draft ? findItem(draft, key)?.item.text : undefined);
  const keyForServerId = (id: string) => (draft ? CATEGORIES.flatMap(c => draft[c].items).find(i => i.id === id)?.key : undefined);
  const localProblems: Problem[] = localIssues.map(i => ({
    code: i.code,
    text: describeIssue({ code: i.code, category: i.category, itemName: i.itemKey ? itemNameOf(i.itemKey) : null, params: i.params }, issueCtx),
    itemKey: i.itemKey, category: i.category as CategoryKey | undefined,
  }));
  const serverProblems: Problem[] = (problem?.issues ?? []).map(x => ({
    code: x.code,
    text: describeIssue({ code: x.code, message: x.message, category: x.category, itemName: x.item_id ? textOf(x.item_id) : null, params: x.params }, issueCtx),
    itemKey: x.item_id ? keyForServerId(x.item_id) : undefined, category: (x.category ?? undefined) as CategoryKey | undefined,
  }));
  // the problems the draft has by its own rules. Saving is never blocked here: the server decides (revision conflicts first, then the rules),
  // and its answer is shown with the same wording.
  const shownProblems: Problem[] = serverProblems.length > 0 ? serverProblems : localProblems;
  const itemProblems = (key: string) => shownProblems.filter(p => p.itemKey === key).map(p => p.text);
  const categoryProblems = (c: CategoryKey) => shownProblems.filter(p => p.category === c && !p.itemKey).map(p => p.text);
  // the category weight is marked for problems with the category weight itself
  const CATEGORY_WEIGHT_CODES = ['bad_category_weight', 'category_weight_not_positive', 'category_weight_without_required_items', 'category_weights_total'];
  const categoryInvalid = (c: CategoryKey) => shownProblems.some(p => p.category === c && !p.itemKey && CATEGORY_WEIGHT_CODES.includes(p.code));
  const original = view?.original ?? null;
  const comparison = useMemo(() => (draft && original ? compareWithOriginal(draft, original) : null), [draft, original]);

  const editedCat = (c: CategoryKey) => {
    if (comparison) { const cm = comparison[c]; return cm.weightChanged || cm.removed.length > 0 || cm.current.some(x => x.kind !== 'same'); }
    return !!view?.edited_categories?.[c];
  };
  const dirtyCat = (c: CategoryKey) => !!(draft && base && JSON.stringify(toPayload(draft).categories[c]) !== JSON.stringify(toPayload(base).categories[c]));

  // Items the draft changed relative to the SAVED version it started from (wording, Required/Preferred, structure, removal):
  // the warnings, acknowledgments and confirmations shown for them describe the saved version, not the draft.
  const changedIds = useMemo(() => {
    const out = new Set<string>();
    if (!draft || !base) return out;
    for (const c of CATEGORIES) {
      for (const b of base[c].items) {
        const f = findItem(draft, b.key);
        if (!b.id) continue;
        if (!f || f.item.text !== b.text || f.item.importance !== b.importance
            || JSON.stringify(f.item.alternatives) !== JSON.stringify(b.alternatives) || JSON.stringify(f.item.experience) !== JSON.stringify(b.experience)) out.add(b.id);
      }
    }
    return out;
  }, [draft, base]);

  const theirsDraft = useMemo(() => (conflict ? draftFromServer(conflict.current.requirements) : null), [conflict]);
  const analysis = useMemo(() => (conflict && draft && base && theirsDraft ? threeWayMerge(base, draft, theirsDraft) : null), [conflict, draft, base, theirsDraft]);

  const infoFor = (item: DraftItem): ItemInfo => {
    const cmp = comparison && findItem(draft!, item.key);
    const ch = cmp && comparison![cmp.category].current.find(x => x.item.key === item.key);
    const st = item.id ? view?.structure_review.items.find(x => x.item_id === item.id) : undefined;
    return {
      edited: !!ch && ch.kind !== 'same', isNew: !item.id,
      structureState: st?.state ?? null, structureRecord: st?.record ?? null,
      classification: item.id ? (view?.classification_warnings ?? []).filter(w => w.item_id === item.id) : [],
      similar: item.id ? (view?.similarity_warnings ?? []).filter(w => w.items.some(x => x.item_id === item.id)).map(w => {
        const other = w.items.find(x => x.item_id !== item.id)!;
        return { id: w.id, kind: w.kind, differences: w.differences, other, label: '', stale: changedIds.has(item.id as string) || changedIds.has(other.item_id) };
      }) : [],
      issues: itemProblems(item.key), changedInDraft: !!item.id && changedIds.has(item.id),
    };
  };

  // Whether the DRAFT already carries a correction for an issue (the issue itself only clears after the next save: the server re-checks).
  const issuePending = (i: PipelineIssue): boolean => {
    if (!draft || !base) return false;
    if (i.kind === 'injection_weights') return !!i.category && draft[i.category as CategoryKey]?.weight !== base[i.category as CategoryKey]?.weight;
    return i.item_ids.length > 0 && i.item_ids.some(id => !findItem(draft, id) || changedIds.has(id));
  };

  // ── draft edits (always explicit; no automatic redistribution) ─────────────
  const edit = (fn: (d: Draft) => Draft) => { setDraft(d => (d ? fn(d) : d)); setProblem(null); };

  // Review by default. The editor opens on request and Apply changes only the draft (wording, importance, weight, structure together).
  const startEdit = (key: string) => { setEditing({ key, isNew: false }); };
  const closeEdit = (key: string) => {
    setEditing(null);
    window.setTimeout(() => document.getElementById(`req-item-${key}`)?.focus({ preventScroll: true }), 0);
  };
  const applyEdit = (key: string, v: EditValues) => {
    edit(d => {
      const found = findItem(d, key);
      if (!found) return d;
      const before = found.item;
      let n = setText(d, key, v.text);
      if (v.importance !== before.importance) n = setImportance(n, key, v.importance);
      if (v.importance === 'required') n = setItemWeight(n, key, v.weight);
      if (JSON.stringify(v.alternatives) !== JSON.stringify(before.alternatives)) n = setAlternatives(n, key, v.alternatives);
      if (JSON.stringify(v.experience) !== JSON.stringify(before.experience)) n = setExperience(n, key, v.experience);
      return n;
    });
    closeEdit(key);
  };
  // Cancel discards the editor's values; a new item that was never applied is removed from the draft again.
  const cancelEdit = () => {
    if (!editing) return;
    const { key, isNew } = editing;
    if (isNew) edit(d => removeItem(d, key));
    closeEdit(key);
  };
  const openCatEdit = (c: CategoryKey) => { if (draft) setCatEdit({ category: c, text: draft[c].weight === null ? '' : String(draft[c].weight), error: null }); };
  const applyCatEdit = () => {
    if (!catEdit || !draft) return;
    const n = parseWhole(catEdit.text);
    if (n === undefined) {
      setCatEdit({ ...catEdit, error: describeIssue({ code: 'not_whole_number', params: { field: `${s.categories[catEdit.category]} (${s.categoryWeight})`, value: catEdit.text } }, issueCtx) });
      return;
    }
    edit(d => setCategoryWeight(d, catEdit.category, n));
    setCatEdit(null);
  };
  const actions: RowActions = {
    toggleImportance: (k) => edit(d => { const f = findItem(d, k); return f ? setImportance(d, k, f.item.importance === 'required' ? 'preferred' : 'required') : d; }),
    remove: (k) => { edit(d => removeItem(d, k)); say(s.delete); },
    confirmStructure: (id) => runAction(() => apiRef.current.confirmStructure(jobId, baseRevision, id)),
    goToItem,
  };
  // Corrections offered by the pipeline panels: they change the DRAFT only (never save, never touch other weights).
  const corrections: CorrectionActions = {
    removeItem: (id) => { edit(d => removeItem(d, id)); say(s.correctionApplied); },
    replaceItem: (id) => {
      if (!draft) return;
      const r = replaceWithBlank(draft, id);
      if (r) { setDraft(r.draft); setEditing({ key: r.key, isNew: false }); setProblem(null); say(s.correctionApplied); }
    },
    editCategoryWeight: (c) => openCatEdit(c as CategoryKey),
    keepOne: (issue, keepId) => {
      const options = issue.details?.options ?? [];
      edit(d => keepOneOfSplit(d, keepId, options, issue.item_ids));
      say(s.correctionApplied);
    },
    editStructure: goToItem,
    goToItem,
  };
  const add = (c: CategoryKey, importance: 'required' | 'preferred') => {
    if (!draft) return;
    const r = addItem(draft, c, importance);
    setDraft(r.draft); setProblem(null);
    setEditing({ key: r.key, isNew: true });                  // the new item opens in the editor: it is not in the saved version until Save
  };

  const handleProblem = async (e: any, fromSave: boolean) => {
    const p = describeApiError(e);
    if (p.status === 409 && p.code === 'requirements_revision_conflict' && p.current) {
      if (fromSave && dirty) { setConflict({ current: p.current as RequirementsView }); setChoices({}); setProblem(null); return; }
      apply(p.current as RequirementsView);                      // nothing of the recruiter's would be lost
      toast(s.conflictTitle, 'info');
      return;
    }
    setProblem(p);
    const msg = (s as any)[`err_${p.code}`] || p.message;
    toast(msg, 'error');
  };

  const save = async () => {
    if (!draft || busy) return;
    setBusy('save'); say(s.saving);
    try {
      const v = await apiRef.current.save(jobId, baseRevision, toPayload(draft));
      // The saved version replaces the draft with what the server stored (ids for new items, normalised state).
      apply(v);
      toast(v.changed === false ? s.savedNoChange : s.saved, 'success'); say(s.saved);
    } catch (e) { await handleProblem(e, true); }
    finally { if (alive.current) setBusy(null); }
  };

  async function runAction(call: () => Promise<RequirementsView>) {
    if (busy) return;
    setBusy('action');
    try { apply(await call()); say(s.saved); }
    catch (e) { await handleProblem(e, false); }
    finally { if (alive.current) setBusy(null); }
  }

  // Asks for a new extraction attempt (server decides who may, and only for a failed attempt with no document), then reloads the status.
  const retryExtraction = async () => {
    if (busy) return;
    setBusy('action');
    try { await apiRef.current.retryExtraction(jobId); toast(s.extractionQueued, 'info'); }
    catch (e) { const p = describeApiError(e); toast((s as any)[`err_${p.code}`] || p.message, 'error'); }
    finally { if (alive.current) setBusy(null); }
    await load();
  };

  const discard = () => { if (base) { setDraft(base); setProblem(null); setFallback(null); setNormalizeNote(null); setEditing(null); setCatEdit(null); } };
  // Resolution never saves anything: it builds the merged draft from the recruiter's explicit choices and rebases the
  // comparison point onto the latest saved version, so the next save is checked against that revision.
  const applyMerge = () => {
    if (!conflict || !analysis || !theirsDraft) return;
    const merged = analysis.build(choices);
    setDraft(merged); setBase(theirsDraft); setBaseRevision(conflict.current.revision); setView(conflict.current);
    setConflict(null); setChoices({}); setProblem(null); setMergedNotice(true); say(s.mergedNotice);
  };
  const discardForLatest = () => {
    if (!conflict) return;
    if ((confirmFn || ((m: string) => window.confirm(m)))(s.conflictConfirmDiscard)) apply(conflict.current);
  };

  const normalize = () => {
    if (!draft) return;
    const r = computeNormalize(draft);
    if (r.status === 'no_eligible') { setNormalizeNote(s.normalizeNothing); setFallback(null); return; }
    if (r.status === 'fallback_required') { setFallback(r.suggested); setNormalizeNote(null); return; }
    setDraft(applyCategoryWeights(draft, r.weights)); setFallback(null);
    setNormalizeNote(r.raised.length ? s.normalizeRaised : null); say(s.normalize);
  };

  // ── render ─────────────────────────────────────────────────────────────────
  if (busy === 'load' && !view) return <section className="bg-white rounded-2xl border border-border p-6 text-sm text-textMuted" aria-busy="true">{s.loading}</section>;
  if (loadError || !view || !draft) {
    return (
      <section className="bg-white rounded-2xl border border-border p-6" dir={isAr ? 'rtl' : 'ltr'}>
        <p className="text-sm text-error" role="alert">{loadError || s.loadFailed}</p>
        <button type="button" className={`${btnSecondary} mt-3`} onClick={load}>{s.retry}</button>
      </section>
    );
  }

  // No document yet (requirements-v2 extraction pending or failed): the editor is not shown; the status card is.
  if (view.readiness.basis === 'extraction') {
    return (
      <section dir={isAr ? 'rtl' : 'ltr'} lang={isAr ? 'ar' : 'en'} aria-labelledby="req-title-text" className="space-y-4" data-testid="requirements-v2">
        <header className="bg-white rounded-2xl border border-border shadow-sm px-4 py-4">
          <h3 id="req-title" className="text-base font-black text-textMain"><span id="req-title-text">{s.title}</span></h3>
          <p className="text-xs text-textMuted mt-0.5">{s.subtitle}</p>
        </header>
        <ExtractionCard s={s} extraction={view.extraction ?? null} busy={busy !== null} onRetry={retryExtraction} onRefresh={load} />
      </section>
    );
  }

  const total = categoryTotal(draft);
  const anyRequired = CATEGORIES.some(c => draft[c].items.some(i => i.importance === 'required'));
  const totalOk = !anyRequired || total === 100;
  const problemOther = problem && problem.issues.length === 0 ? ((s as any)[`err_${problem.code}`] || problem.message) : null;

  return (
    <section dir={isAr ? 'rtl' : 'ltr'} lang={isAr ? 'ar' : 'en'} aria-labelledby="req-title-text" className="space-y-4" data-testid="requirements-v2">
      <div role="status" aria-live="polite" className="sr-only">{status}</div>

      <header className="bg-white rounded-2xl border border-border shadow-sm px-4 py-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="req-title" className="text-base font-black text-textMain flex items-center gap-2 flex-wrap">
            <span id="req-title-text">{s.title}</span>
            {dirty && <span className="text-[10px] font-black uppercase px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600" data-testid="unsaved-badge">{s.unsaved}</span>}
          </h3>
          <p className="text-xs text-textMuted mt-0.5">{s.subtitle}</p>
          {!canEdit && <p className="text-xs text-textMuted mt-1">{s.readOnly}</p>}
        </div>
        {canEdit && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={btnPrimary} onClick={save} disabled={!dirty || busy !== null} data-testid="save">{busy === 'save' ? s.saving : s.save}</button>
            <button type="button" className={btnSecondary} onClick={discard} disabled={!dirty || busy !== null}>{s.discard}</button>
          </div>
        )}
      </header>

      <EvaluationUnavailable s={s} />

      {conflict && analysis && <ConflictResolver s={s} latestRevision={conflict.current.revision} analysis={analysis} choices={choices}
                                                  onChoose={(k, c) => setChoices(prev => ({ ...prev, [k]: c }))} onApply={applyMerge} onDiscard={discardForLatest} />}
      {mergedNotice && !conflict && <p role="status" className="rounded-xl border border-green-300 bg-green-50 p-3 text-xs text-green-900" data-testid="merged-notice">{s.mergedNotice}</p>}
      {shownProblems.length > 0 && (
        <section aria-labelledby="req-issues-h" className="rounded-xl border-2 border-error bg-red-50 p-4" data-testid="issue-summary">
          <h4 id="req-issues-h" className="text-sm font-black text-red-900">{serverProblems.length > 0 ? s.issuesServerTitle : s.issuesFixTitle}</h4>
          <p className="text-xs text-red-900 mt-0.5">{shownProblems.length === 1 ? s.issueCountOne : fmt(s.issueCount, { n: shownProblems.length })}</p>
          <ul className="mt-2 space-y-2">
            {shownProblems.map((p, n) => (
              <li key={n} className="flex flex-wrap items-start gap-2 text-xs text-red-900">
                <span className="flex-1 min-w-0">{p.text}</span>
                <button type="button" className={btnSecondary}
                        onClick={() => (p.itemKey ? goToItem(p.itemKey) : p.category ? goToCategory(p.category) : goTo('req-category-total'))}>
                  {s.goToIssue}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {problemOther && <p role="alert" className="rounded-xl border border-red-300 bg-red-50 p-3 text-xs text-red-900" data-testid="problem">{problemOther}</p>}

      <PipelineStatusCard s={s} view={view} canEdit={canEdit} />
      <BlockerPanel s={s} view={view} canEdit={canEdit && !conflict} dirty={dirty} busy={busy !== null} textOf={textOf} pending={issuePending} actions={corrections} />
      <ReadinessCard s={s} view={view} goToCategory={goToCategory} goToItem={goToItem} dirty={dirty} />
      <IssuesPanel s={s} view={view} dirty={dirty} textOf={textOf} pending={issuePending} goToItem={goToItem} goToCategory={goToCategory} />


      <ClassificationPanel dirty={dirty} changedIds={changedIds} s={s} view={view} canEdit={canEdit} canAct={canAct} busy={busy !== null} textOf={textOf} fmtDate={fmtDate} who={who}
                           onAck={(id) => runAction(() => apiRef.current.acknowledge(jobId, baseRevision, id))} goToItem={goToItem} />
      <ConflictPanel s={s} view={view} canEdit={canEdit} canAct={canAct} busy={busy !== null} dirty={dirty} changedIds={changedIds} textOf={textOf} fmtDate={fmtDate} who={who}
                     onAck={(id) => runAction(() => apiRef.current.acknowledge(jobId, baseRevision, id, 'conflict'))} goToItem={goToItem} />
      <PreferredOnlyCard dirty={dirty} s={s} view={view} canEdit={canEdit} canAct={canAct} busy={busy !== null} fmtDate={fmtDate} who={who}
                         onConfirm={() => runAction(() => apiRef.current.confirmNoScore(jobId, baseRevision))} />
      {/* informational, not blocking: collapsed until asked for */}
      {(view.informational?.generic_model_notes.length || view.informational?.parser_review.length) ? (
        <details data-testid="informational-disclosure">
          <summary className="cursor-pointer text-xs font-bold text-textMuted px-1 py-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded">{s.informationalSummary}</summary>
          <div className="mt-2"><InformationalPanel s={s} view={view} /></div>
        </details>
      ) : null}
      {view.similarity_warnings.length > 0 && (
        <details data-testid="similarity-disclosure">
          <summary className="cursor-pointer text-xs font-bold text-textMuted px-1 py-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded">{s.similaritySummary}</summary>
          <div className="mt-2"><SimilarityPanel dirty={dirty} changedIds={changedIds} s={s} view={view} textOf={textOf} goToItem={goToItem} /></div>
        </details>
      )}

      <div className="bg-white rounded-2xl border border-border shadow-sm px-4 py-3 flex flex-wrap items-center justify-between gap-3">
        {totalOk ? (
          <p id="req-category-total" tabIndex={-1} className="text-xs text-textMuted" data-testid="category-total">
            {fmt(s.categoryTotal, { total })} {anyRequired && <span>({s.totalsOk})</span>}
          </p>
        ) : (
          <p id="req-category-total" tabIndex={-1} role="status" className="basis-full sm:basis-auto flex-1 min-w-0 rounded-lg border-2 border-error bg-red-50 px-3 py-2 text-sm font-bold text-red-900" data-testid="category-total">
            {fmt(s.categoryTotalOff, { total, difference: total - 100 > 0 ? `+${total - 100}` : String(total - 100) })}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canEdit && <button type="button" className={btnSecondary} title={s.normalizeHint} aria-label={`${s.normalize}. ${s.normalizeHint}`} onClick={normalize} data-testid="normalize">{s.normalize}</button>}
          <button type="button" className={btnSecondary} aria-pressed={showCompare} onClick={() => setShowCompare(v => !v)} data-testid="toggle-compare">{showCompare ? s.hideCompare : s.compare}</button>
        </div>
        {normalizeNote && <p className="basis-full text-xs text-textMuted" role="status">{normalizeNote}</p>}
        {fallback && (
          <div className="basis-full text-xs text-amber-900 bg-amber-50 border border-amber-300 rounded-lg p-2" role="alert">
            {s.normalizeFallback}{' '}
            <button type="button" className={btnSecondary} onClick={() => { setDraft(applyCategoryWeights(draft, fallback)); setFallback(null); }}>{s.normalizeApplyEqual}</button>
          </div>
        )}
      </div>

      {showCompare && <ComparisonView s={s} original={original} comparison={comparison} />}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {CATEGORIES.map(c => (
          <CategoryCard key={c} category={c} cat={draft[c]} s={s} canEdit={canEdit} canAct={canAct} busy={busy !== null}
                        edited={editedCat(c)} dirty={dirtyCat(c)}
                        issues={categoryProblems(c)} categoryInvalid={categoryInvalid(c)}
                        infoFor={infoFor} actions={actions} fmtDate={fmtDate} who={who}
                        editing={editing} startEdit={startEdit} applyEdit={applyEdit} cancelEdit={cancelEdit}
                        catEdit={catEdit?.category === c ? catEdit : null} openCatEdit={() => openCatEdit(c)}
                        setCatText={(text) => setCatEdit(e => (e && e.category === c ? { ...e, text, error: null } : e))}
                        applyCatEdit={applyCatEdit} cancelCatEdit={() => setCatEdit(null)}
                        equalDisabled={isEqualSplit(draft[c])}
                        equalize={() => { if (!isEqualSplit(draft[c])) { edit(d => equalizeCategory(d, c)); say(s.equalize); } }}
                        add={(imp) => add(c, imp)} />
        ))}
      </div>

      {canEdit && dirty && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={btnPrimary} onClick={save} disabled={busy !== null}>{busy === 'save' ? s.saving : s.save}</button>
          <span className="text-xs text-textMuted">{s.unsavedHint}</span>
        </div>
      )}
    </section>
  );
};

const ExtractionCard: React.FC<{
  s: Strings; extraction: RequirementsView['extraction'] | null; busy: boolean; onRetry: () => void; onRefresh: () => void;
}> = ({ s, extraction, busy, onRetry, onRefresh }) => {
  const status = extraction?.status ?? 'pending';
  const failed = status === 'failed';
  const completed = status === 'completed';
  const queued = status === 'pending';
  const steps: { key: string; label: string; active: boolean }[] = failed
    ? [{ key: 'failed', label: s.extractionStepFailed, active: true }]
    : [
        { key: 'pending', label: s.extractionStepQueued, active: queued },
        { key: 'processing', label: s.extractionStepProcessing, active: status === 'processing' },
        { key: 'completed', label: s.extractionStepCompleted, active: completed },
      ];
  const title = failed ? s.extractionFailedTitle : completed ? s.extractionCompletedTitle : queued ? s.extractionQueuedTitle : s.extractionPendingTitle;
  const body = failed ? s.extractionFailedBody : completed ? s.extractionCompletedBody : queued ? s.extractionQueuedBody : s.extractionPendingBody;
  return (
    <section aria-labelledby="req-extraction-h" className={`rounded-xl border p-4 space-y-2 ${failed ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-white'}`}
             data-testid="extraction-status" data-status={status}>
      <ol className="flex flex-wrap gap-2" aria-label={s.title}>
        {steps.map(step => (
          <li key={step.key} data-testid={`extraction-step-${step.key}`} aria-current={step.active ? 'step' : undefined}
              className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full ${step.active ? (failed ? 'bg-red-600 text-white' : 'bg-primary text-white') : 'bg-slate-100 text-slate-500'}`}>
            {step.label}
          </li>
        ))}
      </ol>
      <h4 id="req-extraction-h" className={`text-sm font-black ${failed ? 'text-red-900' : 'text-textMain'}`}>{title}</h4>
      <p className={`text-xs ${failed ? 'text-red-900' : 'text-textMuted'}`}>{body}</p>
      {failed && extraction?.error && (
        <p className="text-xs text-red-900" data-testid="extraction-error"><span className="font-bold">{s.extractionReason}:</span> {extraction.error}</p>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        {failed && extraction?.retry_available && (
          <button type="button" className={btnPrimary} onClick={onRetry} disabled={busy} data-testid="extraction-retry">{s.extractionRetry}</button>
        )}
        {!failed && (
          <button type="button" className={btnSecondary} onClick={onRefresh} disabled={busy} data-testid="extraction-refresh">{s.extractionRefresh}</button>
        )}
      </div>
    </section>
  );
};

export default RequirementsV2Panel;
