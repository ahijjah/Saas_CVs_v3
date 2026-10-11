import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { QualifyingContext, QualifyingContextReview } from '../types';

// Recruiter review of the "required experience context". The backend review status is the single
// source of truth; a missing value is "not assessed" (section hidden), never "no restriction".

const T = {
  en: {
    title: 'Required experience context',
    identified: 'Only experience gained in the following counts:',
    none: 'No additional qualifying context restriction.',
    needsLabel: 'Needs confirmation:',
    needsText: 'The job description may limit which experience counts. Please review and choose.',
    possible: 'Possible restriction:',
    suggested: 'Suggested automatically — not yet confirmed',
    confirmedBy: 'Confirmed by {name} on {date}',
    setBy: 'Set by {name} on {date}',
    failed: 'Could not be assessed automatically. Please review the job description and set it.',
    question: 'Does this job limit which experience counts?',
    yes: 'Yes — only experience in:',
    no: 'No — any relevant experience counts',
    onePerLine: 'One per line',
    validation: 'Add at least one context, or choose "No".',
    conflict: 'This was changed by someone else or by an automatic update. The latest version is shown.',
    saved: 'Required experience context saved',
    confirm: 'Confirm',
    edit: 'Edit',
    save: 'Save',
    cancel: 'Cancel',
  },
  ar: {
    title: 'سياق الخبرة المطلوب',
    identified: 'تُحتسب فقط الخبرة المكتسبة في:',
    none: 'لا يوجد قيد إضافي على سياق الخبرة.',
    needsLabel: 'بحاجة إلى تأكيد:',
    needsText: 'قد يحدد الوصف الوظيفي نوع الخبرة المحتسبة. يرجى المراجعة والاختيار.',
    possible: 'القيد المحتمل:',
    suggested: 'مقترح تلقائيًا — لم يتم تأكيده بعد',
    confirmedBy: 'تم التأكيد بواسطة {name} بتاريخ {date}',
    setBy: 'تم التحديد بواسطة {name} بتاريخ {date}',
    failed: 'تعذر التقييم التلقائي. يرجى مراجعة الوصف الوظيفي وتحديده.',
    question: 'هل تحدد هذه الوظيفة نوع الخبرة المحتسبة؟',
    yes: 'نعم — فقط الخبرة في:',
    no: 'لا — تُحتسب أي خبرة ذات صلة',
    onePerLine: 'سطر لكل قيد',
    validation: 'أضف سياقًا واحدًا على الأقل أو اختر "لا".',
    conflict: 'تم تغيير هذا من قبل مستخدم آخر أو تحديث تلقائي. يتم عرض أحدث نسخة.',
    saved: 'تم حفظ سياق الخبرة المطلوب',
    confirm: 'تأكيد',
    edit: 'تعديل',
    save: 'حفظ',
    cancel: 'إلغاء',
  },
};

interface Props {
  jobId: string;
  review?: QualifyingContextReview;
  stored: QualifyingContext | null | undefined;   // exactly as returned by the API (used for the conflict check)
  canEdit: boolean;                               // page-level permission (admin / hr_manager)
  isAr: boolean;
  token: string;
  onReload: () => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const fill = (tpl: string, name: string | null, date: string | null, isAr: boolean) => {
  const d = date ? new Date(date) : null;
  const dateText = d && !isNaN(d.getTime()) ? d.toLocaleDateString(isAr ? 'ar' : 'en-GB') : '—';
  return tpl.replace('{name}', name || '—').replace('{date}', dateText);
};

const Chips: React.FC<{ items: string[] }> = ({ items }) => (
  <div className="flex flex-wrap gap-1.5 mt-1">
    {items.map((c, i) => (
      <span key={i} className="px-2 py-0.5 bg-white border border-border rounded text-[10px] font-bold text-textMain">{c}</span>
    ))}
  </div>
);

export const QualifyingContextPanel: React.FC<Props> = ({ jobId, review, stored, canEdit, isAr, token, onReload, addToast }) => {
  const t = isAr ? T.ar : T.en;
  const [editing, setEditing] = useState(false);
  const [restricts, setRestricts] = useState(true);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!review || review.status === 'not_assessed') return null;

  const base = `${WEBHOOK_CONFIG.QUALIFYING_CONTEXT_BASE_URL}/${jobId}/criteria/qualifying-context`;
  const expected = stored ?? null;

  const handleError = async (err: any) => {
    if (err?.status === 409) {
      await onReload();
      setEditing(false);
      addToast(t.conflict, 'info');
    } else {
      addToast(err?.message || String(err), 'error');
    }
  };

  const confirm = async () => {
    setBusy(true);
    try {
      await apiService.post(`${base}/confirm`, { expected_qualifying_context: expected }, token);
      await onReload();
      addToast(t.saved, 'success');
    } catch (err) {
      await handleError(err);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = () => {
    setRestricts(review.state !== 'none');
    setDraft((review.contexts || []).join('\n'));
    setError(null);
    setEditing(true);
  };

  const save = async () => {
    const contexts = draft.split('\n').map(x => x.trim()).filter(Boolean);
    if (restricts && contexts.length === 0) {
      setError(t.validation);
      return;
    }
    setBusy(true);
    try {
      await apiService.put(base, {
        state: restricts ? 'identified' : 'none',
        contexts: restricts ? contexts : [],
        expected_qualifying_context: expected,
      }, token);
      await onReload();
      setEditing(false);
      addToast(t.saved, 'success');
    } catch (err) {
      await handleError(err);
    } finally {
      setBusy(false);
    }
  };

  const showConfirm = canEdit && review.can_confirm && !editing;
  const showEdit = canEdit && review.can_edit && !editing;
  const valueView = review.state === 'identified'
    ? (<><p className="text-xs font-bold text-textMain">{t.identified}</p><Chips items={review.contexts} /></>)
    : review.state === 'none'
      ? <p className="text-xs font-bold text-textMain">{t.none}</p>
      : null;

  return (
    <div className="mt-3 pt-3 border-t border-border" dir={isAr ? 'rtl' : 'ltr'}>
      <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.title}</p>

      {review.status === 'assessment_failed' && (
        <p className="text-xs font-bold text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">{t.failed}</p>
      )}

      {review.status === 'needs_confirmation' && (
        <div className="text-xs bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">
          <p className="font-bold text-amber-800"><span className="font-black">{t.needsLabel}</span> {t.needsText}</p>
          {review.contexts.length > 0 && (<><p className="mt-1 font-bold text-textMuted">{t.possible}</p><Chips items={review.contexts} /></>)}
        </div>
      )}

      {(review.status === 'awaiting_confirmation' || review.status === 'confirmed' || review.status === 'edited') && (
        <div>
          {valueView}
          <p className="mt-1 text-[10px] font-bold text-textMuted">
            {review.status === 'awaiting_confirmation' && t.suggested}
            {review.status === 'confirmed' && fill(t.confirmedBy, review.changed_by_name, review.changed_at, isAr)}
            {review.status === 'edited' && fill(t.setBy, review.changed_by_name, review.changed_at, isAr)}
          </p>
        </div>
      )}

      {(showConfirm || showEdit) && (
        <div className="flex gap-2 mt-2">
          {showConfirm && (
            <button disabled={busy} onClick={confirm}
              className="px-3 py-1 rounded-lg bg-primary text-white text-[11px] font-black disabled:opacity-50">{t.confirm}</button>
          )}
          {showEdit && (
            <button disabled={busy} onClick={startEdit}
              className="px-3 py-1 rounded-lg border border-border bg-white text-[11px] font-black text-textMain disabled:opacity-50">{t.edit}</button>
          )}
        </div>
      )}

      {editing && canEdit && (
        <div className="mt-2 space-y-2 bg-white border border-border rounded-xl p-3">
          <p className="text-xs font-black text-textMain">{t.question}</p>
          <label className="flex items-center gap-2 text-xs font-bold text-textMain">
            <input type="radio" checked={restricts} onChange={() => { setRestricts(true); setError(null); }} />
            {t.yes}
          </label>
          {restricts && (
            <textarea rows={3} value={draft} placeholder={t.onePerLine}
              onChange={e => { setDraft(e.target.value); setError(null); }}
              className="w-full px-3 py-2 border border-border rounded-xl text-xs resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
          )}
          <label className="flex items-center gap-2 text-xs font-bold text-textMain">
            <input type="radio" checked={!restricts} onChange={() => { setRestricts(false); setError(null); }} />
            {t.no}
          </label>
          {error && <p className="text-[11px] font-bold text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button disabled={busy} onClick={save}
              className="px-3 py-1 rounded-lg bg-primary text-white text-[11px] font-black disabled:opacity-50">{t.save}</button>
            <button disabled={busy} onClick={() => setEditing(false)}
              className="px-3 py-1 rounded-lg border border-border bg-white text-[11px] font-black text-textMain disabled:opacity-50">{t.cancel}</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default QualifyingContextPanel;
