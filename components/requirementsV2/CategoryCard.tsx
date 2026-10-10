import React, { useState } from 'react';
import {
  CATEGORIES, CategoryKey, DraftCategory, DraftItem, ExperienceStructure, parseWhole, preferredItems, requiredItems, requiredStatus,
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
  setWeightText(key: string, raw: string): void;          // the typed text; the panel commits whole numbers and keeps anything else as typed
  confirmStructure(itemId: string): void;
  goToItem(id: string): void;
}

export interface ItemInfo {
  weightMarked: boolean;                   // the item's Required weight is part of a problem (the item itself, or its category's Required total)
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
  info: ItemInfo; actions: RowActions; autoFocus: boolean; rawWeight: string | undefined;
  fmtDate: (d: string) => string; who: (id: string) => string;
}

const inputCls = 'w-full px-3 py-2 border border-border rounded-xl text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none disabled:bg-slate-50 disabled:text-textMuted';
const invalidCls = '!border-error focus:!border-error';
const btnGhost = 'px-2.5 py-1.5 text-xs font-bold rounded-lg border border-border text-textMain hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50 disabled:cursor-not-allowed';

const Badge: React.FC<{ tone: 'blue' | 'amber' | 'slate' | 'green' | 'red'; children: React.ReactNode; title?: string }> = ({ tone, children, title }) => {
  const cls = { blue: 'bg-indigo-100 text-indigo-700', amber: 'bg-amber-100 text-amber-800', slate: 'bg-slate-100 text-slate-600',
    green: 'bg-green-100 text-green-800', red: 'bg-red-100 text-red-700' }[tone];
  return <span title={title} className={`inline-flex items-center gap-1 text-[10px] font-black uppercase px-1.5 py-0.5 rounded-full ${cls}`}>{children}</span>;
};

// One compact row per item: the wording, its importance and (for Required items) its weight are always visible. Source wording, OR alternatives and
// structured experience are under "Details"; moving or deleting the item is under "Actions". Blocking matters stay visible in the row: a problem with
// the item, a structure review that is still needed, and an unresolved classification.
const ItemRow: React.FC<ItemRowProps> = ({ item, category, s, isAr, canEdit, canAct, busy, info, actions, autoFocus, rawWeight, fmtDate, who }) => {
  const [details, setDetails] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const k = item.key;
  const detailsId = `req-details-${k}`;
  const actionsId = `req-actions-${k}`;
  const ro = !canEdit;
  const label = item.text.trim() || s.itemText;
  const isReq = item.importance === 'required';
  const hasIssues = info.issues.length > 0;
  const classificationOpen = info.classification.filter(w => w.state === 'unresolved' || w.state === 'acknowledged');
  const weightShown = rawWeight !== undefined ? rawWeight : (item.weight === null ? '' : String(item.weight));
  const weightInvalid = rawWeight !== undefined || info.weightMarked;

  const meta: React.ReactNode[] = [];
  meta.push(<Badge key="imp" tone={isReq ? 'blue' : 'slate'}>{isReq ? s.required : s.preferred}</Badge>);
  if (item.origin === 'from_responsibilities') meta.push(<Badge key="resp" tone="blue"><BriefcaseIcon width={12} height={12} />{s.fromResponsibilities}</Badge>);
  if (item.origin === 'recruiter_added' && !info.isNew) meta.push(<Badge key="added" tone="slate">{s.addedByRecruiter}</Badge>);
  if (info.isNew) meta.push(<Badge key="new" tone="slate">{s.newItem}</Badge>);
  if (info.edited && !info.isNew) meta.push(<Badge key="edited" tone="amber" title={s.editedHint}>{s.edited}</Badge>);
  if (info.structureState === 'needs_review') meta.push(<Badge key="review" tone="red"><WarnIcon width={12} height={12} />{s.structureNeedsReview}</Badge>);
  if (info.structureState === 'confirmed' || info.structureState === 'corrected' || info.structureState === 'entered')
    meta.push(<Badge key="ok" tone="green">{s.structureOk}</Badge>);
  if (info.changedInDraft && info.structureState && info.structureState !== 'original')
    meta.push(<Badge key="saved" tone="amber" title={s.itemEditedSaved}>{s.savedBadge}</Badge>);

  return (
    <li id={`req-item-${k}`} tabIndex={-1}
        className={`rounded-xl border p-2.5 bg-white scroll-mt-24 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary ${hasIssues ? 'border-error' : 'border-border'}`}>
      <div className="flex flex-wrap items-center gap-1.5 mb-1.5">{meta}</div>

      {/* the wording takes the full width of the card; the weight and the two controls sit on the line below (narrow cards stay readable) */}
      <div className="min-w-0">
        <label className="sr-only" htmlFor={`req-text-${k}`}>{s.itemText}</label>
        <input id={`req-text-${k}`} type="text" value={item.text} readOnly={ro} autoFocus={autoFocus} dir="auto"
               aria-invalid={hasIssues} aria-describedby={hasIssues ? `req-err-${k}` : undefined}
               onChange={e => actions.setText(k, e.target.value)} className={inputCls} />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        {isReq ? (
          <label htmlFor={`req-weight-${k}`} className="flex items-center gap-2 text-[10px] font-black text-textMuted uppercase tracking-widest">
            {s.itemWeight}
            <input id={`req-weight-${k}`} type="text" inputMode="numeric" dir="ltr" readOnly={ro}
                   aria-label={fmt(s.weightOf, { name: label })} aria-invalid={weightInvalid}
                   aria-describedby={hasIssues ? `req-err-${k}` : undefined}
                   value={weightShown} onChange={e => actions.setWeightText(k, e.target.value)}
                   className={`${inputCls} !w-24 text-center normal-case tracking-normal font-normal ${weightInvalid ? invalidCls : ''}`} />
          </label>
        ) : null}
        <div className="flex gap-1.5 ms-auto">
          <button type="button" className={btnGhost} aria-expanded={details} aria-controls={detailsId} onClick={() => setDetails(o => !o)}>
            {details ? s.hideDetails : s.details}
          </button>
          {canEdit && (
            <button type="button" className={btnGhost} aria-expanded={actionsOpen} aria-controls={actionsId} onClick={() => setActionsOpen(o => !o)}>
              {actionsOpen ? s.hideActions : s.actions}
            </button>
          )}
        </div>
      </div>

      {hasIssues && (
        <ul id={`req-err-${k}`} className="mt-2 text-xs text-error space-y-0.5">
          {info.issues.map((m, n) => <li key={n} className="flex gap-1"><WarnIcon width={12} height={12} className="mt-0.5 shrink-0" /><span>{m}</span></li>)}
        </ul>
      )}

      {/* blocking, always visible: a structure review still needed, and an unresolved classification */}
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

      {/* under Details: the source wording, the similarity notes and the structured fields */}
      <div id={detailsId} hidden={!details} className="mt-2 border-t border-border pt-2 space-y-3">
        {details && (
          <>
            <div>
              <p className="text-[10px] font-black uppercase text-textMuted mb-0.5">{s.sourceWording}</p>
              {item.source_text
                ? <blockquote lang={/[؀-ۿ]/.test(item.source_text) ? 'ar' : 'en'} dir="auto" className="text-xs border-s-4 border-slate-300 ps-3 py-1 text-textMain bg-slate-50 rounded">{item.source_text}</blockquote>
                : <p className="text-xs text-textMuted">{s.noSource}</p>}
            </div>
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
            <StructureEditor item={item} category={category} s={s} ro={ro} actions={actions} />
          </>
        )}
      </div>

      {/* under Actions: secondary actions for this item */}
      {canEdit && (
        <div id={actionsId} hidden={!actionsOpen} className="mt-2 border-t border-border pt-2 flex flex-wrap gap-2">
          {actionsOpen && (
            <>
              <button type="button" className={btnGhost} onClick={() => actions.toggleImportance(k)}>{isReq ? s.makePreferred : s.makeRequired}</button>
              <button type="button" className={`${btnGhost} text-error`} aria-label={`${s.deleteItem}: ${label}`} onClick={() => actions.remove(k)}>{s.delete}</button>
            </>
          )}
        </div>
      )}
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
  edited: boolean; dirty: boolean; issues: string[]; categoryInvalid: boolean;
  infoFor: (item: DraftItem) => ItemInfo; actions: RowActions;
  rawWeights: Record<string, string>; rawCategoryWeight: string | undefined;
  setCategoryWeightText(raw: string): void; equalDisabled: boolean; equalize(): void; add(importance: 'required' | 'preferred'): void;
  focusKey: string | null; fmtDate: (d: string) => string; who: (id: string) => string;
}

export const CategoryCard: React.FC<CategoryCardProps> = (p) => {
  const { category, cat, s, canEdit } = p;
  const req = requiredItems(cat), pref = preferredItems(cat);
  const status = requiredStatus(cat);
  const hid = `req-cat-${category}`;
  const catWeightShown = p.rawCategoryWeight !== undefined ? p.rawCategoryWeight : (cat.weight === null ? '' : String(cat.weight));
  const catInvalid = p.rawCategoryWeight !== undefined || p.categoryInvalid;
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
          <input id={`${hid}-w`} type="text" inputMode="numeric" dir="ltr" readOnly={!canEdit} aria-invalid={catInvalid}
                 className={`${inputCls} !w-20 text-center ${catInvalid ? invalidCls : ''}`}
                 value={catWeightShown}
                 onChange={e => p.setCategoryWeightText(e.target.value)} />
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
                <span role="status" className={`text-xs font-bold ${status.ok ? 'text-success' : 'text-error'}`} data-testid={`required-total-${category}`}>
                  {status.ok
                    ? <>{fmt(s.requiredTotal, { total: status.total })} <span className="font-normal">({s.totalsOk})</span></>
                    : fmt(s.requiredTotalOff, { total: status.total, difference: status.difference > 0 ? `+${status.difference}` : String(status.difference) })}
                </span>
              )}
              {canEdit && req.length > 0 && (
                <button type="button" className={btnGhost} title={p.equalDisabled ? s.equalizeAlready : s.equalizeHint}
                        aria-label={`${s.equalize}: ${s.categories[category]}. ${p.equalDisabled ? s.equalizeAlready : s.equalizeHint}`}
                        disabled={p.equalDisabled || p.busy} onClick={p.equalize}>{s.equalize}</button>
              )}
            </div>
          </div>
          {req.length === 0 ? <p className="text-xs text-textMuted">{s.noRequired}</p> : (
            <ul className="space-y-2">{req.map(i => <ItemRow key={i.key} item={i} category={category} s={s} isAr={p.isAr} canEdit={canEdit} canAct={p.canAct} busy={p.busy}
                                                         info={p.infoFor(i)} actions={p.actions} autoFocus={p.focusKey === i.key} rawWeight={p.rawWeights[`item:${i.key}`]}
                                                         fmtDate={p.fmtDate} who={p.who} />)}</ul>
          )}
          {canEdit && <button type="button" className={`${btnGhost} mt-2`} onClick={() => p.add('required')}>+ {s.addRequired}</button>}
          {canEdit && req.length > 0 && <p className="text-[11px] text-textMuted mt-2">{s.lastRequiredNote}</p>}
        </div>

        <div>
          <h5 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{s.preferred}</h5>
          <p className="text-[11px] text-textMuted mb-2">{s.preferredNoWeight}</p>
          {pref.length === 0 ? <p className="text-xs text-textMuted">{s.noPreferred}</p> : (
            <ul className="space-y-2">{pref.map(i => <ItemRow key={i.key} item={i} category={category} s={s} isAr={p.isAr} canEdit={canEdit} canAct={p.canAct} busy={p.busy}
                                                         info={p.infoFor(i)} actions={p.actions} autoFocus={p.focusKey === i.key} rawWeight={undefined}
                                                         fmtDate={p.fmtDate} who={p.who} />)}</ul>
          )}
          {canEdit && <button type="button" className={`${btnGhost} mt-2`} onClick={() => p.add('preferred')}>+ {s.addPreferred}</button>}
        </div>
      </div>
    </section>
  );
};

export { CATEGORIES };
