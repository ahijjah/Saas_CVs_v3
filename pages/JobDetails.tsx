
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { JobDetails as JobDetailsType, AuthState, UploadedCV, UploadQueueStatus } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface JobDetailsProps {
  jobId: string;
  auth: AuthState;
  onBack: () => void;
  onViewApplications: (jobId: string, filter: string) => void;
  onOpenApplication: (jobId: string, applicationId: string) => void;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
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
    waysToReceive: 'Ways to Receive Applications',
    option1Title: 'Forward to Central Email',
    option1Desc: 'Forward CVs from your system to the central inbox. Include the job code for automatic routing.',
    option1ForwardTo: 'Forward CVs to',
    option2Title: 'Dedicated Job Email Alias',
    option2Desc: 'Share this address directly with applicants or publish it on your careers page. CVs are automatically assigned to this job.',
    option2AliasLabel: 'Dedicated alias',
    jobRef: 'Job reference',
    jobRefHint: 'Include this job code in the email subject or body.',
    copyBtn: 'Copy',
    copied: 'Copied!',
    enabled: 'Enabled',
    disabled: 'Disabled',
    recommended: 'Recommended',
    manualUploadTitle: 'Manual CV Upload',
    manualUploadDesc: 'For internal or recruiter use. Upload PDF, DOC, or DOCX files directly to this job.',
    uploadBtn: 'Upload CVs',
    uploadingBtn: 'Uploading...',
    chooseFiles: 'Choose files (PDF / DOC / DOCX)',
    uploadedCVsTitle: 'Uploaded CVs',
    noUploads: 'No CVs uploaded yet.',
    scoreBtnLabel: 'Score uploaded CVs',
    scoringBtnLabel: 'Scoring...',
    progressLabel: '{scored} of {total} CVs processed — {pct}%',
    deleteCV: 'Delete',
    deletingCV: 'Deleting...',
    statusPending: 'Pending',
    statusQueued: 'In Queue',
    statusL1Screen: 'L1: Pre-screening...',
    statusL2Screen: 'L2: AI screening...',
    statusL3Score: 'L3: Full scoring...',
    statusScored: 'Scored',
    statusLowMatch: 'Rejected — L1',
    statusRejectedL2: 'Rejected — L2',
    statusFailed: 'Failed',
    exitReason: 'Reason:',
    resetStuck: 'Reset stuck CVs',
    resettingStuck: 'Resetting...',
    criteriaPending: 'AI criteria analysis is being generated. This page will refresh automatically.',
    criteriaProcessing: 'AI criteria analysis is in progress. This page will refresh automatically.',
    criteriaFailed: 'AI criteria analysis failed.',
    retryExtraction: 'Retry',
    restrictSender: 'Restrict to tenant email domain',
    restrictSenderHint: 'Only accept forwarded CVs from your own email domain.',
    confirmationSettings: 'Confirmation Email & AI Settings',
    confirmUpload: 'Send confirmation to candidate email on manual upload',
    confirmFwdCvEmail: 'Send confirmation to candidate email on forwarding',
    confirmFwdSenderEmail: 'Send confirmation to forwarding sender',
    confirmPlatformEmail: 'Send confirmation to candidate email on platform email',
    aiComparisonToggle: 'Enable AI comparison scoring',
    aiComparisonHint: 'Run a secondary LLM to compare scoring results.',
    viewApplications: 'View Applications',
    jobMetadata: 'Job Details',
    editMeta: 'Edit',
    saveMeta: 'Save Changes',
    cancelMeta: 'Cancel',
    savingMeta: 'Saving...',
    metaSaved: 'Job details updated',
    metaStatusLabel: 'Status',
    metaLocation: 'Location',
    metaJobType: 'Job Type',
    metaDepartment: 'Department',
    metaExperienceLevel: 'Experience Level',
    metaWorkMode: 'Work Mode',
    metaDeadline: 'Application Deadline',
    metaVacancies: 'Vacancies',
    metaDuration: 'Duration',
    metaCreated: 'Created',
    metaUpdated: 'Last Updated',
    metaCreatedBy: 'Created By',
    metaUpdatedBy: 'Last Modified By',
    metaJobCode: 'Job Code',
    jobTypeOptions: ['Full-time', 'Part-time', 'Contract', 'Temporary', 'Internship'],
    expLevelOptions: ['Entry', 'Mid', 'Senior', 'Managerial'],
    workModeOptions: ['On-site', 'Remote', 'Hybrid'],
    statusOptions: ['Active', 'Inactive', 'Closed'],
    publicApplyLink: 'Public Apply Link',
    publicApplyHint: 'Share this link with candidates to apply directly online.',
    intakeChannels: 'Intake Channels',
    appSummary: 'Applications Summary',
    notSet: 'Not set',
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
    waysToReceive: 'طرق استقبال الطلبات',
    option1Title: 'إعادة التوجيه إلى البريد المركزي',
    option1Desc: 'أرسل السير الذاتية من نظامك إلى البريد المركزي. أدرج رمز الوظيفة للتوجيه التلقائي.',
    option1ForwardTo: 'أرسل السير الذاتية إلى',
    option2Title: 'بريد مخصص لكل وظيفة',
    option2Desc: 'شارك هذا العنوان مع المتقدمين مباشرةً أو انشره في صفحة الوظائف. يُعيَّن البريد الوارد تلقائياً لهذه الوظيفة.',
    option2AliasLabel: 'البريد المخصص',
    jobRef: 'رمز الوظيفة',
    jobRefHint: 'أدرج هذا الرمز في موضوع البريد الإلكتروني أو نصه.',
    copyBtn: 'نسخ',
    copied: 'تم النسخ!',
    enabled: 'مفعّل',
    disabled: 'معطّل',
    recommended: 'مُوصى به',
    manualUploadTitle: 'رفع السيرة الذاتية يدوياً',
    manualUploadDesc: 'للاستخدام الداخلي أو من قِبل المسؤولين عن التوظيف. ارفع ملفات PDF أو DOC أو DOCX مباشرةً.',
    uploadBtn: 'رفع السير الذاتية',
    uploadingBtn: 'جارٍ الرفع...',
    chooseFiles: 'اختر ملفات (PDF / DOC / DOCX)',
    uploadedCVsTitle: 'السير الذاتية المرفوعة',
    noUploads: 'لم يتم رفع أي سيرة ذاتية بعد.',
    scoreBtnLabel: 'تقييم السير الذاتية المرفوعة',
    scoringBtnLabel: 'جارٍ التقييم...',
    progressLabel: '{scored} من {total} تمت معالجتها — {pct}%',
    deleteCV: 'حذف',
    deletingCV: 'جارٍ الحذف...',
    statusPending: 'في الانتظار',
    statusQueued: 'في الطابور',
    statusL1Screen: 'م1: الفرز الأولي...',
    statusL2Screen: 'م2: الفرز بالذكاء...',
    statusL3Score: 'م3: التقييم الكامل...',
    statusScored: 'تم التقييم',
    statusLowMatch: 'مرفوض — م1',
    statusRejectedL2: 'مرفوض — م2',
    statusFailed: 'فشل',
    exitReason: 'السبب:',
    resetStuck: 'إعادة تعيين المعلّقة',
    resettingStuck: 'جارٍ الإعادة...',
    criteriaPending: 'جارٍ إنشاء تحليل معايير الذكاء الاصطناعي. ستُحدَّث هذه الصفحة تلقائياً.',
    criteriaProcessing: 'تحليل معايير الذكاء الاصطناعي قيد التنفيذ. ستُحدَّث هذه الصفحة تلقائياً.',
    criteriaFailed: 'فشل تحليل معايير الذكاء الاصطناعي.',
    retryExtraction: 'إعادة المحاولة',
    restrictSender: 'تقييد بنطاق البريد الخاص بالمستأجر',
    restrictSenderHint: 'قبول السير المُعاد توجيهها من نطاقك فقط.',
    confirmationSettings: 'إعدادات تأكيد البريد والذكاء الاصطناعي',
    confirmUpload: 'إرسال تأكيد لبريد المرشح عند الرفع اليدوي',
    confirmFwdCvEmail: 'إرسال تأكيد لبريد المرشح عند إعادة التوجيه',
    confirmFwdSenderEmail: 'إرسال تأكيد لمُرسل الإعادة',
    confirmPlatformEmail: 'إرسال تأكيد لبريد المرشح عبر البريد المخصص',
    aiComparisonToggle: 'تفعيل التقييم المقارن بالذكاء الاصطناعي',
    aiComparisonHint: 'تشغيل نموذج ذكاء اصطناعي ثانوي لمقارنة النتائج.',
    viewApplications: 'عرض الطلبات',
    jobMetadata: 'تفاصيل الوظيفة',
    editMeta: 'تعديل',
    saveMeta: 'حفظ التغييرات',
    cancelMeta: 'إلغاء',
    savingMeta: 'جارٍ الحفظ...',
    metaSaved: 'تم تحديث تفاصيل الوظيفة',
    metaStatusLabel: 'الحالة',
    metaLocation: 'الموقع',
    metaJobType: 'نوع الوظيفة',
    metaDepartment: 'القسم',
    metaExperienceLevel: 'مستوى الخبرة',
    metaWorkMode: 'طريقة العمل',
    metaDeadline: 'آخر موعد للتقديم',
    metaVacancies: 'عدد الشواغر',
    metaDuration: 'المدة',
    metaCreated: 'تاريخ الإنشاء',
    metaUpdated: 'آخر تحديث',
    metaCreatedBy: 'أنشئ بواسطة',
    metaUpdatedBy: 'عُدّل بواسطة',
    metaJobCode: 'رمز الوظيفة',
    publicApplyLink: 'رابط التقديم العام',
    publicApplyHint: 'شارك هذا الرابط مع المرشحين للتقديم مباشرة عبر الإنترنت.',
    intakeChannels: 'قنوات الاستقبال',
    appSummary: 'ملخص الطلبات',
    notSet: 'غير محدد',
  },
};

export const JobDetails: React.FC<JobDetailsProps> = ({ jobId, auth, onBack, onViewApplications, onOpenApplication, addToast }) => {
  const { lang, isAr } = useLanguage();
  const t = T[lang];
  const isSuperAdmin = (auth.user?.role || '').toLowerCase() === 'super_admin';
  const role = (auth.user?.role || '').toLowerCase();
  const canEdit = role === 'admin' || role === 'hr_manager';

  const [details, setDetails] = useState<JobDetailsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [descExpanded, setDescExpanded] = useState(false);
  const [copiedAlias, setCopiedAlias] = useState(false);
  const [copiedJobRef, setCopiedJobRef] = useState(false);
  const [copiedFwdEmail, setCopiedFwdEmail] = useState(false);
  const [togglingFwd, setTogglingFwd] = useState(false);
  const [togglingAlias, setTogglingAlias] = useState(false);
  const [editingWeights, setEditingWeights] = useState(false);
  const [draftWeights, setDraftWeights] = useState<Record<string, number>>({});
  const [savingWeights, setSavingWeights] = useState(false);

  const [duplicateLogs, setDuplicateLogs] = useState<any[]>([]);
  const [loadingDupLogs, setLoadingDupLogs] = useState(false);

  // Criteria extraction polling tick — increments after each poll so the
  // effect always re-schedules even when status string stays unchanged.
  const [criteriaPolltick, setCriteriaPolltick] = useState(0);

  // Manual CV upload state
  const [uploadedCVs, setUploadedCVs] = useState<UploadedCV[]>([]);
  const [loadingUploads, setLoadingUploads] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [deletingCVId, setDeletingCVId] = useState<string | null>(null);
  const [queueStatus, setQueueStatus] = useState<UploadQueueStatus | null>(null);
  const [resettingStuck, setResettingStuck] = useState(false);
  const prevIsProcessingRef = useRef<boolean>(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [editingMeta, setEditingMeta] = useState(false);
  const [draftMeta, setDraftMeta] = useState<Record<string, string>>({});
  const [savingMeta, setSavingMeta] = useState(false);
  const [copiedApplyLink, setCopiedApplyLink] = useState(false);

  // ── Initial job details fetch ───────────────────────────────────────────────
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
          setDetails({ ...payload.details, analysis_json: payload.analysis });
        } else {
          throw new Error('No data received for this job ID.');
        }
      } catch (err: any) {
        const errorMsg =
          err.name === 'TypeError' && err.message === 'Failed to fetch'
            ? 'Network connection error.'
            : err.message || 'Failed to load job details.';
        setError(errorMsg);
        addToast(errorMsg, 'error');
        // Provide stub data for offline development
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
          ingestion_note: '',
          ingestion_mode: 'forwarding',
          ingestion_email: null,
          applications_total: 142,
          applications_qualified: 24,
          applications_partial: 45,
          applications_rejected: 73,
          applications_above_threshold: 18,
          applications_below_threshold: 102,
          applications_recommended: 12,
          description: 'We are seeking a highly skilled Senior Frontend Engineer to lead the development of our core product interface.',
          analysis_json: {
            skills: {
              required: ['React', 'TypeScript', 'Tailwind CSS', 'State Management (Redux/Zustand)'],
              preferred: ['Next.js', 'GraphQL', 'Jest/Cypress', 'Web Accessibility (WCAG)'],
            },
            experience: {
              minimum_years: 5,
              relevant_roles: ['Senior Frontend Engineer', 'Lead Developer', 'React Specialist'],
              key_responsibilities: ['Architect scalable frontend components', 'Optimizing application performance', 'Mentoring junior engineering staff'],
            },
            education: {
              minimum_level: "Bachelor's Degree",
              fields_of_study: ['Computer Science', 'Software Engineering', 'Information Technology'],
            },
            certifications: ['AWS Certified Developer', 'Meta Frontend Professional'],
            domain_knowledge: ['FinTech', 'Enterprise SaaS', 'Data Visualization'],
            other_requirements: ['Strong communication skills', 'Agile/Scrum experience'],
            scoring_weights: { skills: 40, experience: 30, education: 10, certifications: 5, soft_skills: 10, domain_knowledge: 5 },
          },
        });
      } finally {
        setLoading(false);
      }
    };
    fetchDetails();
  }, [jobId, auth.token]);

  // ── Poll job analysis status every 5 s while pending/processing ────────────
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
      } catch { /* ignore transient polling errors */ }
      setCriteriaPolltick(t => t + 1); // always re-trigger effect
    }, 5000);
    return () => clearTimeout(timer);
  }, [criteriaPolltick, details?.criteria_extraction_status, jobId, auth.token]);

  // ── Fetch uploaded CVs ──────────────────────────────────────────────────────
  const fetchUploadedCVs = useCallback(async () => {
    if (!jobId) return;
    setLoadingUploads(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.UPLOADED_CVS_URL,
        { job_id: jobId },
        auth.token!
      );
      setUploadedCVs(Array.isArray(data) ? data : []);
    } catch { /* ignore */ } finally {
      setLoadingUploads(false);
    }
  }, [jobId, auth.token]);

  // Silent background refresh of job details (stats/KPIs). No loading state,
  // no error toast — used after scoring completion so counters update in place.
  const fetchJobDetails = useCallback(async () => {
    if (!jobId) return;
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
    } catch { /* ignore transient errors during background refresh */ }
  }, [jobId, auth.token]);

  const fetchQueueStatus = useCallback(async () => {
    if (!jobId) return;
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.QUEUE_STATUS_URL,
        { job_id: jobId },
        auth.token!
      );
      setQueueStatus(data);
    } catch { /* ignore */ }
  }, [jobId, auth.token]);

  const fetchDuplicateLogs = useCallback(async () => {
    if (!auth.token || !jobId) return;
    setLoadingDupLogs(true);
    try {
      const data = await apiService.get(
        `${WEBHOOK_CONFIG.DUPLICATE_LOGS_BASE_URL}/${jobId}/duplicate-logs`,
        {},
        auth.token
      );
      setDuplicateLogs(data.duplicate_logs || []);
    } catch {
      // non-critical
    } finally {
      setLoadingDupLogs(false);
    }
  }, [jobId, auth.token]);

  useEffect(() => { fetchUploadedCVs(); }, [fetchUploadedCVs]);
  useEffect(() => { fetchQueueStatus(); }, [fetchQueueStatus]);
  useEffect(() => { fetchDuplicateLogs(); }, [fetchDuplicateLogs]);

  // ── Poll while a batch is in-flight ───────────────────────────────────────
  useEffect(() => {
    if (!queueStatus?.is_processing && !scoring) return;
    const timer = setTimeout(async () => {
      await fetchQueueStatus();
      await fetchUploadedCVs();
    }, 3000);
    return () => clearTimeout(timer);
  }, [queueStatus, scoring, fetchQueueStatus, fetchUploadedCVs]);

  // ── Detect batch completion → refresh job stats + CV list + dup logs ────────
  useEffect(() => {
    const isNowProcessing = queueStatus?.is_processing ?? false;
    if (prevIsProcessingRef.current && !isNowProcessing) {
      // Batch just finished: pull fresh KPI counters, clear queue list, refresh dup logs
      fetchJobDetails();
      fetchUploadedCVs();
      fetchDuplicateLogs();
    }
    prevIsProcessingRef.current = isNowProcessing;
  }, [queueStatus?.is_processing, fetchJobDetails, fetchUploadedCVs, fetchDuplicateLogs]);

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleRetryExtraction = useCallback(async () => {
    if (!details) return;
    try {
      await apiService.post(
        `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${details.job_id}/criteria/retry`,
        {},
        auth.token!
      );
      setDetails(prev => prev ? { ...prev, criteria_extraction_status: 'pending', criteria_extraction_error: null } : prev);
      setCriteriaPolltick(0);
      addToast('AI analysis retry queued.', 'success');
    } catch {
      addToast('Failed to retry AI analysis.', 'error');
    }
  }, [details, auth.token, addToast]);

  const handleCopy = useCallback((text: string, setFlag: (v: boolean) => void) => {
    const confirm = () => { setFlag(true); setTimeout(() => setFlag(false), 2000); };
    const fallback = () => {
      try {
        const el = document.createElement('textarea');
        el.value = text;
        el.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
        confirm();
      } catch {
        addToast('Could not copy — please copy manually.', 'error');
      }
    };
    navigator.clipboard ? navigator.clipboard.writeText(text).then(confirm).catch(fallback) : fallback();
  }, [addToast]);

  const handleViewCV = useCallback(async (downloadUrl: string, filename: string) => {
    try {
      const resp = await fetch(downloadUrl, { headers: { Authorization: `Bearer ${auth.token!}` } });
      if (!resp.ok) throw new Error('CV not available');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename || 'cv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch {
      addToast('Could not download CV file.', 'error');
    }
  }, [auth.token, addToast]);

  const handleIngestionToggle = useCallback(async (
    field: 'receive_cv_via_forwarding_email' | 'receive_cv_via_platform_email' | 'restrict_forwarding_sender_to_tenant_email',
    value: boolean,
  ) => {
    if (!details) return;
    const setToggling = field === 'receive_cv_via_forwarding_email' ? setTogglingFwd
                      : field === 'receive_cv_via_platform_email' ? setTogglingAlias
                      : null;
    setToggling?.(true);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.JOB_INGESTION_BASE_URL}/${details.job_id}/ingestion`,
        { [field]: value },
        auth.token!
      );
      setDetails(prev => prev ? { ...prev, [field]: value } : prev);
    } catch {
      addToast('Failed to update ingestion setting.', 'error');
    } finally {
      setToggling?.(false);
    }
  }, [details, auth.token, addToast]);

  const handleSettingsToggle = useCallback(async (
    field: 'send_confirmation_to_cv_email_for_upload'
         | 'send_confirmation_to_cv_email_for_forwarding'
         | 'send_confirmation_to_sender_for_forwarding'
         | 'send_confirmation_to_cv_email_for_platform_email'
         | 'enable_ai_comparison',
    value: boolean,
  ) => {
    if (!details) return;
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.JOB_SETTINGS_BASE_URL}/${details.job_id}/settings`,
        { [field]: value },
        auth.token!
      );
      setDetails(prev => prev ? { ...prev, [field]: value } : prev);
      addToast('Setting updated.', 'success');
    } catch {
      addToast('Failed to update setting.', 'error');
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
          weight_skills:           draftWeights['skills']             ?? 0,
          weight_experience:       draftWeights['experience']         ?? 0,
          weight_education:        draftWeights['education']          ?? 0,
          weight_certifications:   draftWeights['certifications']     ?? 0,
          weight_soft_skills:      draftWeights['soft_skills']        ?? 0,
          weight_domain_knowledge: draftWeights['domain_knowledge']   ?? 0,
          weight_other:            draftWeights['other_requirements']  ?? 0,
        },
        auth.token!
      );
      setDetails(prev => {
        if (!prev || !prev.analysis_json) return prev;
        return { ...prev, analysis_json: { ...prev.analysis_json, scoring_weights: { ...draftWeights } as any } };
      });
      setEditingWeights(false);
      addToast('Evaluation weights updated successfully.', 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to update weights.', 'error');
    } finally {
      setSavingWeights(false);
    }
  }, [details, draftWeights, auth.token, addToast]);

  const handleUpload = useCallback(async (files: FileList) => {
    if (!files.length) return;
    setUploading(true);
    let successCount = 0;
    let failCount = 0;
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append('job_id', jobId);
      fd.append('candidate_name', file.name.replace(/\.[^.]+$/, ''));
      fd.append('file', file);
      try {
        await apiService.postForm(WEBHOOK_CONFIG.CV_UPLOAD_URL, fd, auth.token!);
        successCount++;
      } catch {
        failCount++;
      }
    }
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (successCount > 0) addToast(`${successCount} CV(s) uploaded successfully.`, 'success');
    if (failCount > 0) addToast(`${failCount} CV(s) failed to upload.`, 'error');
    await fetchUploadedCVs();
  }, [jobId, auth.token, addToast, fetchUploadedCVs]);

  const handleScorePending = useCallback(async () => {
    setScoring(true);
    try {
      const result = await apiService.post(
        WEBHOOK_CONFIG.SCORE_PENDING_URL,
        { job_id: jobId },
        auth.token!
      );
      if (result?.queued === 0) {
        addToast(result?.message || 'No pending CVs to score.', 'success');
        return;
      }
      addToast(result?.message || 'CV scoring queued.', 'success');
      await Promise.all([fetchQueueStatus(), fetchUploadedCVs()]);
    } catch (err: any) {
      addToast(err?.message || 'Failed to trigger scoring.', 'error');
    } finally {
      setScoring(false);
    }
  }, [jobId, auth.token, addToast, fetchUploadedCVs, fetchQueueStatus]);

  const handleResetStuck = useCallback(async () => {
    setResettingStuck(true);
    try {
      const result = await apiService.post(
        WEBHOOK_CONFIG.RESET_STUCK_URL,
        { job_id: jobId },
        auth.token!
      );
      addToast(result?.message || 'Stuck CVs reset to pending.', 'success');
      await Promise.all([fetchQueueStatus(), fetchUploadedCVs()]);
    } catch (err: any) {
      addToast(err?.message || 'Failed to reset stuck CVs.', 'error');
    } finally {
      setResettingStuck(false);
    }
  }, [jobId, auth.token, addToast, fetchUploadedCVs, fetchQueueStatus]);

  const handleDeleteCV = useCallback(async (applicationId: string) => {
    setDeletingCVId(applicationId);
    try {
      await apiService.delete(`${WEBHOOK_CONFIG.DELETE_APPLICATION_URL}/${applicationId}`, auth.token!);
      setUploadedCVs(prev => prev.filter(cv => cv.application_id !== applicationId));
      addToast('CV deleted.', 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to delete CV.', 'error');
    } finally {
      setDeletingCVId(null);
    }
  }, [auth.token, addToast]);

  const handleMetaEdit = useCallback(() => {
    if (!details) return;
    setDraftMeta({
      title:                (details as any).job_title || '',
      department:           (details as any).job_client || '',
      location:             (details as any).location || '',
      job_type:             (details as any).job_type || '',
      duration:             (details as any).duration || '',
      experience_level:     (details as any).experience_level || '',
      work_mode:            (details as any).work_mode || '',
      application_deadline: (details as any).application_deadline || '',
      vacancies_count:      String((details as any).vacancies_count || ''),
      status:               ((details as any).job_status || 'active').toLowerCase(),
    });
    setEditingMeta(true);
  }, [details]);

  const handleMetaSave = useCallback(async () => {
    if (!details) return;
    setSavingMeta(true);
    try {
      const payload: Record<string, string | number | null> = {
        department:           draftMeta.department,
        location:             draftMeta.location,
        job_type:             draftMeta.job_type,
        duration:             draftMeta.duration,
        experience_level:     draftMeta.experience_level,
        work_mode:            draftMeta.work_mode,
        application_deadline: draftMeta.application_deadline || null,
        vacancies_count:      parseInt(draftMeta.vacancies_count) || 1,
        status:               draftMeta.status,
      };
      if (draftMeta.title) payload.title = draftMeta.title;
      await apiService.put(
        `${WEBHOOK_CONFIG.UPDATE_JOB_URL}/${(details as any).job_id}`,
        payload,
        auth.token!
      );
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL, { job_id: (details as any).job_id }, auth.token!);
      if (data) {
        const p = Array.isArray(data) ? data[0] : data;
        setDetails({ ...p.details, analysis_json: p.analysis });
      }
      setEditingMeta(false);
      addToast(t.metaSaved, 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to update job.', 'error');
    } finally {
      setSavingMeta(false);
    }
  }, [details, draftMeta, auth.token, addToast, t.metaSaved]);

  // ── Derived values ─────────────────────────────────────────────────────────

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
  const publicApplyUrl = `${window.location.origin}/apply/${details.job_code}`;
  const statusColorMap: Record<string, string> = {
    active: 'bg-emerald-100 text-emerald-700',
    inactive: 'bg-amber-100 text-amber-700',
    closed: 'bg-slate-100 text-slate-500',
  };
  const statusColor = statusColorMap[(details.job_status || '').toLowerCase()] || 'bg-slate-100 text-slate-500';
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

  const jobCode = details.job_code || details.job_id;
  // Active queue: CVs still in flight (for button/progress logic)
  const cvsInQueue = uploadedCVs.filter(cv =>
    cv.processing_status === 'pending' ||
    cv.processing_status === 'queued' ||
    cv.processing_status === 'processing'
  );
  // All CVs are shown in the display list — completed ones show their stage/exit reason
  const cvsDisplay = uploadedCVs;
  const cvsScoredCount = queueStatus?.completed ?? uploadedCVs.filter(cv => cv.processing_status === 'scored' || cv.processing_status === 'low_match').length;
  const cvsTotal = queueStatus?.total ?? uploadedCVs.length;
  const cvsActivelyProcessing = queueStatus?.is_processing ?? cvsInQueue.some(cv => cv.processing_status === 'queued' || cv.processing_status === 'processing');
  const cvsHasPending = cvsInQueue.some(cv => cv.processing_status === 'pending');
  const cvsScoringInProgress = scoring || cvsActivelyProcessing;
  const progressPct = queueStatus && cvsTotal > 0
    ? queueStatus.percentage
    : cvsTotal > 0 ? Math.round((cvsScoredCount / cvsTotal) * 100) : 0;

  const cvStatusDisplay = (cv: UploadedCV) => {
    switch (cv.processing_status) {
      case 'pending':  return { label: t.statusPending, color: 'text-amber-600 bg-amber-50 border-amber-200',   spin: false };
      case 'queued':   return { label: t.statusQueued,  color: 'text-indigo-600 bg-indigo-50 border-indigo-200', spin: true  };
      case 'processing': {
        // Show live level based on evaluation_stage committed after each level passes
        if (cv.evaluation_stage === 2) return { label: t.statusL3Score,  color: 'text-violet-600 bg-violet-50 border-violet-200', spin: true };
        if (cv.evaluation_stage === 1) return { label: t.statusL2Screen, color: 'text-blue-600 bg-blue-50 border-blue-200',       spin: true };
        return                                { label: t.statusL1Screen, color: 'text-sky-600 bg-sky-50 border-sky-200',           spin: true };
      }
      case 'scored':
        if (cv.evaluation_stage != null && cv.evaluation_stage < 3)
          return { label: t.statusRejectedL2, color: 'text-red-600 bg-red-50 border-red-200',    spin: false };
        return   { label: cv.score != null ? `${Math.round(cv.score)}` : t.statusScored,
                   color: 'text-success bg-green-50 border-green-200', spin: false };
      case 'low_match': return { label: t.statusLowMatch, color: 'text-slate-500 bg-slate-50 border-slate-200', spin: false };
      case 'failed':    return { label: t.statusFailed,   color: 'text-error bg-red-50 border-red-200',         spin: false };
      default:            return { label: cv.processing_status, color: 'text-textMuted bg-slate-50 border-slate-200', spin: false };
    }
  };

  const decisionBadge = (cv: UploadedCV) => {
    if (cv.processing_status !== 'scored' || !cv.decision) return null;
    const map: Record<string, string> = {
      qualified: 'bg-green-100 text-success',
      partial:   'bg-amber-100 text-warning',
      rejected:  'bg-red-100 text-error',
    };
    return <span className={`text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full ${map[cv.decision] || 'bg-slate-100 text-textMuted'}`}>{cv.decision}</span>;
  };

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12 animate-fade-in">

      {/* ── Job Metadata Card ───────────────────────────────────────────────── */}
      <section className="bg-white rounded-3xl shadow-sm border border-border overflow-hidden">

        {/* Header */}
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
            <button
              onClick={() => onViewApplications(details.job_id, 'all')}
              className="flex items-center gap-2 bg-primary text-white px-5 py-2.5 rounded-xl text-sm font-bold shadow-lg shadow-primary/20 hover:bg-primaryDark transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {t.viewApplications}
            </button>
          </div>
        </div>

        {/* ── B. Job Metadata ─────────────────────────────────────────────────── */}
        <div className="border-b border-border px-8 py-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {t.jobMetadata}
            </h3>
            {canEdit && !editingMeta && (
              <button onClick={handleMetaEdit} className="flex items-center gap-1.5 text-[10px] font-black text-primary hover:text-primaryDark uppercase tracking-widest transition-colors">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
                {t.editMeta}
              </button>
            )}
          </div>
          {editingMeta ? (
            <div className="space-y-4 animate-fade-in">
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaStatusLabel}</label>
                  <select className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.status} onChange={e => setDraftMeta(p => ({ ...p, status: e.target.value }))}>
                    {['active', 'inactive', 'closed'].map(s => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaJobType}</label>
                  <select className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.job_type} onChange={e => setDraftMeta(p => ({ ...p, job_type: e.target.value }))}>
                    <option value="">{t.notSet}</option>
                    {['Full-time', 'Part-time', 'Contract', 'Temporary', 'Internship'].map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaWorkMode}</label>
                  <select className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.work_mode} onChange={e => setDraftMeta(p => ({ ...p, work_mode: e.target.value }))}>
                    <option value="">{t.notSet}</option>
                    {['On-site', 'Remote', 'Hybrid'].map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaExperienceLevel}</label>
                  <select className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.experience_level} onChange={e => setDraftMeta(p => ({ ...p, experience_level: e.target.value }))}>
                    <option value="">{t.notSet}</option>
                    {['Entry', 'Mid', 'Senior', 'Managerial'].map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaLocation}</label>
                  <input type="text" placeholder="e.g. Riyadh, SA" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.location} onChange={e => setDraftMeta(p => ({ ...p, location: e.target.value }))} />
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaDepartment}</label>
                  <input type="text" placeholder="Engineering" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.department} onChange={e => setDraftMeta(p => ({ ...p, department: e.target.value }))} />
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaDeadline}</label>
                  <input type="date" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.application_deadline} onChange={e => setDraftMeta(p => ({ ...p, application_deadline: e.target.value }))} />
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaVacancies}</label>
                  <input type="number" min={1} className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.vacancies_count} onChange={e => setDraftMeta(p => ({ ...p, vacancies_count: e.target.value }))} />
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaDuration}</label>
                  <input type="text" placeholder="Permanent / 6 months" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.duration} onChange={e => setDraftMeta(p => ({ ...p, duration: e.target.value }))} />
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setEditingMeta(false)} disabled={savingMeta} className="px-5 py-2 text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">{t.cancelMeta}</button>
                <button onClick={handleMetaSave} disabled={savingMeta} className="flex items-center gap-2 px-6 py-2 bg-primary text-white text-sm font-bold rounded-lg hover:bg-primaryDark transition-colors disabled:opacity-50">
                  {savingMeta && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                  {savingMeta ? t.savingMeta : t.saveMeta}
                </button>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-4">
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaJobCode}</p><p className="text-sm font-bold text-textMain font-mono">{details.job_code}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaStatusLabel}</p><span className={`inline-flex px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${statusColor}`}>{details.job_status}</span></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaJobType}</p><p className="text-sm font-bold text-textMain">{(details as any).job_type || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaWorkMode}</p><p className="text-sm font-bold text-textMain">{(details as any).work_mode || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaExperienceLevel}</p><p className="text-sm font-bold text-textMain">{(details as any).experience_level || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaLocation}</p><p className="text-sm font-bold text-textMain">{(details as any).location || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaDepartment}</p><p className="text-sm font-bold text-textMain">{details.job_client || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaDuration}</p><p className="text-sm font-bold text-textMain">{(details as any).duration || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaDeadline}</p><p className="text-sm font-bold text-textMain">{(details as any).application_deadline ? new Date((details as any).application_deadline).toLocaleDateString() : t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaVacancies}</p><p className="text-sm font-bold text-textMain">{(details as any).vacancies_count ?? t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaCreated}</p><p className="text-sm font-bold text-textMain">{details.posted_date || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaUpdated}</p><p className="text-sm font-bold text-textMain">{(details as any).updated_at ? new Date((details as any).updated_at).toLocaleDateString() : t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaCreatedBy}</p><p className="text-sm font-bold text-textMain">{(details as any).created_by_name || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaUpdatedBy}</p><p className="text-sm font-bold text-textMain">{(details as any).updated_by_name || t.notSet}</p></div>
            </div>
          )}
        </div>

        {/* ── C. Intake Channels ──────────────────────────────────────────────── */}
        <div className="border-b border-border px-8 py-6">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-5 flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
            {t.intakeChannels}
          </h3>

          {/* Top row: Option 1 + Option 2 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">

            {/* Option 1 — Forward to central email */}
            <div className={`rounded-2xl border-2 p-5 transition-all ${details.receive_cv_via_forwarding_email ? 'border-primary/20 bg-blue-50/40' : 'border-slate-200 bg-slate-50/60 opacity-60'}`}>
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <p className="text-xs font-black text-textMain">{t.option1Title}</p>
                  <p className="text-[11px] text-textMuted mt-0.5 leading-relaxed">{t.option1Desc}</p>
                </div>
                <button
                  disabled={togglingFwd}
                  onClick={() => handleIngestionToggle('receive_cv_via_forwarding_email', !details.receive_cv_via_forwarding_email)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.receive_cv_via_forwarding_email ? 'bg-primary' : 'bg-slate-300'} ${togglingFwd ? 'opacity-50' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.receive_cv_via_forwarding_email ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option1ForwardTo}</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 text-primary truncate">{details.forwarding_email || 'jobs@ai970.cloud'}</code>
                  <button
                    onClick={() => handleCopy(details.forwarding_email || 'jobs@ai970.cloud', setCopiedFwdEmail)}
                    className="shrink-0 px-3 py-1.5 bg-primary text-white text-[10px] font-black rounded-lg hover:bg-blue-700 transition-colors"
                  >
                    {copiedFwdEmail ? t.copied : t.copyBtn}
                  </button>
                </div>
              </div>
              {/* Job reference — nested inside Option 1 */}
              <div className="mt-3 bg-white/60 border border-blue-100 rounded-xl px-3 py-2.5 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-[9px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.jobRef}</p>
                  <code className="text-xs font-mono font-black text-textMain">{jobCode}</code>
                  <p className="text-[10px] text-textMuted mt-0.5 leading-tight">{t.jobRefHint}</p>
                </div>
                <button
                  onClick={() => handleCopy(jobCode, setCopiedJobRef)}
                  className="shrink-0 px-3 py-1.5 bg-white border border-border text-[10px] font-black text-textMain rounded-lg hover:bg-slate-50 transition-colors"
                >
                  {copiedJobRef ? t.copied : t.copyBtn}
                </button>
              </div>
              {/* Restrict sender domain — only shown when forwarding is enabled */}
              {details.receive_cv_via_forwarding_email && (
                <div className="mt-3 flex items-center justify-between bg-white/70 border border-blue-100 rounded-xl px-3 py-2">
                  <div>
                    <p className="text-[10px] font-black text-textMain">{t.restrictSender}</p>
                    <p className="text-[9px] text-textMuted">{t.restrictSenderHint}</p>
                  </div>
                  <button
                    onClick={() => handleIngestionToggle('restrict_forwarding_sender_to_tenant_email', !details.restrict_forwarding_sender_to_tenant_email)}
                    className={`shrink-0 relative w-9 h-5 rounded-full transition-colors focus:outline-none ${details.restrict_forwarding_sender_to_tenant_email ? 'bg-primary' : 'bg-slate-300'}`}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.restrict_forwarding_sender_to_tenant_email ? (isAr ? '-translate-x-4' : 'translate-x-4') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                  </button>
                </div>
              )}
              <p className={`text-[10px] font-black uppercase tracking-wider mt-3 ${details.receive_cv_via_forwarding_email ? 'text-success' : 'text-textMuted'}`}>
                {details.receive_cv_via_forwarding_email ? `● ${t.enabled}` : `○ ${t.disabled}`}
              </p>
            </div>

            {/* Option 2 — Dedicated alias */}
            <div className={`rounded-2xl border-2 p-5 transition-all ${details.receive_cv_via_platform_email ? 'border-success/30 bg-green-50/40' : 'border-slate-200 bg-slate-50/60 opacity-60'}`}>
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
                  onClick={() => handleIngestionToggle('receive_cv_via_platform_email', !details.receive_cv_via_platform_email)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.receive_cv_via_platform_email ? 'bg-success' : 'bg-slate-300'} ${togglingAlias ? 'opacity-50' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.receive_cv_via_platform_email ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option2AliasLabel}</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 text-success truncate">{details.platform_email || '—'}</code>
                  {details.platform_email && (
                    <button
                      onClick={() => handleCopy(details.platform_email!, setCopiedAlias)}
                      className="shrink-0 px-3 py-1.5 bg-success text-white text-[10px] font-black rounded-lg hover:bg-green-700 transition-colors"
                    >
                      {copiedAlias ? t.copied : t.copyBtn}
                    </button>
                  )}
                </div>
              </div>
              <p className={`text-[10px] font-black uppercase tracking-wider mt-3 ${details.receive_cv_via_platform_email ? 'text-success' : 'text-textMuted'}`}>
                {details.receive_cv_via_platform_email ? `● ${t.enabled}` : `○ ${t.disabled}`}
              </p>
            </div>
          </div>

          {/* Public Apply Link */}
          <div className="mb-4 rounded-2xl border border-violet-200 bg-violet-50/30 p-4">
            <div className="flex items-start gap-3">
              <div className="shrink-0 w-7 h-7 bg-violet-100 rounded-lg flex items-center justify-center mt-0.5">
                <svg className="w-3.5 h-3.5 text-violet-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-black text-violet-900 mb-0.5">{t.publicApplyLink}</p>
                <p className="text-[10px] text-violet-600/80 mb-2">{t.publicApplyHint}</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-[11px] font-mono bg-white border border-violet-200 rounded-lg px-3 py-1.5 text-violet-700 truncate">
                    {publicApplyUrl}
                  </code>
                  <button
                    onClick={() => handleCopy(publicApplyUrl, setCopiedApplyLink)}
                    className="shrink-0 px-3 py-1.5 bg-violet-600 text-white text-[10px] font-black rounded-lg hover:bg-violet-700 transition-colors"
                  >
                    {copiedApplyLink ? t.copied : t.copyBtn}
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* ── Confirmation Email & AI Comparison Settings ──────────────────── */}
          <div className="mt-4 bg-slate-50 rounded-2xl border border-border p-5 space-y-3">
            <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.confirmationSettings}</p>

            {/* Confirmation toggles */}
            {([
              ['send_confirmation_to_cv_email_for_upload',         t.confirmUpload,          'send_confirmation_to_cv_email_for_upload'],
              ['send_confirmation_to_cv_email_for_forwarding',     t.confirmFwdCvEmail,      'send_confirmation_to_cv_email_for_forwarding'],
              ['send_confirmation_to_sender_for_forwarding',       t.confirmFwdSenderEmail,  'send_confirmation_to_sender_for_forwarding'],
              ['send_confirmation_to_cv_email_for_platform_email', t.confirmPlatformEmail,   'send_confirmation_to_cv_email_for_platform_email'],
            ] as [keyof typeof details, string, 'send_confirmation_to_cv_email_for_upload' | 'send_confirmation_to_cv_email_for_forwarding' | 'send_confirmation_to_sender_for_forwarding' | 'send_confirmation_to_cv_email_for_platform_email'][]).map(([key, label, field]) => (
              <div key={field} className="flex items-center justify-between gap-3 py-1.5 border-b border-slate-200 last:border-0">
                <p className="text-xs text-textMain">{label}</p>
                <button
                  onClick={() => handleSettingsToggle(field, !details[key])}
                  className={`shrink-0 relative w-9 h-5 rounded-full transition-colors focus:outline-none ${details[key] ? 'bg-primary' : 'bg-slate-300'}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details[key] ? (isAr ? '-translate-x-4' : 'translate-x-4') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
            ))}

            {/* AI Comparison — super_admin can toggle; tenant admin sees read-only badge */}
            <div className="flex items-center justify-between gap-3 pt-2">
              <div>
                <p className="text-xs font-black text-textMain">{t.aiComparisonToggle}</p>
                <p className="text-[10px] text-textMuted">{t.aiComparisonHint}</p>
              </div>
              {isSuperAdmin ? (
                <button
                  onClick={() => handleSettingsToggle('enable_ai_comparison', !details.enable_ai_comparison)}
                  className={`shrink-0 relative w-9 h-5 rounded-full transition-colors focus:outline-none ${details.enable_ai_comparison ? 'bg-violet-500' : 'bg-slate-300'}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.enable_ai_comparison ? (isAr ? '-translate-x-4' : 'translate-x-4') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              ) : (
                <span className={`text-[10px] font-black uppercase px-2 py-1 rounded-full ${details.enable_ai_comparison ? 'bg-violet-100 text-violet-700' : 'bg-slate-100 text-slate-500'}`}>
                  {details.enable_ai_comparison ? t.enabled : t.disabled}
                </span>
              )}
            </div>
          </div>

          {/* Divider */}
          <div className="relative my-6">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center">
              <span className="px-3 bg-white text-[10px] font-black text-textMuted uppercase tracking-widest">
                {isAr ? 'أو ارفع يدوياً' : 'or upload manually'}
              </span>
            </div>
          </div>

          {/* ── Option 3: Manual CV Upload (internal/recruiter) ─────────────── */}
          <div className="rounded-2xl border-2 border-dashed border-indigo-200 bg-indigo-50/30 p-5">
            <div className="flex items-start gap-3 mb-4">
              <div className="shrink-0 w-8 h-8 bg-indigo-100 rounded-xl flex items-center justify-center">
                <svg className="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                </svg>
              </div>
              <div>
                <p className="text-xs font-black text-indigo-900">{t.manualUploadTitle}</p>
                <p className="text-[11px] text-indigo-600/80 mt-0.5 leading-relaxed">{t.manualUploadDesc}</p>
              </div>
            </div>

            {/* File input + upload button */}
            <div className="flex flex-wrap items-center gap-3 mb-4">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                className="hidden"
                id="cv-file-input"
                onChange={e => { if (e.target.files?.length) handleUpload(e.target.files); }}
              />
              <label
                htmlFor="cv-file-input"
                className={`inline-flex items-center gap-2 px-4 py-2 bg-white border border-indigo-200 text-[11px] font-black text-indigo-700 rounded-xl transition-colors ${uploading || cvsScoringInProgress || !!deletingCVId ? 'opacity-50 cursor-not-allowed pointer-events-none' : 'cursor-pointer hover:bg-indigo-50'}`}
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
                {t.chooseFiles}
              </label>
              <button
                disabled={uploading || cvsScoringInProgress || !!deletingCVId}
                onClick={() => fileInputRef.current?.click()}
                className={`inline-flex items-center gap-2 px-5 py-2 text-[11px] font-black rounded-xl transition-colors ${uploading || cvsScoringInProgress || !!deletingCVId ? 'bg-indigo-300 text-white cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}
              >
                {uploading ? (
                  <>
                    <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                    {t.uploadingBtn}
                  </>
                ) : (
                  <>
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" /></svg>
                    {t.uploadBtn}
                  </>
                )}
              </button>
            </div>

            {/* Uploaded CVs — all uploads shown; in-flight show live stage, completed show result */}
            {loadingUploads && uploadedCVs.length === 0 ? (
              <div className="flex items-center gap-2 text-[11px] text-textMuted py-2">
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                Loading...
              </div>
            ) : cvsDisplay.length > 0 ? (
              <div className="mt-2">
                {/* Header row */}
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[10px] font-black text-indigo-800 uppercase tracking-wider">
                    {t.uploadedCVsTitle} ({cvsDisplay.length})
                  </p>
                  {/* Progress label — only while scoring is running */}
                  {cvsScoringInProgress && cvsTotal > 0 && (
                    <p className="text-[10px] font-bold text-indigo-600">
                      {t.progressLabel
                        .replace('{scored}', String(cvsScoredCount))
                        .replace('{total}', String(cvsTotal))
                        .replace('{pct}', String(progressPct))}
                    </p>
                  )}
                </div>

                {/* Progress bar — only while scoring is running */}
                {cvsScoringInProgress && cvsTotal > 0 && (
                  <div className="w-full bg-indigo-100 h-1.5 rounded-full mb-3 overflow-hidden">
                    <div
                      className="bg-indigo-500 h-full rounded-full transition-all duration-500"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                )}

                {/* CV rows — all uploaded CVs with stage-aware status badges */}
                <div className="space-y-1.5 max-h-56 overflow-y-auto">
                  {cvsDisplay.map(cv => {
                    const st = cvStatusDisplay(cv);
                    const isDeleting = deletingCVId === cv.application_id;
                    const canDelete = cv.processing_status === 'pending' && !cvsScoringInProgress;
                    const isDone = cv.processing_status === 'scored' || cv.processing_status === 'low_match';
                    const hasExitReason = cv.evaluation_exit_reason && (
                      cv.processing_status === 'low_match' ||
                      (cv.processing_status === 'scored' && cv.evaluation_stage != null && cv.evaluation_stage < 3)
                    );
                    return (
                      <div key={cv.application_id} className={`flex flex-col gap-1 rounded-xl border px-3 py-2 transition-colors ${isDone ? 'bg-slate-50/60 border-slate-100' : 'bg-white border-indigo-100'}`}>
                        <div className="flex items-center gap-3">
                          <svg className={`w-3.5 h-3.5 shrink-0 ${isDone ? 'text-slate-300' : 'text-indigo-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                          <p className={`flex-1 text-[11px] font-bold truncate min-w-0 ${isDone ? 'text-textMuted' : 'text-textMain'}`}>
                            {cv.original_filename || cv.candidate_name}
                          </p>
                          <span className={`shrink-0 inline-flex items-center gap-1 text-[9px] font-black border rounded-full px-2 py-0.5 ${st.color}`}>
                            {st.spin && <svg className="w-2.5 h-2.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
                            {st.label}
                          </span>
                          {canDelete && (
                            <button
                              disabled={!!deletingCVId}
                              onClick={() => handleDeleteCV(cv.application_id)}
                              title={isDeleting ? t.deletingCV : t.deleteCV}
                              className={`shrink-0 p-1 rounded-lg transition-colors ${deletingCVId ? 'opacity-40 cursor-not-allowed' : 'text-red-400 hover:text-red-600 hover:bg-red-50'}`}
                            >
                              {isDeleting
                                ? <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                                : <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>}
                            </button>
                          )}
                        </div>
                        {/* Exit reason for early-rejected CVs */}
                        {hasExitReason && (
                          <p className="text-[9px] text-slate-400 leading-tight pl-6 truncate" title={cv.evaluation_exit_reason!}>
                            <span className="font-black uppercase">{t.exitReason}</span> {cv.evaluation_exit_reason}
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Score button row — reset-stuck appears when there are stuck CVs */}
                <div className="mt-3 flex items-center justify-between gap-2 flex-wrap">
                  {queueStatus?.has_stuck && (
                    <button
                      disabled={resettingStuck}
                      onClick={handleResetStuck}
                      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-black rounded-lg border transition-colors ${
                        resettingStuck
                          ? 'border-red-200 bg-red-50 text-red-400 cursor-not-allowed'
                          : 'border-red-300 bg-red-50 text-red-600 hover:bg-red-100'
                      }`}
                    >
                      {resettingStuck ? (
                        <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                      ) : (
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                      )}
                      {resettingStuck ? t.resettingStuck : t.resetStuck}
                    </button>
                  )}
                  <div className="flex-1" />
                  <button
                    disabled={!cvsHasPending || cvsScoringInProgress || !!deletingCVId}
                    onClick={handleScorePending}
                    className={`inline-flex items-center gap-2 px-5 py-2 text-[11px] font-black rounded-xl transition-colors ${
                      !cvsHasPending || cvsScoringInProgress || !!deletingCVId
                        ? 'bg-slate-200 text-textMuted cursor-not-allowed'
                        : 'bg-indigo-600 text-white hover:bg-indigo-700'
                    }`}
                  >
                    {cvsScoringInProgress ? (
                      <>
                        <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                        {t.scoringBtnLabel}
                      </>
                    ) : (
                      <>
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
                        {t.scoreBtnLabel}
                      </>
                    )}
                  </button>
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-indigo-400 italic">{t.noUploads}</p>
            )}
          </div>
        </div>

      </section>

      {/* ── F. Applications Summary ─────────────────────────────────────── */}
      <section className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-5 border-b border-border flex items-center justify-between">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            {t.appSummary}
          </h3>
          <button
            onClick={() => onViewApplications(details.job_id, 'all')}
            className="flex items-center gap-1.5 text-[10px] font-black text-primary hover:text-primaryDark uppercase tracking-widest transition-colors"
          >
            {t.viewApplications}
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </div>
        <div className="p-6 grid grid-cols-2 lg:grid-cols-4 gap-4">
        {kpiValues.map((kpi, idx) => (
          <button
            key={idx}
            onClick={() => onViewApplications(details.job_id, kpi.filter)}
            className="bg-slate-50 p-5 rounded-2xl border border-border flex flex-col items-center justify-center text-center cursor-pointer hover:border-primary/50 hover:bg-white hover:shadow-md transition-all group"
          >
            <span className={`text-2xl font-black ${kpi.color} group-hover:scale-110 transition-transform`}>{kpi.value}</span>
            <span className="text-[10px] font-black text-textMuted uppercase tracking-widest mt-1 group-hover:text-primary transition-colors">{t.kpiLabels[idx]}</span>
          </button>
        ))}
        </div>
      </section>

      {/* Duplicate submissions */}
      {duplicateLogs.length > 0 && (
        <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-border flex items-center gap-2">
            <svg className="w-4 h-4 text-orange-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <h3 className="text-sm font-bold text-textMain uppercase tracking-widest">
              Duplicate Submissions <span className="ml-1 px-2 py-0.5 bg-orange-100 text-orange-700 rounded-full text-xs font-black">{duplicateLogs.length}</span>
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm divide-y divide-gray-100">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Applicant</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Detection</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Score</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Received</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Original</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Duplicate CV</th>
                  <th className="px-4 py-3 text-left text-[10px] font-black text-textMuted uppercase tracking-widest">Original CV</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {duplicateLogs.map((log) => {
                  const reasonLabel = log.duplicate_reason === 'high_content_similarity' ? 'Content Similarity'
                    : log.duplicate_reason === 'identity_match' ? 'Identity Match'
                    : 'Exact Match';
                  const reasonStyle = log.duplicate_reason === 'high_content_similarity'
                    ? 'bg-orange-100 text-orange-700'
                    : log.duplicate_reason === 'identity_match'
                    ? 'bg-yellow-100 text-yellow-700'
                    : 'bg-teal-100 text-teal-700';
                  const score = log.duplicate_similarity_score != null
                    ? `${Number(log.duplicate_similarity_score).toFixed(1)}%`
                    : '100%';
                  return (
                    <tr key={log.log_id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5 mb-0.5">
                          <p className="font-semibold text-textMain text-sm">{log.duplicate_name || '—'}</p>
                          {log.source === 'manual_upload' && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-blue-100 text-blue-700">Manual</span>
                          )}
                        </div>
                        <p className="text-xs text-textMuted">{log.duplicate_email || (log.submitted_by_email ? `Uploaded by: ${log.submitted_by_name || log.submitted_by_email}` : '—')}</p>
                        {log.raw_filename && <p className="text-xs text-textMuted truncate max-w-[180px]">{log.raw_filename}</p>}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider ${reasonStyle}`}>
                          {reasonLabel}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm font-bold text-textMain">{score}</span>
                      </td>
                      <td className="px-4 py-3 text-xs text-textMuted whitespace-nowrap">
                        {log.received_at ? new Date(log.received_at).toLocaleString() : '—'}
                      </td>
                      <td className="px-4 py-3">
                        {log.original_application_id ? (
                          <button
                            onClick={() => onOpenApplication(jobId, log.original_application_id)}
                            className="text-left group"
                          >
                            <p className="text-sm font-semibold text-primary group-hover:underline">
                              {log.original_candidate_name || log.original_application_id.slice(0, 8) + '…'}
                            </p>
                            {log.original_applied_at && (
                              <p className="text-xs text-textMuted">
                                {new Date(log.original_applied_at).toLocaleDateString()}
                              </p>
                            )}
                          </button>
                        ) : <span className="text-textMuted">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        {log.has_duplicate_cv ? (
                          <button
                            onClick={() => handleViewCV(
                              `${WEBHOOK_CONFIG.DUPLICATE_CV_BASE_URL}/${jobId}/duplicate-logs/${log.log_id}/cv`,
                              log.duplicate_original_filename || log.raw_filename || 'duplicate-cv'
                            )}
                            className="inline-flex items-center gap-1 px-3 py-1.5 bg-slate-100 hover:bg-primary hover:text-white text-textMain text-xs font-semibold rounded-lg transition-colors"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                            </svg>
                            View Duplicate CV
                          </button>
                        ) : (
                          <span
                            title="File not stored — only the hash was recorded for this entry"
                            className="inline-flex items-center gap-1 px-3 py-1.5 bg-slate-50 text-slate-400 text-xs font-semibold rounded-lg cursor-not-allowed border border-slate-200"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                            </svg>
                            Not available
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {log.original_application_id && log.original_cv_filename ? (
                          <button
                            onClick={() => handleViewCV(`${WEBHOOK_CONFIG.CV_DOWNLOAD_BASE_URL}/${log.original_application_id}/cv`, log.original_cv_filename)}
                            className="inline-flex items-center gap-1 px-3 py-1.5 bg-slate-100 hover:bg-primary hover:text-white text-textMain text-xs font-semibold rounded-lg transition-colors"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                            </svg>
                            View Original CV
                          </button>
                        ) : (
                          <span className="text-textMuted text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── AI Criteria Extraction Status Banner ───────────────────────────── */}
      {details.criteria_extraction_status && details.criteria_extraction_status !== 'completed' && (
        <div className={`rounded-2xl border p-4 flex items-center justify-between gap-4 ${
          details.criteria_extraction_status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'
        }`}>
          <div className="flex items-center gap-3">
            {details.criteria_extraction_status === 'failed' ? (
              <svg className="w-5 h-5 text-error shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            ) : (
              <svg className="animate-spin w-5 h-5 text-amber-500 shrink-0" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            <div>
              <p className="text-sm font-bold text-textMain">
                {details.criteria_extraction_status === 'failed'
                  ? t.criteriaFailed
                  : details.criteria_extraction_status === 'processing'
                  ? t.criteriaProcessing
                  : t.criteriaPending}
              </p>
              {details.criteria_extraction_status === 'failed' && details.criteria_extraction_error && (
                <p className="text-xs text-error/80 mt-0.5">{details.criteria_extraction_error}</p>
              )}
            </div>
          </div>
          {details.criteria_extraction_status === 'failed' && (
            <button onClick={handleRetryExtraction} className="shrink-0 px-4 py-1.5 bg-error text-white text-xs font-bold rounded-lg hover:bg-red-700 transition-colors">
              {t.retryExtraction}
            </button>
          )}
        </div>
      )}

      {/* ── Main Grid: Criteria + Eval Logic ───────────────────────────────── */}
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
                  {(cat.items || []).length > 0
                    ? (cat.items || []).map((item, i) => (
                        <li key={i} className="text-[11px] font-bold text-textMain leading-snug">• {item}</li>
                      ))
                    : <li className="text-[10px] text-textMuted italic">{t.noData}</li>}
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
              {canEdit && !editingWeights && analysis?.scoring_weights && (
                <button onClick={handleEditWeights} className="text-[10px] font-black text-indigo-600 hover:text-indigo-800 uppercase tracking-widest transition-colors">
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

                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button onClick={handleNormalizeWeights} className="px-2 py-2 text-[10px] font-black text-indigo-600 border border-indigo-200 rounded-xl hover:bg-indigo-50 uppercase tracking-widest transition-colors leading-tight">
                    {t.normalizeWeights}
                  </button>
                  <button onClick={handleResetWeights} className="px-2 py-2 text-[10px] font-black text-textMuted border border-border rounded-xl hover:bg-slate-50 uppercase tracking-widest transition-colors leading-tight">
                    {t.resetAiWeights}
                  </button>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button onClick={() => setEditingWeights(false)} className="px-3 py-2.5 text-xs font-bold text-textMuted border border-border rounded-xl hover:bg-slate-50 transition-colors">
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

      {/* ── Job Description ────────────────────────────────────────────────── */}
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
