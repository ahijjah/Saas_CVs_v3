import React, { useState } from 'react';
import {
  CATEGORIES, CategoryKey, DraftCategory, DraftItem, ExperienceStructure, parseWhole, preferredItems,
  requiredItems, requiredTotal,
} from '../../utils/requirementsV2';
import { fmt, Strings } from './i18n';
import { BriefcaseIcon, ChevronIcon, QuoteIcon, WarnIcon } from './icons';
import type { RequirementsView } from '../../services/requirementsV2Api';

export interface RowActions {
  setText(key: string, v: string): void;
  setWeight(key: string, v: number | null): void;
  toggleImportance(key: string): void;
  remove(key: string): void;
  setAlternatives(key: string, v: string[] | null): void;
  setExperience(key: string, v: ExperienceStructure | null): void;
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

interface ItemRowProps {
  item: DraftItem; category: CategoryKey; s: Strings; isAr: boolean; canEdit: boolean; canAct: boolean; busy: boolean;
  info: ItemInfo; actions: RowActions; autoFocus: boolean;
  fmtDate: (d: string) => string; who: (id: string) => string;
}

const inputCls = 'w-full px-3 py-2 border border-border rounded-xl text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none disabled:bg-slate-50 disabled:text-textMuted';
const btnGhost = 'px-2.5 py-1.5 text-xs font-bold rounded-lg border border-border text-textMain hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed';

const Badge: React.FC<{ tone: 'blue' | 'amber' | 'slate' | 'green' | 'red'; children: React.ReactNode; title?: string }> = ({ tone, children, title }) => {
  const cls = { blue: 'bg-indigo-100 text-indigo-700', amber: 'bg-amber-100 text-amber-800', slate: 'bg-slate-100 text-slate-600',
    green: 'bg-green-100 text-green-800', red: 'bg-red-100 text-red-700' }[tone];
  return <span title={title} className={`inline-flex items-center gap-1 text-[10px] font-black uppercase px-1.5 py-0.5 rounded-full ${cls}`}>{children}</span>;
};

const ItemRow: React.FC<ItemRowProps> = ({ item, category, s, isAr, canEdit, canAct, busy, info, actions, autoFocus, fmtDate, who }) => {
  const [open, setOpen] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const k = item.key;
  const detailsId = `req-details-${k}`;
  const sourceId = `req-source-${k}`;
  const ro = !canEdit;
  const label = item.text.trim() || s.itemText;
  const isReq = item.importance === 'required';
  const classificationOpen = info.classification.filter(w => w.state === 'unresolved' || w.state === 'acknowledged');

  return (
    <li id={`req-item-${k}`} tabIndex={-1}
        className={`rounded-xl border p-3 bg-white scroll-mt-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${info.issues.length ? 'border-error' : 'border-border'}`}>
      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        {item.origin === 'from_responsibilities' && (
          <Badge tone="blue"><BriefcaseIcon width={12} height={12} />{s.fromResponsibilities}</Badge>
        )}
        {item.origin === 'recruiter_added' && !info.isNew && <Badge tone="slate">{s.addedByRecruiter}</Badge>}
        {info.isNew && <Badge tone="slate">{s.newItem}</Badge>}
        {info.edited && !info.isNew && <Badge tone="amber" title={s.editedHint}>{s.edited}</Badge>}
        {info.structureState === 'needs_review' && <Badge tone="red"><WarnIcon width={12} height={12} />{s.structureNeedsReview}</Badge>}
        {(info.structureState === 'confirmed' || info.structureState === 'corrected' || info.structureState === 'entered') &&
          <Badge tone="green">{s.structureOk}</Badge>}
        {info.changedInDraft && info.structureState && info.structureState !== 'original' && <Badge tone="amber" title={s.itemEditedSaved}>{s.savedBadge}</Badge>}
      </div>

      <div className="flex flex-col sm:flex-row gap-2 sm:items-start">
        <div className="flex-1 min-w-0">
          <label className="sr-only" htmlFor={`req-text-${k}`}>{s.itemText}</label>
          <textarea id={`req-text-${k}`} rows={2} value={item.text} readOnly={ro} autoFocus={autoFocus} dir="auto"
                    aria-invalid={info.issues.length > 0} aria-describedby={info.issues.length ? `req-err-${k}` : undefined}
                    onChange={e => actions.setText(k, e.target.value)} className={`${inputCls} resize-y`} />
        </div>
        {isReq ? (
          <div className="sm:w-28 shrink-0">
            <label className="block text-[10px] font-black text-textMuted uppercase tracking-widest mb-1" htmlFor={`req-weight-${k}`}>{s.itemWeight}</label>
            <input id={`req-weight-${k}`} type="text" inputMode="numeric" dir="ltr" readOnly={ro}
                   aria-label={fmt(s.weightOf, { name: label })}
                   value={item.weight === null ? '' : String(item.weight)}
                   onChange={e => { const p = parseWhole(e.target.value); if (p !== undefined) actions.setWeight(k, p); }}
                   className={`${inputCls} text-center`} />
          </div>
        ) : null}
      </div>

      {info.issues.length > 0 && (
        <ul id={`req-err-${k}`} className="mt-2 text-xs text-error space-y-0.5">
          {info.issues.map((m, n) => <li key={n} className="flex gap-1"><WarnIcon width={12} height={12} className="mt-0.5 shrink-0" /><span>{m}</span></li>)}
        </ul>
      )}

      {/* structure review: always visible, never hidden inside the details */}
      {info.structureState === 'needs_review' && (
        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3" role="group" aria-label={s.structureReviewTitle} data-saved-only={info.changedInDraft ? 'true' : undefined}>
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
      {info.structureRecord && info.structureState && info.structureState !== 'original' && info.structureState !== 'needs_review' && (
        <p className="mt-2 text-xs text-textMuted">
          {fmt({ confirmed: s.structureConfirmed, corrected: s.structureCorrected, entered: s.structureEntered }[info.structureState] || '',
            { name: who(info.structureRecord.user_id), date: fmtDate(info.structureRecord.recorded_at) })}
          {info.changedInDraft && <span className="block font-bold text-amber-900" data-testid="structure-saved-only">{s.structureSavedOnly}</span>}
        </p>
      )}

      {classificationOpen.map(w => (
        <p key={w.id} className={`mt-2 text-xs flex gap-1 ${info.changedInDraft ? 'text-textMuted border border-dashed border-amber-400 rounded p-1' : 'text-amber-800'}`} data-saved-only={info.changedInDraft ? 'true' : undefined}>
          <WarnIcon width={12} height={12} className="mt-0.5 shrink-0" />
          <span>{info.changedInDraft && <strong className="text-amber-900">{s.savedBadge}: </strong>}{w.state === 'acknowledged' ? s.stateAcknowledged : s.stateUnresolved}: {s.classificationWhy}
            {info.changedInDraft && <em className="block not-italic text-amber-900">{w.state === 'acknowledged' ? s.ackSavedOnly : s.itemEditedSaved}</em>}</span></p>
      ))}

      {info.similar.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={s.similarityTitle}>
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

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className={btnGhost} aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen(o => !o)}>
          <span className="inline-flex items-center gap-1">{open ? s.hideDetails : s.details}<ChevronIcon open={open} width={12} height={12} /></span>
        </button>
        <button type="button" className={btnGhost} aria-expanded={showSource} aria-controls={sourceId} onClick={() => setShowSource(o => !o)}>
          <span className="inline-flex items-center gap-1"><QuoteIcon width={12} height={12} />{showSource ? s.hideSource : s.showSource}</span>
        </button>
        {canEdit && (
          <>
            <button type="button" className={btnGhost} onClick={() => actions.toggleImportance(k)}>{isReq ? s.makePreferred : s.makeRequired}</button>
            <button type="button" className={`${btnGhost} text-error`} aria-label={`${s.deleteItem}: ${label}`} onClick={() => actions.remove(k)}>{s.delete}</button>
          </>
        )}
      </div>

      <div id={sourceId} hidden={!showSource} className="mt-2">
        {showSource && (
          item.source_text
            ? <blockquote lang={/[؀-ۿ]/.test(item.source_text) ? 'ar' : 'en'} dir="auto" className="text-xs border-s-4 border-slate-300 ps-3 py-1 text-textMain bg-slate-50 rounded">
                <span className="block text-[10px] font-black uppercase text-textMuted mb-0.5">{s.sourceWording}</span>{item.source_text}
              </blockquote>
            : <p className="text-xs text-textMuted">{s.noSource}</p>
        )}
      </div>

      <div id={detailsId} hidden={!open} className="mt-3">
        {open && <StructureEditor item={item} category={category} s={s} ro={ro} actions={actions} />}
      </div>
    </li>
  );
};

const StructureSummary: React.FC<{ item: DraftItem; s: Strings }> = ({ item, s }) => (
  <dl className="mt-2 text-xs text-amber-900 space-y-0.5">
    {item.alternatives && <div className="flex gap-1"><dt className="font-bold">{s.alternatives}:</dt><dd dir="auto">{item.alternatives.join(' / ')}</dd></div>}
    {item.experience && (
      <div className="flex gap-1"><dt className="font-bold">{s.experienceTitle}:</dt>
        <dd dir="auto">{[item.experience.subject, item.experience.min_years !== null ? `${item.experience.min_years}+` : null].filter(Boolean).join(' · ')}</dd></div>
    )}
  </dl>
);

const StructureEditor: React.FC<{ item: DraftItem; category: CategoryKey; s: Strings; ro: boolean; actions: RowActions }> = ({ item, category, s, ro, actions }) => {
  const k = item.key;
  const alts = item.alternatives;
  return (
    <div className="space-y-4 border-t border-border pt-3">
      <fieldset>
        <legend className="text-[10px] font-black text-textMuted uppercase tracking-widest">{s.alternatives}</legend>
        {alts ? (
          <>
            <p className="text-xs text-textMuted mb-2">{s.alternativesHint}</p>
            <ul className="space-y-2">
              {alts.map((a, n) => (
                <li key={n} className="flex gap-2 items-center">
                  <label className="sr-only" htmlFor={`req-alt-${k}-${n}`}>{fmt(s.alternativeN, { n: n + 1 })}</label>
                  <input id={`req-alt-${k}-${n}`} className={inputCls} dir="auto" value={a} readOnly={ro}
                         onChange={e => actions.setAlternatives(k, alts.map((x, m) => (m === n ? e.target.value : x)))} />
                  {!ro && <button type="button" className={btnGhost} aria-label={`${s.removeAlternative} ${n + 1}`}
                                  onClick={() => actions.setAlternatives(k, alts.filter((_, m) => m !== n))}>×</button>}
                </li>
              ))}
            </ul>
            {!ro && (
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className={btnGhost} onClick={() => actions.setAlternatives(k, [...alts, ''])}>{s.addAlternative}</button>
                <button type="button" className={btnGhost} onClick={() => actions.setAlternatives(k, null)}>{s.clearAlternatives}</button>
              </div>
            )}
          </>
        ) : !ro && <button type="button" className={`${btnGhost} mt-1`} onClick={() => actions.setAlternatives(k, ['', ''])}>{s.makeAlternatives}</button>}
      </fieldset>

      {category === 'experience' && (
        <fieldset>
          <legend className="text-[10px] font-black text-textMuted uppercase tracking-widest">{s.experienceTitle}</legend>
          {item.experience ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-1">
              <div className="sm:col-span-2">
                <label className="block text-xs font-bold mb-1" htmlFor={`req-exp-subject-${k}`}>{s.subject}</label>
                <input id={`req-exp-subject-${k}`} className={inputCls} dir="auto" readOnly={ro} value={item.experience.subject ?? ''}
                       onChange={e => actions.setExperience(k, { ...item.experience!, subject: e.target.value === '' ? null : e.target.value })} />
              </div>
              <div>
                <label className="block text-xs font-bold mb-1" htmlFor={`req-exp-years-${k}`}>{s.years}</label>
                <input id={`req-exp-years-${k}`} className={inputCls} inputMode="numeric" dir="ltr" readOnly={ro}
                       value={item.experience.min_years === null ? '' : String(item.experience.min_years)}
                       onChange={e => { const p = parseWhole(e.target.value); if (p !== undefined) actions.setExperience(k, { ...item.experience!, min_years: p }); }} />
              </div>
              {!ro && <div className="sm:col-span-3"><button type="button" className={btnGhost} onClick={() => actions.setExperience(k, null)}>{s.clearExperience}</button></div>}
            </div>
          ) : !ro && <button type="button" className={`${btnGhost} mt-1`} onClick={() => actions.setExperience(k, { subject: '', min_years: null })}>{s.makeExperience}</button>}
        </fieldset>
      )}
    </div>
  );
};

// ── category card ────────────────────────────────────────────────────────────

export interface CategoryCardProps {
  category: CategoryKey; cat: DraftCategory; s: Strings; isAr: boolean; canEdit: boolean; canAct: boolean; busy: boolean;
  edited: boolean; dirty: boolean; issues: string[];
  infoFor: (item: DraftItem) => ItemInfo; actions: RowActions;
  setCategoryWeight(v: number | null): void; equalize(): void; add(importance: 'required' | 'preferred'): void;
  focusKey: string | null; fmtDate: (d: string) => string; who: (id: string) => string;
}

export const CategoryCard: React.FC<CategoryCardProps> = (p) => {
  const { category, cat, s, canEdit } = p;
  const req = requiredItems(cat), pref = preferredItems(cat);
  const total = requiredTotal(cat);
  const totalOk = req.length === 0 || total === 100;
  const hid = `req-cat-${category}`;
  return (
    <section id={hid} tabIndex={-1} aria-labelledby={`${hid}-name`} className="bg-white rounded-2xl border border-border shadow-sm scroll-mt-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary">
      <header className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2 justify-between">
        <h4 id={`${hid}-h`} className="text-sm font-black text-textMain flex items-center gap-2 flex-wrap">
          <span id={`${hid}-name`}>{s.categories[category]}</span>
          {p.edited && <Badge tone="amber" title={s.editedHint}>{s.edited}</Badge>}
          {p.dirty && <Badge tone="slate">{s.unsaved}</Badge>}
        </h4>
        <div className="flex items-center gap-2">
          <label className="text-[10px] font-black text-textMuted uppercase tracking-widest" htmlFor={`${hid}-w`}>{s.categoryWeight}</label>
          <input id={`${hid}-w`} type="text" inputMode="numeric" dir="ltr" readOnly={!canEdit} className={`${inputCls} !w-20 text-center`}
                 value={cat.weight === null ? '' : String(cat.weight)}
                 onChange={e => { const v = parseWhole(e.target.value); if (v !== undefined) p.setCategoryWeight(v); }} />
        </div>
      </header>

      {p.issues.length > 0 && (
        <ul className="px-4 pt-3 text-xs text-error space-y-0.5">
          {p.issues.map((m, n) => <li key={n} className="flex gap-1"><WarnIcon width={12} height={12} className="mt-0.5 shrink-0" /><span>{m}</span></li>)}
        </ul>
      )}

      <div className="p-4 space-y-5">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
            <h5 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{s.required}</h5>
            <div className="flex flex-wrap items-center gap-2">
              {req.length > 0 && (
                <span className={`text-xs font-bold ${totalOk ? 'text-success' : 'text-error'}`} data-testid={`required-total-${category}`}>
                  {fmt(s.requiredTotal, { total })} <span className="font-normal">({totalOk ? s.totalsOk : s.totalsOff})</span>
                </span>
              )}
              {canEdit && req.length > 0 && (
                <button type="button" className={btnGhost} title={s.equalizeHint} aria-label={`${s.equalize}: ${s.categories[category]}. ${s.equalizeHint}`} onClick={p.equalize}>{s.equalize}</button>
              )}
            </div>
          </div>
          {req.length === 0 ? <p className="text-xs text-textMuted">{s.noRequired}</p> : (
            <ul className="space-y-2">{req.map(i => <ItemRow key={i.key} item={i} category={category} s={s} isAr={p.isAr} canEdit={canEdit} canAct={p.canAct} busy={p.busy}
                                                         info={p.infoFor(i)} actions={p.actions} autoFocus={p.focusKey === i.key} fmtDate={p.fmtDate} who={p.who} />)}</ul>
          )}
          {canEdit && <button type="button" className={`${btnGhost} mt-2`} onClick={() => p.add('required')}>+ {s.addRequired}</button>}
          {canEdit && req.length > 0 && <p className="text-[11px] text-textMuted mt-2">{s.lastRequiredNote}</p>}
        </div>

        <div>
          <h5 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{s.preferred}</h5>
          <p className="text-[11px] text-textMuted mb-2">{s.preferredNoWeight}</p>
          {pref.length === 0 ? <p className="text-xs text-textMuted">{s.noPreferred}</p> : (
            <ul className="space-y-2">{pref.map(i => <ItemRow key={i.key} item={i} category={category} s={s} isAr={p.isAr} canEdit={canEdit} canAct={p.canAct} busy={p.busy}
                                                         info={p.infoFor(i)} actions={p.actions} autoFocus={p.focusKey === i.key} fmtDate={p.fmtDate} who={p.who} />)}</ul>
          )}
          {canEdit && <button type="button" className={`${btnGhost} mt-2`} onClick={() => p.add('preferred')}>+ {s.addPreferred}</button>}
        </div>
      </div>
    </section>
  );
};

export { CATEGORIES };
