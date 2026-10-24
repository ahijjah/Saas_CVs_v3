import React, { useState } from 'react';
import type { MatrixColumn } from '../services/matrixApi';
import { columnLabel, dropColumn, Lang, matrixCopy, moveColumn, NAME_COLUMN, toggleColumn } from '../utils/matrix';

interface Props {
  registry: MatrixColumn[];
  columns: string[];
  frozen: boolean;
  lang: Lang;
  onColumns: (columns: string[]) => void;
  onFrozen: (frozen: boolean) => void;
}

/** Choose which columns the matrix shows and in which order. Reordering has buttons for keyboards and screen readers, and drag-and-drop for a pointer. */
export default function MatrixColumnChooser({ registry, columns, frozen, lang, onColumns, onFrozen }: Props) {
  const copy = matrixCopy(lang);
  const byId = new Map(registry.map((c) => [c.id, c]));
  const [dragging, setDragging] = useState<string | null>(null);
  const groups = copy.groups;
  const order = Object.keys(groups) as (keyof typeof groups)[];

  return (
    <section data-testid="matrix-column-chooser" aria-label={copy.columns} className="rounded-xl border border-slate-200 bg-white p-4 grid gap-6 md:grid-cols-2">
      <div>
        <h3 className="text-sm font-bold text-slate-900 mb-2">{copy.visibleColumns}</h3>
        <label className="flex items-center gap-2 text-xs mb-3">
          <input type="checkbox" checked={frozen} onChange={(e) => onFrozen(e.target.checked)} data-testid="matrix-freeze" /> {copy.freezeCandidate}
        </label>
        <ol className="space-y-1" data-testid="matrix-visible-columns">
          {columns.map((id, index) => {
            const column = byId.get(id);
            const label = column ? columnLabel(column, lang) : id;
            const locked = id === NAME_COLUMN;
            return (
              <li key={id} data-testid={`visible-${id}`} draggable={!locked} onDragStart={() => setDragging(id)} onDragOver={(e) => e.preventDefault()}
                onDrop={() => { if (dragging) onColumns(dropColumn(columns, dragging, id)); setDragging(null); }}
                className="flex items-center gap-2 rounded border border-slate-200 px-2 py-1 text-sm bg-slate-50">
                <span aria-hidden className="text-slate-400 cursor-grab">⋮⋮</span>
                <span dir="auto" className="flex-1 truncate">{label}</span>
                {locked ? <span className="text-[10px] text-slate-500">{copy.locked}</span> : (
                  <>
                    <button type="button" aria-label={`${copy.moveUp}: ${label}`} disabled={index <= 1} onClick={() => onColumns(moveColumn(columns, id, -1))}
                      className="px-1.5 rounded border border-slate-300 text-xs disabled:opacity-30">↑</button>
                    <button type="button" aria-label={`${copy.moveDown}: ${label}`} disabled={index >= columns.length - 1} onClick={() => onColumns(moveColumn(columns, id, 1))}
                      className="px-1.5 rounded border border-slate-300 text-xs disabled:opacity-30">↓</button>
                    <button type="button" aria-label={`${copy.hideColumn}: ${label}`} onClick={() => onColumns(toggleColumn(columns, id))}
                      className="px-1.5 rounded border border-slate-300 text-xs">✕</button>
                  </>
                )}
              </li>
            );
          })}
        </ol>
      </div>
      <div className="max-h-96 overflow-y-auto pe-2">
        <h3 className="text-sm font-bold text-slate-900 mb-2">{copy.availableColumns}</h3>
        {order.map((group) => {
          const items = registry.filter((c) => c.group === group);
          if (items.length === 0) return null;
          return (
            <fieldset key={group} className="mb-3" data-testid={`group-${group}`}>
              <legend className="text-[11px] font-black uppercase tracking-wide text-slate-500 mb-1">{groups[group]}</legend>
              {items.map((c) => (
                <label key={c.id} className={`flex items-center gap-2 text-sm py-0.5 ${c.available ? '' : 'opacity-50'}`} title={c.unavailable_reason ?? undefined}>
                  <input type="checkbox" checked={columns.includes(c.id)} disabled={!c.available || c.id === NAME_COLUMN}
                    onChange={() => onColumns(toggleColumn(columns, c.id))} data-testid={`toggle-${c.id}`} />
                  <span dir="auto">{columnLabel(c, lang)}</span>
                  {!c.available && <span className="text-[10px] text-slate-500">({copy.unavailable})</span>}
                </label>
              ))}
            </fieldset>
          );
        })}
      </div>
    </section>
  );
}
