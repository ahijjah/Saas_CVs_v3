import React, { useEffect, useRef, useState } from 'react';
import {
  CATEGORIES, CategoryKey, DraftCategory, DraftItem, ExperienceStructure, parseWhole, preferredItems, requiredItems, requiredStatus,
} from '../../utils/requirementsV2';
import { fmt, Strings } from './i18n';
import { BriefcaseIcon, DotsIcon, InfoIcon, PencilIcon, PlusIcon, WarnIcon } from './icons';
import { ItemEditor, EditValues } from './ItemEditor';
import type { RequirementsView } from '../../services/requirementsV2Api';

// Review first: each category lists its items as plain text. Editing opens only when asked for (Actions → Edit, or the pencil on the
// category weight, or an add button). Details and Actions are closed until opened.

export interface RowActions {
  toggleImportance(key: string): void;
  remove(key: string): void;
  confirmStructure(itemId: string): void;
  goToItem(id: string): void;
}

export interface ItemInfo {
  edited: boolean;                         // differs from the original AI analysis (or is new)
  isNew: boolean;
  structureState: 'original' | 'confirmed' | 'corrected' | 'entered' | 'needs_review' | null;
  structureRecord: { kind: string; user_id: string; recorded_at: string } | null;
  classification: RequirementsView['classification_warnings'];
  similar: { id: string; kind: string; differences: string[]; other: { item_id: string; category: string }; label: string; stale: boolean }[];
  issues: string[];                        // already-translated messages for this item
  changedInDraft: boolean;                 // this item's wording / classification / structure differs from the SAVED version
}

export interface EditingState { key: string; isNew: boolean }
export interface CategoryEditState { text: string; error: string | null }

export interface CategoryCardProps {
  category: CategoryKey; cat: DraftCategory; s: Strings; canEdit: boolean; canAct: boolean; busy: boolean;
  edited: boolean; dirty: boolean; issues: string[]; categoryInvalid: boolean;
  infoFor: (item: DraftItem) => ItemInfo; actions: RowActions;
  editing: EditingState | null; startEdit(key: string): void; applyEdit(key: string, v: EditValues): void; cancelEdit(): void;
  catEdit: CategoryEditState | null; openCatEdit(): void; setCatText(text: string): void; applyCatEdit(): void; cancelCatEdit(): void;
  equalDisabled: boolean; equalize(): void; add(importance: 'required' | 'preferred'): void;
  fmtDate: (d: string) => string; who: (id: string) => string;
}

const btnGhost = 'px-2.5 py-1.5 text-xs font-bold rounded-lg border border-border text-textMain hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed';
const iconBtn = 'inline-flex items-center justify-center w-8 h-8 rounded-lg border border-border text-textMain hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed';
const inputCls = 'px-3 py-2 border border-border rounded-xl text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none';

const Badge: React.FC<{ tone: 'blue' | 'amber' | 'slate' | 'green' | 'red'; children: React.ReactNode; title?: string }> = ({ tone, children, title }) => {
  const cls = { blue: 'bg-indigo-100 text-indigo-700', amber: 'bg-amber-100 text-amber-800', slate: 'bg-slate-100 text-slate-600',
    green: 'bg-green-100 text-green-800', red: 'bg-red-100 text-red-700' }[tone];
  return <span title={title} className={`inline-flex items-center gap-1 text-[10px] font-black uppercase px-1.5 py-0.5 rounded-full ${cls}`}>{children}</span>;
};

interface ItemRowProps {
  item: DraftItem; category: CategoryKey; s: Strings; canEdit: boolean; canAct: boolean; busy: boolean; info: ItemInfo;
  actions: RowActions; lastRequired: boolean; editing: EditingState | null; startEdit(key: string): void;
  applyEdit(key: string, v: EditValues): void; cancelEdit(): void; notify(texts: string[]): void;
  fmtDate: (d: string) => string; who: (id: string) => string;
}

// One item. Review shows its wording, importance, Required weight and blocking matters; Details and Actions open on request.
const ItemRow: React.FC<ItemRowProps> = ({ item, category, s, canEdit, canAct, busy, info, actions, lastRequired, editing, startEdit, applyEdit, cancelEdit, notify, fmtDate, who }) => {
  const [details, setDetails] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const k = item.key;
  const detailsId = `req-details-${k}`;
  const menuId = `req-actions-${k}`;
  const label = item.text.trim() || s.itemText;
  const isReq = item.importance === 'required';
  const isEditing = editing?.key === k;
  const hasIssues = info.issues.length > 0;
  const classificationOpen = info.classification.filter(w => w.state === 'unresolved' || w.state === 'acknowledged');
  const menuBtn = useRef<HTMLButtonElement>(null);

  // closing the menu with Escape returns the focus to its button
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setMenuOpen(false); menuBtn.current?.focus(); } };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [menuOpen]);
  useEffect(() => { if (isEditing) setMenuOpen(false); }, [isEditing]);

  const meta: React.ReactNode[] = [];
  if (item.origin === 'from_responsibilities') meta.push(<Badge key="resp" tone="blue"><BriefcaseIcon width={12} height={12} />{s.fromResponsibilities}</Badge>);
  if (info.isNew) meta.push(<Badge key="new" tone="slate">{s.newItem}</Badge>);
  else if (item.origin === 'recruiter_added') meta.push(<Badge key="added" tone="slate">{s.addedByRecruiter}</Badge>);
  if (info.edited && !info.isNew) meta.push(<Badge key="edited" tone="amber" title={s.editedHint}>{s.edited}</Badge>);
  if (info.structureState === 'needs_review') meta.push(<Badge key="review" tone="red"><WarnIcon width={12} height={12} />{s.structureNeedsReview}</Badge>);
  if (info.structureState === 'confirmed' || info.structureState === 'corrected' || info.structureState === 'entered')
    meta.push(<Badge key="ok" tone="green">{s.structureOk}</Badge>);
  if (info.changedInDraft && info.structureState && info.structureState !== 'original')
    meta.push(<Badge key="saved" tone="amber" title={s.itemEditedSaved}>{s.savedBadge}</Badge>);

  // the guidance for the action asked for is said where the action was taken (the category card), not in this row: the row moves between
  // the Required and Preferred lists when its importance changes
  const toggleImportance = () => {
    const n: string[] = [];
    if (isReq) { n.push(s.guidePreferred); if (lastRequired) n.push(s.guideLastRequired); }
    notify(n);
    setMenuOpen(false);
    actions.toggleImportance(k);
  };
  const remove = () => {
    notify(isReq && lastRequired ? [s.guideLastRequired] : []);
    setMenuOpen(false);
    actions.remove(k);
  };

  if (isEditing && canEdit) {
    return (
      <li id={`req-item-${k}`} tabIndex={-1} className="rounded-xl border-2 border-primary/40 p-3 bg-white scroll-mt-24 focus:outline-none">
        <ItemEditor item={item} category={category} s={s} isNew={editing!.isNew} lastRequired={lastRequired}
                    onApply={v => applyEdit(k, v)} onCancel={cancelEdit} />
      </li>
    );
  }

  return (
    <li id={`req-item-${k}`} tabIndex={-1}
        className={`rounded-xl border p-3 bg-white scroll-mt-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${hasIssues ? 'border-error' : 'border-border'}`}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {meta.length > 0 && <div className="flex flex-wrap items-center gap-1.5 mb-1">{meta}</div>}
          <p id={`req-text-${k}`} dir="auto" className={`text-sm break-words whitespace-pre-wrap ${item.text.trim() ? 'text-textMain' : 'text-textMuted italic'}`}>
            {label}
          </p>
          {isReq && (
            <p className="text-xs text-textMuted mt-1" data-testid={`weight-${k}`}>
              {s.weightLabel}: <span dir="ltr" className="font-bold text-textMain">{item.weight === null ? '—' : `${item.weight}%`}</span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0 relative">
          <button type="button" className={iconBtn} aria-label={details ? s.hideDetails : s.details} title={details ? s.hideDetails : s.details}
                  aria-expanded={details} aria-controls={detailsId} onClick={() => setDetails(o => !o)}>
            <InfoIcon width={16} height={16} />
          </button>
          {canEdit && (
            <button ref={menuBtn} type="button" className={iconBtn} aria-label={`${s.moreActions}: ${label}`} title={s.moreActions}
                    aria-expanded={menuOpen} aria-controls={menuId} onClick={() => setMenuOpen(o => !o)}>
              <DotsIcon width={18} height={18} />
            </button>
          )}
        </div>
      </div>

      {/* blocking, always visible beside the item */}
      {hasIssues && (
        <ul id={`req-err-${k}`} className="mt-2 text-xs text-error space-y-0.5">
          {info.issues.map((m, n) => <li key={n} className="flex gap-1"><WarnIcon width={12} height={12} className="mt-0.5 shrink-0" /><span>{m}</span></li>)}
        </ul>
      )}
      {info.structureState === 'needs_review' && (
        <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-3" role="group" aria-label={s.structureReviewTitle} data-saved-only={info.changedInDraft ? 'true' : undefined}>
          {info.changedInDraft && <p className="text-[11px] font-black text-amber-900 mb-1">{s.savedBadge}: {s.itemEditedSaved}</p>}
          <p className="text-xs font-black text-amber-900">{s.structureReviewTitle}</p>
          <p className="text-xs text-amber-900 mt-1">{s.structureReviewBody}</p>
          <StructureSummary item={item} s={s} />
          {item.id && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button type="button" className={`${btnGhost} bg-white`} disabled={!canEdit || !canAct || busy} onClick={() => actions.confirmStructure(item.id as string)}>
                {s.structureConfirm}
              </button>
              {canEdit && !canAct && <span className="text-xs text-amber-900">{s.needsSavedState}</span>}
            </div>
          )}
        </div>
      )}
      {classificationOpen.map(w => (
        <p key={w.id} className={`mt-2 text-xs flex gap-1 ${info.changedInDraft ? 'text-textMuted border border-dashed border-amber-400 rounded p-1' : 'text-amber-800'}`} data-saved-only={info.changedInDraft ? 'true' : undefined}>
          <WarnIcon width={12} height={12} className="mt-0.5 shrink-0" />
          <span>{info.changedInDraft && <strong className="text-amber-900">{s.savedBadge}: </strong>}{w.state === 'acknowledged' ? s.stateAcknowledged : s.stateUnresolved}: {s.classificationWhy}
            {info.changedInDraft && <em className="block not-italic text-amber-900">{w.state === 'acknowledged' ? s.ackSavedOnly : s.itemEditedSaved}</em>}</span></p>
      ))}
      {info.structureRecord && info.structureState && info.structureState !== 'original' && info.structureState !== 'needs_review' && (
        <p className="mt-2 text-xs text-textMuted">
          {fmt({ confirmed: s.structureConfirmed, corrected: s.structureCorrected, entered: s.structureEntered }[info.structureState] || '',
            { name: who(info.structureRecord.user_id), date: fmtDate(info.structureRecord.recorded_at) })}
          {info.changedInDraft && <span className="block font-bold text-amber-900" data-testid="structure-saved-only">{s.structureSavedOnly}</span>}
        </p>
      )}

      {/* Actions: Edit, the importance, and Delete; closed until asked for */}
      {canEdit && (
        <div id={menuId} hidden={!menuOpen} className="mt-2 border-t border-border pt-2 flex flex-wrap gap-2" role="group" aria-label={`${s.moreActions}: ${label}`}>
          {menuOpen && (
            <>
              <button type="button" className={btnGhost} onClick={() => { setMenuOpen(false); startEdit(k); }}>{s.editItem}</button>
              <button type="button" className={btnGhost} onClick={toggleImportance}>{isReq ? s.makePreferred : s.makeRequired}</button>
              <button type="button" className={`${btnGhost} text-error`} aria-label={`${s.deleteItem}: ${label}`} onClick={remove}>{s.delete}</button>
            </>
          )}
        </div>
      )}

      {/* Details: source wording, similarity notes and the structured fields (read-only here) */}
      <div id={detailsId} hidden={!details} className="mt-2 border-t border-border pt-2 space-y-3">
        {details && (
          <>
            <div>
              <p className="text-[10px] font-black uppercase text-textMuted mb-0.5">{s.sourceWording}</p>
              {item.source_text
                ? <blockquote lang={/[؀-ۿ]/.test(item.source_text) ? 'ar' : 'en'} dir="auto" className="text-xs border-s-4 border-slate-300 ps-3 py-1 text-textMain bg-slate-50 rounded break-words">{item.source_text}</blockquote>
                : <p className="text-xs text-textMuted">{s.noSource}</p>}
            </div>
            {(item.alternatives || item.experience) && <StructureSummary item={item} s={s} />}
            {info.similar.length > 0 && (
              <ul className="flex flex-wrap gap-1.5" aria-label={s.similarityTitle}>
                {info.similar.map(w => (
                  <li key={w.id}>
                    <button type="button" onClick={() => actions.goToItem(w.other.item_id)} data-saved-only={w.stale ? 'true' : undefined}
                            className={`text-[11px] font-bold px-2 py-1 rounded-lg border bg-slate-50 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${w.stale ? 'border-dashed border-amber-400 text-textMuted' : 'border-border'}`}>
                      {w.kind === 'possible_duplicate' ? s.possibleDuplicate : s.similarRequirement}{w.stale ? ` (${s.similarSaved})` : ''} → {s.goToItem}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </li>
  );
};

const StructureSummary: React.FC<{ item: DraftItem; s: Strings }> = ({ item, s }) => (
  <dl className="mt-2 text-xs text-amber-900 space-y-0.5">
    {item.alternatives && <div className="flex gap-1"><dt className="font-bold">{s.alternatives}:</dt><dd dir="auto" className="break-words">{item.alternatives.join(' / ')}</dd></div>}
    {item.experience && (
      <div className="flex gap-1"><dt className="font-bold">{s.experienceTitle}:</dt>
        <dd dir="auto">{[item.experience.subject, item.experience.min_years !== null ? `${item.experience.min_years}+` : null].filter(Boolean).join(' · ')}</dd></div>
    )}
  </dl>
);

// ── category card ────────────────────────────────────────────────────────────

export const CategoryCard: React.FC<CategoryCardProps> = (p) => {
  const { category, cat, s, canEdit } = p;
  const req = requiredItems(cat), pref = preferredItems(cat);
  const status = requiredStatus(cat);
  const hid = `req-cat-${category}`;
  const name = s.categories[category];
  const catEditing = p.catEdit !== null;
  const [notes, setNotes] = useState<string[]>([]);
  useEffect(() => { setNotes([]); }, [p.editing?.key]);                // the guidance is for the action just taken: it goes when another edit opens or closes
  const catInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (catEditing) catInputRef.current?.focus(); }, [catEditing]);

  const renderItem = (i: DraftItem, lastRequired: boolean) => (
    <ItemRow key={i.key} item={i} category={category} s={s} canEdit={canEdit} canAct={p.canAct} busy={p.busy} info={p.infoFor(i)}
             actions={p.actions} lastRequired={lastRequired} editing={p.editing} startEdit={p.startEdit} applyEdit={p.applyEdit}
             cancelEdit={p.cancelEdit} notify={setNotes} fmtDate={p.fmtDate} who={p.who} />
  );

  return (
    <section id={hid} tabIndex={-1} aria-labelledby={`${hid}-name`} className="bg-white rounded-2xl border border-border shadow-sm scroll-mt-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary">
      <header className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2 justify-between">
        <h4 className="text-sm font-black text-textMain flex items-center gap-2 flex-wrap">
          <span id={`${hid}-name`}>{name}</span>
          {p.edited && <Badge tone="amber" title={s.editedHint}>{s.edited}</Badge>}
          {p.dirty && <Badge tone="slate">{s.unsaved}</Badge>}
        </h4>
        {catEditing && canEdit ? (
          <form className="flex flex-wrap items-center gap-2" aria-label={fmt(s.editCategoryWeight, { name })}
                onSubmit={e => { e.preventDefault(); p.applyCatEdit(); }}
                onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); p.cancelCatEdit(); } }}>
            <label className="text-[10px] font-black text-textMuted uppercase tracking-widest" htmlFor={`${hid}-w`}>{s.categoryWeight}</label>
            <input id={`${hid}-w`} ref={catInputRef} type="text" inputMode="numeric" dir="ltr"
                   aria-invalid={p.catEdit!.error !== null || p.categoryInvalid}
                   aria-describedby={p.catEdit!.error ? `${hid}-w-err` : undefined}
                   className={`${inputCls} !w-20 text-center ${p.catEdit!.error || p.categoryInvalid ? '!border-error' : ''}`}
                   value={p.catEdit!.text} onChange={e => p.setCatText(e.target.value)} />
            <button type="submit" className="px-3 py-1.5 text-xs font-bold rounded-lg bg-primary text-white hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary">{s.applyEdit}</button>
            <button type="button" className={btnGhost} onClick={p.cancelCatEdit}>{s.cancelEdit}</button>
            {p.catEdit!.error && <p id={`${hid}-w-err`} role="alert" className="basis-full text-xs font-bold text-error">{p.catEdit!.error}</p>}
          </form>
        ) : (
          <div className="flex items-center gap-2">
            <span className={`text-sm font-bold ${p.categoryInvalid ? 'text-error' : 'text-textMain'}`} data-testid={`cat-weight-${category}`}>
              {s.weightLabel}: <span dir="ltr">{cat.weight === null ? '—' : `${cat.weight}%`}</span>
            </span>
            {canEdit && (
              <button type="button" className={iconBtn} aria-label={fmt(s.editCategoryWeight, { name })} title={fmt(s.editCategoryWeight, { name })}
                      disabled={p.busy} onClick={p.openCatEdit}>
                <PencilIcon />
              </button>
            )}
          </div>
        )}
      </header>

      {notes.length > 0 && (
        <div className="px-4 pt-3 space-y-1" role="status" data-testid={`notes-${category}`}>
          {notes.map((n, i) => <p key={i} className="text-xs text-textMuted bg-slate-50 rounded-lg p-2">{n}</p>)}
        </div>
      )}

      {p.issues.length > 0 && (
        <ul className="px-4 pt-3 text-xs text-error space-y-0.5">
          {p.issues.map((m, n) => <li key={n} className="flex gap-1"><WarnIcon width={12} height={12} className="mt-0.5 shrink-0" /><span>{m}</span></li>)}
        </ul>
      )}

      <div className="p-4 space-y-5">
        <div>
          <div className="flex items-center justify-between gap-2 mb-2">
            <h5 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{fmt(s.requiredCount, { n: req.length })}</h5>
            {canEdit && (
              <button type="button" className={iconBtn} aria-label={s.addRequired} title={s.addRequired} disabled={p.busy} onClick={() => p.add('required')}>
                <PlusIcon />
              </button>
            )}
          </div>

          {req.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {status.ok ? (
                <p role="status" className="text-xs text-textMuted" data-testid={`required-total-${category}`}>
                  {fmt(s.requiredTotal, { total: status.total })} <span>({s.totalsOk})</span>
                </p>
              ) : (
                <p role="status" className="flex-1 min-w-0 rounded-lg border border-red-300 bg-red-50 px-2.5 py-1.5 text-xs font-bold text-red-900" data-testid={`required-total-${category}`}>
                  {fmt(s.requiredTotalOff, { total: status.total, difference: status.difference > 0 ? `+${status.difference}` : String(status.difference) })}
                </p>
              )}
              {canEdit && (
                <button type="button" className={btnGhost} title={p.equalDisabled ? s.equalizeAlready : s.equalizeHint}
                        aria-label={`${s.equalize}: ${name}. ${p.equalDisabled ? s.equalizeAlready : s.equalizeHint}`}
                        disabled={p.equalDisabled || p.busy} onClick={p.equalize}>{s.equalize}</button>
              )}
            </div>
          )}

          {req.length === 0 ? <p className="text-xs text-textMuted">{s.noRequired}</p> : (
            <ul className="space-y-2">{req.map(i => renderItem(i, req.length === 1))}</ul>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between gap-2 mb-2">
            <h5 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{fmt(s.preferredCount, { n: pref.length })}</h5>
            {canEdit && (
              <button type="button" className={iconBtn} aria-label={s.addPreferred} title={s.addPreferred} disabled={p.busy} onClick={() => p.add('preferred')}>
                <PlusIcon />
              </button>
            )}
          </div>
          {pref.length === 0 ? <p className="text-xs text-textMuted">{s.noPreferred}</p> : (
            <ul className="space-y-2">{pref.map(i => renderItem(i, false))}</ul>
          )}
        </div>
      </div>
    </section>
  );
};

export { CATEGORIES };
