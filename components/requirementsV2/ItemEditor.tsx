import React, { useId, useState } from 'react';
import { CategoryKey, DraftItem, ExperienceStructure, Importance, parseWhole } from '../../utils/requirementsV2';
import { fmt, Strings } from './i18n';
import { issueText } from './issues';

// The editor for one item. It works on local values only: Apply hands them to the panel, which changes the DRAFT; Cancel (or Escape)
// discards them. Nothing here saves, and a weight that is not a whole number is not applied.

export interface EditValues {
  text: string;
  importance: Importance;
  weight: number | null;
  alternatives: string[] | null;
  experience: ExperienceStructure | null;
}

export interface ItemEditorProps {
  item: DraftItem; category: CategoryKey; s: Strings; isNew: boolean; lastRequired: boolean;
  onApply(values: EditValues): void; onCancel(): void;
}

const inputCls = 'w-full px-3 py-2 border border-border rounded-xl text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none';
const btnGhost = 'px-2.5 py-1.5 text-xs font-bold rounded-lg border border-border text-textMain hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary';
const btnApply = 'px-3 py-1.5 text-xs font-bold rounded-lg bg-primary text-white hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary';

export const ItemEditor: React.FC<ItemEditorProps> = ({ item, category, s, isNew, lastRequired, onApply, onCancel }) => {
  const uid = useId();
  const k = item.key;
  const [text, setText] = useState(item.text);
  const [importance, setImportance] = useState<Importance>(item.importance);
  const [weightRaw, setWeightRaw] = useState(item.weight === null ? '' : String(item.weight));
  const [alts, setAlts] = useState<string[] | null>(item.alternatives ? [...item.alternatives] : null);
  const [expOn, setExpOn] = useState(item.experience !== null);
  const [expSubject, setExpSubject] = useState(item.experience?.subject ?? '');
  const [expYears, setExpYears] = useState(item.experience?.min_years === null || item.experience === null ? '' : String(item.experience.min_years));
  const [error, setError] = useState<string | null>(null);

  const label = item.text.trim() || s.itemText;
  const weightId = `${uid}-weight`;
  const errorId = `${uid}-error`;
  const reclassifiedLast = importance === 'preferred' && item.importance === 'required' && lastRequired;

  const apply = () => {
    const weight = importance === 'required' ? parseWhole(weightRaw) : null;
    if (weight === undefined) {
      setError(issueText({ code: 'not_whole_number', params: { field: fmt(s.weightOf, { name: label }), value: weightRaw } }, { s, categoryName: () => '' }));
      return;
    }
    const years = expOn ? parseWhole(expYears) : null;
    if (years === undefined) {
      setError(issueText({ code: 'not_whole_number', params: { field: s.years, value: expYears } }, { s, categoryName: () => '' }));
      return;
    }
    const experience = expOn ? { subject: expSubject.trim() === '' ? null : expSubject, min_years: years } : null;
    onApply({ text, importance, weight, alternatives: alts, experience: category === 'experience' ? experience : (item.experience ?? null) });
  };

  return (
    <form
      className="space-y-3"
      aria-labelledby={`${uid}-h`}
      data-testid="item-editor"
      onSubmit={e => { e.preventDefault(); apply(); }}
      onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } }}
    >
      <h6 id={`${uid}-h`} className="text-xs font-black text-textMain">{isNew ? s.newHeading : s.editHeading}</h6>

      <div>
        <label className="block text-xs font-bold mb-1" htmlFor={`${uid}-text`}>{s.itemText}</label>
        <textarea id={`${uid}-text`} rows={2} dir="auto" autoFocus value={text} onChange={e => setText(e.target.value)}
                  className={`${inputCls} break-words`} />
        <p className="text-[11px] text-textMuted mt-1">{s.editWordingHint}</p>
      </div>

      <fieldset>
        <legend className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{s.importanceLabel}</legend>
        <div className="flex flex-wrap gap-3 text-xs font-bold">
          {(['required', 'preferred'] as Importance[]).map(v => (
            <label key={v} className="inline-flex items-center gap-1.5">
              <input type="radio" name={`${uid}-imp`} value={v} checked={importance === v} onChange={() => setImportance(v)} />
              {v === 'required' ? s.required : s.preferred}
            </label>
          ))}
        </div>
        {importance === 'preferred' && <p className="text-xs text-textMuted mt-1.5" data-testid="guide-preferred">{s.guidePreferred}</p>}
        {reclassifiedLast && <p className="text-xs text-amber-900 bg-amber-50 border border-amber-300 rounded-lg p-2 mt-1.5" data-testid="guide-last-required">{s.guideLastRequired}</p>}
      </fieldset>

      {importance === 'required' && (
        <div>
          <label className="block text-xs font-bold mb-1" htmlFor={weightId}>{s.itemWeight}</label>
          <input id={weightId} type="text" inputMode="numeric" dir="ltr" value={weightRaw}
                 aria-label={fmt(s.weightOf, { name: label })}
                 aria-invalid={error !== null} aria-describedby={error ? errorId : undefined}
                 onChange={e => { setWeightRaw(e.target.value); setError(null); }}
                 className={`${inputCls} !w-28 text-center ${error ? '!border-error' : ''}`} />
        </div>
      )}

      <fieldset>
        <legend className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{s.alternatives}</legend>
        {alts ? (
          <>
            <p className="text-xs text-textMuted mb-2">{s.alternativesHint}</p>
            <ul className="space-y-2">
              {alts.map((a, n) => (
                <li key={n} className="flex gap-2 items-center">
                  <label className="sr-only" htmlFor={`${uid}-alt-${n}`}>{fmt(s.alternativeN, { n: n + 1 })}</label>
                  <input id={`${uid}-alt-${n}`} className={inputCls} dir="auto" value={a}
                         onChange={e => setAlts(alts.map((x, m) => (m === n ? e.target.value : x)))} />
                  <button type="button" className={btnGhost} aria-label={`${s.removeAlternative} ${n + 1}`}
                          onClick={() => setAlts(alts.filter((_, m) => m !== n))}>×</button>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" className={btnGhost} onClick={() => setAlts([...alts, ''])}>{s.addAlternative}</button>
              <button type="button" className={btnGhost} onClick={() => setAlts(null)}>{s.clearAlternatives}</button>
            </div>
          </>
        ) : <button type="button" className={btnGhost} onClick={() => setAlts(['', ''])}>{s.makeAlternatives}</button>}
      </fieldset>

      {category === 'experience' && (
        <fieldset>
          <legend className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{s.experienceTitle}</legend>
          {expOn ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <div className="sm:col-span-2">
                <label className="block text-xs font-bold mb-1" htmlFor={`${uid}-exp-subject`}>{s.subject}</label>
                <input id={`${uid}-exp-subject`} className={inputCls} dir="auto" value={expSubject} onChange={e => setExpSubject(e.target.value)} />
              </div>
              <div>
                <label className="block text-xs font-bold mb-1" htmlFor={`${uid}-exp-years`}>{s.years}</label>
                <input id={`${uid}-exp-years`} className={inputCls} inputMode="numeric" dir="ltr" value={expYears}
                       onChange={e => setExpYears(e.target.value)} />
              </div>
              <div className="sm:col-span-3"><button type="button" className={btnGhost} onClick={() => setExpOn(false)}>{s.clearExperience}</button></div>
            </div>
          ) : <button type="button" className={btnGhost} onClick={() => { setExpOn(true); setExpSubject(''); setExpYears(''); }}>{s.makeExperience}</button>}
        </fieldset>
      )}

      {error && <p id={errorId} role="alert" className="text-xs font-bold text-error" data-testid="editor-error">{error}</p>}

      <div className="flex flex-wrap gap-2">
        <button type="submit" className={btnApply}>{s.applyEdit}</button>
        <button type="button" className={btnGhost} onClick={onCancel}>{s.cancelEdit}</button>
      </div>
    </form>
  );
};
