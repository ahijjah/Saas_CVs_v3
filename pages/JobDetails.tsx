
import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { JobDetails as JobDetailsType, AuthState } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface JobDetailsProps {
  jobId: string;
  auth: AuthState;
  onBack: () => void;
  onViewApplications: (jobId: string, filter: string) => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

const T = {
  en: {
    loading: 'Syncing campaign data...',
    syncError: 'Sync Error',
    returnDashboard: 'Return to Dashboard',
    reviewPortal: 'Review Portal',
    metaLabels: ['Client', 'Type', 'Location', 'Posted', 'Closing'],
    kpiLabels: ['Total', 'Qualified', 'Partial', 'Rejected'],
    skillsAnalysis: 'Skills Analysis',
    requiredSkills: 'Required Skills',
    preferredSkills: 'Preferred Skills',
    experience: 'Experience',
    minYears: 'Minimum Years',
    years: '+ Years',
    relevantRoles: 'Relevant Roles',
    education: 'Education',
    minLevel: 'Minimum Level',
    fieldsOfStudy: 'Fields of Study',
    certifications: 'Certifications',
    domainKnowledge: 'Domain Knowledge',
    otherRequirements: 'Other Requirements',
    noData: 'No specific data.',
    evalLogic: 'Evaluation Logic',
    evalWeightLabels: ['Skills', 'Experience', 'Education', 'Certifications', 'Soft Skills', 'Domain Knowledge', 'Other'],
    editWeights: 'Edit Weights',
    saveWeights: 'Save Weights',
    cancelEdit: 'Cancel',
    normalizeWeights: 'Normalize to 100%',
    resetAiWeights: 'Reset to AI Weights',
    weightTotal: 'Total',
    weightSuccess: 'Total weight is 100%',
    weightUnder: 'Remaining {r}% must be assigned.',
    weightOver: 'Total exceeds 100% by {e}%.',
    jobDesc: 'Original Job Description',
    cvReceiving: 'CV Receiving Options',
    option1Title: 'Option 1 — Forwarding to Central Email',
    option1Desc: 'The client forwards CVs from their own system to the central inbox. Include the job code in the email subject for automatic routing.',
    option1ForwardTo: 'Forward CVs to',
    option1SubjectHint: 'Include in email subject',
    option2Title: 'Option 2 — Dedicated Alias (Recommended)',
    option2Desc: 'Share this address directly with applicants or publish it on your careers page. CVs are automatically assigned to this job — no job code needed.',
    option2AliasLabel: 'Dedicated alias',
    copyBtn: 'Copy',
    copied: 'Copied!',
    enabled: 'Enabled',
    disabled: 'Disabled',
    recommended: 'Recommended',
    criteriaPending: 'AI criteria analysis is being generated. This page will refresh automatically.',
    criteriaProcessing: 'AI criteria analysis is in progress. This page will refresh automatically.',
    criteriaFailed: 'AI criteria analysis failed.',
    retryExtraction: 'Retry',
  },
  ar: {
    loading: 'جارٍ مزامنة بيانات الحملة...',
    syncError: 'خطأ في المزامنة',
    returnDashboard: 'العودة إلى لوحة التحكم',
    reviewPortal: 'بوابة المراجعة',
    metaLabels: ['العميل', 'النوع', 'الموقع', 'تاريخ النشر', 'تاريخ الإغلاق'],
    kpiLabels: ['الإجمالي', 'مؤهلون', 'جزئيون', 'مرفوضون'],
    skillsAnalysis: 'تحليل المهارات',
    requiredSkills: 'المهارات المطلوبة',
    preferredSkills: 'المهارات المفضلة',
    experience: 'الخبرة',
    minYears: 'الحد الأدنى للسنوات',
    years: '+ سنوات',
    relevantRoles: 'الأدوار ذات الصلة',
    education: 'التعليم',
    minLevel: 'الحد الأدنى للمستوى',
    fieldsOfStudy: 'مجالات الدراسة',
    certifications: 'الشهادات',
    domainKnowledge: 'المعرفة بالمجال',
    otherRequirements: 'متطلبات أخرى',
    noData: 'لا توجد بيانات محددة.',
    evalLogic: 'منطق التقييم',
    evalWeightLabels: ['المهارات', 'الخبرة', 'التعليم', 'الشهادات', 'المهارات الناعمة', 'معرفة المجال', 'أخرى'],
    editWeights: 'تعديل الأوزان',
    saveWeights: 'حفظ الأوزان',
    cancelEdit: 'إلغاء',
    normalizeWeights: 'توحيد إلى 100%',
    resetAiWeights: 'إعادة تعيين أوزان الذكاء الاصطناعي',
    weightTotal: 'المجموع',
    weightSuccess: 'مجموع الأوزان 100%',
    weightUnder: 'يجب تخصيص {r}% المتبقية.',
    weightOver: 'المجموع يتجاوز 100% بمقدار {e}%.',
    jobDesc: 'وصف الوظيفة الأصلي',
    cvReceiving: 'خيارات استقبال السير الذاتية',
    option1Title: 'الخيار 1 — إعادة التوجيه إلى البريد المركزي',
    option1Desc: 'يُعيد العميل توجيه السير الذاتية من نظامه الخاص إلى البريد الوارد المركزي. أدرج رمز الوظيفة في موضوع البريد للتوجيه التلقائي.',
    option1ForwardTo: 'أرسل السير الذاتية إلى',
    option1SubjectHint: 'أدرج في موضوع البريد الإلكتروني',
    option2Title: 'الخيار 2 — بريد مخصص لكل وظيفة (مُوصى به)',
    option2Desc: 'شارك هذا العنوان مع المتقدمين مباشرةً أو انشره في صفحة الوظائف. يُعيَّن البريد الوارد تلقائياً لهذه الوظيفة دون الحاجة لرمز الوظيفة.',
    option2AliasLabel: 'البريد المخصص',
    copyBtn: 'نسخ',
    copied: 'تم النسخ!',
    enabled: 'مفعّل',
    disabled: 'معطّل',
    recommended: 'مُوصى به',
    criteriaPending: 'جارٍ إنشاء تحليل معايير الذكاء الاصطناعي. ستُحدَّث هذه الصفحة تلقائياً.',
    criteriaProcessing: 'تحليل معايير الذكاء الاصطناعي قيد التنفيذ. ستُحدَّث هذه الصفحة تلقائياً.',
    criteriaFailed: 'فشل تحليل معايير الذكاء الاصطناعي.',
    retryExtraction: 'إعادة المحاولة',
  },
};

export const JobDetails: React.FC<JobDetailsProps> = ({ jobId, auth, onBack, onViewApplications, addToast }) => {
  const { lang, isAr } = useLanguage();
  const t = T[lang];

  const [details, setDetails] = useState<JobDetailsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [descExpanded, setDescExpanded] = useState(false);
  const [copiedAlias, setCopiedAlias] = useState(false);
  const [togglingFwd, setTogglingFwd] = useState(false);
  const [togglingAlias, setTogglingAlias] = useState(false);
  const [editingWeights, setEditingWeights] = useState(false);
  const [draftWeights, setDraftWeights] = useState<Record<string, number>>({});
  const [savingWeights, setSavingWeights] = useState(false);

  // Poll every 5 seconds while AI extraction is running
  useEffect(() => {
    const status = details?.criteria_extraction_status;
    if (status !== 'pending' && status !== 'processing') return;
    const timer = setTimeout(async () => {
      try {
        const data = await apiService.get(
          WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL,
          { job_id: jobId },
          auth.token!
        );
        if (data) {
          const payload = Array.isArray(data) ? data[0] : data;
          setDetails({ ...payload.details, analysis_json: payload.analysis });
        }
      } catch { /* ignore polling errors */ }
    }, 5000);
    return () => clearTimeout(timer);
  }, [details?.criteria_extraction_status, jobId, auth.token]);

  const handleRetryExtraction = useCallback(async () => {
    if (!details) return;
    try {
      await apiService.post(
        `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${details.job_id}/criteria/retry`,
        {},
        auth.token!
      );
      setDetails(prev => prev ? { ...prev, criteria_extraction_status: 'pending', criteria_extraction_error: null } : prev);
      addToast('AI analysis retry queued.', 'success');
    } catch {
      addToast('Failed to retry AI analysis.', 'error');
    }
  }, [details, auth.token, addToast]);

  const handleCopyAlias = useCallback((text: string) => {
    const confirm = () => {
      setCopiedAlias(true);
      setTimeout(() => setCopiedAlias(false), 2000);
    };
    const fallback = () => {
      try {
        const el = document.createElement('textarea');
        el.value = text;
        el.style.position = 'fixed';
        el.style.opacity = '0';
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
        confirm();
      } catch {
        addToast('Could not copy — please copy manually.', 'error');
      }
    };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(confirm).catch(fallback);
    } else {
      fallback();
    }
  }, [addToast]);

  const handleToggle = useCallback(async (field: 'forwarding_enabled' | 'alias_enabled', value: boolean) => {
    if (!details) return;
    const setToggling = field === 'forwarding_enabled' ? setTogglingFwd : setTogglingAlias;
    setToggling(true);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${details.job_id}/ingestion`,
        { [field]: value },
        auth.token!,
      );
      setDetails(prev => prev ? { ...prev, [field]: value } : prev);
    } catch {
      addToast('Failed to update ingestion setting.', 'error');
    } finally {
      setToggling(false);
    }
  }, [details, auth.token, addToast]);

  const _weightKeys = ['skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other_requirements'] as const;

  const handleEditWeights = useCallback(() => {
    if (!details) return;
    const current = (details.analysis_json?.scoring_weights ?? {}) as Record<string, number>;
    setDraftWeights(Object.fromEntries(_weightKeys.map(k => [k, current[k] ?? 0])));
    setEditingWeights(true);
  }, [details]);

  const handleNormalizeWeights = useCallback(() => {
    const entries = Object.entries(draftWeights);
    const total = entries.reduce((s, [, v]) => s + v, 0);
    if (total === 0) return;
    const normalized: Record<string, number> = {};
    entries.forEach(([k, v]) => { normalized[k] = Math.round((v / total) * 100); });
    const diff = 100 - Object.values(normalized).reduce((s, v) => s + v, 0);
    if (diff !== 0) {
      const maxKey = [...entries].sort((a, b) => b[1] - a[1])[0][0];
      normalized[maxKey] = (normalized[maxKey] ?? 0) + diff;
    }
    setDraftWeights(normalized);
  }, [draftWeights]);

  const handleResetWeights = useCallback(() => {
    if (!details) return;
    const ai = (details.analysis_json?.scoring_weights ?? {}) as Record<string, number>;
    setDraftWeights(Object.fromEntries(_weightKeys.map(k => [k, ai[k] ?? 0])));
  }, [details]);

  const handleSaveWeights = useCallback(async () => {
    if (!details) return;
    setSavingWeights(true);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${details.job_id}/criteria`,
        {
          weight_skills:           draftWeights['skills']            ?? 0,
          weight_experience:       draftWeights['experience']        ?? 0,
          weight_education:        draftWeights['education']         ?? 0,
          weight_certifications:   draftWeights['certifications']    ?? 0,
          weight_soft_skills:      draftWeights['soft_skills']       ?? 0,
          weight_domain_knowledge: draftWeights['domain_knowledge']  ?? 0,
          weight_other:            draftWeights['other_requirements'] ?? 0,
        },
        auth.token!
      );
      setDetails(prev => {
        if (!prev || !prev.analysis_json) return prev;
        return {
          ...prev,
          analysis_json: {
            ...prev.analysis_json,
            scoring_weights: { ...draftWeights } as any,
          },
        };
      });
      setEditingWeights(false);
      addToast('Evaluation weights updated successfully.', 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to update weights.', 'error');
    } finally {
      setSavingWeights(false);
    }
  }, [details, draftWeights, auth.token, addToast]);

  useEffect(() => {
    const fetchDetails = async () => {
      if (!jobId) return;
      setLoading(true);
      setError(null);

      try {
        const data = await apiService.get(
          WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL,
          { job_id: jobId },
          auth.token!
        );

        if (data) {
          const payload = Array.isArray(data) ? data[0] : data;
          setDetails({
            ...payload.details,
            analysis_json: payload.analysis,
          });
        } else {
          throw new Error("No data received for this job ID.");
        }
      } catch (err: any) {
        console.error("Fetch job details failed:", err);
        const errorMsg = err.name === 'TypeError' && err.message === 'Failed to fetch'
          ? "Network connection error." : (err.message || "Failed to load job details.");

        setError(errorMsg);
        addToast(errorMsg, "error");

        setDetails({
          job_id: jobId,
          job_code: 'JB-772',
          job_title: 'Senior Frontend Engineer',
          job_client: 'Global Finance Solutions',
          job_status: 'Active',
          job_type: 'Full-time',
          location: 'London (Hybrid)',
          posted_date: '2023-10-15',
          closing_date: '2023-11-30',
          ingestion_note: 'CVs for this job must be forwarded to jobs@ai970.cloud. Please include the job reference (JOB-2026-0001) in the email subject or body.',
          ingestion_mode: 'forwarding',
          ingestion_email: null,
          applications_total: 142,
          applications_qualified: 24,
          applications_partial: 45,
          applications_rejected: 73,
          applications_above_threshold: 18,
          applications_below_threshold: 102,
          applications_recommended: 12,
          description: "We are seeking a highly skilled Senior Frontend Engineer to lead the development of our core product interface.",
          analysis_json: {
            skills: {
              required: ["React", "TypeScript", "Tailwind CSS", "State Management (Redux/Zustand)"],
              preferred: ["Next.js", "GraphQL", "Jest/Cypress", "Web Accessibility (WCAG)"]
            },
            experience: {
              minimum_years: 5,
              relevant_roles: ["Senior Frontend Engineer", "Lead Developer", "React Specialist"],
              key_responsibilities: ["Architect scalable frontend components", "Optimizing application performance", "Mentoring junior engineering staff"]
            },
            education: {
              minimum_level: "Bachelor's Degree",
              fields_of_study: ["Computer Science", "Software Engineering", "Information Technology"]
            },
            certifications: ["AWS Certified Developer", "Meta Frontend Professional"],
            domain_knowledge: ["FinTech", "Enterprise SaaS", "Data Visualization"],
            other_requirements: ["Strong communication skills", "Agile/Scrum experience"],
            scoring_weights: { skills: 40, experience: 30, education: 10, certifications: 5, soft_skills: 10, domain_knowledge: 5 }
          }
        });
      } finally {
        setLoading(false);
      }
    };

    fetchDetails();
  }, [jobId, auth.token]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] space-y-4">
        <div className="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin"></div>
        <p className="text-textMuted font-medium animate-pulse tracking-wide">{t.loading}</p>
      </div>
    );
  }

  if (error && !details) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] p-8 text-center">
        <div className="bg-red-50 text-error p-6 rounded-2xl mb-6 max-w-md">
          <p className="font-bold mb-2">{t.syncError}</p>
          <p className="text-sm opacity-80">{error}</p>
        </div>
        <button onClick={onBack} className="bg-primary text-white px-8 py-3 rounded-xl font-bold shadow-lg">{t.returnDashboard}</button>
      </div>
    );
  }

  if (!details) return null;

  const analysis = details.analysis_json ?? undefined;
  const metaValues = [details.job_client, details.job_type || 'Full-time', details.location || 'Remote', details.posted_date || '-', details.closing_date || '-'];
  const kpiValues = [
    { value: details.applications_total, filter: 'all', color: 'text-textMain' },
    { value: details.applications_qualified || 0, filter: 'qualified', color: 'text-success' },
    { value: details.applications_partial || 0, filter: 'partial', color: 'text-warning' },
    { value: details.applications_rejected || 0, filter: 'rejected', color: 'text-error' },
  ];
  const weightLabels = t.evalWeightLabels;
  const weightKeys: ('skills' | 'experience' | 'education' | 'certifications' | 'soft_skills' | 'domain_knowledge' | 'other_requirements')[] = ['skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other_requirements'];
  const otherCats = [
    { label: t.certifications, items: analysis?.certifications },
    { label: t.domainKnowledge, items: analysis?.domain_knowledge },
    { label: t.otherRequirements, items: analysis?.other_requirements },
  ];
  const weightTotal = editingWeights ? Object.values(draftWeights).reduce((s, v) => s + v, 0) : 0;

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12 animate-fade-in">
      {/* Job Metadata */}
      <section className="bg-white rounded-3xl shadow-sm border border-border overflow-hidden">
        <div className="px-8 py-6 bg-slate-50 border-b border-border flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <button onClick={onBack} className="p-2 hover:bg-white rounded-lg transition-colors text-textMuted hover:text-primary">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" /></svg>
            </button>
            <div>
              <div className="flex items-center gap-2 text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">
                <span>{details.job_id}</span>
                <span className="w-1 h-1 rounded-full bg-border"></span>
                <span className={details.job_status === 'Active' ? 'text-success' : 'text-warning'}>{details.job_status}</span>
              </div>
              <h1 className="text-2xl font-black text-textMain tracking-tight">{details.job_title}</h1>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button className="bg-primary text-white px-6 py-2.5 rounded-xl text-sm font-bold shadow-lg shadow-primary/20">{t.reviewPortal}</button>
          </div>
        </div>

        {/* CV Receiving Options */}
        <div className="border-b border-border px-8 py-6">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-5 flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
            {t.cvReceiving}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            {/* Option 1 — Forwarding */}
            <div className={`rounded-2xl border-2 p-5 transition-all ${details.forwarding_enabled ? 'border-primary/20 bg-blue-50/40' : 'border-slate-200 bg-slate-50/60 opacity-60'}`}>
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <p className="text-xs font-black text-textMain">{t.option1Title}</p>
                  <p className="text-[11px] text-textMuted mt-0.5 leading-relaxed">{t.option1Desc}</p>
                </div>
                <button
                  disabled={togglingFwd}
                  onClick={() => handleToggle('forwarding_enabled', !details.forwarding_enabled)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.forwarding_enabled ? 'bg-primary' : 'bg-slate-300'} ${togglingFwd ? 'opacity-50' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.forwarding_enabled ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div className="space-y-2">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option1ForwardTo}</p>
                  <code className="text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 block text-primary">{details.forwarding_email || 'jobs@ai970.cloud'}</code>
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option1SubjectHint}</p>
                  <code className="text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 block text-textMain">{details.job_code || details.job_id}</code>
                </div>
              </div>
              <p className={`text-[10px] font-black uppercase tracking-wider mt-3 ${details.forwarding_enabled ? 'text-success' : 'text-textMuted'}`}>
                {details.forwarding_enabled ? `● ${t.enabled}` : `○ ${t.disabled}`}
              </p>
            </div>

            {/* Option 2 — Dedicated Alias */}
            <div className={`rounded-2xl border-2 p-5 transition-all ${details.alias_enabled ? 'border-success/30 bg-green-50/40' : 'border-slate-200 bg-slate-50/60 opacity-60'}`}>
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-xs font-black text-textMain">{t.option2Title}</p>
                    <span className="text-[9px] font-black bg-success text-white px-1.5 py-0.5 rounded-full uppercase">{t.recommended}</span>
                  </div>
                  <p className="text-[11px] text-textMuted mt-0.5 leading-relaxed">{t.option2Desc}</p>
                </div>
                <button
                  disabled={togglingAlias}
                  onClick={() => handleToggle('alias_enabled', !details.alias_enabled)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.alias_enabled ? 'bg-success' : 'bg-slate-300'} ${togglingAlias ? 'opacity-50' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.alias_enabled ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option2AliasLabel}</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 text-success truncate">{details.platform_email || '—'}</code>
                  {details.platform_email && (
                    <button
                      onClick={() => handleCopyAlias(details.platform_email!)}
                      className="shrink-0 px-3 py-1.5 bg-success text-white text-[10px] font-black rounded-lg hover:bg-green-700 transition-colors"
                    >
                      {copiedAlias ? t.copied : t.copyBtn}
                    </button>
                  )}
                </div>
              </div>
              <p className={`text-[10px] font-black uppercase tracking-wider mt-3 ${details.alias_enabled ? 'text-success' : 'text-textMuted'}`}>
                {details.alias_enabled ? `● ${t.enabled}` : `○ ${t.disabled}`}
              </p>
            </div>

          </div>
        </div>

        <div className="p-8 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-6">
          {t.metaLabels.map((label, idx) => (
            <div key={idx}>
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{label}</p>
              <p className="text-sm font-bold text-textMain truncate">{metaValues[idx]}</p>
            </div>
          ))}
        </div>
      </section>

      {/* KPIs */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {kpiValues.map((kpi, idx) => (
          <button
            key={idx}
            onClick={() => onViewApplications(details.job_id, kpi.filter)}
            className="bg-white p-5 rounded-2xl border border-border shadow-sm flex flex-col items-center justify-center text-center cursor-pointer hover:border-primary/50 hover:shadow-md transition-all group"
          >
            <span className={`text-2xl font-black ${kpi.color} group-hover:scale-110 transition-transform`}>{kpi.value}</span>
            <span className="text-[10px] font-black text-textMuted uppercase tracking-widest mt-1 group-hover:text-primary transition-colors">{t.kpiLabels[idx]}</span>
          </button>
        ))}
      </section>

      {/* AI Criteria Extraction Status Banner */}
      {details.criteria_extraction_status && details.criteria_extraction_status !== 'completed' && (
        <div className={`rounded-2xl border p-4 flex items-center justify-between gap-4 ${
          details.criteria_extraction_status === 'failed'
            ? 'bg-red-50 border-red-200'
            : 'bg-amber-50 border-amber-200'
        }`}>
          <div className="flex items-center gap-3">
            {details.criteria_extraction_status === 'failed' ? (
              <svg className="w-5 h-5 text-error shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            ) : (
              <svg className="animate-spin w-5 h-5 text-amber-500 shrink-0" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
            <div>
              <p className="text-sm font-bold text-textMain">
                {details.criteria_extraction_status === 'failed' ? t.criteriaFailed : details.criteria_extraction_status === 'processing' ? t.criteriaProcessing : t.criteriaPending}
              </p>
              {details.criteria_extraction_status === 'failed' && details.criteria_extraction_error && (
                <p className="text-xs text-error/80 mt-0.5">{details.criteria_extraction_error}</p>
              )}
            </div>
          </div>
          {details.criteria_extraction_status === 'failed' && (
            <button
              onClick={handleRetryExtraction}
              className="shrink-0 px-4 py-1.5 bg-error text-white text-xs font-bold rounded-lg hover:bg-red-700 transition-colors"
            >
              {t.retryExtraction}
            </button>
          )}
        </div>
      )}

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          {/* Skills */}
          <div className="bg-white rounded-3xl border border-border p-8 shadow-sm">
            <h3 className="text-sm font-black text-textMain uppercase tracking-widest mb-6 flex items-center">
              <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> {t.skillsAnalysis}
            </h3>
            <div className="space-y-6">
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.requiredSkills}</p>
                <div className="flex flex-wrap gap-2">
                  {(analysis?.skills?.required || []).map((s, i) => (
                    <span key={i} className="px-4 py-1.5 bg-slate-100 rounded-lg text-xs font-bold text-textMain">{s}</span>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.preferredSkills}</p>
                <div className="flex flex-wrap gap-2">
                  {(analysis?.skills?.preferred || []).map((s, i) => (
                    <span key={i} className="px-4 py-1.5 bg-blue-50 text-primary border border-blue-100 rounded-lg text-xs font-bold">{s}</span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Experience & Education */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-white rounded-3xl border border-border p-8 shadow-sm">
              <h3 className="text-sm font-black text-textMain uppercase tracking-widest mb-6 flex items-center">
                <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> {t.experience}
              </h3>
              <div className="space-y-4">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.minYears}</p>
                  <p className="text-lg font-black text-primary">{analysis?.experience?.minimum_years || 0}{t.years}</p>
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.relevantRoles}</p>
                  <ul className="space-y-1">
                    {(analysis?.experience?.relevant_roles || []).map((r, i) => (
                      <li key={i} className="text-xs font-bold text-textMain flex items-center">
                        <span className="w-1 h-1 rounded-full bg-border mr-2"></span> {r}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            <div className="bg-white rounded-3xl border border-border p-8 shadow-sm">
              <h3 className="text-sm font-black text-textMain uppercase tracking-widest mb-6 flex items-center">
                <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> {t.education}
              </h3>
              <div className="space-y-4">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.minLevel}</p>
                  <p className="text-sm font-black text-textMain">{analysis?.education?.minimum_level || 'N/A'}</p>
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.fieldsOfStudy}</p>
                  <div className="flex flex-wrap gap-2">
                    {(analysis?.education?.fields_of_study || []).map((f, i) => (
                      <span key={i} className="px-3 py-1 bg-slate-50 border border-border rounded-lg text-[10px] font-bold text-textMuted">{f}</span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Other Categories */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {otherCats.map((cat, idx) => (
              <div key={idx} className="bg-white rounded-3xl border border-border p-6 shadow-sm">
                <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">{cat.label}</h4>
                <ul className="space-y-2">
                  {(cat.items || []).length > 0 ? (cat.items || []).map((item, i) => (
                    <li key={i} className="text-[11px] font-bold text-textMain leading-snug">• {item}</li>
                  )) : <li className="text-[10px] text-textMuted italic">{t.noData}</li>}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {/* Evaluation Logic */}
        <div className="space-y-6">
          <div className="bg-white rounded-3xl border border-border p-8 shadow-sm sticky top-8">
            <div className="flex items-center justify-between mb-8">
              <h3 className="text-sm font-black text-textMain uppercase tracking-widest flex items-center">
                <span className="w-2 h-4 bg-indigo-600 rounded-full mr-3"></span> {t.evalLogic}
              </h3>
              {!editingWeights && analysis?.scoring_weights && (
                <button
                  onClick={handleEditWeights}
                  className="text-[10px] font-black text-indigo-600 hover:text-indigo-800 uppercase tracking-widest transition-colors"
                >
                  {t.editWeights}
                </button>
              )}
            </div>

            {editingWeights ? (
              <>
                <div className="space-y-4">
                  {weightKeys.map((key, i) => {
                    const val = draftWeights[key] ?? 0;
                    const isOver = weightTotal > 100 && val > 0;
                    return (
                      <div key={i}>
                        <div className="flex justify-between text-[10px] font-black uppercase mb-1.5">
                          <span className="text-textMuted">{weightLabels[i]}</span>
                          <span className={isOver ? 'text-error' : 'text-textMuted'}>{val}%</span>
                        </div>
                        <input
                          type="number"
                          min={0}
                          max={100}
                          value={val}
                          onChange={e => {
                            const v = Math.max(0, Math.min(100, parseInt(e.target.value) || 0));
                            setDraftWeights(prev => ({ ...prev, [key]: v }));
                          }}
                          className={`w-full px-3 py-2 text-sm font-bold rounded-xl border-2 focus:outline-none transition-colors ${
                            isOver
                              ? 'border-error bg-red-50 text-error'
                              : 'border-border bg-slate-50 text-textMain focus:border-indigo-400'
                          }`}
                        />
                      </div>
                    );
                  })}
                </div>

                {/* Live total indicator */}
                <div className={`mt-4 px-3 py-2.5 rounded-xl text-[11px] font-bold flex items-center justify-between ${
                  weightTotal === 100
                    ? 'bg-green-50 text-success border border-green-200'
                    : weightTotal > 100
                    ? 'bg-red-50 text-error border border-red-200'
                    : 'bg-amber-50 text-warning border border-amber-200'
                }`}>
                  <span>
                    {weightTotal === 100
                      ? t.weightSuccess
                      : weightTotal > 100
                      ? t.weightOver.replace('{e}', String(weightTotal - 100))
                      : t.weightUnder.replace('{r}', String(100 - weightTotal))}
                  </span>
                  <span className="font-black shrink-0 ml-2">{t.weightTotal}: {weightTotal}%</span>
                </div>

                {/* Utility buttons */}
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button
                    onClick={handleNormalizeWeights}
                    className="px-2 py-2 text-[10px] font-black text-indigo-600 border border-indigo-200 rounded-xl hover:bg-indigo-50 uppercase tracking-widest transition-colors leading-tight"
                  >
                    {t.normalizeWeights}
                  </button>
                  <button
                    onClick={handleResetWeights}
                    className="px-2 py-2 text-[10px] font-black text-textMuted border border-border rounded-xl hover:bg-slate-50 uppercase tracking-widest transition-colors leading-tight"
                  >
                    {t.resetAiWeights}
                  </button>
                </div>

                {/* Save / Cancel */}
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setEditingWeights(false)}
                    className="px-3 py-2.5 text-xs font-bold text-textMuted border border-border rounded-xl hover:bg-slate-50 transition-colors"
                  >
                    {t.cancelEdit}
                  </button>
                  <button
                    onClick={handleSaveWeights}
                    disabled={weightTotal !== 100 || savingWeights}
                    className={`px-3 py-2.5 text-xs font-bold rounded-xl transition-colors ${
                      weightTotal === 100 && !savingWeights
                        ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                        : 'bg-slate-200 text-textMuted cursor-not-allowed'
                    }`}
                  >
                    {savingWeights ? '…' : t.saveWeights}
                  </button>
                </div>
              </>
            ) : (
              <div className="space-y-6">
                {weightKeys.map((key, i) => {
                  const weight = analysis?.scoring_weights?.[key];
                  return weight ? (
                    <div key={i}>
                      <div className="flex justify-between text-[10px] font-black uppercase mb-2">
                        <span className="text-textMuted">{weightLabels[i]}</span>
                        <span className="text-textMain">{weight}%</span>
                      </div>
                      <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                        <div className="bg-indigo-600 h-full rounded-full" style={{ width: `${weight}%` }}></div>
                      </div>
                    </div>
                  ) : null;
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Job Description */}
      <section className="bg-white rounded-3xl border border-border overflow-hidden">
        <button
          onClick={() => setDescExpanded(!descExpanded)}
          className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <h3 className="text-sm font-black text-textMain uppercase tracking-widest flex items-center">
            <span className="w-2 h-4 bg-slate-400 rounded-full mr-3"></span> {t.jobDesc}
          </h3>
          <svg className={`w-6 h-6 transform transition-transform ${descExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {descExpanded && (
          <div className="px-10 pb-10 pt-4 animate-fade-in">
            <div className="prose prose-slate max-w-none text-textMain text-sm leading-relaxed opacity-80 whitespace-pre-wrap">
              {details.description}
            </div>
          </div>
        )}
      </section>
    </div>
  );
};
