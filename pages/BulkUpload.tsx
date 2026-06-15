
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';
import { usePageTitle } from '../context/PageTitleContext';

// ── Types ─────────────────────────────────────────────────────────────────────

type PagePhase =
  | 'history'       // landing: show batch history + New Batch button
  | 'new_batch'     // batch created, awaiting file uploads
  | 'validated'     // excel parsed, showing preview + import controls
  | 'processing'    // import done, scoring in progress (polling)
  | 'completed';    // all rows in terminal state

type ValidationFilter = 'all' | 'ready' | 'warning' | 'error';

interface BatchSummary {
  batch_id: string;
  batch_status: string;
  row_count: number;
  valid_row_count: number;
  warning_row_count: number;
  error_row_count: number;
  imported_row_count: number;
  failed_row_count: number;
  completed_row_count: number;
  unmatched_cv_count?: number;
  zip_file_path?: string;
  created_at: string;
  job_id: string;
}

interface ValidationRow {
  row_number: number;
  name: string;
  email: string;
  cv_filename: string;
  validation_status: 'ready' | 'warning' | 'error';
  warnings: string[];
  errors: string[];
}

interface ProcessingRow {
  row_number: number;
  candidate_name: string;
  application_id: string | null;
  import_status: string;
  processing_stage: string;
  progress_percentage: number;
  final_score: number | null;
  decision: string | null;
  failure_reason: string | null;
}

interface ProcessingSummary {
  batch_id: string;
  batch_status: string;
  total_rows: number;
  imported_rows: number;
  queued_rows: number;
  processing_rows: number;
  completed_rows: number;
  failed_rows: number;
  progress_percentage: number;
}

// ── Small reusable primitives ─────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  ready:      'bg-emerald-100 text-emerald-700',
  warning:    'bg-amber-100   text-amber-700',
  error:      'bg-red-100     text-red-700',
  pending:    'bg-slate-100   text-slate-600',
  queued:     'bg-blue-100    text-blue-700',
  processing: 'bg-indigo-100  text-indigo-700',
  completed:  'bg-emerald-100 text-emerald-700',
  failed:     'bg-red-100     text-red-700',
  imported:   'bg-teal-100    text-teal-700',
  validated:  'bg-blue-100    text-blue-700',
  created:    'bg-slate-100   text-slate-500',
  uploaded:   'bg-sky-100     text-sky-700',
  uploading:  'bg-violet-100  text-violet-700',
  importing:  'bg-indigo-100  text-indigo-700',
  skipped:    'bg-slate-100   text-slate-500',
  ai_scored:  'bg-emerald-100 text-emerald-700',
  low_match:  'bg-orange-100  text-orange-700',
};

const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const cls = STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-600';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-wider ${cls}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
};

const SummaryCard: React.FC<{ label: string; value: number | string; color?: string; onClick?: () => void }> = ({
  label, value, color = 'text-slate-800', onClick,
}) => (
  <div
    onClick={onClick}
    className={`bg-white rounded-xl border border-slate-200 p-4 flex flex-col items-center justify-center text-center min-h-[90px] transition-all ${onClick ? 'cursor-pointer hover:border-indigo-400 hover:bg-indigo-50/40 hover:shadow-sm' : ''}`}
  >
    <p className={`text-3xl font-bold ${color}`}>{value}</p>
    <p className="text-[11px] text-slate-500 mt-1 leading-tight">{label}</p>
  </div>
);

const ProgressBar: React.FC<{ pct: number }> = ({ pct }) => (
  <div className="w-full bg-slate-100 rounded-full h-3 overflow-hidden">
    <div
      className="h-full rounded-full transition-all duration-700 bg-indigo-500"
      style={{ width: `${Math.min(pct, 100)}%` }}
    />
  </div>
);

const Spinner: React.FC<{ small?: boolean }> = ({ small }) => (
  <svg
    className={`animate-spin ${small ? 'w-4 h-4' : 'w-5 h-5'} text-indigo-600`}
    fill="none" viewBox="0 0 24 24"
  >
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
  </svg>
);

const UploadZone: React.FC<{
  label: string;
  hint: string;
  accept: string;
  uploading: boolean;
  done: boolean;
  inputRef: React.RefObject<HTMLInputElement>;
  onFile: (f: File) => void;
  accentColor?: string;
}> = ({ label, hint, accept, uploading, done, inputRef, onFile, accentColor = 'indigo' }) => {
  const borderDone = done ? `border-emerald-400 bg-emerald-50/40` : `border-slate-300 hover:border-${accentColor}-400 hover:bg-${accentColor}-50/20`;
  return (
    <div
      onClick={() => !uploading && inputRef.current?.click()}
      className={`relative border-2 border-dashed rounded-2xl p-8 flex flex-col items-center gap-3 cursor-pointer transition-all ${borderDone}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) { onFile(f); e.target.value = ''; } }}
      />
      {uploading ? (
        <>
          <Spinner />
          <p className="text-sm font-medium text-slate-600">Uploading…</p>
        </>
      ) : done ? (
        <>
          <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center">
            <svg className="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <p className="text-sm font-bold text-emerald-700">{label} uploaded</p>
          <p className="text-[11px] text-slate-500">Click to replace</p>
        </>
      ) : (
        <>
          <div className={`w-10 h-10 rounded-full bg-${accentColor}-100 flex items-center justify-center`}>
            <svg className={`w-5 h-5 text-${accentColor}-600`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
          </div>
          <p className="text-sm font-bold text-slate-700">{label}</p>
          <p className="text-[11px] text-slate-500 text-center leading-relaxed">{hint}</p>
        </>
      )}
    </div>
  );
};

// ── Main page ─────────────────────────────────────────────────────────────────

interface BulkUploadPageProps {
  jobId: string;
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
  onBack: () => void;
  onOpenApplication: (jobId: string, appId: string) => void;
}

export const BulkUploadPage: React.FC<BulkUploadPageProps> = ({
  jobId, auth, addToast, onBack, onOpenApplication,
}) => {
  const { setPageTitle } = usePageTitle();

  useEffect(() => { setPageTitle('Bulk Upload Centre'); }, [setPageTitle]);

  const BASE   = WEBHOOK_CONFIG.BULK_UPLOAD_BASE_URL;
  const token  = auth.token!;

  // ── Phase & active batch ─────────────────────────────────────────────────
  const [phase, setPhase]               = useState<PagePhase>('history');
  const [activeBatch, setActiveBatch]   = useState<BatchSummary | null>(null);
  const [batchHistory, setBatchHistory] = useState<BatchSummary[]>([]);

  // ── Validation state ─────────────────────────────────────────────────────
  const [validationRows, setValidationRows]     = useState<ValidationRow[]>([]);
  const [validationFilter, setValidFilter]      = useState<ValidationFilter>('all');
  const [validationSearch, setValidSearch]      = useState('');
  const [expandedRow, setExpandedRow]           = useState<number | null>(null);
  const [includeWarnings, setIncludeWarnings]   = useState(true);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [hasZip, setHasZip]                     = useState(false);

  // ── Processing state ─────────────────────────────────────────────────────
  const [procSummary, setProcSummary]   = useState<ProcessingSummary | null>(null);
  const [procRows, setProcRows]         = useState<ProcessingRow[]>([]);
  const [pollTick, setPollTick]         = useState(0);
  const [procPage, setProcPage]         = useState(0);
  const [loadingProcRows, setLoadingProcRows] = useState(false);
  const PAGE_SIZE = 50;

  // ── Loading states ────────────────────────────────────────────────────────
  const [loadingHistory, setLoadingHistory]   = useState(false);
  const [creatingBatch, setCreatingBatch]     = useState(false);
  const [uploadingZip, setUploadingZip]       = useState(false);
  const [openingBatchId, setOpeningBatchId]   = useState<string | null>(null);
  const [uploadingExcel, setUploadingExcel]   = useState(false);
  const [importing, setImporting]             = useState(false);

  const zipRef   = useRef<HTMLInputElement>(null);
  const xlsxRef  = useRef<HTMLInputElement>(null);

  // ── Fetch batch history ──────────────────────────────────────────────────
  const fetchHistory = useCallback(async () => {
    setLoadingHistory(true);
    try {
      const data = await apiService.get(`${BASE}/jobs/${jobId}/batches`, {}, token);
      setBatchHistory(data.batches || []);
    } catch {
      // silent
    } finally {
      setLoadingHistory(false);
    }
  }, [BASE, jobId, token]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // ── Create new batch ──────────────────────────────────────────────────────
  const handleCreateBatch = async () => {
    setCreatingBatch(true);
    try {
      const data = await apiService.post(`${BASE}/batches`, { job_id: jobId }, token);
      setActiveBatch(data);
      setHasZip(false);
      setValidationRows([]);
      setPhase('new_batch');
      addToast('Batch created — upload your ZIP and Excel files.', 'success');
    } catch (err: any) {
      addToast(err.message || 'Failed to create batch', 'error');
    } finally {
      setCreatingBatch(false);
    }
  };

  // ── Upload ZIP ────────────────────────────────────────────────────────────
  const handleZipFile = async (file: File) => {
    if (!activeBatch) return;
    setUploadingZip(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      await apiService.postForm(`${BASE}/batches/${activeBatch.batch_id}/zip`, fd, token);
      setHasZip(true);
      addToast('ZIP uploaded successfully.', 'success');
    } catch (err: any) {
      addToast(err.message || 'ZIP upload failed', 'error');
    } finally {
      setUploadingZip(false);
    }
  };

  // ── Upload & validate Excel ───────────────────────────────────────────────
  const handleExcelFile = async (file: File) => {
    if (!activeBatch) return;
    setUploadingExcel(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      await apiService.postForm(`${BASE}/batches/${activeBatch.batch_id}/excel`, fd, token);

      // Fetch validation report and updated batch
      const [valData, batchData] = await Promise.all([
        apiService.get(`${BASE}/batches/${activeBatch.batch_id}/validation`, {}, token),
        apiService.get(`${BASE}/batches/${activeBatch.batch_id}`, {}, token),
      ]);

      setValidationRows(valData.rows || []);
      setActiveBatch(batchData);
      setPhase('validated');
      addToast('Excel validated — review results before importing.', 'success');
    } catch (err: any) {
      addToast(err.message || 'Excel validation failed', 'error');
    } finally {
      setUploadingExcel(false);
    }
  };

  // ── Confirm import ────────────────────────────────────────────────────────
  const handleImport = async () => {
    if (!activeBatch) return;
    setImporting(true);
    setShowImportDialog(false);
    try {
      await apiService.post(
        `${BASE}/batches/${activeBatch.batch_id}/import?include_warning_rows=${includeWarnings}`,
        {},
        token,
      );
      setPhase('processing');
      setPollTick(t => t + 1);
      addToast('Import started — monitoring processing progress.', 'info');
    } catch (err: any) {
      addToast(err.message || 'Import failed', 'error');
    } finally {
      setImporting(false);
    }
  };

  // ── Polling ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (phase !== 'processing' || !activeBatch) return;
    const timer = setTimeout(async () => {
      let terminal = false;
      try {
        const summary: ProcessingSummary = await apiService.get(
          `${BASE}/batches/${activeBatch.batch_id}/processing`, {}, token,
        );
        setProcSummary(summary);

        setLoadingProcRows(true);
        try {
          const rowsData = await apiService.get(
            `${BASE}/batches/${activeBatch.batch_id}/processing/rows`,
            { limit: String(PAGE_SIZE), offset: String(procPage * PAGE_SIZE) },
            token,
          );
          setProcRows(rowsData.rows || []);
        } finally {
          setLoadingProcRows(false);
        }

        // Terminal when batch_status is done, or counters show no more work
        const countersDone =
          summary.queued_rows === 0 &&
          summary.processing_rows === 0 &&
          (summary.completed_rows + summary.failed_rows) >= summary.imported_rows;
        terminal =
          summary.batch_status === 'completed' ||
          summary.batch_status === 'failed' ||
          summary.progress_percentage >= 100 ||
          countersDone;

        if (terminal) {
          // Final fetch: backend sync has now written completed statuses to
          // bulk_upload_rows, so this returns accurate scores/decisions.
          try {
            const [finalSummary, finalRowsData] = await Promise.all([
              apiService.get(`${BASE}/batches/${activeBatch.batch_id}/processing`, {}, token),
              apiService.get(
                `${BASE}/batches/${activeBatch.batch_id}/processing/rows`,
                { limit: String(PAGE_SIZE), offset: '0' },
                token,
              ),
            ]);
            setProcSummary(finalSummary);
            setProcRows(finalRowsData.rows || []);
            setProcPage(0);
          } catch { /* keep last-known procRows */ }
          setPhase('completed');
          fetchHistory().catch(() => {});
        }
      } catch {
        // network / auth error — keep polling
      }
      if (!terminal) {
        setPollTick(t => t + 1);
      }
    }, 5000);
    return () => clearTimeout(timer);
  }, [phase, activeBatch, token, BASE, pollTick, procPage, fetchHistory]);

  // ── Open historical batch ─────────────────────────────────────────────────
  const openBatch = async (batch: BatchSummary) => {
    setOpeningBatchId(batch.batch_id);
    setActiveBatch(batch);
    // Clear stale data from any previously viewed batch
    setProcSummary(null);
    setProcRows([]);
    setValidationRows([]);
    setProcPage(0);
    const st = batch.batch_status;
    try {
      if (st === 'validated') {
        const valData = await apiService.get(`${BASE}/batches/${batch.batch_id}/validation`, {}, token);
        setValidationRows(valData.rows || []);
        setHasZip(!!batch.zip_file_path);
        setPhase('validated');

      } else if (st === 'processing' || st === 'importing' || st === 'imported') {
        // Fetch an initial snapshot so the table isn't blank while polling waits
        try {
          const [summary, rowsData] = await Promise.all([
            apiService.get(`${BASE}/batches/${batch.batch_id}/processing`, {}, token),
            apiService.get(
              `${BASE}/batches/${batch.batch_id}/processing/rows`,
              { limit: String(PAGE_SIZE), offset: '0' },
              token,
            ),
          ]);
          setProcSummary(summary);
          setProcRows(rowsData.rows || []);
        } catch { /* snapshot is optional — polling fills in the data */ }
        setPhase('processing');
        setPollTick(t => t + 1);

      } else if (st === 'completed' || st === 'failed') {
        const [summary, rowsData] = await Promise.all([
          apiService.get(`${BASE}/batches/${batch.batch_id}/processing`, {}, token),
          apiService.get(
            `${BASE}/batches/${batch.batch_id}/processing/rows`,
            { limit: String(PAGE_SIZE), offset: '0' },
            token,
          ),
        ]);
        setProcSummary(summary);
        setProcRows(rowsData.rows || []);
        setPhase('completed');

      } else {
        // 'created' / 'uploaded' / 'validating' — batch needs file uploads
        setHasZip(!!batch.zip_file_path);
        setPhase('new_batch');
      }
    } catch (err: any) {
      addToast(err?.message || 'Failed to load batch details', 'error');
      // Stay in history — never silently reset to upload workflow
      setActiveBatch(null);
      setPhase('history');
    } finally {
      setOpeningBatchId(null);
    }
  };

  // ── Filtered validation rows ──────────────────────────────────────────────
  const filteredRows = validationRows.filter(r => {
    if (validationFilter !== 'all' && r.validation_status !== validationFilter) return false;
    if (validationSearch) {
      const q = validationSearch.toLowerCase();
      if (
        !r.name.toLowerCase().includes(q) &&
        !r.email.toLowerCase().includes(q) &&
        !r.cv_filename.toLowerCase().includes(q)
      ) return false;
    }
    return true;
  });

  // Import row counts (for dialog)
  const readyCount   = validationRows.filter(r => r.validation_status === 'ready').length;
  const warningCount = validationRows.filter(r => r.validation_status === 'warning').length;
  const importCount  = readyCount + (includeWarnings ? warningCount : 0);

  // ── Decision badge colour ─────────────────────────────────────────────────
  const decisionColour = (d: string | null) => {
    if (!d) return 'text-slate-400';
    if (d === 'qualified')  return 'text-emerald-700 font-bold';
    if (d === 'partial')    return 'text-amber-700 font-bold';
    if (d === 'rejected')   return 'text-red-600 font-bold';
    if (d === 'low_match')  return 'text-orange-600 font-bold';
    return 'text-slate-600';
  };

  // ────────────────────────────────────────────────────────────────────────────
  // RENDER
  // ────────────────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-slate-50 pb-16">

      {/* ── Header ── */}
      <div className="bg-white border-b border-border sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-3">
          <button
            onClick={onBack}
            className="p-2 rounded-lg hover:bg-slate-100 text-textMuted transition-colors"
            aria-label="Back"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-black text-textMain">Bulk Upload Centre</h1>
            <p className="text-[10px] text-textMuted">Upload + Answers Sheet • ZIP + Excel</p>
          </div>
          {phase !== 'history' && (
            <button
              onClick={() => { setPhase('history'); setActiveBatch(null); fetchHistory(); }}
              className="text-[11px] font-bold text-textMuted hover:text-textMain px-3 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            >
              ← All Batches
            </button>
          )}
          {phase === 'history' && (
            <button
              onClick={handleCreateBatch}
              disabled={creatingBatch}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-xs font-black rounded-xl hover:bg-indigo-700 transition-colors disabled:opacity-60"
            >
              {creatingBatch ? <Spinner small /> : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                </svg>
              )}
              New Batch
            </button>
          )}
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 space-y-6">

        {/* ══════════════════════════════════════════════════════════════════
            PHASE: HISTORY
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'history' && (
          <div className="space-y-6">
            {/* Intro banner */}
            <div className="bg-indigo-50 border border-indigo-200 rounded-2xl p-5 flex items-start gap-4">
              <div className="w-10 h-10 rounded-xl bg-indigo-100 flex items-center justify-center shrink-0">
                <svg className="w-5 h-5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
              </div>
              <div>
                <p className="text-sm font-black text-indigo-900 mb-1">Bulk Upload + Answers Sheet</p>
                <p className="text-[11px] text-indigo-700 leading-relaxed">
                  Upload a ZIP of CV files and an Excel sheet with candidate details and knockout answers.
                  The system validates each row, then imports and scores matched applications automatically.
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-[10px] font-bold text-indigo-600">
                  <span className="bg-white border border-indigo-200 rounded-full px-2 py-0.5">1 · Upload ZIP + Excel</span>
                  <span className="text-indigo-400">→</span>
                  <span className="bg-white border border-indigo-200 rounded-full px-2 py-0.5">2 · Validate rows</span>
                  <span className="text-indigo-400">→</span>
                  <span className="bg-white border border-indigo-200 rounded-full px-2 py-0.5">3 · Confirm import</span>
                  <span className="text-indigo-400">→</span>
                  <span className="bg-white border border-indigo-200 rounded-full px-2 py-0.5">4 · Monitor processing</span>
                </div>
              </div>
            </div>

            {/* Batch history table */}
            <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
              <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest">Batch History</h3>
                <button onClick={fetchHistory} className="p-1.5 rounded-lg hover:bg-slate-100 text-textMuted transition-colors">
                  <svg className={`w-4 h-4 ${loadingHistory ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
              </div>
              {loadingHistory ? (
                <div className="flex justify-center py-12"><Spinner /></div>
              ) : batchHistory.length === 0 ? (
                <div className="py-14 text-center">
                  <p className="text-sm font-bold text-textMuted">No batches yet</p>
                  <p className="text-[11px] text-textMuted mt-1">Create your first batch to get started.</p>
                  <button
                    onClick={handleCreateBatch}
                    disabled={creatingBatch}
                    className="mt-4 px-5 py-2.5 bg-indigo-600 text-white text-xs font-black rounded-xl hover:bg-indigo-700 transition-colors disabled:opacity-60"
                  >
                    {creatingBatch ? 'Creating…' : 'Create First Batch'}
                  </button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b border-border">
                        {['Batch ID', 'Status', 'Rows', 'Imported', 'Completed', 'Failed', 'Created', ''].map(h => (
                          <th key={h} className="px-4 py-3 text-[9px] font-black text-textMuted uppercase tracking-wider whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {batchHistory.map(b => (
                        <tr key={b.batch_id} className="border-b border-border/50 hover:bg-slate-50/60 transition-colors">
                          <td className="px-4 py-3">
                            <code className="text-[10px] font-mono text-textMuted">{b.batch_id.slice(0, 8)}…</code>
                          </td>
                          <td className="px-4 py-3"><StatusBadge status={b.batch_status} /></td>
                          <td className="px-4 py-3 text-xs text-textMain font-bold">{b.row_count}</td>
                          <td className="px-4 py-3 text-xs text-teal-700 font-bold">{b.imported_row_count}</td>
                          <td className="px-4 py-3 text-xs text-emerald-700 font-bold">{b.completed_row_count}</td>
                          <td className="px-4 py-3 text-xs text-red-600 font-bold">{b.failed_row_count}</td>
                          <td className="px-4 py-3 text-[11px] text-textMuted whitespace-nowrap">
                            {b.created_at ? new Date(b.created_at).toLocaleDateString() : '—'}
                          </td>
                          <td className="px-4 py-3">
                            <button
                              onClick={() => openBatch(b)}
                              disabled={openingBatchId !== null}
                              className="text-[11px] font-bold text-indigo-600 hover:text-indigo-800 hover:underline disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                            >
                              {openingBatchId === b.batch_id ? (
                                <><Spinner small /> Opening…</>
                              ) : 'Open →'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            BATCH HEADER (shown in all non-history phases)
        ══════════════════════════════════════════════════════════════════ */}
        {phase !== 'history' && activeBatch && (
          <div className="bg-white rounded-2xl shadow-sm border border-border px-5 py-4 space-y-3">
            <div className="flex flex-wrap items-start gap-4">
              <div className="flex-1 min-w-0">
                <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-1">Batch Details</p>
                <div className="flex flex-wrap items-center gap-2 mb-0.5">
                  <StatusBadge status={activeBatch.batch_status} />
                  <code className="text-[10px] font-mono text-textMuted break-all">{activeBatch.batch_id}</code>
                </div>
                <p className="text-[10px] text-textMuted">
                  Created {activeBatch.created_at ? new Date(activeBatch.created_at).toLocaleString() : '—'}
                </p>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                {/* ← Back to History */}
                <button
                  onClick={() => { setPhase('history'); setActiveBatch(null); fetchHistory(); }}
                  className="text-[11px] font-bold text-textMuted hover:text-textMain flex items-center gap-1 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
                  </svg>
                  Back to History
                </button>
                {/* Phase breadcrumb */}
                <div className="hidden sm:flex items-center gap-1 text-[9px] font-black uppercase tracking-wider">
                  {(['Upload', 'Validate', 'Import', 'Processing', 'Done'] as const).map((step, i) => {
                    const done =
                      (i === 0 && ['validated', 'processing', 'completed'].includes(activeBatch.batch_status)) ||
                      (i === 1 && ['processing', 'completed', 'imported'].includes(activeBatch.batch_status)) ||
                      (i === 2 && ['processing', 'completed', 'importing'].includes(activeBatch.batch_status)) ||
                      (i === 3 && ['completed', 'failed'].includes(activeBatch.batch_status));
                    const active =
                      (i === 0 && phase === 'new_batch') ||
                      (i === 1 && phase === 'validated') ||
                      (i === 2 && phase === 'validated') ||
                      (i === 3 && phase === 'processing') ||
                      (i === 4 && phase === 'completed');
                    return (
                      <React.Fragment key={step}>
                        <span className={`px-2 py-0.5 rounded-full ${
                          active ? 'bg-indigo-600 text-white' : done ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'
                        }`}>{step}</span>
                        {i < 4 && <span className="text-slate-300">›</span>}
                      </React.Fragment>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE: NEW_BATCH — file upload controls
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'new_batch' && (
          <div className="space-y-4">
            <div className="bg-white rounded-2xl shadow-sm border border-border p-6">
              <h2 className="text-xs font-black text-textMain mb-1">Step 1 — Upload Files</h2>
              <p className="text-[11px] text-textMuted mb-5">
                Upload a ZIP containing CV files (PDF/DOCX/DOC), then upload the Excel sheet with candidate details.
                You can upload in any order — validation runs when the Excel is uploaded.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-2">ZIP Archive (CV Files)</p>
                  <UploadZone
                    label="Upload ZIP"
                    hint="Drop a .zip file containing CV files (PDF, DOCX, DOC). Max 200 MB."
                    accept=".zip"
                    uploading={uploadingZip}
                    done={hasZip}
                    inputRef={zipRef}
                    onFile={handleZipFile}
                    accentColor="violet"
                  />
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-2">Excel Sheet (Candidate Data)</p>
                  <UploadZone
                    label="Upload Excel"
                    hint="Drop a .xlsx file with columns: CV Filename, Name, Email, Phone, and any knockout answer columns. Max 10 MB."
                    accept=".xlsx,.xls"
                    uploading={uploadingExcel}
                    done={false}
                    inputRef={xlsxRef}
                    onFile={handleExcelFile}
                    accentColor="indigo"
                  />
                </div>
              </div>
              {!hasZip && (
                <div className="mt-4 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 flex items-center gap-2">
                  <svg className="w-4 h-4 text-amber-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <p className="text-[11px] font-bold text-amber-800">
                    ZIP not yet uploaded — CV matching will be limited. You can upload the ZIP at any time before importing.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE: VALIDATED — preview + import
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'validated' && activeBatch && (
          <div className="space-y-5">

            {/* Validation Summary Cards */}
            <div>
              <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-3">Validation Summary</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <SummaryCard label="Total Rows"   value={activeBatch.row_count}       color="text-slate-800" onClick={() => setValidFilter('all')} />
                <SummaryCard label="Ready"        value={activeBatch.valid_row_count}  color="text-emerald-600" onClick={() => setValidFilter('ready')} />
                <SummaryCard label="Warnings"     value={activeBatch.warning_row_count} color="text-amber-600" onClick={() => setValidFilter('warning')} />
                <SummaryCard label="Errors"       value={activeBatch.error_row_count}  color="text-red-600" onClick={() => setValidFilter('error')} />
                <SummaryCard label="Matched CVs"  value={activeBatch.row_count - (activeBatch.unmatched_cv_count || 0)} color="text-teal-600" />
                <SummaryCard label="Unmatched CVs" value={activeBatch.unmatched_cv_count || 0} color={(activeBatch.unmatched_cv_count || 0) > 0 ? 'text-amber-600' : 'text-slate-400'} />
              </div>
            </div>

            {/* Validation Preview Table */}
            <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
              <div className="px-5 py-4 border-b border-border">
                <div className="flex flex-wrap items-center gap-3">
                  <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest mr-auto">Validation Preview</h3>
                  {/* Filter buttons */}
                  {(['all', 'ready', 'warning', 'error'] as ValidationFilter[]).map(f => (
                    <button
                      key={f}
                      onClick={() => setValidFilter(f)}
                      className={`text-[10px] font-black px-3 py-1 rounded-full transition-colors ${
                        validationFilter === f ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-textMuted hover:bg-slate-200'
                      }`}
                    >
                      {f === 'all' ? `All (${validationRows.length})` :
                       f === 'ready' ? `Ready (${activeBatch.valid_row_count})` :
                       f === 'warning' ? `Warning (${activeBatch.warning_row_count})` :
                       `Error (${activeBatch.error_row_count})`}
                    </button>
                  ))}
                  {/* Search */}
                  <input
                    type="text"
                    placeholder="Search name / email / file…"
                    value={validationSearch}
                    onChange={e => setValidSearch(e.target.value)}
                    className="border border-border rounded-lg px-3 py-1.5 text-xs outline-none focus:border-indigo-400 w-48"
                  />
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border bg-slate-50/60">
                      {['#', 'Name', 'Email', 'CV File', 'Status', 'Warnings', 'Errors', ''].map(h => (
                        <th key={h} className="px-4 py-3 text-[9px] font-black text-textMuted uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRows.slice(0, 500).map(r => (
                      <React.Fragment key={r.row_number}>
                        <tr
                          className={`border-b border-border/50 hover:bg-slate-50/60 cursor-pointer transition-colors ${expandedRow === r.row_number ? 'bg-indigo-50/40' : ''}`}
                          onClick={() => setExpandedRow(expandedRow === r.row_number ? null : r.row_number)}
                        >
                          <td className="px-4 py-3 text-[11px] text-textMuted font-mono">{r.row_number}</td>
                          <td className="px-4 py-3 text-xs text-textMain font-bold max-w-[140px] truncate">{r.name || <span className="text-slate-400 italic">—</span>}</td>
                          <td className="px-4 py-3 text-[11px] text-textMuted max-w-[160px] truncate">{r.email || <span className="text-slate-400 italic">—</span>}</td>
                          <td className="px-4 py-3 text-[11px] font-mono text-textMuted max-w-[160px] truncate">{r.cv_filename || <span className="text-slate-400 italic">—</span>}</td>
                          <td className="px-4 py-3"><StatusBadge status={r.validation_status} /></td>
                          <td className="px-4 py-3 text-xs text-amber-600 font-bold">{r.warnings.length || '—'}</td>
                          <td className="px-4 py-3 text-xs text-red-600 font-bold">{r.errors.length || '—'}</td>
                          <td className="px-4 py-3 text-slate-400">
                            <svg className={`w-3.5 h-3.5 transition-transform ${expandedRow === r.row_number ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
                            </svg>
                          </td>
                        </tr>
                        {expandedRow === r.row_number && (
                          <tr className="bg-indigo-50/30">
                            <td colSpan={8} className="px-6 py-3">
                              {r.errors.length > 0 && (
                                <div className="mb-2">
                                  <p className="text-[9px] font-black text-red-600 uppercase tracking-wider mb-1">Errors</p>
                                  <ul className="space-y-0.5">
                                    {r.errors.map((e, i) => (
                                      <li key={i} className="text-[11px] text-red-700 flex items-start gap-1.5">
                                        <span className="mt-0.5 text-red-400">•</span>{e}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {r.warnings.length > 0 && (
                                <div>
                                  <p className="text-[9px] font-black text-amber-600 uppercase tracking-wider mb-1">Warnings</p>
                                  <ul className="space-y-0.5">
                                    {r.warnings.map((w, i) => (
                                      <li key={i} className="text-[11px] text-amber-700 flex items-start gap-1.5">
                                        <span className="mt-0.5 text-amber-400">•</span>{w}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                              {r.errors.length === 0 && r.warnings.length === 0 && (
                                <p className="text-[11px] text-emerald-600 font-bold">✓ No issues — row is ready to import</p>
                              )}
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))}
                    {filteredRows.length === 0 && (
                      <tr>
                        <td colSpan={8} className="py-10 text-center text-[11px] text-textMuted">No rows match the current filter.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {filteredRows.length > 500 && (
                <div className="px-5 py-3 border-t border-border text-[10px] text-textMuted text-center">
                  Showing first 500 of {filteredRows.length} rows — use filters or search to narrow results.
                </div>
              )}
            </div>

            {/* Import Section */}
            <div className="bg-white rounded-2xl shadow-sm border border-border p-6">
              <h2 className="text-xs font-black text-textMain mb-4">Step 2 — Confirm Import</h2>
              <div className="flex flex-wrap gap-6 items-start">
                <div className="flex-1 space-y-3 min-w-[260px]">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={includeWarnings}
                      onChange={e => setIncludeWarnings(e.target.checked)}
                      className="mt-0.5 w-4 h-4 rounded accent-indigo-600"
                    />
                    <div>
                      <p className="text-xs font-bold text-textMain">Include warning rows</p>
                      <p className="text-[11px] text-textMuted mt-0.5">
                        Import rows that have warnings (e.g. missing phone). Error rows are always skipped.
                      </p>
                    </div>
                  </label>
                  <div className="pl-7 space-y-1 text-[11px] text-textMuted">
                    <p>• <span className="font-bold text-emerald-700">{readyCount} ready</span> rows will be imported</p>
                    <p>• <span className={`font-bold ${includeWarnings ? 'text-amber-700' : 'text-slate-400 line-through'}`}>{warningCount} warning</span> rows {includeWarnings ? 'will be imported' : 'will be skipped'}</p>
                    <p>• <span className="font-bold text-red-600">{activeBatch.error_row_count} error</span> rows will always be skipped</p>
                  </div>
                </div>
                <div className="shrink-0">
                  <button
                    onClick={() => setShowImportDialog(true)}
                    disabled={importing || importCount === 0}
                    className="flex items-center gap-2 px-6 py-3 bg-indigo-600 text-white text-sm font-black rounded-xl hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {importing ? <Spinner small /> : (
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                      </svg>
                    )}
                    Import {importCount} Candidate{importCount !== 1 ? 's' : ''}
                  </button>
                  {importCount === 0 && (
                    <p className="text-[10px] text-red-600 mt-1.5 text-center">No eligible rows to import.</p>
                  )}
                </div>
              </div>

              {/* Also allow re-uploading Excel or ZIP */}
              <div className="mt-5 pt-5 border-t border-border flex flex-wrap gap-3 items-center">
                <p className="text-[11px] text-textMuted flex-1">Need to fix the spreadsheet? Re-upload to re-validate.</p>
                <button
                  onClick={() => xlsxRef.current?.click()}
                  disabled={uploadingExcel}
                  className="text-[11px] font-bold text-indigo-600 hover:underline"
                >
                  Re-upload Excel
                </button>
                <input
                  ref={xlsxRef}
                  type="file"
                  accept=".xlsx,.xls"
                  className="hidden"
                  onChange={e => { const f = e.target.files?.[0]; if (f) { handleExcelFile(f); e.target.value = ''; } }}
                />
                {!hasZip && (
                  <>
                    <span className="text-slate-300">|</span>
                    <button
                      onClick={() => zipRef.current?.click()}
                      disabled={uploadingZip}
                      className="text-[11px] font-bold text-violet-600 hover:underline"
                    >
                      Upload ZIP
                    </button>
                    <input
                      ref={zipRef}
                      type="file"
                      accept=".zip"
                      className="hidden"
                      onChange={e => { const f = e.target.files?.[0]; if (f) { handleZipFile(f); e.target.value = ''; } }}
                    />
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE: PROCESSING — live progress dashboard
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'processing' && (
          <div className="space-y-5">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                {(() => {
                  const s = procSummary;
                  const done = s && (
                    s.batch_status === 'completed' ||
                    s.batch_status === 'failed' ||
                    s.progress_percentage >= 100 ||
                    (s.queued_rows === 0 && s.processing_rows === 0 &&
                      (s.completed_rows + s.failed_rows) >= s.imported_rows)
                  );
                  return done ? (
                    <p className="text-sm font-bold text-emerald-600">Processing complete — finalizing…</p>
                  ) : (
                    <>
                      <Spinner />
                      <p className="text-sm font-bold text-textMain">Processing in progress — auto-refreshing every 5 seconds</p>
                    </>
                  );
                })()}</div>
              <button
                onClick={() => setPollTick(t => t + 1)}
                className="ml-auto text-[11px] font-bold text-indigo-600 hover:underline flex items-center gap-1"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh now
              </button>
            </div>

            {procSummary && (
              <>
                {/* Progress bar */}
                <div className="bg-white rounded-2xl shadow-sm border border-border p-5">
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-xs font-black text-textMain">Overall Progress</p>
                    <p className="text-2xl font-black text-indigo-600">{procSummary.progress_percentage}%</p>
                  </div>
                  <ProgressBar pct={procSummary.progress_percentage} />
                  <p className="text-[10px] text-textMuted mt-2">
                    {procSummary.completed_rows} completed · {procSummary.processing_rows} processing · {procSummary.queued_rows} queued · {procSummary.failed_rows} failed
                  </p>
                </div>

                {/* Summary cards */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                  <SummaryCard label="Total Imported" value={procSummary.imported_rows} color="text-slate-800" />
                  <SummaryCard label="Queued"         value={procSummary.queued_rows}    color="text-blue-600" />
                  <SummaryCard label="Processing"     value={procSummary.processing_rows} color="text-indigo-600" />
                  <SummaryCard label="Completed"      value={procSummary.completed_rows}  color="text-emerald-600" />
                  <SummaryCard label="Failed"         value={procSummary.failed_rows}     color={procSummary.failed_rows > 0 ? 'text-red-600' : 'text-slate-400'} />
                </div>
              </>
            )}

            {/* Processing rows table */}
            <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
              <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest">Candidate Processing Status</h3>
                {loadingProcRows && <Spinner small />}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border bg-slate-50/60">
                      {['#', 'Candidate', 'Stage', 'Progress', 'Score', 'Decision', 'Reason', 'Actions'].map(h => (
                        <th key={h} className="px-4 py-3 text-[9px] font-black text-textMuted uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {procRows.map(r => (
                      <tr key={r.row_number} className="border-b border-border/50 hover:bg-slate-50/40 transition-colors">
                        <td className="px-4 py-3 text-[11px] text-textMuted font-mono">{r.row_number}</td>
                        <td className="px-4 py-3 text-xs text-textMain font-bold max-w-[160px] truncate">{r.candidate_name}</td>
                        <td className="px-4 py-3"><StatusBadge status={r.processing_stage || 'pending'} /></td>
                        <td className="px-4 py-3 min-w-[100px]">
                          <div className="flex items-center gap-2">
                            <div className="flex-1 bg-slate-100 rounded-full h-1.5 overflow-hidden">
                              <div className="h-full bg-indigo-400 transition-all" style={{ width: `${r.progress_percentage}%` }} />
                            </div>
                            <span className="text-[10px] font-bold text-textMuted w-7 text-right">{r.progress_percentage}%</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-xs font-black text-textMain">{r.final_score != null ? r.final_score : <span className="text-slate-400">—</span>}</td>
                        <td className={`px-4 py-3 text-xs capitalize ${decisionColour(r.decision)}`}>{r.decision || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3 text-[11px] text-red-600 max-w-[160px] truncate">{r.failure_reason || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3">
                          {r.application_id ? (
                            <button
                              onClick={() => onOpenApplication(activeBatch!.job_id, r.application_id!)}
                              className="text-[10px] font-bold text-indigo-600 hover:underline whitespace-nowrap"
                            >
                              View →
                            </button>
                          ) : (
                            <span className="text-slate-300 text-[10px]">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {procRows.length === 0 && (
                      <tr>
                        <td colSpan={8} className="py-10 text-center text-[11px] text-textMuted">
                          {loadingProcRows ? 'Loading…' : 'No row data yet — check back in a moment.'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {/* Pagination */}
              {procRows.length === PAGE_SIZE && (
                <div className="px-5 py-3 border-t border-border flex items-center justify-between">
                  <button
                    onClick={() => { setProcPage(p => Math.max(0, p - 1)); setPollTick(t => t + 1); }}
                    disabled={procPage === 0}
                    className="text-[11px] font-bold text-indigo-600 disabled:text-slate-400 hover:underline"
                  >
                    ← Previous
                  </button>
                  <span className="text-[10px] text-textMuted">Page {procPage + 1}</span>
                  <button
                    onClick={() => { setProcPage(p => p + 1); setPollTick(t => t + 1); }}
                    className="text-[11px] font-bold text-indigo-600 hover:underline"
                  >
                    Next →
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ══════════════════════════════════════════════════════════════════
            PHASE: COMPLETED — completion banner + final results
        ══════════════════════════════════════════════════════════════════ */}
        {phase === 'completed' && (
          <div className="space-y-5">
            {/* Banner */}
            <div className={`rounded-2xl border p-6 flex items-start gap-4 ${
              (procSummary?.failed_rows || 0) > 0 && (procSummary?.completed_rows || 0) === 0
                ? 'bg-red-50 border-red-200'
                : 'bg-emerald-50 border-emerald-200'
            }`}>
              <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${
                (procSummary?.failed_rows || 0) > 0 && (procSummary?.completed_rows || 0) === 0
                  ? 'bg-red-100' : 'bg-emerald-100'
              }`}>
                {(procSummary?.failed_rows || 0) > 0 && (procSummary?.completed_rows || 0) === 0 ? (
                  <svg className="w-6 h-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                ) : (
                  <svg className="w-6 h-6 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </div>
              <div className="flex-1">
                <p className="text-base font-black text-textMain mb-1">Batch Processing Completed</p>
                <p className="text-[11px] text-textMuted">
                  {procSummary ? (
                    <>
                      <span className="font-bold text-emerald-700">{procSummary.completed_rows} completed</span>
                      {procSummary.failed_rows > 0 && (
                        <> · <span className="font-bold text-red-600">{procSummary.failed_rows} failed</span></>
                      )}
                      {' '}out of {procSummary.imported_rows} imported candidates.
                    </>
                  ) : 'Processing complete.'}
                </p>
              </div>
              <button
                onClick={handleCreateBatch}
                disabled={creatingBatch}
                className="shrink-0 px-4 py-2 bg-indigo-600 text-white text-xs font-black rounded-xl hover:bg-indigo-700 transition-colors disabled:opacity-60"
              >
                New Batch
              </button>
            </div>

            {/* Final summary cards */}
            {procSummary && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <SummaryCard label="Total Imported"  value={procSummary.imported_rows}  color="text-slate-800" />
                <SummaryCard label="Completed"        value={procSummary.completed_rows} color="text-emerald-600" />
                <SummaryCard label="Failed"           value={procSummary.failed_rows}    color={procSummary.failed_rows > 0 ? 'text-red-600' : 'text-slate-400'} />
                <SummaryCard label="Progress"         value={`${procSummary.progress_percentage}%`} color="text-indigo-600" />
              </div>
            )}

            {/* Final results table */}
            <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
              <div className="px-5 py-4 border-b border-border flex items-center justify-between">
                <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest">Processing Results</h3>
                <button
                  onClick={async () => {
                    if (!activeBatch) return;
                    try {
                      const rowsData = await apiService.get(
                        `${BASE}/batches/${activeBatch.batch_id}/processing/rows`,
                        { limit: '50', offset: String(procPage * PAGE_SIZE) },
                        token,
                      );
                      setProcRows(rowsData.rows || []);
                    } catch { /* ignore */ }
                  }}
                  className="text-[11px] font-bold text-indigo-600 hover:underline"
                >
                  Refresh
                </button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-border bg-slate-50/60">
                      {['#', 'Candidate', 'Stage', 'Score', 'Decision', 'Failure Reason', 'Actions'].map(h => (
                        <th key={h} className="px-4 py-3 text-[9px] font-black text-textMuted uppercase tracking-wider whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {procRows.map(r => (
                      <tr key={r.row_number} className="border-b border-border/50 hover:bg-slate-50/40 transition-colors">
                        <td className="px-4 py-3 text-[11px] text-textMuted font-mono">{r.row_number}</td>
                        <td className="px-4 py-3 text-xs text-textMain font-bold max-w-[160px] truncate">{r.candidate_name}</td>
                        <td className="px-4 py-3"><StatusBadge status={r.processing_stage || 'pending'} /></td>
                        <td className="px-4 py-3 text-xs font-black text-textMain">{r.final_score != null ? r.final_score : <span className="text-slate-400">—</span>}</td>
                        <td className={`px-4 py-3 text-xs capitalize ${decisionColour(r.decision)}`}>{r.decision || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3 text-[11px] text-red-600 max-w-[160px] truncate">{r.failure_reason || <span className="text-slate-400">—</span>}</td>
                        <td className="px-4 py-3">
                          {r.application_id ? (
                            <button
                              onClick={() => onOpenApplication(activeBatch!.job_id, r.application_id!)}
                              className="text-[10px] font-bold text-indigo-600 hover:underline whitespace-nowrap"
                            >
                              View Application →
                            </button>
                          ) : (
                            <span className="text-slate-300 text-[10px]">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {procRows.length === 0 && (
                      <tr>
                        <td colSpan={7} className="py-10 text-center text-[11px] text-textMuted">No data available.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════════════
          IMPORT CONFIRMATION DIALOG
      ══════════════════════════════════════════════════════════════════ */}
      {showImportDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm px-4">
          <div className="bg-white rounded-2xl shadow-2xl border border-border w-full max-w-md p-6">
            <h3 className="text-base font-black text-textMain mb-2">Confirm Import</h3>
            <p className="text-[11px] text-textMuted mb-5 leading-relaxed">
              This will create application records for the following candidates and submit them to the scoring pipeline. This action cannot be undone.
            </p>
            <div className="bg-slate-50 rounded-xl border border-border px-4 py-3 mb-5 space-y-1.5 text-[11px]">
              <div className="flex justify-between">
                <span className="text-textMuted">Ready rows</span>
                <span className="font-bold text-emerald-700">{readyCount}</span>
              </div>
              {includeWarnings && (
                <div className="flex justify-between">
                  <span className="text-textMuted">Warning rows (included)</span>
                  <span className="font-bold text-amber-700">{warningCount}</span>
                </div>
              )}
              <div className="flex justify-between border-t border-border/60 pt-1.5 mt-1.5">
                <span className="font-bold text-textMain">Total to import</span>
                <span className="font-black text-indigo-600">{importCount}</span>
              </div>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowImportDialog(false)}
                className="flex-1 py-2.5 border border-border rounded-xl text-xs font-bold text-textMuted hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleImport}
                className="flex-1 py-2.5 bg-indigo-600 text-white text-xs font-black rounded-xl hover:bg-indigo-700 transition-colors"
              >
                Import {importCount} Candidates
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
