import React, { useState } from 'react';
import type { GateStatus, MandatoryStatus, MatrixColumn, MatrixFilters, PreferredStatus } from '../services/matrixApi';
import {
  columnLabel, ELIGIBILITIES, FLAG_KINDS, Lang, matrixCopy, statusLabel, toggleValue, WORKFLOW_STATUSES, workflowLabel,
} from '../utils/matrix';

interface Props {
  registry: MatrixColumn[];
  filters: MatrixFilters;
  lang: Lang;
  onChange: (filters: MatrixFilters) => void;
}

const RESULTS = {
  mandatory: ['met', 'partially_met', 'not_met', 'unclear', 'not_assessed'],
  preferred: ['yes', 'no', 'unclear', 'not_assessed'],        // a Preferred item has no partial result
  gate: ['passed', 'failed', 'review_required', 'not_assessed'],
} as const;

const chip = (on: boolean) => `px-2 py-0.5 rounded-full border text-xs ${on ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-700 border-slate-300'}`;

/** Explicit filters. Each one narrows the list; none of them ranks or scores anything. */
export default function MatrixFiltersPanel({ registry, filters, lang, onChange }: Props) {
  const copy = matrixCopy(lang);
  const criteria = registry.filter((c) => c.kind !== 'static' && c.available);
  const [target, setTarget] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const column = criteria.find((c) => c.id === target) ?? null;
  const set = (patch: Partial<MatrixFilters>) => onChange({ ...filters, ...patch });
  const active = [
    ...(filters.mandatory ?? []).map((f) => ({ kind: 'mandatory' as const, ref: f.item_id, results: f.results as string[] })),
    ...(filters.preferred ?? []).map((f) => ({ kind: 'preferred' as const, ref: f.item_id, results: f.results as string[] })),
    ...(filters.gates ?? []).map((f) => ({ kind: 'gate' as const, ref: f.gate_key, results: f.results as string[] })),
  ];
  const nameOf = (kind: string, ref: string) => {
    const found = criteria.find((c) => c.kind === kind && c.ref === ref);
    return found ? columnLabel(found, lang) : ref;
  };
  const removeCriteria = (kind: string, ref: string) => set({
    mandatory: (filters.mandatory ?? []).filter((f) => !(kind === 'mandatory' && f.item_id === ref)),
    preferred: (filters.preferred ?? []).filter((f) => !(kind === 'preferred' && f.item_id === ref)),
    gates: (filters.gates ?? []).filter((f) => !(kind === 'gate' && f.gate_key === ref)),
  });
  const addCriteria = () => {
    if (!column || column.ref === null || picked.length === 0) return;
    if (column.kind === 'mandatory') set({ mandatory: [...(filters.mandatory ?? []).filter((f) => f.item_id !== column.ref), { item_id: column.ref, results: picked as MandatoryStatus[] }] });
    if (column.kind === 'preferred') set({ preferred: [...(filters.preferred ?? []).filter((f) => f.item_id !== column.ref), { item_id: column.ref, results: picked as PreferredStatus[] }] });
    if (column.kind === 'gate') set({ gates: [...(filters.gates ?? []).filter((f) => f.gate_key !== column.ref), { gate_key: column.ref, results: picked as GateStatus[] }] });
    setTarget('');
    setPicked([]);
  };

  return (
    <section data-testid="matrix-filters" aria-label={copy.filters} className="rounded-xl border border-slate-200 bg-white p-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.eligibility}</legend>
        <div className="flex flex-wrap gap-1">{ELIGIBILITIES.map((e) => (
          <button key={e} type="button" aria-pressed={!!filters.eligibility?.includes(e)} data-testid={`filter-eligibility-${e}`} className={chip(!!filters.eligibility?.includes(e))}
            onClick={() => set({ eligibility: toggleValue(filters.eligibility, e) })}>{statusLabel(e, lang)}</button>))}
        </div></fieldset>
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.scoreRange}</legend>
        <div className="flex gap-2">
          <input type="number" min={0} max={100} aria-label={`${copy.scoreRange} ${copy.min}`} placeholder={copy.min} value={filters.score_min ?? ''} data-testid="filter-score-min"
            onChange={(e) => set({ score_min: e.target.value === '' ? null : Number(e.target.value) })} className="w-20 border border-slate-300 rounded px-2 py-1 text-sm" />
          <input type="number" min={0} max={100} aria-label={`${copy.scoreRange} ${copy.max}`} placeholder={copy.max} value={filters.score_max ?? ''} data-testid="filter-score-max"
            onChange={(e) => set({ score_max: e.target.value === '' ? null : Number(e.target.value) })} className="w-20 border border-slate-300 rounded px-2 py-1 text-sm" />
        </div></fieldset>
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.scoringStatus}</legend>
        <div className="flex flex-wrap gap-1">{(['scored', 'review_required', 'not_scored', 'legacy', 'none'] as const).filter((s) => s !== 'none').map((s) => (
          <button key={s} type="button" aria-pressed={!!filters.scoring_status?.includes(s)} className={chip(!!filters.scoring_status?.includes(s))}
            onClick={() => set({ scoring_status: toggleValue(filters.scoring_status, s) })}>{copy.scoringState[s]}</button>))}
        </div></fieldset>
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.workflow}</legend>
        <div className="flex flex-wrap gap-1">{WORKFLOW_STATUSES.map((s) => (
          <button key={s} type="button" aria-pressed={!!filters.workflow_status?.includes(s)} className={chip(!!filters.workflow_status?.includes(s))}
            onClick={() => set({ workflow_status: toggleValue(filters.workflow_status, s) })}>{workflowLabel(s, lang)}</button>))}
        </div></fieldset>
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.flags}</legend>
        <div className="flex flex-wrap gap-1">{FLAG_KINDS.map((k) => (
          <button key={k} type="button" aria-pressed={!!filters.flags?.includes(k)} data-testid={`filter-flag-${k}`} className={chip(!!filters.flags?.includes(k))}
            onClick={() => set({ flags: toggleValue(filters.flags, k) })}>{copy.flag[k]}</button>))}
        </div></fieldset>
      <fieldset><legend className="text-xs font-bold text-slate-700 mb-1">{copy.applied}</legend>
        <div className="flex gap-2">
          <input type="date" aria-label={`${copy.applied} ${copy.from}`} value={filters.applied_after ?? ''} onChange={(e) => set({ applied_after: e.target.value || null })} className="border border-slate-300 rounded px-2 py-1 text-sm" />
          <input type="date" aria-label={`${copy.applied} ${copy.to}`} value={filters.applied_before ?? ''} onChange={(e) => set({ applied_before: e.target.value || null })} className="border border-slate-300 rounded px-2 py-1 text-sm" />
        </div></fieldset>
      <fieldset className="md:col-span-2 lg:col-span-3"><legend className="text-xs font-bold text-slate-700 mb-1">{copy.criteria}</legend>
        <ul className="flex flex-wrap gap-2 mb-2" data-testid="criteria-filters">{active.map((f) => (
          <li key={`${f.kind}:${f.ref}`} className="flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs">
            <span dir="auto">{nameOf(f.kind, f.ref)}: {f.results.map((r) => statusLabel(r, lang)).join(' / ')}</span>
            <button type="button" aria-label={`${copy.removeFilter}: ${nameOf(f.kind, f.ref)}`} onClick={() => removeCriteria(f.kind, f.ref)}>✕</button>
          </li>))}</ul>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label={copy.chooseCriteria} value={target} data-testid="criteria-target" onChange={(e) => { setTarget(e.target.value); setPicked([]); }}
            className="border border-slate-300 rounded px-2 py-1 text-sm max-w-xs">
            <option value="">{copy.chooseCriteria}</option>
            {criteria.map((c) => <option key={c.id} value={c.id}>{copy.groups[c.group]} — {columnLabel(c, lang)}</option>)}
          </select>
          {column && RESULTS[column.kind as 'mandatory' | 'preferred' | 'gate'].map((r) => (
            <button key={r} type="button" aria-pressed={picked.includes(r)} className={chip(picked.includes(r))} onClick={() => setPicked(toggleValue(picked, r))}>{statusLabel(r, lang)}</button>))}
          <button type="button" disabled={!column || picked.length === 0} onClick={addCriteria} data-testid="criteria-add"
            className="px-3 py-1 rounded bg-slate-900 text-white text-xs font-semibold disabled:opacity-40">{copy.addCriteria}</button>
        </div>
      </fieldset>
    </section>
  );
}
