// Final Candidate Matrix: the on-screen wording (English and Arabic) and the small pure helpers the screen shares.
// Nothing here scores, ranks or decides: every status and number comes from the server (backend/services/matrix).

import type {
  ColumnGroup, Eligibility, FlagKind, MatrixColumn, MatrixFilters, MatrixLayout, MatrixRow, MatrixSort, Severity,
} from '../services/matrixApi';

export type Lang = 'en' | 'ar';
export const NAME_COLUMN = 'candidate.name';
export const DEFAULT_SORT: MatrixSort = { column: 'candidate.applied_at', direction: 'desc' };

const COPY = {
  en: {
    title: 'Candidate Matrix', back: 'Back to job', loading: 'Loading the matrix…', loadFailed: 'The matrix could not be loaded.',
    empty: 'No candidates match.', emptyHint: 'Change or clear the filters.', retry: 'Try again',
    principle: 'The overall score is one decision-support element. Eligibility, mandatory criteria, gates and review flags are shown beside it, and candidates are never ranked automatically: sorting is your choice.',
    openCandidate: 'Open candidate', rowsCount: (n: number) => `${n} candidate${n === 1 ? '' : 's'}`,
    page: (p: number, pages: number) => `Page ${p} of ${pages}`, previous: 'Previous', next: 'Next',
    columns: 'Columns', filters: 'Filters', export: 'Export CSV', exporting: 'Exporting…', exportFailed: 'The export failed.',
    saveMine: 'Save my view', saveShared: 'Save for this job', resetMine: 'Reset my view', resetShared: 'Reset job layout',
    saved: 'Saved.', resetDone: 'Reset.', viewSource: { personal: 'Your personal view', job: 'Job configuration', tenant: 'Tenant default', system: 'System default' },
    unsaved: 'Unsaved changes', readOnly: 'Read-only: you can arrange the columns for this session but not save them.',
    conflict: 'The layout was changed by someone else. Reload it and try again.',
    unavailableColumns: (n: number) => `${n} saved column${n === 1 ? ' is' : 's are'} no longer available for this job.`,
    groups: { candidate: 'Candidate information', decision: 'Decision and score', mandatory: 'Mandatory requirements', preferred: 'Preferred requirements', gates: 'Gates', workflow: 'Workflow information' } as Record<ColumnGroup, string>,
    freezeCandidate: 'Keep the candidate column in view', moveUp: 'Move up', moveDown: 'Move down', hideColumn: 'Hide', showColumn: 'Show',
    visibleColumns: 'Visible columns, in order', availableColumns: 'Available columns', locked: 'Always shown',
    unavailable: 'Not available', sortBy: 'Sort by', ascending: 'Ascending', descending: 'Descending', clearFilters: 'Clear filters',
    search: 'Search candidate name', eligibility: 'Eligibility', scoreRange: 'Overall score', min: 'Min', max: 'Max', scoringStatus: 'Scoring status',
    workflow: 'Workflow status', flags: 'Review flags', criteria: 'Criteria', addCriteria: 'Add criteria filter', chooseCriteria: 'Choose a requirement or gate',
    removeFilter: 'Remove filter', applied: 'Application date', from: 'From', to: 'To',
    status: {
      eligible: 'Eligible', not_eligible: 'Not Eligible', review_required: 'Review Required', not_assessed: 'Not Assessed',
      met: 'Met', partially_met: 'Partially Met', not_met: 'Not Met', unclear: 'Unclear', yes: 'Yes', no: 'No', passed: 'Passed', failed: 'Failed',
    } as Record<string, string>,
    scoringState: { scored: 'Scored', review_required: 'Review Required', not_scored: 'Not scored', legacy: 'Legacy score', none: 'Not scored' } as Record<string, string>,
    method: { item_level_v2: 'Item-level', legacy_category: 'Legacy category', none: '' } as Record<string, string>,
    stale: 'Out of date', staleHelp: (used: number | null) => `Scored against requirements revision ${used ?? '?'}; the requirements have changed since.`,
    summary: { met: 'Met', partially_met: 'Partially Met', not_met: 'Not Met', unclear: 'Unclear', yes: 'Yes', no: 'No', passed: 'Passed', failed: 'Failed', review_required: 'Review Required', not_assessed: 'Not Assessed', total: 'Total' } as Record<string, string>,
    flag: { unclear_criteria: 'Unclear criteria', conflicting_evidence: 'Conflicting evidence', evidence_not_found: 'Evidence not found in CV', low_confidence: 'Low confidence', processing_error: 'Processing error', unresolved_review: 'Unresolved review' } as Record<FlagKind, string>,
    severity: { high: 'High', medium: 'Medium', low: 'Low' } as Record<Severity, string>,
    noFlags: 'None', gateOverridden: 'Overridden by a person',
    drawerTitle: 'Evidence and review', drawerClose: 'Close', evidence: 'Evidence', noEvidence: 'No supporting quotation from the CV', explanation: 'Explanation',
    confidence: 'Confidence', aiResult: 'AI result', humanResolution: 'Human resolution', resolvedBy: 'Resolved by', reason: 'Reason', history: 'History',
    gateEvidence: 'Gate evidence', openFull: 'Open full candidate details', flaggedTargets: 'Flagged items', allCriteria: 'All criteria', loadingEvidence: 'Loading evidence…',
    weight: 'Weight', result: 'Result', requirement: 'Requirement',
  },
  ar: {
    title: 'مصفوفة المرشحين', back: 'العودة إلى الوظيفة', loading: 'جارٍ تحميل المصفوفة…', loadFailed: 'تعذّر تحميل المصفوفة.',
    empty: 'لا يوجد مرشحون مطابقون.', emptyHint: 'غيّر المرشحات أو امسحها.', retry: 'حاول مرة أخرى',
    principle: 'الدرجة الإجمالية عنصر واحد لدعم القرار. تُعرض الأهلية والمعايير الإلزامية والبوابات وتنبيهات المراجعة بجانبها، ولا يتم ترتيب المرشحين تلقائيًا: الترتيب باختيارك.',
    openCandidate: 'فتح المرشح', rowsCount: (n: number) => `${n} مرشح`,
    page: (p: number, pages: number) => `صفحة ${p} من ${pages}`, previous: 'السابق', next: 'التالي',
    columns: 'الأعمدة', filters: 'المرشحات', export: 'تصدير CSV', exporting: 'جارٍ التصدير…', exportFailed: 'فشل التصدير.',
    saveMine: 'حفظ عرضي', saveShared: 'حفظ لهذه الوظيفة', resetMine: 'إعادة تعيين عرضي', resetShared: 'إعادة تعيين تخطيط الوظيفة',
    saved: 'تم الحفظ.', resetDone: 'تمت إعادة التعيين.', viewSource: { personal: 'عرضك الشخصي', job: 'إعداد الوظيفة', tenant: 'الإعداد الافتراضي للمنظمة', system: 'الإعداد الافتراضي للنظام' },
    unsaved: 'تغييرات غير محفوظة', readOnly: 'للقراءة فقط: يمكنك ترتيب الأعمدة لهذه الجلسة دون حفظها.',
    conflict: 'غيّر شخص آخر التخطيط. أعد تحميله وحاول مرة أخرى.',
    unavailableColumns: (n: number) => `${n} من الأعمدة المحفوظة لم تعد متاحة لهذه الوظيفة.`,
    groups: { candidate: 'معلومات المرشح', decision: 'القرار والدرجة', mandatory: 'المتطلبات الإلزامية', preferred: 'المتطلبات المفضلة', gates: 'البوابات', workflow: 'معلومات سير العمل' } as Record<ColumnGroup, string>,
    freezeCandidate: 'إبقاء عمود المرشح ظاهرًا', moveUp: 'تحريك لأعلى', moveDown: 'تحريك لأسفل', hideColumn: 'إخفاء', showColumn: 'إظهار',
    visibleColumns: 'الأعمدة الظاهرة بالترتيب', availableColumns: 'الأعمدة المتاحة', locked: 'ظاهر دائمًا',
    unavailable: 'غير متاح', sortBy: 'الترتيب حسب', ascending: 'تصاعدي', descending: 'تنازلي', clearFilters: 'مسح المرشحات',
    search: 'ابحث باسم المرشح', eligibility: 'الأهلية', scoreRange: 'الدرجة الإجمالية', min: 'الأدنى', max: 'الأعلى', scoringStatus: 'حالة التقييم',
    workflow: 'حالة سير العمل', flags: 'تنبيهات المراجعة', criteria: 'المعايير', addCriteria: 'إضافة مرشح معيار', chooseCriteria: 'اختر متطلبًا أو بوابة',
    removeFilter: 'إزالة المرشح', applied: 'تاريخ التقديم', from: 'من', to: 'إلى',
    status: {
      eligible: 'مؤهل', not_eligible: 'غير مؤهل', review_required: 'مطلوب مراجعة', not_assessed: 'لم يُقيَّم',
      met: 'مستوفى', partially_met: 'مستوفى جزئيًا', not_met: 'غير مستوفى', unclear: 'غير واضح', yes: 'نعم', no: 'لا', passed: 'اجتاز', failed: 'لم يجتز',
    } as Record<string, string>,
    scoringState: { scored: 'تم التقييم', review_required: 'مطلوب مراجعة', not_scored: 'بدون درجة', legacy: 'درجة سابقة', none: 'بدون درجة' } as Record<string, string>,
    method: { item_level_v2: 'على مستوى البنود', legacy_category: 'على مستوى الفئات', none: '' } as Record<string, string>,
    stale: 'قديمة', staleHelp: (used: number | null) => `تم التقييم وفق نسخة المتطلبات ${used ?? '؟'}؛ وقد تغيّرت المتطلبات منذ ذلك الحين.`,
    summary: { met: 'مستوفى', partially_met: 'جزئي', not_met: 'غير مستوفى', unclear: 'غير واضح', yes: 'نعم', no: 'لا', passed: 'اجتاز', failed: 'لم يجتز', review_required: 'مراجعة', not_assessed: 'لم يُقيَّم', total: 'الإجمالي' } as Record<string, string>,
    flag: { unclear_criteria: 'معايير غير واضحة', conflicting_evidence: 'أدلة متعارضة', evidence_not_found: 'الدليل غير موجود في السيرة الذاتية', low_confidence: 'ثقة منخفضة', processing_error: 'خطأ في المعالجة', unresolved_review: 'مراجعة غير محسومة' } as Record<FlagKind, string>,
    severity: { high: 'مرتفعة', medium: 'متوسطة', low: 'منخفضة' } as Record<Severity, string>,
    noFlags: 'لا يوجد', gateOverridden: 'تجاوزه شخص',
    drawerTitle: 'الدليل والمراجعة', drawerClose: 'إغلاق', evidence: 'الدليل', noEvidence: 'لا يوجد اقتباس داعم من السيرة الذاتية', explanation: 'التفسير',
    confidence: 'الثقة', aiResult: 'نتيجة الذكاء الاصطناعي', humanResolution: 'الحسم البشري', resolvedBy: 'حسمه', reason: 'السبب', history: 'السجل',
    gateEvidence: 'دليل البوابة', openFull: 'فتح التفاصيل الكاملة للمرشح', flaggedTargets: 'العناصر المنبّه عليها', allCriteria: 'كل المعايير', loadingEvidence: 'جارٍ تحميل الدليل…',
    weight: 'الوزن', result: 'النتيجة', requirement: 'المتطلب',
  },
} as const;

export type MatrixCopy = typeof COPY['en'] | typeof COPY['ar'];
export function matrixCopy(lang: Lang): MatrixCopy { return lang === 'ar' ? COPY.ar : COPY.en; }

export type Tone = 'green' | 'amber' | 'red' | 'slate';
export function statusTone(status: string): Tone {
  switch (status) {
    case 'eligible': case 'met': case 'yes': case 'passed': case 'scored': return 'green';
    case 'partially_met': case 'unclear': case 'review_required': return 'amber';
    case 'not_eligible': case 'not_met': case 'no': case 'failed': return 'red';
    default: return 'slate';
  }
}
export const TONE_CLASS: Record<Tone, string> = {
  green: 'bg-emerald-100 text-emerald-800', amber: 'bg-amber-100 text-amber-800', red: 'bg-red-100 text-red-800', slate: 'bg-slate-100 text-slate-700',
};
export const SEVERITY_TONE: Record<Severity, Tone> = { high: 'red', medium: 'amber', low: 'slate' };

export function statusLabel(status: string, lang: Lang): string {
  const copy = matrixCopy(lang);
  return copy.status[status] ?? status;
}

export function columnLabel(column: MatrixColumn, lang: Lang): string { return lang === 'ar' ? column.label.ar : column.label.en; }

/** The highest severity among a row's flags, or null when it has none. Only a label of how directly a flag can change the outcome. */
export function topSeverity(row: MatrixRow): Severity | null {
  const order: Severity[] = ['high', 'medium', 'low'];
  for (const s of order) if (row.flags.items.some((f) => f.severity === s)) return s;
  return null;
}

/** "Met 4 · Partially Met 1 · Not Met 1 · Unclear 1 · Total 7", or Not Assessed when the application has no item-level result. */
export function summaryText(parts: [string, number | null][], total: number | null, assessed: boolean, lang: Lang): string {
  const copy = matrixCopy(lang);
  if (!assessed) return copy.status.not_assessed;
  return [...parts.map(([k, v]) => `${copy.summary[k]} ${v ?? 0}`), `${copy.summary.total} ${total ?? 0}`].join(' · ');
}

// ── layout editing (pure) ───────────────────────────────────────────────────────────────────────────────────────────

export function sameLayout(a: MatrixLayout, b: MatrixLayout): boolean { return JSON.stringify(a) === JSON.stringify(b); }

export function toggleColumn(columns: string[], id: string): string[] {
  if (id === NAME_COLUMN) return columns;
  return columns.includes(id) ? columns.filter((c) => c !== id) : [...columns, id];
}

/** Move a column one place; the candidate column always stays first. */
export function moveColumn(columns: string[], id: string, delta: -1 | 1): string[] {
  const from = columns.indexOf(id);
  const to = from + delta;
  if (id === NAME_COLUMN || from < 0 || to < 1 || to >= columns.length) return columns;
  const next = [...columns];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

/** Drag-and-drop reorder: put `id` where `target` is. The candidate column cannot move or be displaced. */
export function dropColumn(columns: string[], id: string, target: string): string[] {
  if (id === NAME_COLUMN || target === NAME_COLUMN || id === target || !columns.includes(id) || !columns.includes(target)) return columns;
  const without = columns.filter((c) => c !== id);
  without.splice(without.indexOf(target) + (columns.indexOf(id) < columns.indexOf(target) ? 1 : 0), 0, id);
  return without;
}

export function toggleValue<T>(list: T[] | null | undefined, value: T): T[] {
  const current = list ?? [];
  return current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
}

export function hasFilters(f: MatrixFilters): boolean {
  return !!(f.search || f.eligibility?.length || f.scoring_status?.length || f.score_min != null || f.score_max != null || f.workflow_status?.length
    || f.source?.length || f.flags?.length || f.applied_after || f.applied_before || f.assigned_to || f.mandatory?.length || f.preferred?.length || f.gates?.length);
}

export const EMPTY_FILTERS: MatrixFilters = { mandatory: [], preferred: [], gates: [] };
export const WORKFLOW_STATUSES = ['awaiting_review', 'under_review', 'shortlisted', 'interviewing', 'offer_made', 'hired', 'rejected', 'withdrawn', 'on_hold'] as const;
export const ELIGIBILITIES: Eligibility[] = ['eligible', 'not_eligible', 'review_required', 'not_assessed'];
export const FLAG_KINDS: FlagKind[] = ['unclear_criteria', 'conflicting_evidence', 'evidence_not_found', 'low_confidence', 'processing_error', 'unresolved_review'];

export function workflowLabel(status: string | null, lang: Lang): string {
  const ar: Record<string, string> = { awaiting_review: 'بانتظار المراجعة', under_review: 'قيد المراجعة', shortlisted: 'في القائمة المختصرة', interviewing: 'المقابلة', offer_made: 'تم تقديم عرض', hired: 'تم التوظيف', rejected: 'مرفوض', withdrawn: 'منسحب', on_hold: 'معلّق' };
  if (!status) return '';
  if (lang === 'ar') return ar[status] ?? status;
  return status.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
