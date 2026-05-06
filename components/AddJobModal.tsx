
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG, GLOBAL_FORWARDING_EMAIL } from '../config';
import { User } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface AddJobModalProps {
  onClose: () => void;
  onSuccess: (jobId: string) => void;
  token: string;
  user: User | null;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface FormData {
  title: string;
  department: string;
  location: string;
  job_type: string;
  duration: string;
  description: string;
}

const T = {
  en: {
    title: 'Create New Job',
    subtitle: 'Campaign Details & Candidate Criteria',
    inboxNote: 'Dedicated Job Inbox',
    inboxDesc: 'A dedicated job inbox will be generated automatically. You can find the address in the job details after creation.',
    fwdNote: 'Forwarding Required',
    fwdDesc: (email: string) => `Please forward all CVs for this job to ${email}. Our AI will route them automatically.`,
    jobTitle: 'Job Title',
    department: 'Department / Client',
    jobLocation: 'Location',
    jobType: 'Job Type',
    duration: 'Duration',
    jobDesc: 'Job Description',
    jobDescPlaceholder: 'Describe the role, responsibilities, required skills and experience…',
    cancel: 'Cancel',
    creating: 'Creating…',
    submit: 'Create Job',
    errorTitle: 'Job title is required.',
    errorDesc: 'Job description is required.',
  },
  ar: {
    title: 'إنشاء وظيفة جديدة',
    subtitle: 'تفاصيل الحملة ومعايير المرشحين',
    inboxNote: 'صندوق بريد وظيفي مخصص',
    inboxDesc: 'سيتم إنشاء صندوق بريد مخصص للوظيفة تلقائياً. يمكنك العثور على العنوان في تفاصيل الوظيفة بعد الإنشاء.',
    fwdNote: 'التوجيه مطلوب',
    fwdDesc: (email: string) => `يرجى إعادة توجيه جميع السير الذاتية لهذه الوظيفة إلى ${email}. سيقوم الذكاء الاصطناعي بتوجيهها تلقائياً.`,
    jobTitle: 'المسمى الوظيفي',
    department: 'القسم / العميل',
    jobLocation: 'الموقع',
    jobType: 'نوع الوظيفة',
    duration: 'المدة',
    jobDesc: 'وصف الوظيفة',
    jobDescPlaceholder: 'صف الدور والمسؤوليات والمهارات والخبرات المطلوبة…',
    cancel: 'إلغاء',
    creating: 'جارٍ الإنشاء…',
    submit: 'إنشاء الوظيفة',
    errorTitle: 'المسمى الوظيفي مطلوب.',
    errorDesc: 'وصف الوظيفة مطلوب.',
  },
};

export const AddJobModal: React.FC<AddJobModalProps> = ({ onClose, onSuccess, token, user, addToast }) => {
  const { lang, isAr } = useLanguage();
  const t = T[lang];

  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState<FormData>({
    title: '',
    department: '',
    location: '',
    job_type: '',
    duration: '',
    description: '',
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const title = formData.title.trim();
    const description = formData.description.trim();

    if (!title) { addToast(t.errorTitle, 'error'); return; }
    if (!description) { addToast(t.errorDesc, 'error'); return; }

    setLoading(true);
    try {
      const payload: Record<string, string> = { title, description };
      if (formData.department.trim()) payload.department = formData.department.trim();
      if (formData.location.trim()) payload.location = formData.location.trim();
      if (formData.job_type.trim()) payload.job_type = formData.job_type.trim();
      if (formData.duration.trim()) payload.duration = formData.duration.trim();

      const responseData = await apiService.post(
        WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL,
        payload,
        token
      );

      addToast('Job created successfully!', 'success');
      onSuccess(responseData.job_id || '');
    } catch (err: any) {
      const errorMsg = err.name === 'TypeError' && err.message === 'Failed to fetch'
        ? 'Network error. The creation service is unreachable.'
        : (err.message || 'Failed to create job.');
      addToast(errorMsg, 'error');
    } finally {
      setLoading(false);
    }
  };

  const isFormValid = formData.title.trim().length > 0 && formData.description.trim().length > 0;
  const normalizedMode = user?.cv_ingestion_mode?.toLowerCase();

  return (
    <div className="fixed inset-0 bg-textMain/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-8 overflow-hidden animate-scale-in">
        <form onSubmit={handleSubmit} className="flex flex-col h-full">
          <div className="px-8 py-6 border-b border-border flex justify-between items-center bg-white sticky top-0 z-10">
            <div>
              <h3 className="text-xl font-bold text-textMain">{t.title}</h3>
              <p className="text-xs text-textMuted uppercase tracking-wider font-semibold mt-0.5">{t.subtitle}</p>
            </div>
            <button type="button" onClick={onClose} className="text-textMuted hover:text-textMain transition-colors p-2 hover:bg-slate-100 rounded-full">
               <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-8 space-y-5 max-h-[70vh] overflow-y-auto bg-white">

            {normalizedMode === 'platform_email' && (
              <div className={`bg-blue-50 ${isAr ? 'border-r-4' : 'border-l-4'} border-blue-500 rounded-xl p-4 flex items-start gap-4`}>
                <div className="text-blue-500 shrink-0 mt-0.5">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <div>
                  <h4 className="text-sm font-bold text-blue-900 uppercase tracking-tight">{t.inboxNote}</h4>
                  <p className="text-sm text-blue-800 leading-relaxed mt-1">{t.inboxDesc}</p>
                </div>
              </div>
            )}

            {normalizedMode === 'forwarding' && (
              <div className={`bg-amber-50 ${isAr ? 'border-r-4' : 'border-l-4'} border-amber-500 rounded-xl p-4 flex items-start gap-4`}>
                <div className="text-amber-500 shrink-0 mt-0.5">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <div>
                  <h4 className="text-sm font-bold text-amber-900 uppercase tracking-tight">{t.fwdNote}</h4>
                  <p className="text-sm text-amber-800 leading-relaxed mt-1">{t.fwdDesc(GLOBAL_FORWARDING_EMAIL)}</p>
                </div>
              </div>
            )}

            {/* Title + Department */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                  {t.jobTitle} <span className="text-error">*</span>
                </label>
                <input
                  required
                  name="title"
                  type="text"
                  placeholder="Senior Backend Developer"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.title}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.department}</label>
                <input
                  name="department"
                  type="text"
                  placeholder="Engineering / Acme Corp"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.department}
                  onChange={handleChange}
                />
              </div>
            </div>

            {/* Location + Job Type + Duration */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.jobLocation}</label>
                <input
                  name="location"
                  type="text"
                  placeholder="Hybrid / Riyadh, SA"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.location}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.jobType}</label>
                <input
                  name="job_type"
                  type="text"
                  placeholder="Full-time"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_type}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.duration}</label>
                <input
                  name="duration"
                  type="text"
                  placeholder="Permanent"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.duration}
                  onChange={handleChange}
                />
              </div>
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                {t.jobDesc} <span className="text-error">*</span>
              </label>
              <textarea
                required
                name="description"
                rows={7}
                placeholder={t.jobDescPlaceholder}
                className="w-full px-4 py-3 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm leading-relaxed"
                value={formData.description}
                onChange={handleChange}
              />
              <p className="text-xs text-textMuted">
                {formData.description.trim().length > 0
                  ? `${formData.description.trim().length} chars — AI will extract criteria automatically`
                  : 'The more detail you provide, the better the AI scoring will be.'}
              </p>
            </div>
          </div>

          <div className="px-8 py-5 bg-slate-50 border-t border-border flex justify-end items-center gap-4 sticky bottom-0 z-10">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-6 py-2 text-sm font-semibold text-textMuted hover:text-textMain transition-colors disabled:opacity-50"
            >
              {t.cancel}
            </button>
            <button
              type="submit"
              disabled={loading || !isFormValid}
              className="bg-primary hover:bg-primaryDark disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-10 py-2.5 rounded-lg font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center min-w-[140px]"
            >
              {loading ? (
                <>
                  <svg className="animate-spin h-5 w-5 text-white mr-2" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  {t.creating}
                </>
              ) : t.submit}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
