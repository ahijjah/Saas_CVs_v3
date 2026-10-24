import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLanguage } from '../context/LanguageContext';
import MatrixColumnChooser from '../components/MatrixColumnChooser';
import MatrixEvidenceDrawer, { DrawerFocus } from '../components/MatrixEvidenceDrawer';
import MatrixFiltersPanel from '../components/MatrixFilters';
import {
  exportMatrix, getMatrixColumns, getMatrixConfig, getMatrixRows, MatrixApiError, MatrixColumn, MatrixConfig, MatrixLayout, MatrixPage, MatrixRow,
  resetMatrixLayout, saveMatrixLayout,
} from '../services/matrixApi';
import {
  columnLabel, DEFAULT_SORT, EMPTY_FILTERS, hasFilters, Lang, matrixCopy, NAME_COLUMN, sameLayout, SEVERITY_TONE, statusLabel, statusTone, summaryText,
  TONE_CLASS, topSeverity, workflowLabel,
} from '../utils/matrix';

interface Props {
  jobId: string;
  auth: { user?: { role?: string } | null };
  onBack: () => void;
  onOpenApplication: (jobId: string, applicationId: string) => void;
  /** Test seam: skips the first requests. */
  initial?: { columns: MatrixColumn[]; config: MatrixConfig; page: MatrixPage };
}

const Badge = ({ status, lang, testid }: { status: string; lang: Lang; testid?: string }) => (
  <span data-testid={testid} data-status={status} className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-bold whitespace-nowrap ${TONE_CLASS[statusTone(status)]}`}>
    {statusLabel(status, lang)}
  </span>
);

/** The Final Candidate Matrix of one job: a transparent longlist where the overall score is one element among several. */
export const CandidateMatrix: React.FC<Props> = ({ jobId, auth, onBack, onOpenApplication, initial }) => {
  const { lang: language } = useLanguage();
  const lang: Lang = language === 'ar' ? 'ar' : 'en';
  const copy = matrixCopy(lang);
  const role = auth?.user?.role;
  const [registry, setRegistry] = useState<MatrixColumn[]>(initial?.columns ?? []);
  const [config, setConfig] = useState<MatrixConfig | null>(initial?.config ?? null);
  const [layout, setLayout] = useState<MatrixLayout | null>(initial?.config.effective ?? null);
  const [saved, setSaved] = useState<MatrixLayout | null>(initial?.config.effective ?? null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<MatrixPage | null>(initial?.page ?? null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowsLoading, setRowsLoading] = useState(!initial);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [panel, setPanel] = useState<'columns' | 'filters' | null>(null);
  const [drawer, setDrawer] = useState<{ row: MatrixRow; focus: DrawerFocus } | null>(null);
  const [searchDraft, setSearchDraft] = useState(initial?.config.effective.filters.search ?? '');
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const skipFirst = useRef(!!initial);

  const byId = useMemo(() => new Map(registry.map((c) => [c.id, c])), [registry]);
  const canShared = !!config?.permissions.edit_shared;
  const canPersonal = !!config?.permissions.edit_personal;
  const canExport = !!config?.permissions.export;

  const loadConfig = useCallback(async () => {
    const next = await getMatrixConfig(jobId);
    setConfig(next);
    setLayout(next.effective);
    setSaved(next.effective);
    setSearchDraft(next.effective.filters.search ?? '');
    setPage(1);
    return next;
  }, [jobId]);

  useEffect(() => {
    if (initial) return;
    let live = true;
    Promise.all([getMatrixColumns(jobId), getMatrixConfig(jobId)])
      .then(([cols, cfg]) => {
        if (!live) return;
        setRegistry(cols.columns); setConfig(cfg); setLayout(cfg.effective); setSaved(cfg.effective); setSearchDraft(cfg.effective.filters.search ?? '');
      })
      .catch((err) => { if (live) setLoadError(err instanceof Error ? err.message : copy.loadFailed); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // The search box commits to the filters shortly after typing stops.
  useEffect(() => {
    if (!layout || (layout.filters.search ?? '') === searchDraft.trim()) return;
    const timer = setTimeout(() => { setLayout((l) => l && { ...l, filters: { ...l.filters, search: searchDraft.trim() || null } }); setPage(1); }, 300);
    return () => clearTimeout(timer);
  }, [searchDraft, layout]);

  const sort = layout?.sort ?? DEFAULT_SORT;
  useEffect(() => {
    if (!layout) return;
    if (skipFirst.current) { skipFirst.current = false; return; }
    const mine = ++request.current;
    setRowsLoading(true);
    getMatrixRows(jobId, { columns: layout.columns, filters: layout.filters, sort, page, page_size: layout.page_size })
      .then((result) => { if (mine === request.current) { setData(result); setLoadError(null); } })
      .catch((err) => { if (mine === request.current) setLoadError(err instanceof Error ? err.message : copy.loadFailed); })
      .finally(() => { if (mine === request.current) setRowsLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, layout?.columns, layout?.filters, layout?.sort, layout?.page_size, page]);

  if (loadError && !data && !layout) {
    return <div data-testid="matrix-error" role="alert" className="p-6 text-sm text-red-700">{copy.loadFailed} <button type="button" className="underline" onClick={() => window.location.reload()}>{copy.retry}</button></div>;
  }
  if (!layout || !config) return <div data-testid="matrix-loading" className="p-6 text-sm text-slate-500">{copy.loading}</div>;

  const dirty = saved ? !sameLayout(layout, saved) : false;
  const patch = (next: Partial<MatrixLayout>) => { setLayout({ ...layout, ...next }); setPage(1); };
  const visible = layout.columns.map((id) => byId.get(id)).filter((c): c is MatrixColumn => !!c && c.available);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const flash = (tone: 'ok' | 'error', text: string) => setNotice({ tone, text });
  const run = async (work: () => Promise<void>) => {
    setBusy(true); setNotice(null);
    try { await work(); } catch (err) {
      flash('error', err instanceof MatrixApiError && err.code === 'version_conflict' ? copy.conflict : err instanceof Error ? err.message : copy.loadFailed);
    } finally { setBusy(false); }
  };
  const save = (scope: 'job' | 'personal') => run(async () => {
    await saveMatrixLayout(jobId, scope, layout, config.layers[scope]?.version ?? null);
    await loadConfig(); flash('ok', copy.saved);
  });
  const reset = (scope: 'job' | 'personal') => run(async () => { await resetMatrixLayout(jobId, scope); await loadConfig(); flash('ok', copy.resetDone); });
  const download = () => run(async () => {
    const { blob, filename } = await exportMatrix(jobId, { columns: layout.columns, filters: layout.filters, sort, lang });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  });
  const toggleSort = (id: string) => patch({ sort: { column: id, direction: sort.column === id && sort.direction === 'asc' ? 'desc' : 'asc' } });
  const open = (row: MatrixRow, focus: Partial<DrawerFocus> = {}) => setDrawer({ row, focus: { itemIds: focus.itemIds ?? [], gateKeys: focus.gateKeys ?? [] } });

  const frozenStyle = (id: string, header = false): React.CSSProperties =>
    layout.frozen_candidate && id === NAME_COLUMN ? { position: 'sticky', insetInlineStart: 0, zIndex: header ? 3 : 2, background: header ? '#f8fafc' : '#ffffff', boxShadow: '1px 0 0 #e2e8f0' } : {};

  const cell = (row: MatrixRow, column: MatrixColumn): React.ReactNode => {
    switch (column.id) {
      case NAME_COLUMN:
        return (
          <div className="flex flex-col gap-0.5">
            <button type="button" data-testid={`open-candidate-${row.application_id}`} onClick={() => onOpenApplication(jobId, row.application_id)}
              dir="auto" className="text-start font-semibold text-slate-900 underline-offset-2 hover:underline">{row.candidate_name || '—'}</button>
          </div>);
      case 'candidate.applied_at': return row.applied_at ? new Date(row.applied_at).toLocaleDateString(lang === 'ar' ? 'ar' : 'en') : '';
      case 'candidate.source': return (row.source ?? '').replace(/_/g, ' ');
      case 'candidate.assigned_to': return row.assigned_to?.name ?? '';
      case 'candidate.tags': return row.tags.map((t) => t.name).join(', ');
      case 'workflow.status': return workflowLabel(row.workflow_status, lang);
      case 'decision.eligibility': return <Badge status={row.eligibility} lang={lang} testid={`eligibility-${row.application_id}`} />;
      case 'decision.score':
        return (
          <span data-testid={`score-${row.application_id}`} className="tabular-nums">
            {row.score ?? '—'}{row.scoring.stale && <span className="ms-1 text-[10px] text-amber-700" title={copy.staleHelp(row.scoring.revision_used)}>{copy.stale}</span>}
          </span>);
      case 'decision.scoring':
        return <span className="text-xs">{copy.scoringState[row.scoring.state] ?? row.scoring.state}{row.scoring.method !== 'none' && <span className="text-slate-500"> · {copy.method[row.scoring.method]}</span>}</span>;
      case 'mandatory.summary': {
        const m = row.mandatory;
        return (
          <button type="button" data-testid={`mandatory-summary-${row.application_id}`} onClick={() => open(row)} className="text-start text-xs hover:underline">
            {summaryText([['met', m.met], ['partially_met', m.partially_met], ['not_met', m.not_met], ['unclear', m.unclear]], m.total, m.assessed, lang)}
            {m.assessed && (m.unclear ?? 0) > 0 && <span className="ms-1 text-amber-700 font-bold" aria-label={statusLabel('unclear', lang)}>⚑</span>}
          </button>);
      }
      case 'preferred.summary': {
        const p = row.preferred;
        return <button type="button" data-testid={`preferred-summary-${row.application_id}`} onClick={() => open(row)} className="text-start text-xs hover:underline">
          {summaryText([['yes', p.yes], ['no', p.no], ['unclear', p.unclear]], p.total, p.assessed, lang)}</button>;
      }
      case 'gates.summary': {
        const g = row.gates;
        return <button type="button" data-testid={`gates-summary-${row.application_id}`} onClick={() => open(row)} className="text-start text-xs hover:underline">
          {summaryText([['passed', g.passed], ['failed', g.failed], ['review_required', g.review_required], ['not_assessed', g.not_assessed]], g.total, g.assessed, lang)}</button>;
      }
      case 'decision.flags': {
        const top = topSeverity(row);
        if (!top) return <span className="text-xs text-slate-500">{copy.noFlags}</span>;
        return (
          <div className="flex flex-col items-start gap-1">
            <button type="button" data-testid={`flags-${row.application_id}`} data-severity={top} onClick={() => open(row, {
              itemIds: row.flags.items.flatMap((f) => f.targets.filter((t) => t.type === 'item').map((t) => t.id)),
              gateKeys: row.flags.items.flatMap((f) => f.targets.filter((t) => t.type === 'gate').map((t) => t.id)) })}
              className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${TONE_CLASS[SEVERITY_TONE[top]]}`}>
              {row.flags.total} · {copy.severity[top]}
            </button>
            <ul className="text-[11px] text-slate-600">{row.flags.items.map((f) => <li key={f.kind}>{copy.flag[f.kind]} {f.count}</li>)}</ul>
          </div>);
      }
      default: {
        const c = row.cells[column.id];
        if (!c) return null;
        const label = <Badge status={c.status} lang={lang} />;
        if (c.status === 'not_assessed') return label;
        return (
          <button type="button" data-testid={`result-${column.id}-${row.application_id}`} data-status={c.status} onClick={() => open(row, column.kind === 'gate' ? { gateKeys: [column.ref ?? ''] } : { itemIds: [column.ref ?? ''] })}
            className="inline-flex items-center gap-1" aria-label={`${columnLabel(column, lang)}: ${statusLabel(c.status, lang)}`}>
            {label}{c.status === 'unclear' && <span className="text-amber-700 font-bold" title={copy.flag.unclear_criteria}>⚑</span>}{c.overridden && <span className="text-blue-700" title={copy.gateOverridden}>✎</span>}
          </button>);
      }
    }
  };

  return (
    <div data-testid="matrix-page" className="space-y-4 p-4 md:p-6 max-w-full">
      <header className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onBack} className="text-sm underline">{copy.back}</button>
        <h1 className="text-xl font-black text-slate-900 flex-1">{copy.title}</h1>
        <span data-testid="matrix-source" className="text-xs rounded-full bg-slate-100 px-2 py-1">{copy.viewSource[config.source]}</span>
        {dirty && <span data-testid="matrix-unsaved" className="text-xs rounded-full bg-amber-100 text-amber-800 px-2 py-1">{copy.unsaved}</span>}
      </header>
      <p data-testid="matrix-principle" className="text-xs text-slate-600 rounded-lg border border-slate-200 bg-slate-50 p-3">{copy.principle}</p>
      {config.unavailable_columns.length > 0 && <p data-testid="matrix-unavailable" className="text-xs text-amber-800">{copy.unavailableColumns(config.unavailable_columns.length)}</p>}
      {!canPersonal && !canShared && <p className="text-xs text-slate-500">{copy.readOnly}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <input value={searchDraft} onChange={(e) => setSearchDraft(e.target.value)} aria-label={copy.search} placeholder={copy.search} data-testid="matrix-search"
          dir="auto" className="border border-slate-300 rounded px-3 py-1.5 text-sm w-56" />
        <button type="button" aria-expanded={panel === 'filters'} onClick={() => setPanel(panel === 'filters' ? null : 'filters')} data-testid="matrix-filters-button"
          className="px-3 py-1.5 rounded border border-slate-300 text-sm">{copy.filters}</button>
        <button type="button" aria-expanded={panel === 'columns'} onClick={() => setPanel(panel === 'columns' ? null : 'columns')} data-testid="matrix-columns-button"
          className="px-3 py-1.5 rounded border border-slate-300 text-sm">{copy.columns}</button>
        <label className="text-xs flex items-center gap-1">{copy.sortBy}
          <select value={sort.column} onChange={(e) => patch({ sort: { column: e.target.value, direction: sort.direction } })} data-testid="matrix-sort-select" className="border border-slate-300 rounded px-2 py-1 text-sm">
            {registry.filter((c) => c.sortable && c.available).map((c) => <option key={c.id} value={c.id}>{columnLabel(c, lang)}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => patch({ sort: { column: sort.column, direction: sort.direction === 'asc' ? 'desc' : 'asc' } })} data-testid="matrix-sort-direction"
          className="px-2 py-1.5 rounded border border-slate-300 text-sm">{sort.direction === 'asc' ? copy.ascending : copy.descending}</button>
        {hasFilters(layout.filters) && <button type="button" onClick={() => { setSearchDraft(''); patch({ filters: { ...EMPTY_FILTERS } }); }} data-testid="matrix-clear-filters" className="px-2 py-1.5 text-sm underline">{copy.clearFilters}</button>}
        <span className="flex-1" />
        {canPersonal && <button type="button" disabled={busy} onClick={() => save('personal')} data-testid="matrix-save-personal" className="px-3 py-1.5 rounded bg-slate-900 text-white text-sm disabled:opacity-40">{copy.saveMine}</button>}
        {canPersonal && config.layers.personal && <button type="button" disabled={busy} onClick={() => reset('personal')} data-testid="matrix-reset-personal" className="px-3 py-1.5 rounded border border-slate-300 text-sm">{copy.resetMine}</button>}
        {canShared && <button type="button" disabled={busy} onClick={() => save('job')} data-testid="matrix-save-shared" className="px-3 py-1.5 rounded bg-primary text-white text-sm disabled:opacity-40">{copy.saveShared}</button>}
        {canShared && config.layers.job && <button type="button" disabled={busy} onClick={() => reset('job')} data-testid="matrix-reset-shared" className="px-3 py-1.5 rounded border border-slate-300 text-sm">{copy.resetShared}</button>}
        {canExport && <button type="button" disabled={busy} onClick={download} data-testid="matrix-export" className="px-3 py-1.5 rounded border border-slate-300 text-sm">{copy.export}</button>}
      </div>
      {notice && <p role={notice.tone === 'error' ? 'alert' : 'status'} data-testid="matrix-notice" className={`text-sm ${notice.tone === 'error' ? 'text-red-700' : 'text-emerald-700'}`}>{notice.text}</p>}

      {panel === 'columns' && <MatrixColumnChooser registry={registry} columns={layout.columns} frozen={layout.frozen_candidate} lang={lang}
        onColumns={(columns) => patch({ columns })} onFrozen={(frozen_candidate) => patch({ frozen_candidate })} />}
      {panel === 'filters' && <MatrixFiltersPanel registry={registry} filters={layout.filters} lang={lang} onChange={(filters) => patch({ filters })} />}

      {loadError && <p role="alert" data-testid="matrix-error" className="text-sm text-red-700">{loadError}</p>}
      <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto" tabIndex={0} aria-label={copy.title} data-testid="matrix-scroll">
        <table data-testid="matrix-table" className="min-w-full text-sm border-separate border-spacing-0">
          <thead>
            <tr>
              {visible.map((c) => (
                <th key={c.id} scope="col" data-testid={`header-${c.id}`} style={frozenStyle(c.id, true)}
                  aria-sort={sort.column === c.id ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                  className="text-start text-[11px] font-black uppercase tracking-wide text-slate-600 bg-slate-50 border-b border-slate-200 px-3 py-2 whitespace-nowrap">
                  {c.sortable
                    ? <button type="button" onClick={() => toggleSort(c.id)} className="uppercase font-black" dir="auto">{columnLabel(c, lang)}{sort.column === c.id ? (sort.direction === 'asc' ? ' ▲' : ' ▼') : ''}</button>
                    : <span dir="auto">{columnLabel(c, lang)}</span>}
                </th>))}
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((row) => (
              <tr key={row.application_id} data-testid={`matrix-row-${row.application_id}`} data-eligibility={row.eligibility} className="hover:bg-slate-50">
                {visible.map((c) => (
                  <td key={c.id} data-testid={`cell-${c.id}-${row.application_id}`} style={frozenStyle(c.id)} className="border-b border-slate-100 px-3 py-2 align-top">{cell(row, c)}</td>))}
              </tr>))}
          </tbody>
        </table>
        {data && data.rows.length === 0 && !rowsLoading && (
          <div data-testid="matrix-empty" className="p-8 text-center text-sm text-slate-600"><p className="font-semibold">{copy.empty}</p><p className="text-xs">{copy.emptyHint}</p></div>)}
        {rowsLoading && <p data-testid="matrix-rows-loading" className="p-3 text-xs text-slate-500">{copy.loading}</p>}
      </div>
      {data && (
        <footer className="flex flex-wrap items-center gap-3 text-sm">
          <span data-testid="matrix-count">{copy.rowsCount(data.total)}</span>
          <span className="flex-1" />
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} data-testid="matrix-prev" className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40">{copy.previous}</button>
          <span data-testid="matrix-page-label">{copy.page(page, pages)}</span>
          <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)} data-testid="matrix-next" className="px-3 py-1 rounded border border-slate-300 disabled:opacity-40">{copy.next}</button>
        </footer>)}
      {drawer && <MatrixEvidenceDrawer applicationId={drawer.row.application_id} candidateName={drawer.row.candidate_name} focus={drawer.focus} lang={lang}
        onClose={() => setDrawer(null)} onOpenCandidate={() => onOpenApplication(jobId, drawer.row.application_id)} />}
    </div>
  );
};

export default CandidateMatrix;
