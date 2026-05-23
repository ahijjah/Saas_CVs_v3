
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User, ClientOrganization, KnockoutQuestion } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { evaluateJobDescriptionQuality, validateJobTitle } from '../utils/jobDescriptionQuality';

interface AddJobModalProps {
  onClose: () => void;
  onSuccess: (jobId: string) => void;
  token: string;
  user: User | null;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface FormData {
  job_title: string;
  job_description: string;
  client: string;
  job_location: string;
  job_type: string;
  work_mode: string;
  experience_level: string;
  job_duration: string;
  application_deadline: string;
  vacancies_count: string;
  client_organization_id: string;
}

const T = {
  en: {
    title: 'Create New Job',
    subtitle: 'Campaign Details & Candidate Criteria',
    jobTitle: 'Job Title',
    clientOrg: 'Client Organization',
    clientOrgPlaceholder: 'Select client…',
    clientOrgGeneral: 'General (No specific client)',
    clientOrgRequired: 'Please select a client or General.',
    noClientsWarning: 'No client organizations found. You can create a General job or add clients first.',
    department: 'Department / Client',
    jobLocation: 'Location',
    jobType: 'Job Type',
    jobTypePlaceholder: 'Select type…',
    jobTypes: ['Full-time', 'Part-time', 'Contract', 'Freelance', 'Internship', 'Temporary'],
    workMode: 'Work Mode',
    workModePlaceholder: 'Select mode…',
    workModes: ['On-site', 'Remote', 'Hybrid'],
    experienceLevel: 'Experience Level',
    experienceLevelPlaceholder: 'Select level…',
    experienceLevels: ['Entry-level', 'Junior', 'Mid-level', 'Senior', 'Lead', 'Executive'],
    duration: 'Duration',
    applicationDeadline: 'Application Deadline',
    vacancies: 'Vacancies',
    vacanciesPlaceholder: 'e.g. 3',
    jobDesc: 'Job Description',
    jobDescPlaceholder: 'Describe the role, responsibilities, required skills and experience…',
    systemNote: 'Job code, status, created date, and audit fields are generated automatically by the system.',
    cancel: 'Cancel',
    creating: 'Creating…',
    submit: 'Create Job',
    errorTitle: 'Job title is required.',
    errorDesc: 'Job description is required.',
    qualityLabel: 'Description Quality',
    qualityInsufficient: 'Insufficient',
    qualityNeedsImprovement: 'Needs Improvement',
    qualityReady: 'Ready for AI Analysis',
    qualityBlocksSubmit: 'Please add meaningful job details before creating the campaign.',
    titleInvalid: 'Please enter a real job title, such as Sales Assistant, Accountant, Driver, or HR Manager.',
    knockoutTitle: 'Knockout Questions',
    knockoutSubtitle: 'Optional pre-screening questions shown to candidates before they submit.',
    knockoutToggleOn: 'Add Knockout Questions',
    knockoutToggleOff: 'Remove Knockout Questions',
    knockoutAddQuestion: 'Add Question',
    knockoutQuestionPlaceholder: 'e.g. Do you have a valid driving licence?',
    knockoutTypeyesNo: 'Yes / No',
    knockoutTypeSingleChoice: 'Single Choice',
    knockoutTypeNumber: 'Number',
    knockoutRequired: 'Required',
    knockoutDisqualify: 'Disqualifying answer',
    knockoutDisqualifyNone: 'None',
    knockoutOptions: 'Options (one per line)',
    knockoutOptionsPlaceholder: 'Option A\nOption B\nOption C',
    knockoutRemove: 'Remove',
    knockoutMaxReached: (n: number) => `Maximum ${n} questions allowed.`,
  },
  ar: {
    title: 'إنشاء وظيفة جديدة',
    subtitle: 'تفاصيل الحملة ومعايير المرشحين',
    jobTitle: 'المسمى الوظيفي',
    clientOrg: 'منظمة العميل',
    clientOrgPlaceholder: 'اختر عميلاً…',
    clientOrgGeneral: 'عام (بدون عميل محدد)',
    clientOrgRequired: 'يرجى اختيار عميل أو عام.',
    noClientsWarning: 'لا توجد منظمات عملاء. يمكنك إنشاء وظيفة عامة أو إضافة عملاء أولاً.',
    department: 'القسم / العميل',
    jobLocation: 'الموقع',
    jobType: 'نوع الوظيفة',
    jobTypePlaceholder: 'اختر النوع…',
    jobTypes: ['دوام كامل', 'دوام جزئي', 'عقد', 'مستقل', 'تدريب', 'مؤقت'],
    workMode: 'طريقة العمل',
    workModePlaceholder: 'اختر الطريقة…',
    workModes: ['حضوري', 'عن بُعد', 'هجين'],
    experienceLevel: 'مستوى الخبرة',
    experienceLevelPlaceholder: 'اختر المستوى…',
    experienceLevels: ['مبتدئ', 'مساعد', 'متوسط', 'أول', 'قيادي', 'تنفيذي'],
    duration: 'المدة',
    applicationDeadline: 'آخر موعد للتقديم',
    vacancies: 'الشواغر',
    vacanciesPlaceholder: 'مثال: 3',
    jobDesc: 'وصف الوظيفة',
    jobDescPlaceholder: 'صف الدور والمسؤوليات والمهارات والخبرات المطلوبة…',
    systemNote: 'رمز الوظيفة والحالة وتاريخ الإنشاء وحقول المراجعة تُنشأ تلقائياً بواسطة النظام.',
    cancel: 'إلغاء',
    creating: 'جارٍ الإنشاء…',
    submit: 'إنشاء الوظيفة',
    errorTitle: 'المسمى الوظيفي مطلوب.',
    errorDesc: 'وصف الوظيفة مطلوب.',
    qualityLabel: 'جودة الوصف',
    qualityInsufficient: 'غير كافٍ',
    qualityNeedsImprovement: 'يحتاج تحسين',
    qualityReady: 'جاهز للتحليل',
    qualityBlocksSubmit: 'يرجى إضافة تفاصيل وظيفية مفيدة قبل إنشاء الحملة.',
    titleInvalid: 'يرجى إدخال مسمى وظيفي حقيقي، مثل: مساعد مبيعات، محاسب، سائق، أو مدير موارد بشرية.',
    knockoutTitle: 'أسئلة الإقصاء',
    knockoutSubtitle: 'أسئلة اختيارية تُعرض للمرشحين قبل التقديم.',
    knockoutToggleOn: 'إضافة أسئلة إقصاء',
    knockoutToggleOff: 'إزالة أسئلة الإقصاء',
    knockoutAddQuestion: 'إضافة سؤال',
    knockoutQuestionPlaceholder: 'مثال: هل تمتلك رخصة قيادة سارية؟',
    knockoutTypeyesNo: 'نعم / لا',
    knockoutTypeSingleChoice: 'اختيار واحد',
    knockoutTypeNumber: 'رقمي',
    knockoutRequired: 'إلزامي',
    knockoutDisqualify: 'الإجابة المُقصية',
    knockoutDisqualifyNone: 'لا يوجد',
    knockoutOptions: 'الخيارات (سطر لكل خيار)',
    knockoutOptionsPlaceholder: 'خيار أ\nخيار ب\nخيار ج',
    knockoutRemove: 'حذف',
    knockoutMaxReached: (n: number) => `الحد الأقصى ${n} أسئلة مسموح بها.`,
  },
};

// ── Quality indicator ─────────────────────────────────────────────────────────

interface QualityT {
  qualityLabel: string;
  qualityInsufficient: string;
  qualityNeedsImprovement: string;
  qualityReady: string;
}

const QUALITY_STYLES = {
  insufficient:      { bg: 'bg-red-50',     border: 'border-red-200',     text: 'text-red-700',     bar: 'bg-red-400',     dot: 'bg-red-500'     },
  needs_improvement: { bg: 'bg-amber-50',   border: 'border-amber-200',   text: 'text-amber-700',   bar: 'bg-amber-400',   dot: 'bg-amber-500'   },
  ready:             { bg: 'bg-emerald-50', border: 'border-emerald-200', text: 'text-emerald-700', bar: 'bg-emerald-500', dot: 'bg-emerald-500' },
} as const;

function DescriptionQualityIndicator({
  quality,
  t,
}: {
  quality: ReturnType<typeof evaluateJobDescriptionQuality>;
  t: QualityT;
}) {
  const s = QUALITY_STYLES[quality.state];
  const stateLabel = quality.state === 'insufficient' ? t.qualityInsufficient
    : quality.state === 'needs_improvement' ? t.qualityNeedsImprovement
    : t.qualityReady;

  return (
    <div className={`rounded-xl border px-3 py-2.5 ${s.bg} ${s.border}`}>
      {/* Score bar */}
      <div className="flex items-center gap-3 mb-2">
        <span className={`text-[9px] font-black uppercase tracking-widest shrink-0 ${s.text}`}>{t.qualityLabel}</span>
        <div className="flex-1 h-1.5 bg-white/60 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${s.bar}`}
            style={{ width: `${quality.score}%` }}
          />
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
          <span className={`text-[10px] font-black ${s.text}`}>{stateLabel}</span>
        </div>
      </div>

      {/* Issues */}
      {quality.issues.length > 0 && (
        <ul className={`space-y-0.5 mb-1.5 ${s.text}`}>
          {quality.issues.map((issue, i) => (
            <li key={i} className="text-[11px] flex items-start gap-1.5">
              <span className="shrink-0 mt-0.5">•</span>{issue}
            </li>
          ))}
        </ul>
      )}

      {/* Suggestions (only when not ready) */}
      {quality.state !== 'ready' && quality.suggestions.length > 0 && (
        <ul className="space-y-0.5 text-textMuted">
          {quality.suggestions.map((s, i) => (
            <li key={i} className="text-[11px] flex items-start gap-1.5">
              <span className="shrink-0 mt-0.5 opacity-60">→</span>{s}
            </li>
          ))}
        </ul>
      )}

      {/* Ready success message */}
      {quality.state === 'ready' && (
        <p className={`text-[11px] font-bold ${s.text}`}>
          {quality.suggestions[0] ?? 'Description is ready for AI analysis.'}
        </p>
      )}
    </div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export const AddJobModal: React.FC<AddJobModalProps> = ({ onClose, onSuccess, token, user, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];

  const tenantType = user?.tenant_type ?? '';
  const isAgencyTenant = tenantType === 'agency' || tenantType === 'individual_recruiter';

  const MAX_KNOCKOUT = 5;

  const [loading, setLoading] = useState(false);
  const [clientOrgs, setClientOrgs] = useState<ClientOrganization[]>([]);
  const [clientOrgsLoading, setClientOrgsLoading] = useState(false);
  const [showKnockout, setShowKnockout] = useState(false);
  const [knockoutQuestions, setKnockoutQuestions] = useState<Partial<KnockoutQuestion & { optionsText: string }>[]>([]);

  const [formData, setFormData] = useState<FormData>({
    job_title: '',
    job_description: '',
    client: '',
    job_location: '',
    job_type: '',
    work_mode: '',
    experience_level: '',
    job_duration: '',
    application_deadline: '',
    vacancies_count: '',
    client_organization_id: '',
  });

  useEffect(() => {
    if (!token || !isAgencyTenant) return;
    setClientOrgsLoading(true);
    apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, token)
      .then((data: any) => {
        const orgs: ClientOrganization[] = data?.client_organizations ?? [];
        setClientOrgs(orgs.filter(o => o.status === 'active'));
      })
      .catch(() => {})
      .finally(() => setClientOrgsLoading(false));
  }, [token, isAgencyTenant]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleClientOrgChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const orgId = e.target.value;
    if (orgId === '__general__') {
      setFormData(prev => ({ ...prev, client_organization_id: '__general__', client: '' }));
      return;
    }
    const org = clientOrgs.find(o => o.client_organization_id === orgId);
    setFormData(prev => ({
      ...prev,
      client_organization_id: orgId,
      client: org ? org.organization_name : '',
    }));
  };

  const addKnockoutQuestion = () => {
    if (knockoutQuestions.length >= MAX_KNOCKOUT) return;
    setKnockoutQuestions(prev => [...prev, { question_text: '', question_type: 'yes_no', is_required: true, disqualifying_answer: undefined, optionsText: '' }]);
  };

  const removeKnockoutQuestion = (idx: number) => {
    setKnockoutQuestions(prev => prev.filter((_, i) => i !== idx));
  };

  const updateKnockoutQuestion = (idx: number, field: string, value: unknown) => {
    setKnockoutQuestions(prev => prev.map((q, i) => i === idx ? { ...q, [field]: value } : q));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const title = formData.job_title.trim();
    const description = formData.job_description.trim();

    if (!title) { addToast(t.errorTitle, 'error'); return; }
    if (!description) { addToast(t.errorDesc, 'error'); return; }
    if (isAgencyTenant && !formData.client_organization_id) {
      addToast(t.clientOrgRequired, 'error'); return;
    }

    setLoading(true);
    try {
      const isGeneral = formData.client_organization_id === '__general__';
      const payload: Record<string, string | number | null> = { title, description };

      if (!isGeneral && formData.client_organization_id) payload.client_organization_id = formData.client_organization_id;
      if (!isAgencyTenant && formData.client.trim()) payload.department = formData.client.trim();
      if (formData.job_location.trim()) payload.location = formData.job_location.trim();
      if (formData.job_type) payload.job_type = formData.job_type;
      if (formData.work_mode) payload.work_mode = formData.work_mode;
      if (formData.experience_level) payload.experience_level = formData.experience_level;
      if (formData.job_duration.trim()) payload.duration = formData.job_duration.trim();
      if (formData.application_deadline) payload.application_deadline = formData.application_deadline;
      const vac = parseInt(formData.vacancies_count);
      if (vac > 0) payload.vacancies_count = vac;

      if (showKnockout && knockoutQuestions.length > 0) {
        const questions = knockoutQuestions
          .filter(q => q.question_text?.trim())
          .map(q => {
            const opts = q.question_type === 'single_choice' && q.optionsText
              ? q.optionsText.split('\n').map((s: string) => s.trim()).filter(Boolean)
              : undefined;
            return {
              question_text: q.question_text!.trim(),
              question_type: q.question_type || 'yes_no',
              is_required: q.is_required ?? true,
              disqualifying_answer: q.disqualifying_answer || null,
              options: opts || null,
            };
          });
        if (questions.length > 0) payload.knockout_questions = questions as any;
      }

      const responseData = await apiService.post(WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL, payload, token);
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

  const hasTitle = formData.job_title.trim().length > 0;
  const hasDesc = formData.job_description.trim().length > 0;
  const clientOrgSatisfied = !isAgencyTenant || !!formData.client_organization_id;

  const descQuality = hasDesc ? evaluateJobDescriptionQuality(formData.job_description) : null;
  const descQualityBlocks = descQuality?.state === 'insufficient';

  const titleValidation = hasTitle ? validateJobTitle(formData.job_title, lang) : null;
  const titleInvalid = titleValidation !== null && !titleValidation.valid;

  const isFormValid = hasTitle && hasDesc && clientOrgSatisfied && !descQualityBlocks && !titleInvalid;

  const inputCls = 'w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm';
  const selectCls = `${inputCls} bg-white`;
  const labelCls = 'text-xs font-bold text-textMuted uppercase tracking-widest';

  return (
    <div className="fixed inset-0 bg-textMain/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl my-8 overflow-hidden animate-scale-in">
        <form onSubmit={handleSubmit} className="flex flex-col h-full">

          {/* Header */}
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

          <div className="p-8 space-y-5 max-h-[75vh] overflow-y-auto bg-white">

            {/* Warning: no active clients */}
            {isAgencyTenant && !clientOrgsLoading && clientOrgs.length === 0 && (
              <div className="flex items-center gap-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800">
                <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
                <span>{t.noClientsWarning}</span>
              </div>
            )}

            {/* Row 1: Job Title + Client Org / Department */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="space-y-1.5">
                <label className={labelCls}>{t.jobTitle} <span className="text-error">*</span></label>
                <input
                  required
                  name="job_title"
                  type="text"
                  placeholder="Senior Backend Developer"
                  className={`${inputCls} ${titleInvalid ? 'border-red-400 focus:ring-red-200/40 focus:border-red-400' : ''}`}
                  value={formData.job_title}
                  onChange={handleChange}
                />
                {titleInvalid && (
                  <p className="text-[11px] text-red-600 font-bold leading-snug">{(t as any).titleInvalid}</p>
                )}
              </div>

              {isAgencyTenant ? (
                <div className="space-y-1.5">
                  <label className={labelCls}>{t.clientOrg} <span className="text-error">*</span></label>
                  {clientOrgsLoading ? (
                    <div className="w-full px-4 py-2.5 border border-border rounded-lg text-sm text-textMuted bg-slate-50">Loading clients…</div>
                  ) : (
                    <select value={formData.client_organization_id} onChange={handleClientOrgChange} className={selectCls}>
                      <option value="">{t.clientOrgPlaceholder}</option>
                      <option value="__general__">{t.clientOrgGeneral}</option>
                      {clientOrgs.map(org => (
                        <option key={org.client_organization_id} value={org.client_organization_id}>{org.organization_name}</option>
                      ))}
                    </select>
                  )}
                </div>
              ) : (
                <div className="space-y-1.5">
                  <label className={labelCls}>{t.department}</label>
                  <input
                    name="client"
                    type="text"
                    placeholder="Engineering / Finance"
                    className={inputCls}
                    value={formData.client}
                    onChange={handleChange}
                  />
                </div>
              )}
            </div>

            {/* Row 2: Job Type + Work Mode + Experience Level */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div className="space-y-1.5">
                <label className={labelCls}>{t.jobType}</label>
                <select name="job_type" value={formData.job_type} onChange={handleChange} className={selectCls}>
                  <option value="">{t.jobTypePlaceholder}</option>
                  {t.jobTypes.map(jt => <option key={jt} value={jt}>{jt}</option>)}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className={labelCls}>{t.workMode}</label>
                <select name="work_mode" value={formData.work_mode} onChange={handleChange} className={selectCls}>
                  <option value="">{t.workModePlaceholder}</option>
                  {t.workModes.map(wm => <option key={wm} value={wm}>{wm}</option>)}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className={labelCls}>{t.experienceLevel}</label>
                <select name="experience_level" value={formData.experience_level} onChange={handleChange} className={selectCls}>
                  <option value="">{t.experienceLevelPlaceholder}</option>
                  {t.experienceLevels.map(el => <option key={el} value={el}>{el}</option>)}
                </select>
              </div>
            </div>

            {/* Row 3: Location + Duration + Vacancies + Deadline */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
              <div className="space-y-1.5">
                <label className={labelCls}>{t.jobLocation}</label>
                <input
                  name="job_location"
                  type="text"
                  placeholder="Hybrid / Riyadh, SA"
                  className={inputCls}
                  value={formData.job_location}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className={labelCls}>{t.duration}</label>
                <input
                  name="job_duration"
                  type="text"
                  placeholder="Permanent"
                  className={inputCls}
                  value={formData.job_duration}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className={labelCls}>{t.vacancies}</label>
                <input
                  name="vacancies_count"
                  type="number"
                  min={1}
                  placeholder={t.vacanciesPlaceholder}
                  className={inputCls}
                  value={formData.vacancies_count}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className={labelCls}>{t.applicationDeadline}</label>
                <input
                  name="application_deadline"
                  type="date"
                  className={inputCls}
                  value={formData.application_deadline}
                  onChange={handleChange}
                />
              </div>
            </div>

            {/* Row 4: Description + live quality indicator */}
            <div className="space-y-1.5">
              <label className={labelCls}>{t.jobDesc} <span className="text-error">*</span></label>
              <textarea
                required
                name="job_description"
                rows={7}
                placeholder={t.jobDescPlaceholder}
                className={`w-full px-4 py-3 border rounded-lg focus:ring-2 outline-none transition-all text-sm leading-relaxed ${
                  descQuality?.state === 'insufficient' ? 'border-red-300 focus:ring-red-200 focus:border-red-400' :
                  descQuality?.state === 'needs_improvement' ? 'border-amber-300 focus:ring-amber-200 focus:border-amber-400' :
                  descQuality?.state === 'ready' ? 'border-emerald-300 focus:ring-emerald-200 focus:border-emerald-400' :
                  'border-border focus:ring-primary/20 focus:border-primary'
                }`}
                value={formData.job_description}
                onChange={handleChange}
              />

              {/* Quality indicator — shown once user starts typing */}
              {descQuality && (
                <DescriptionQualityIndicator quality={descQuality} t={t} />
              )}

              {!hasDesc && (
                <p className="text-xs text-textMuted">
                  The more detail you provide, the better the AI scoring will be.
                </p>
              )}
            </div>

            {/* Knockout Questions */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className={`${labelCls}`}>{(t as any).knockoutTitle}</p>
                  <p className="text-[11px] text-textMuted mt-0.5">{(t as any).knockoutSubtitle}</p>
                </div>
                <button
                  type="button"
                  onClick={() => { setShowKnockout(v => !v); if (showKnockout) setKnockoutQuestions([]); }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                    showKnockout
                      ? 'bg-red-50 border-red-200 text-red-700 hover:bg-red-100'
                      : 'bg-primary/5 border-primary/20 text-primary hover:bg-primary/10'
                  }`}
                >
                  {showKnockout ? (t as any).knockoutToggleOff : (t as any).knockoutToggleOn}
                </button>
              </div>

              {showKnockout && (
                <div className="space-y-3">
                  {knockoutQuestions.map((q, idx) => (
                    <div key={idx} className="border border-border rounded-xl p-4 space-y-3 bg-slate-50">
                      <div className="flex items-start gap-3">
                        <span className="w-5 h-5 bg-primary text-white text-[10px] font-black rounded-full flex items-center justify-center shrink-0 mt-0.5">{idx + 1}</span>
                        <div className="flex-1 space-y-2">
                          <input
                            type="text"
                            value={q.question_text || ''}
                            onChange={e => updateKnockoutQuestion(idx, 'question_text', e.target.value)}
                            placeholder={(t as any).knockoutQuestionPlaceholder}
                            className={inputCls}
                          />
                          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                            <select
                              value={q.question_type || 'yes_no'}
                              onChange={e => updateKnockoutQuestion(idx, 'question_type', e.target.value)}
                              className={selectCls}
                            >
                              <option value="yes_no">{(t as any).knockoutTypeyesNo}</option>
                              <option value="single_choice">{(t as any).knockoutTypeSingleChoice}</option>
                              <option value="number">{(t as any).knockoutTypeNumber}</option>
                            </select>
                            <label className="flex items-center gap-2 cursor-pointer">
                              <input
                                type="checkbox"
                                checked={q.is_required ?? true}
                                onChange={e => updateKnockoutQuestion(idx, 'is_required', e.target.checked)}
                                className="rounded border-border"
                              />
                              <span className="text-xs text-textMuted font-semibold">{(t as any).knockoutRequired}</span>
                            </label>
                            {(q.question_type === 'yes_no' || q.question_type === 'single_choice') && (
                              <div className="space-y-1">
                                <p className="text-[10px] text-textMuted font-bold uppercase tracking-widest">{(t as any).knockoutDisqualify}</p>
                                {q.question_type === 'yes_no' ? (
                                  <select
                                    value={q.disqualifying_answer || ''}
                                    onChange={e => updateKnockoutQuestion(idx, 'disqualifying_answer', e.target.value || undefined)}
                                    className={selectCls}
                                  >
                                    <option value="">{(t as any).knockoutDisqualifyNone}</option>
                                    <option value="yes">Yes</option>
                                    <option value="no">No</option>
                                  </select>
                                ) : (
                                  <select
                                    value={q.disqualifying_answer || ''}
                                    onChange={e => updateKnockoutQuestion(idx, 'disqualifying_answer', e.target.value || undefined)}
                                    className={selectCls}
                                  >
                                    <option value="">{(t as any).knockoutDisqualifyNone}</option>
                                    {(q.optionsText || '').split('\n').map((o: string) => o.trim()).filter(Boolean).map((o: string) => (
                                      <option key={o} value={o}>{o}</option>
                                    ))}
                                  </select>
                                )}
                              </div>
                            )}
                          </div>
                          {q.question_type === 'single_choice' && (
                            <div className="space-y-1">
                              <p className="text-[10px] text-textMuted font-bold uppercase tracking-widest">{(t as any).knockoutOptions}</p>
                              <textarea
                                rows={3}
                                value={q.optionsText || ''}
                                onChange={e => updateKnockoutQuestion(idx, 'optionsText', e.target.value)}
                                placeholder={(t as any).knockoutOptionsPlaceholder}
                                className={`${inputCls} resize-none font-mono text-xs`}
                              />
                            </div>
                          )}
                        </div>
                        <button
                          type="button"
                          onClick={() => removeKnockoutQuestion(idx)}
                          className="text-textMuted hover:text-red-600 transition-colors p-1 rounded"
                          title={(t as any).knockoutRemove}
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  ))}

                  {knockoutQuestions.length < MAX_KNOCKOUT ? (
                    <button
                      type="button"
                      onClick={addKnockoutQuestion}
                      className="w-full py-2 border-2 border-dashed border-border rounded-xl text-xs font-bold text-textMuted hover:border-primary hover:text-primary transition-colors"
                    >
                      + {(t as any).knockoutAddQuestion}
                    </button>
                  ) : (
                    <p className="text-[11px] text-amber-600 text-center">{(t as any).knockoutMaxReached(MAX_KNOCKOUT)}</p>
                  )}
                </div>
              )}
            </div>

            {/* System-generated fields note */}
            <div className="flex items-start gap-2.5 px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl">
              <svg className="w-4 h-4 shrink-0 mt-0.5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-xs text-textMuted leading-relaxed">{t.systemNote}</p>
            </div>

          </div>

          {/* Footer */}
          <div className="px-8 py-5 bg-slate-50 border-t border-border flex flex-wrap justify-end items-center gap-4 sticky bottom-0 z-10">
            {(descQualityBlocks || titleInvalid) && (
              <p className="text-xs text-error font-bold flex-1 min-w-0 text-left">
                {titleInvalid ? (t as any).titleInvalid : t.qualityBlocksSubmit}
              </p>
            )}
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
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
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
