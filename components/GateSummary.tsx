import React, { useCallback, useEffect, useState } from 'react';
import {
  clearGateOverride, evaluateGates, GateItem, GatesApiError, GateSummaryData, getGates, overrideGate, recordGateResult,
} from '../services/gatesApi';
import { canEditRequirements } from '../utils/permissions';
import { gateCopy, Lang, resultTone, statusLabel, statusTone } from '../utils/gates';

const TONES = {
  green: { box: 'bg-emerald-50 border-emerald-300 text-emerald-900', chip: 'bg-emerald-100 text-emerald-800' },
  red: { box: 'bg-red-50 border-red-400 text-red-900', chip: 'bg-red-100 text-red-800' },
  amber: { box: 'bg-amber-50 border-amber-400 text-amber-900', chip: 'bg-amber-100 text-amber-800' },
  slate: { box: 'bg-slate-50 border-slate-300 text-slate-800', chip: 'bg-slate-100 text-slate-700' },
  blue: { box: 'bg-blue-50 border-blue-300 text-blue-900', chip: 'bg-blue-100 text-blue-800' },
} as const;

interface Props {
  applicationId: string;
  userRole: string | null | undefined;
  lang: Lang;
  /** Called with the new status whenever the summary is loaded or changed, so the page can keep its header in step. */
  onStatus?: (status: GateSummaryData['eligibility_status'], effectiveDecision: string | null) => void;
  /** Test seam: an already-loaded summary replaces the first request. */
  initial?: GateSummaryData | null;
}

function formatWhen(value: string | null, lang: Lang): string {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString(lang === 'ar' ? 'ar' : 'en');
}

/** The Gate Summary of one application. It sits above the numeric score and says, in words, that the score cannot override a failed gate. */
export default function GateSummary({ applicationId, userRole, lang, onStatus, initial = null }: Props) {
  const copy = gateCopy(lang);
  const canAct = canEditRequirements(userRole);
  const [data, setData] = useState<GateSummaryData | null>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [overriding, setOverriding] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [recording, setRecording] = useState<string | null>(null);
  const [note, setNote] = useState('');

  const apply = useCallback((next: GateSummaryData) => {
    setData(next);
    onStatus?.(next.eligibility_status, next.effective_decision);
  }, [onStatus]);

  useEffect(() => {
    if (initial) { onStatus?.(initial.eligibility_status, initial.effective_decision); return; }
    let live = true;
    setData(null);
    getGates(applicationId).then((next) => { if (live) { apply(next); setError(null); } })
      .catch((err) => { if (live) setError(err instanceof Error ? err.message : copy.loadFailed); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId]);

  const run = async (work: () => Promise<GateSummaryData>) => {
    setBusy(true);
    setError(null);
    try {
      apply(await work());
      setOverriding(null);
      setRecording(null);
      setReason('');
      setNote('');
    } catch (err) {
      setError(err instanceof GatesApiError || err instanceof Error ? err.message : copy.loadFailed);
    } finally {
      setBusy(false);
    }
  };

  if (error && !data) {
    return <section data-testid="gate-summary-error" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-700" role="status">{copy.loadFailed}</section>;
  }
  if (!data) {
    return <section data-testid="gate-summary-loading" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm text-slate-500">{copy.loading}</section>;
  }
  // A job without enforced gates shows nothing: there is no eligibility to report.
  if (data.eligibility_status === 'not_applicable' && data.gates.length === 0) return null;

  const tone = TONES[statusTone(data.eligibility_status)];
  const status = data.eligibility_status;

  return (
    <section data-testid="gate-summary" data-status={status} aria-label={copy.summaryTitle}
      className={`rounded-2xl border-2 p-5 space-y-4 ${tone.box}`}>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest opacity-70">{copy.summaryTitle}</p>
          <p className="text-xs opacity-80" data-testid="gate-summary-explain">{(copy.statusExplain as Record<string, string>)[status]}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold opacity-70">{copy.overall}</span>
          <span data-testid="gate-overall-status" className={`px-3 py-1.5 rounded-full text-sm font-black ${tone.chip}`}>{statusLabel(status, lang)}</span>
          {canAct && (
            <button type="button" disabled={busy} className="text-xs underline disabled:opacity-50"
              onClick={() => void run(() => evaluateGates(applicationId))}>{copy.evaluateAgain}</button>
          )}
        </div>
      </header>

      <p className="text-xs font-medium" data-testid="gate-score-note">{copy.scoreNote}</p>

      <ul className="space-y-3">
        {data.gates.map((gate) => (
          <GateRow key={gate.gate_key} gate={gate} lang={lang} canAct={canAct} busy={busy}
            overriding={overriding === gate.gate_key} recording={recording === gate.gate_key}
            reason={reason} note={note} setReason={setReason} setNote={setNote}
            startOverride={() => { setOverriding(gate.gate_key); setRecording(null); setReason(''); }}
            startRecording={() => { setRecording(gate.gate_key); setOverriding(null); setNote(''); }}
            cancel={() => { setOverriding(null); setRecording(null); }}
            confirmOverride={() => void run(() => overrideGate(applicationId, gate.gate_key, reason.trim()))}
            clearOverride={() => void run(() => clearGateOverride(applicationId, gate.gate_key))}
            record={(result) => void run(() => recordGateResult(applicationId, gate.gate_key, result, note.trim() || null))} />
        ))}
      </ul>
      {error && <p role="alert" className="text-xs text-red-800">{error}</p>}
      {!canAct && <p className="text-xs opacity-70">{copy.viewOnly}</p>}
    </section>
  );
}

interface RowProps {
  gate: GateItem; lang: Lang; canAct: boolean; busy: boolean; overriding: boolean; recording: boolean; reason: string; note: string;
  setReason: (v: string) => void; setNote: (v: string) => void; startOverride: () => void; startRecording: () => void; cancel: () => void;
  confirmOverride: () => void; clearOverride: () => void; record: (result: 'passed' | 'failed' | 'needs_verification') => void;
}

function GateRow(p: RowProps) {
  const { gate, lang } = p;
  const copy = gateCopy(lang);
  const tone = TONES[resultTone(gate.result)];
  const failed = gate.result === 'failed';
  const recordable = gate.verification_method === 'assessment' || gate.verification_method === 'manual';
  return (
    <li data-testid={`gate-${gate.gate_key}`} data-result={gate.result} data-processing-error={gate.processing_error ? 'true' : 'false'}
      className={`rounded-xl border bg-white/80 p-3 space-y-2 ${failed ? 'border-red-500 border-s-4' : 'border-white/60'}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p dir="auto" className="text-sm font-semibold text-slate-900 flex-1 min-w-[10rem]">{gate.label}</p>
        <span data-testid={`gate-result-${gate.gate_key}`} className={`px-2.5 py-1 rounded-full text-xs font-bold ${tone.chip}`}>{gate.processing_error ? copy.processingError : copy.result[gate.result]}</span>
      </div>
      <p className="text-xs text-slate-600">
        {copy.method}: {copy.methods[gate.verification_method]}
        {' · '}{copy.evidenceHeading}: {copy.evidence[gate.evidence_source]}
      </p>
      {gate.processing_error && <p className="text-xs text-amber-800" data-testid={`gate-processing-error-${gate.gate_key}`}>{copy.processingErrorHelp}</p>}
      {gate.evidence_summary && !gate.processing_error && <p dir="auto" className="text-xs text-slate-700">{gate.evidence_summary}</p>}
      {gate.result === 'overridden' && (
        <div className="rounded-md bg-blue-50 border border-blue-200 p-2 text-xs text-blue-900" data-testid={`gate-override-${gate.gate_key}`}>
          <p>{copy.originalResult}: {copy.result[gate.computed_result]}</p>
          {gate.override_reason && <p dir="auto">{gate.override_reason}</p>}
        </div>
      )}
      {gate.history.length > 0 && (
        <details className="text-xs text-slate-600">
          <summary className="cursor-pointer">{copy.history} ({gate.history.length})</summary>
          <ul className="mt-1 space-y-1" data-testid={`gate-history-${gate.gate_key}`}>
            {gate.history.map((e, i) => (
              <li key={i} dir="auto">
                {formatWhen(e.at, lang)} · {copy.events[e.event_type]}
                {e.original_result ? ` · ${copy.result[e.original_result]} → ${copy.result[e.new_result]}` : ` · ${copy.result[e.new_result]}`}
                {e.by ? ` · ${e.by}` : ''}{e.reason ? ` · ${e.reason}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      {p.canAct && (
        <div className="flex flex-wrap items-center gap-2">
          {gate.result === 'overridden' ? (
            <button type="button" disabled={p.busy} className="text-xs underline" onClick={p.clearOverride}>{copy.clearOverride}</button>
          ) : (
            <button type="button" disabled={p.busy} className="text-xs underline" onClick={p.startOverride}>{copy.overrideAction}</button>
          )}
          {recordable && gate.result !== 'overridden' && (
            <button type="button" disabled={p.busy} className="text-xs underline" onClick={p.startRecording}>{copy.recordResult}</button>
          )}
        </div>
      )}
      {p.overriding && (
        <div className="space-y-2" data-testid="gate-override-form">
          <label className="block text-xs text-slate-700">{copy.overrideReason}
            <textarea dir="auto" aria-label={copy.overrideReason} rows={2} className="mt-1 w-full border border-slate-300 rounded px-2 py-1 text-sm"
              value={p.reason} onChange={(e) => p.setReason(e.target.value)} />
          </label>
          <div className="flex gap-2">
            <button type="button" disabled={p.busy || !p.reason.trim()} className="px-3 py-1 rounded bg-blue-700 text-white text-xs disabled:opacity-50"
              onClick={p.confirmOverride}>{copy.overrideConfirm}</button>
            <button type="button" className="px-3 py-1 rounded border border-slate-300 text-xs" onClick={p.cancel}>{copy.cancel}</button>
          </div>
        </div>
      )}
      {p.recording && (
        <div className="space-y-2" data-testid="gate-record-form">
          <label className="block text-xs text-slate-700">{copy.summaryNote}
            <input dir="auto" aria-label={copy.summaryNote} className="mt-1 w-full border border-slate-300 rounded px-2 py-1 text-sm"
              value={p.note} onChange={(e) => p.setNote(e.target.value)} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={p.busy} className="px-3 py-1 rounded bg-emerald-700 text-white text-xs" onClick={() => p.record('passed')}>{copy.recordPassed}</button>
            <button type="button" disabled={p.busy} className="px-3 py-1 rounded bg-red-700 text-white text-xs" onClick={() => p.record('failed')}>{copy.recordFailed}</button>
            <button type="button" disabled={p.busy} className="px-3 py-1 rounded bg-amber-600 text-white text-xs" onClick={() => p.record('needs_verification')}>{copy.recordNeedsVerification}</button>
            <button type="button" className="px-3 py-1 rounded border border-slate-300 text-xs" onClick={p.cancel}>{copy.cancel}</button>
          </div>
        </div>
      )}
    </li>
  );
}
