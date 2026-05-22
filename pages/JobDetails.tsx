
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
    statusL1Screen: 'Pre-screening...',
    statusL2Screen: 'AI scoring...',
    statusL3Score: 'AI scoring...',
    statusScored: 'Scored',
    statusLowMatch: 'Rejected — L1',
    statusRejectedL2: 'Rejected — L1',
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
    viewApplications: 'View Applications',
    editCriteria: 'Edit Criteria',
    saveCriteria: 'Save Criteria',
    savingCriteria: 'Saving...',
    criteriaSaved: 'Evaluation criteria updated',
    criteriaWarningTitle: 'Update Evaluation Logic?',
    criteriaWarningBody: 'Some applications may have already been evaluated using the previous evaluation logic. Updating will not automatically re-evaluate existing applications.',
    criteriaConfirm: 'Confirm Update',
    criteriaListHint: '(one per line)',
    viewOriginalCriteria: 'View Original AI Criteria',
    hideOriginalCriteria: 'Hide Original',
    modifiedBadge: 'Modified',
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
    appControls: 'Application Controls',
    maxApplications: 'Maximum Applications',
    maxApplicationsHint: 'Limit the number of valid submissions accepted. Leave empty for unlimited.',
    autoClose: 'Auto-close when limit reached',
    autoCloseHint: 'Automatically set job to Closed when the limit is hit.',
    validCount: 'Valid received',
    unlimited: 'Unlimited',
    intakeOpen: 'Intake Open',
    intakeClosed: 'Intake Closed',
    editControls: 'Edit',
    saveControls: 'Save',
    savingControls: 'Saving...',
    controlsSaved: 'Application controls updated',
    duplicateCount: 'Duplicate Submissions',
    viewDuplicates: 'View Duplicate Submissions',
    noDuplicates: 'No duplicate submissions',
    dupSubmissionsTitle: 'Duplicate Submissions',
    dupSubmissionsHint: 'These CVs were rejected before scoring because they matched an existing submission for this job (same file hash or near-identical content). They are not scored applications.',
    dupColName: 'Duplicate Candidate',
    dupColEmail: 'Email',
    dupColFile: 'File',
    dupColReceived: 'Received',
    dupColReason: 'Reason',
    dupColSimilarity: 'Similarity',
    dupColOriginal: 'Original Submission',
    dupDownloadCV: 'Download',
    dupShowAll: 'Show all',
    dupShowLess: 'Show less',
    refresh: 'Refresh',
    tabPublicLink: 'Public Link',
    tabEmailAlias: 'Email Alias',
    tabEmailFwd: 'Email Forwarding',
    tabManualUpload: 'Manual Upload',
    badgeExternal: 'External',
    badgeInternal: 'Internal',
    batchComplete: 'Batch complete — {count} CVs analyzed and added to applications.',
    scoringInProgress: 'Scoring in progress',
    scoringProgressDetail: '{count} CV(s) pending evaluation',
    progressQueued: '{n} queued',
    progressProcessing: '{n} processing',
    progressScored: '{n} scored',
    progressFailed: '{n} failed',
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
    statusL1Screen: 'الفرز الأولي...',
    statusL2Screen: 'التقييم بالذكاء...',
    statusL3Score: 'التقييم بالذكاء...',
    statusScored: 'تم التقييم',
    statusLowMatch: 'مرفوض — م1',
    statusRejectedL2: 'مرفوض — م1',
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
    viewApplications: 'عرض الطلبات',
    editCriteria: 'تعديل المعايير',
    saveCriteria: 'حفظ المعايير',
    savingCriteria: 'جارٍ الحفظ...',
    criteriaSaved: 'تم تحديث معايير التقييم',
    criteriaWarningTitle: 'تحديث منطق التقييم؟',
    criteriaWarningBody: 'قد تكون بعض الطلبات قد تم تقييمها بناءً على منطق التقييم السابق. لن يؤدي التحديث إلى إعادة تقييم الطلبات الموجودة تلقائياً.',
    criteriaConfirm: 'تأكيد التحديث',
    criteriaListHint: '(سطر لكل عنصر)',
    viewOriginalCriteria: 'عرض المعايير الأصلية',
    hideOriginalCriteria: 'إخفاء الأصلية',
    modifiedBadge: 'معدَّل',
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
    appControls: 'ضوابط الاستقبال',
    maxApplications: 'الحد الأقصى للطلبات',
    maxApplicationsHint: 'تحديد عدد الطلبات المقبولة. اتركه فارغاً لعدم التحديد.',
    autoClose: 'الإغلاق التلقائي عند بلوغ الحد',
    autoCloseHint: 'تغيير حالة الوظيفة إلى مغلقة تلقائياً عند الوصول للحد.',
    validCount: 'المستلمة الصالحة',
    unlimited: 'غير محدود',
    intakeOpen: 'الاستقبال مفتوح',
    intakeClosed: 'الاستقبال مغلق',
    editControls: 'تعديل',
    saveControls: 'حفظ',
    savingControls: 'جارٍ الحفظ...',
    controlsSaved: 'تم تحديث ضوابط الاستقبال',
    duplicateCount: 'طلبات مكررة',
    viewDuplicates: 'عرض الطلبات المكررة',
    noDuplicates: 'لا توجد طلبات مكررة',
    dupSubmissionsTitle: 'الطلبات المكررة',
    dupSubmissionsHint: 'هذه السير الذاتية رُفضت قبل التقييم لأنها تطابقت مع طلب موجود لهذه الوظيفة (نفس الملف أو محتوى متطابق تقريبًا). لم يتم تقييمها.',
    dupColName: 'المرشح المكرر',
    dupColEmail: 'البريد الإلكتروني',
    dupColFile: 'الملف',
    dupColReceived: 'وُصل في',
    dupColReason: 'السبب',
    dupColSimilarity: 'التشابه',
    dupColOriginal: 'الطلب الأصلي',
    dupDownloadCV: 'تحميل',
    dupShowAll: 'عرض الكل',
    dupShowLess: 'عرض أقل',
    refresh: 'تحديث',
    tabPublicLink: 'الرابط العام',
    tabEmailAlias: 'بريد مخصص',
    tabEmailFwd: 'إعادة توجيه',
    tabManualUpload: 'رفع يدوي',
    badgeExternal: 'خارجي',
    badgeInternal: 'داخلي',
    batchComplete: 'اكتملت الدفعة — تم تحليل {count} سيرة ذاتية وإضافتها للطلبات.',
    scoringInProgress: 'جاري التقييم',
    scoringProgressDetail: '{count} سيرة ذاتية قيد التقييم',
    progressQueued: '{n} في الطابور',
    progressProcessing: '{n} قيد المعالجة',
    progressScored: '{n} تم تقييمه',
    progressFailed: '{n} فشل',
  },
};

export const JobDetails: React.FC<JobDetailsProps> = ({ jobId, auth, onBack, onViewApplications, onOpenApplication, addToast }) => {
  const { lang, isAr } = useLanguage();
  const t = T[lang];
  const isSuperAdmin = (auth.user?.role || '').toLowerCase() === 'super_admin';
  const role = (auth.user?.role || '').toLowerCase();
  const canEdit = role === 'admin' || role === 'hr_manager';
  const tenantType = auth.user?.tenant_type ?? '';
  const isAgencyTenant = tenantType === 'agency' || tenantType === 'individual_recruiter';

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
  const [refreshing, setRefreshing] = useState(false);

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

  // Application controls (max_applications, auto_close)
  const [editingControls, setEditingControls] = useState(false);
  const [draftControls, setDraftControls] = useState<{ max_applications: string; auto_close: boolean }>({ max_applications: '', auto_close: true });
  const [savingControls, setSavingControls] = useState(false);

  // Criteria content editing
  const [editingCriteria, setEditingCriteria] = useState(false);
  const [draftCriteria, setDraftCriteria] = useState<Record<string, string>>({});
  const [savingCriteria, setSavingCriteria] = useState(false);
  const [showCriteriaWarning, setShowCriteriaWarning] = useState(false);
  const [showOriginalCriteria, setShowOriginalCriteria] = useState(false);
  const originalAiCriteriaRef = useRef<any>(null);

  // Duplicate submissions section ref + display state
  const duplicateSubmissionsRef = useRef<HTMLDivElement>(null);
  const [showAllDuplicates, setShowAllDuplicates] = useState(false);
  const [dupSectionExpanded, setDupSectionExpanded] = useState(false);

  // Intake channel tabs (Public Link default)
  const [activeIntakeTab, setActiveIntakeTab] = useState<'public-link' | 'email-alias' | 'email-forwarding' | 'manual-upload'>('public-link');

  // Session-scoped upload tracking — starts empty each page load
  const sessionIdsRef = useRef<Set<string>>(new Set());
  const [sessionUploadIds, _setSessionUploadIds] = useState<Set<string>>(new Set());
  const setSessionUploadIds = useCallback((s: Set<string>) => {
    sessionIdsRef.current = s;
    _setSessionUploadIds(s);
  }, []);
  const [batchCompleteCount, setBatchCompleteCount] = useState<number | null>(null);

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
          setDetails({ ...payload.details, analysis_json: payload.analysis, original_analysis_json: payload.original_analysis });
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

  // Capture original AI criteria once (first load)
  useEffect(() => {
    if (details && !originalAiCriteriaRef.current) {
      const orig = (details as any).original_analysis_json;
      originalAiCriteriaRef.current = orig ?? details.analysis_json ?? null;
    }
  }, [details]);

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
          setDetails({ ...payload.details, analysis_json: payload.analysis, original_analysis_json: payload.original_analysis });
        }
      } catch { /* ignore transient polling errors */ }
      setCriteriaPolltick(t => t + 1); // always re-trigger effect
    }, 5000);
    return () => clearTimeout(timer);
  }, [criteriaPolltick, details?.criteria_extraction_status, jobId, auth.token]);

  // ── Fetch uploaded CVs ──────────────────────────────────────────────────────
  const fetchUploadedCVs = useCallback(async (): Promise<UploadedCV[]> => {
    if (!jobId) return [];
    setLoadingUploads(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.UPLOADED_CVS_URL,
        { job_id: jobId },
        auth.token!
      );
      const cvs: UploadedCV[] = Array.isArray(data) ? data : [];
      setUploadedCVs(cvs);
      return cvs;
    } catch { return []; } finally {
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

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([fetchJobDetails(), fetchQueueStatus(), fetchDuplicateLogs()]);
    setRefreshing(false);
  }, [fetchJobDetails, fetchQueueStatus, fetchDuplicateLogs]);

  const handleDownloadDupCV = useCallback(async (logId: string, filename: string) => {
    try {
      const url = `${WEBHOOK_CONFIG.DUPLICATE_LOGS_BASE_URL}/${jobId}/duplicate-logs/${logId}/cv`;
      const resp = await fetch(url, { headers: { Authorization: `Bearer ${auth.token!}` } });
      if (!resp.ok) throw new Error('CV not available');
      const blob = await resp.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = filename || 'cv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(objUrl), 10000);
    } catch {
      addToast('Could not download duplicate CV file.', 'error');
    }
  }, [jobId, auth.token, addToast]);

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
      fetchJobDetails();
      fetchUploadedCVs();
      fetchDuplicateLogs();
      // Show success banner if this session had uploads
      if (sessionIdsRef.current.size > 0) {
        const count = queueStatus?.completed ?? sessionIdsRef.current.size;
        setBatchCompleteCount(count);
        setTimeout(() => {
          setBatchCompleteCount(null);
          // Keep sessionUploadIds so the batch panel stays visible
        }, 6000);
      }
    }
    prevIsProcessingRef.current = isNowProcessing;
  }, [queueStatus?.is_processing, fetchJobDetails, fetchUploadedCVs, fetchDuplicateLogs, setSessionUploadIds]);

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
    const beforeIds = new Set(uploadedCVs.map(cv => cv.application_id));
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
    const newCVs = await fetchUploadedCVs();
    const newIds = newCVs
      .filter(cv => !beforeIds.has(cv.application_id))
      .map(cv => cv.application_id);
    if (newIds.length > 0) {
      setSessionUploadIds(new Set([...sessionIdsRef.current, ...newIds]));
    }
  }, [jobId, auth.token, addToast, fetchUploadedCVs, uploadedCVs, setSessionUploadIds]);

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

  const handleControlsEdit = useCallback(() => {
    if (!details) return;
    setDraftControls({
      max_applications: (details as any).max_applications != null ? String((details as any).max_applications) : '',
      auto_close: (details as any).auto_close_when_limit_reached ?? true,
    });
    setEditingControls(true);
  }, [details]);

  const handleControlsSave = useCallback(async () => {
    if (!details) return;
    setSavingControls(true);
    try {
      const maxVal = draftControls.max_applications.trim();
      await apiService.put(
        `${WEBHOOK_CONFIG.UPDATE_JOB_URL}/${(details as any).job_id}`,
        {
          max_applications: maxVal === '' ? 0 : Math.max(1, parseInt(maxVal) || 1),
          auto_close_when_limit_reached: draftControls.auto_close,
        },
        auth.token!
      );
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL, { job_id: (details as any).job_id }, auth.token!);
      if (data) {
        const p = Array.isArray(data) ? data[0] : data;
        setDetails({ ...p.details, analysis_json: p.analysis, original_analysis_json: p.original_analysis });
      }
      setEditingControls(false);
      addToast(t.controlsSaved, 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to update application controls.', 'error');
    } finally {
      setSavingControls(false);
    }
  }, [details, draftControls, auth.token, addToast, t.controlsSaved]);

  const handleCriteriaEdit = useCallback(() => {
    if (!details) return;
    const a = details.analysis_json ?? {};
    setDraftCriteria({
      required_skills:    (a.skills?.required    || []).join('\n'),
      preferred_skills:   (a.skills?.preferred   || []).join('\n'),
      minimum_years:      String(a.experience?.minimum_years ?? ''),
      relevant_roles:     (a.experience?.relevant_roles || []).join('\n'),
      minimum_education:  a.education?.minimum_level || '',
      fields_of_study:    (a.education?.fields_of_study || []).join('\n'),
      certifications:     (a.certifications      || []).join('\n'),
      domain_knowledge:   (a.domain_knowledge    || []).join('\n'),
      other_requirements: (a.other_requirements  || []).join('\n'),
    });
    setEditingCriteria(true);
    setShowCriteriaWarning(false);
  }, [details]);

  const handleCriteriaSaveConfirmed = useCallback(async () => {
    if (!details) return;
    setSavingCriteria(true);
    setShowCriteriaWarning(false);
    const splitLines = (s: string) => s.split('\n').map(x => x.trim()).filter(Boolean);
    try {
      await apiService.put(
        `${WEBHOOK_CONFIG.UPDATE_CRITERIA_CONTENT_URL}/${(details as any).job_id}/criteria/content`,
        {
          required_skills:    splitLines(draftCriteria.required_skills),
          preferred_skills:   splitLines(draftCriteria.preferred_skills),
          minimum_years:      parseInt(draftCriteria.minimum_years) || 0,
          relevant_roles:     splitLines(draftCriteria.relevant_roles),
          minimum_education:  draftCriteria.minimum_education.trim(),
          fields_of_study:    splitLines(draftCriteria.fields_of_study),
          certifications:     splitLines(draftCriteria.certifications),
          domain_knowledge:   splitLines(draftCriteria.domain_knowledge),
          other_requirements: splitLines(draftCriteria.other_requirements),
        },
        auth.token!
      );
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL, { job_id: (details as any).job_id }, auth.token!);
      if (data) {
        const p = Array.isArray(data) ? data[0] : data;
        setDetails({ ...p.details, analysis_json: p.analysis, original_analysis_json: p.original_analysis });
      }
      setEditingCriteria(false);
      addToast(t.criteriaSaved, 'success');
    } catch (err: any) {
      addToast(err?.message || 'Failed to update criteria.', 'error');
    } finally {
      setSavingCriteria(false);
    }
  }, [details, draftCriteria, auth.token, addToast, t.criteriaSaved]);

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

  const canEditIntake = canEdit;

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
  const isModified = (getter: (a: any) => any): boolean => {
    const orig = originalAiCriteriaRef.current;
    if (!orig || !analysis) return false;
    try { return JSON.stringify(getter(analysis)) !== JSON.stringify(getter(orig)); } catch { return false; }
  };
  const modifiedBadge = <span className="ml-2 text-[9px] font-black uppercase px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 align-middle">{t.modifiedBadge}</span>;
  const weightTotal = editingWeights ? Object.values(draftWeights).reduce((s, v) => s + v, 0) : 0;

  const jobCode = details.job_code || details.job_id;
  // Session-only display: only CVs uploaded in this browser session
  const cvsDisplay = uploadedCVs.filter(cv => sessionUploadIds.has(cv.application_id));
  // Active queue scoped to session CVs
  const cvsInQueue = cvsDisplay.filter(cv =>
    cv.processing_status === 'pending' ||
    cv.processing_status === 'queued' ||
    cv.processing_status === 'processing'
  );
  const cvsScoredCount = queueStatus?.completed ?? cvsDisplay.filter(cv => cv.processing_status === 'scored' || cv.processing_status === 'low_match').length;
  const cvsTotal = queueStatus?.total ?? cvsDisplay.length;
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
        // stage=1: gatekeeper passed, L3 LLM running; stage=0/null: gatekeeper running
        if (cv.evaluation_stage === 1) return { label: t.statusL3Score, color: 'text-violet-600 bg-violet-50 border-violet-200', spin: true };
        return                                { label: t.statusL1Screen, color: 'text-sky-600 bg-sky-50 border-sky-200',          spin: true };
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

  // ── Mini donut chart ───────────────────────────────────────────────────────
  const qualifiedCount = details.applications_qualified || 0;
  const partialCount   = details.applications_partial   || 0;
  const rejectedCount  = details.applications_rejected  || 0;
  const chartTotal     = qualifiedCount + partialCount + rejectedCount;
  const dupCount       = duplicateLogs.length;

  const renderDonut = () => {
    const r = 32; const circ = 2 * Math.PI * r;
    if (chartTotal === 0) return (
      <svg width="80" height="80" viewBox="0 0 80 80">
        <circle cx="40" cy="40" r={r} fill="none" stroke="#e2e8f0" strokeWidth="12" />
        <text x="40" y="45" textAnchor="middle" fill="#94a3b8" fontSize="10" fontWeight="700">—</text>
      </svg>
    );
    const segs = [
      { count: qualifiedCount, color: '#16a34a' },
      { count: partialCount,   color: '#d97706' },
      { count: rejectedCount,  color: '#dc2626' },
    ];
    let cum = 0;
    return (
      <svg width="80" height="80" viewBox="0 0 80 80">
        <g transform="rotate(-90 40 40)">
          {segs.map((seg, i) => {
            const len = (seg.count / chartTotal) * circ;
            const off = -cum;
            cum += len;
            return <circle key={i} cx="40" cy="40" r={r} fill="none" stroke={seg.color} strokeWidth="12" strokeDasharray={`${len} ${circ}`} strokeDashoffset={off} />;
          })}
        </g>
        <text x="40" y="45" textAnchor="middle" fill="#1e293b" fontSize="14" fontWeight="800">{chartTotal}</text>
      </svg>
    );
  };

  return (
    <div className="max-w-7xl mx-auto pb-16 animate-fade-in">

      {/* A. Sticky compact header ──────────────────────────────────────────── */}
      <div className="sticky top-0 z-20 bg-white/95 backdrop-blur-sm border-b border-border mb-6 px-6 py-3 flex flex-wrap items-center justify-between gap-3 shadow-sm -mx-6 md:-mx-8 px-6 md:px-8">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={onBack} className="shrink-0 p-1.5 hover:bg-slate-100 rounded-lg transition-colors text-textMuted hover:text-primary">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" /></svg>
          </button>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-base font-black text-textMain truncate">{details.job_title}</h1>
              <span className={`shrink-0 px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${statusColor}`}>{details.job_status}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <p className="text-[10px] text-textMuted font-mono">{details.job_code}</p>
              {((details as any).client_org_name || details.job_client) && (
                <span className="text-[10px] text-textMuted">·</span>
              )}
              {((details as any).client_org_name != null) && (
                <span className={`text-[10px] font-bold ${(details as any).client_org_name ? 'text-indigo-600' : 'text-slate-400'}`}>
                  {(details as any).client_org_name || 'General'}
                </span>
              )}
              {(details as any).client_org_name == null && details.job_client && (
                <span className="text-[10px] text-textMuted">{details.job_client}</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border text-textMuted text-[10px] font-black rounded-lg hover:bg-slate-50 transition-colors"
          >
            <svg className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            {t.refresh}
          </button>
          <button onClick={() => handleCopy(publicApplyUrl, setCopiedApplyLink)} className="flex items-center gap-1.5 px-3 py-1.5 border border-violet-200 bg-violet-50 text-violet-700 text-[10px] font-black rounded-lg hover:bg-violet-100 transition-colors">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
            {copiedApplyLink ? t.copied : t.copyBtn}
          </button>
          <button onClick={() => onViewApplications(details.job_id, 'all')} className="flex items-center gap-1.5 px-4 py-1.5 bg-primary text-white text-[10px] font-black rounded-lg hover:bg-primaryDark transition-colors shadow-sm">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
            {t.viewApplications}
          </button>
        </div>
      </div>

      {/* B. Applications insight strip ─────────────────────────────────────── */}
      <div className="mb-6 bg-white rounded-2xl border border-border shadow-sm">
        <div className="px-5 py-4 flex flex-wrap items-center gap-4">
          <div className="shrink-0">{renderDonut()}</div>
          <div className="flex-1 grid grid-cols-2 sm:grid-cols-4 gap-3 min-w-0">
            {kpiValues.map((kpi, idx) => (
              <button key={idx} onClick={() => onViewApplications(details.job_id, kpi.filter)}
                className="flex flex-col items-center py-3 px-2 rounded-xl bg-slate-50 hover:bg-slate-100 border border-transparent hover:border-border transition-all group cursor-pointer">
                <span className={`text-xl font-black ${kpi.color} group-hover:scale-105 transition-transform`}>{kpi.value ?? 0}</span>
                <span className="text-[9px] font-black text-textMuted uppercase tracking-wider mt-0.5 group-hover:text-primary transition-colors">{t.kpiLabels[idx]}</span>
              </button>
            ))}
          </div>
          {dupCount > 0 ? (
            <button
              onClick={() => {
                setDupSectionExpanded(true);
                setTimeout(() => {
                  duplicateSubmissionsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 50);
              }}
              className="shrink-0 flex items-center gap-2 px-3 py-2.5 rounded-xl bg-orange-50 border border-orange-200 hover:bg-orange-100 transition-colors group">
              <svg className="w-4 h-4 text-orange-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
              <div className="text-left">
                <p className="text-[11px] font-black text-orange-800">{dupCount} {t.duplicateCount}</p>
                <p className="text-[9px] text-orange-600 group-hover:underline">{t.viewDuplicates}</p>
              </div>
            </button>
          ) : !loadingDupLogs ? (
            <div className="shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-50 border border-slate-200">
              <span className="w-1.5 h-1.5 rounded-full bg-slate-300" />
              <p className="text-[10px] text-textMuted font-bold">{t.noDuplicates}</p>
            </div>
          ) : null}
          {((details as any).applications_in_progress ?? 0) > 0 && (
            <div className="shrink-0 flex items-center gap-2 px-3 py-2.5 rounded-xl bg-blue-50 border border-blue-200">
              <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse shrink-0" />
              <div>
                <p className="text-[11px] font-black text-blue-800">{t.scoringInProgress}</p>
                <p className="text-[9px] text-blue-600">{t.scoringProgressDetail.replace('{count}', String((details as any).applications_in_progress))}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI extraction status banner ───────────────────────────────────────── */}
      {details.criteria_extraction_status && details.criteria_extraction_status !== 'completed' && (
        <div className={`mb-6 rounded-2xl border p-4 flex items-center justify-between gap-4 ${details.criteria_extraction_status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}>
          <div className="flex items-center gap-3">
            {details.criteria_extraction_status === 'failed'
              ? <svg className="w-5 h-5 text-error shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
              : <svg className="animate-spin w-5 h-5 text-amber-500 shrink-0" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
            <div>
              <p className="text-sm font-bold text-textMain">
                {details.criteria_extraction_status === 'failed' ? t.criteriaFailed : details.criteria_extraction_status === 'processing' ? t.criteriaProcessing : t.criteriaPending}
              </p>
              {details.criteria_extraction_status === 'failed' && details.criteria_extraction_error && <p className="text-xs text-error/80 mt-0.5">{details.criteria_extraction_error}</p>}
            </div>
          </div>
          {details.criteria_extraction_status === 'failed' && (
            <button onClick={handleRetryExtraction} className="shrink-0 px-4 py-1.5 bg-error text-white text-xs font-bold rounded-lg hover:bg-red-700 transition-colors">{t.retryExtraction}</button>
          )}
        </div>
      )}

      {/* Main grid ─────────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 lg:items-start">
      <div className="lg:col-span-2 space-y-6">

      {/* C. Job Metadata ──────────────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
            {t.jobMetadata}
          </h3>
          {canEdit && !editingMeta && (
            <button onClick={handleMetaEdit} className="text-[10px] font-black text-primary hover:text-primaryDark uppercase tracking-widest transition-colors">{t.editMeta}</button>
          )}
        </div>
        <div className="px-6 py-5">
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
                    {['Entry-level', 'Junior', 'Mid-level', 'Senior', 'Lead', 'Executive'].map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaLocation}</label>
                  <input type="text" placeholder="e.g. Riyadh, SA" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.location} onChange={e => setDraftMeta(p => ({ ...p, location: e.target.value }))} />
                </div>
                {!isAgencyTenant && (
                  <div className="space-y-1">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.metaDepartment}</label>
                    <input type="text" placeholder="Engineering" className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" value={draftMeta.department} onChange={e => setDraftMeta(p => ({ ...p, department: e.target.value }))} />
                  </div>
                )}
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
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">
                  {isAgencyTenant ? (lang === 'ar' ? 'العميل' : 'Client') : t.metaDepartment}
                </p>
                <p className="text-sm font-bold text-textMain">
                  {isAgencyTenant
                    ? ((details as any).client_org_name || (lang === 'ar' ? 'عام' : 'General'))
                    : (details.job_client || t.notSet)}
                </p>
              </div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaDuration}</p><p className="text-sm font-bold text-textMain">{(details as any).duration || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaDeadline}</p><p className="text-sm font-bold text-textMain">{(details as any).application_deadline ? new Date((details as any).application_deadline).toLocaleDateString() : t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaVacancies}</p><p className="text-sm font-bold text-textMain">{(details as any).vacancies_count ?? t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaCreated}</p><p className="text-sm font-bold text-textMain">{(details as any).created_at ? new Date((details as any).created_at).toLocaleDateString() : (details.posted_date || t.notSet)}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaUpdated}</p><p className="text-sm font-bold text-textMain">{(details as any).updated_at ? new Date((details as any).updated_at).toLocaleDateString() : t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaCreatedBy}</p><p className="text-sm font-bold text-textMain">{(details as any).created_by_name || t.notSet}</p></div>
              <div><p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.metaUpdatedBy}</p><p className="text-sm font-bold text-textMain">{(details as any).updated_by_name || t.notSet}</p></div>
            </div>
          )}
        </div>

      </section>

      {/* D. Application Controls ──────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" /></svg>
            {t.appControls}
          </h3>
          {canEdit && !editingControls && (
            <button onClick={handleControlsEdit} className="text-[10px] font-black text-primary hover:text-primaryDark uppercase tracking-widest transition-colors">{t.editControls}</button>
          )}
        </div>
        <div className="px-6 py-5">
          {editingControls ? (
            <div className="space-y-4 animate-fade-in">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.maxApplications}</label>
                  <p className="text-[10px] text-textMuted mb-1">{t.maxApplicationsHint}</p>
                  <input
                    type="number"
                    min={1}
                    placeholder={t.unlimited}
                    value={draftControls.max_applications}
                    onChange={e => setDraftControls(p => ({ ...p, max_applications: e.target.value }))}
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  />
                </div>
                <div className="space-y-1 flex flex-col justify-end">
                  <div className="flex items-center justify-between bg-slate-50 rounded-xl px-4 py-3 border border-border">
                    <div>
                      <p className="text-xs font-black text-textMain">{t.autoClose}</p>
                      <p className="text-[10px] text-textMuted">{t.autoCloseHint}</p>
                    </div>
                    <button
                      onClick={() => setDraftControls(p => ({ ...p, auto_close: !p.auto_close }))}
                      className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${draftControls.auto_close ? 'bg-primary' : 'bg-slate-300'}`}
                    >
                      <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${draftControls.auto_close ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                    </button>
                  </div>
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-1">
                <button onClick={() => setEditingControls(false)} disabled={savingControls} className="px-5 py-2 text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">{t.cancelMeta}</button>
                <button onClick={handleControlsSave} disabled={savingControls} className="flex items-center gap-2 px-6 py-2 bg-primary text-white text-sm font-bold rounded-lg hover:bg-primaryDark transition-colors disabled:opacity-50">
                  {savingControls && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                  {savingControls ? t.savingControls : t.saveControls}
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-6 items-center">
              {(() => {
                const maxApps = (details as any).max_applications as number | null;
                const validCount = (details as any).applications_valid_count as number ?? 0;
                const limitReached = maxApps != null && validCount >= maxApps;
                const intakeOpenStatus = details.job_status?.toLowerCase() === 'active' && !limitReached;
                return (
                  <>
                    <div className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-black uppercase tracking-wider ${intakeOpenStatus ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${intakeOpenStatus ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                      {intakeOpenStatus ? t.intakeOpen : t.intakeClosed}
                    </div>
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{t.maxApplications}</p>
                      <p className="text-sm font-bold text-textMain">
                        {maxApps != null ? `${validCount} / ${maxApps}` : <span className="text-textMuted">{t.unlimited}</span>}
                      </p>
                    </div>
                    {maxApps != null && (
                      <div>
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{t.autoClose}</p>
                        <p className="text-sm font-bold text-textMain">{(details as any).auto_close_when_limit_reached ? 'Yes' : 'No'}</p>
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          )}
        </div>

      </section>

      {/* E. Intake Channels ────────────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
            {t.intakeChannels}
          </h3>
          {!canEditIntake && (
            <span className="text-[10px] text-textMuted font-bold px-2 py-0.5 bg-slate-100 rounded-full">{isAr ? 'للعرض فقط' : 'View only'}</span>
          )}
        </div>

        {/* ── Tab strip ── */}
        <div className="flex border-b border-border overflow-x-auto">
          {([
            { key: 'public-link'     as const, label: t.tabPublicLink,    badge: t.badgeExternal,                                                          badgeColor: 'bg-violet-100 text-violet-700'                                           },
            { key: 'email-alias'     as const, label: t.tabEmailAlias,    badge: details.receive_cv_via_platform_email   ? t.enabled : t.disabled,          badgeColor: details.receive_cv_via_platform_email   ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500' },
            { key: 'email-forwarding'as const, label: t.tabEmailFwd,      badge: details.receive_cv_via_forwarding_email ? t.enabled : t.disabled,          badgeColor: details.receive_cv_via_forwarding_email ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500' },
            { key: 'manual-upload'   as const, label: t.tabManualUpload,  badge: t.badgeInternal,                                                          badgeColor: 'bg-indigo-100 text-indigo-700'                                           },
          ]).map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveIntakeTab(tab.key)}
              className={`flex-1 min-w-[90px] px-3 py-3 flex flex-col items-center gap-1 transition-all border-b-2 ${activeIntakeTab === tab.key ? 'border-primary text-primary bg-primary/5' : 'border-transparent text-textMuted hover:text-textMain hover:bg-slate-50'}`}
            >
              <span className="text-[9px] font-black uppercase tracking-wider whitespace-nowrap">{tab.label}</span>
              <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded-full ${tab.badgeColor}`}>{tab.badge}</span>
            </button>
          ))}
        </div>

        {/* ── Tab content ── */}
        <div className="px-6 py-5">

          {/* Public Link tab */}
          {activeIntakeTab === 'public-link' && (
            <div className="rounded-xl border border-violet-200 bg-violet-50/40 p-4">
              <div className="flex items-start gap-3">
                <div className="shrink-0 w-7 h-7 bg-violet-100 rounded-lg flex items-center justify-center mt-0.5">
                  <svg className="w-3.5 h-3.5 text-violet-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" /></svg>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <p className="text-xs font-black text-violet-900">{t.publicApplyLink}</p>
                    <span className="text-[9px] font-bold bg-violet-100 text-violet-600 px-1.5 py-0.5 rounded-full">{t.badgeExternal}</span>
                  </div>
                  <p className="text-[10px] text-violet-600/80 mb-3">{t.publicApplyHint}</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-[11px] font-mono bg-white border border-violet-200 rounded-lg px-3 py-1.5 text-violet-700 truncate">{publicApplyUrl}</code>
                    <button onClick={() => handleCopy(publicApplyUrl, setCopiedApplyLink)} className="shrink-0 px-3 py-1.5 bg-violet-600 text-white text-[10px] font-black rounded-lg hover:bg-violet-700 transition-colors">
                      {copiedApplyLink ? t.copied : t.copyBtn}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Email Alias tab */}
          {activeIntakeTab === 'email-alias' && (
            <div>
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <div className="flex items-center gap-2 mb-0.5">
                    <p className="text-xs font-black text-textMain">{t.option2Title}</p>
                    <span className="text-[9px] font-black bg-success text-white px-1.5 py-0.5 rounded-full uppercase">{t.recommended}</span>
                  </div>
                  <p className="text-[11px] text-textMuted leading-relaxed max-w-sm">{t.option2Desc}</p>
                </div>
                <button
                  disabled={togglingAlias || !canEditIntake}
                  onClick={() => canEditIntake && handleIngestionToggle('receive_cv_via_platform_email', !details.receive_cv_via_platform_email)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.receive_cv_via_platform_email ? 'bg-success' : 'bg-slate-300'} ${togglingAlias || !canEditIntake ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.receive_cv_via_platform_email ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div className={`rounded-xl border p-4 ${details.receive_cv_via_platform_email ? 'border-success/20 bg-green-50/40' : 'border-slate-200 bg-slate-50'}`}>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option2AliasLabel}</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 text-success truncate">{details.platform_email || '—'}</code>
                  {details.platform_email && (
                    <button onClick={() => handleCopy(details.platform_email!, setCopiedAlias)} className="shrink-0 px-3 py-1.5 bg-success text-white text-[10px] font-black rounded-lg hover:bg-green-700 transition-colors">
                      {copiedAlias ? t.copied : t.copyBtn}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Email Forwarding tab */}
          {activeIntakeTab === 'email-forwarding' && (
            <div>
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <p className="text-xs font-black text-textMain mb-0.5">{t.option1Title}</p>
                  <p className="text-[11px] text-textMuted leading-relaxed max-w-sm">{t.option1Desc}</p>
                </div>
                <button
                  disabled={togglingFwd || !canEditIntake}
                  onClick={() => canEditIntake && handleIngestionToggle('receive_cv_via_forwarding_email', !details.receive_cv_via_forwarding_email)}
                  className={`shrink-0 relative w-10 h-5 rounded-full transition-colors focus:outline-none ${details.receive_cv_via_forwarding_email ? 'bg-primary' : 'bg-slate-300'} ${togglingFwd || !canEditIntake ? 'opacity-50 cursor-not-allowed' : ''}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.receive_cv_via_forwarding_email ? (isAr ? '-translate-x-5' : 'translate-x-5') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                </button>
              </div>
              <div className={`rounded-xl border p-4 space-y-3 ${details.receive_cv_via_forwarding_email ? 'border-primary/20 bg-blue-50/40' : 'border-slate-200 bg-slate-50'}`}>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-1">{t.option1ForwardTo}</p>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 text-xs font-mono bg-white border border-border rounded-lg px-3 py-1.5 text-primary truncate">{details.forwarding_email || 'jobs@ai970.cloud'}</code>
                    <button onClick={() => handleCopy(details.forwarding_email || 'jobs@ai970.cloud', setCopiedFwdEmail)} className="shrink-0 px-3 py-1.5 bg-primary text-white text-[10px] font-black rounded-lg hover:bg-blue-700 transition-colors">
                      {copiedFwdEmail ? t.copied : t.copyBtn}
                    </button>
                  </div>
                </div>
                <div className="bg-white/60 border border-blue-100 rounded-xl px-3 py-2.5 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-[9px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.jobRef}</p>
                    <code className="text-xs font-mono font-black text-textMain">{jobCode}</code>
                    <p className="text-[10px] text-textMuted mt-0.5 leading-tight">{t.jobRefHint}</p>
                  </div>
                  <button onClick={() => handleCopy(jobCode, setCopiedJobRef)} className="shrink-0 px-3 py-1.5 bg-white border border-border text-[10px] font-black text-textMain rounded-lg hover:bg-slate-50 transition-colors">
                    {copiedJobRef ? t.copied : t.copyBtn}
                  </button>
                </div>
                {details.receive_cv_via_forwarding_email && (
                  <div className="flex items-center justify-between bg-white/70 border border-blue-100 rounded-xl px-3 py-2">
                    <div>
                      <p className="text-[10px] font-black text-textMain">{t.restrictSender}</p>
                      <p className="text-[9px] text-textMuted">{t.restrictSenderHint}</p>
                    </div>
                    <button
                      disabled={!canEdit}
                      onClick={() => canEdit && handleIngestionToggle('restrict_forwarding_sender_to_tenant_email', !details.restrict_forwarding_sender_to_tenant_email)}
                      className={`shrink-0 relative w-9 h-5 rounded-full transition-colors focus:outline-none ${details.restrict_forwarding_sender_to_tenant_email ? 'bg-primary' : 'bg-slate-300'} ${!canEdit ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${details.restrict_forwarding_sender_to_tenant_email ? (isAr ? '-translate-x-4' : 'translate-x-4') : (isAr ? '-translate-x-0.5' : 'translate-x-0.5')}`} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Manual Upload tab */}
          {activeIntakeTab === 'manual-upload' && (
            <div className="rounded-2xl border-2 border-dashed border-indigo-200 bg-indigo-50/30 p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className="shrink-0 w-8 h-8 bg-indigo-100 rounded-xl flex items-center justify-center">
                  <svg className="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" /></svg>
                </div>
                <div>
                  <p className="text-xs font-black text-indigo-900">{t.manualUploadTitle}</p>
                  <p className="text-[11px] text-indigo-600/80 mt-0.5 leading-relaxed">{t.manualUploadDesc}</p>
                </div>
              </div>

              {/* Batch success banner */}
              {batchCompleteCount !== null && (
                <div className="mb-4 rounded-xl bg-emerald-50 border border-emerald-200 px-4 py-3 flex items-center gap-2.5">
                  <svg className="w-4 h-4 text-emerald-600 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                  <p className="text-sm font-bold text-emerald-700">{t.batchComplete.replace('{count}', String(batchCompleteCount))}</p>
                </div>
              )}

              {/* File input */}
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
                  {uploading ? t.uploadingBtn : t.chooseFiles}
                </label>
              </div>

              {/* Session CV list */}
              {loadingUploads && cvsDisplay.length === 0 ? (
                <div className="flex items-center gap-2 text-[11px] text-textMuted py-2">
                  <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  Loading...
                </div>
              ) : cvsDisplay.length > 0 ? (
                <div className="mt-2">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-[10px] font-black text-indigo-800 uppercase tracking-wider">{t.uploadedCVsTitle} ({cvsDisplay.length})</p>
                    {cvsScoringInProgress && cvsTotal > 0 && (
                      <p className="text-[10px] font-bold text-indigo-600">
                        {t.progressLabel.replace('{scored}', String(cvsScoredCount)).replace('{total}', String(cvsTotal)).replace('{pct}', String(progressPct))}
                      </p>
                    )}
                  </div>
                  {cvsScoringInProgress && cvsTotal > 0 && (
                    <>
                      <div className="w-full bg-indigo-100 h-1.5 rounded-full mb-2 overflow-hidden">
                        <div className="bg-indigo-500 h-full rounded-full transition-all duration-500" style={{ width: `${progressPct}%` }} />
                      </div>
                      {queueStatus && (
                        <div className="flex flex-wrap gap-x-3 gap-y-0.5 mb-2">
                          {queueStatus.queued > 0 && <span className="text-[9px] text-indigo-500 font-bold">{t.progressQueued.replace('{n}', String(queueStatus.queued))}</span>}
                          {queueStatus.processing > 0 && <span className="text-[9px] text-blue-500 font-bold">{t.progressProcessing.replace('{n}', String(queueStatus.processing))}</span>}
                          {queueStatus.completed > 0 && <span className="text-[9px] text-success font-bold">{t.progressScored.replace('{n}', String(queueStatus.completed))}</span>}
                          {queueStatus.failed > 0 && <span className="text-[9px] text-error font-bold">{t.progressFailed.replace('{n}', String(queueStatus.failed))}</span>}
                        </div>
                      )}
                    </>
                  )}
                  <div className="space-y-1.5 max-h-56 overflow-y-auto">
                    {cvsDisplay.map(cv => {
                      const st = cvStatusDisplay(cv);
                      const isDeleting = deletingCVId === cv.application_id;
                      const canDelete = cv.processing_status === 'pending' && !cvsScoringInProgress;
                      const isDone = cv.processing_status === 'scored' || cv.processing_status === 'low_match' || cv.processing_status === 'failed';
                      const hasExitReason = cv.evaluation_exit_reason && (
                        cv.processing_status === 'failed' ||
                        cv.processing_status === 'low_match' ||
                        (cv.processing_status === 'scored' && cv.evaluation_stage != null && cv.evaluation_stage < 3)
                      );
                      return (
                        <div key={cv.application_id} className={`flex flex-col gap-1 rounded-xl border px-3 py-2 transition-colors ${isDone ? 'bg-slate-50/60 border-slate-100' : 'bg-white border-indigo-100'}`}>
                          <div className="flex items-center gap-3">
                            <svg className={`w-3.5 h-3.5 shrink-0 ${isDone ? 'text-slate-300' : 'text-indigo-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                            <p className={`flex-1 text-[11px] font-bold truncate min-w-0 ${isDone ? 'text-textMuted' : 'text-textMain'}`}>{cv.original_filename || cv.candidate_name}</p>
                            <span className={`shrink-0 inline-flex items-center gap-1 text-[9px] font-black border rounded-full px-2 py-0.5 ${st.color}`}>
                              {st.spin && <svg className="w-2.5 h-2.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
                              {st.label}
                            </span>
                            {canDelete && (
                              <button disabled={!!deletingCVId} onClick={() => handleDeleteCV(cv.application_id)} title={isDeleting ? t.deletingCV : t.deleteCV}
                                className={`shrink-0 p-1 rounded-lg transition-colors ${deletingCVId ? 'opacity-40 cursor-not-allowed' : 'text-red-400 hover:text-red-600 hover:bg-red-50'}`}>
                                {isDeleting
                                  ? <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                                  : <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>}
                              </button>
                            )}
                          </div>
                          {hasExitReason && (
                            <p className="text-[9px] text-slate-400 leading-tight pl-6 truncate" title={cv.evaluation_exit_reason!}>
                              <span className="font-black uppercase">{t.exitReason}</span> {cv.evaluation_exit_reason}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2 flex-wrap">
                    {queueStatus?.has_stuck && (
                      <button disabled={resettingStuck} onClick={handleResetStuck}
                        className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-black rounded-lg border transition-colors ${resettingStuck ? 'border-red-200 bg-red-50 text-red-400 cursor-not-allowed' : 'border-red-300 bg-red-50 text-red-600 hover:bg-red-100'}`}>
                        {resettingStuck
                          ? <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                          : <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>}
                        {resettingStuck ? t.resettingStuck : t.resetStuck}
                      </button>
                    )}
                    <div className="flex-1" />
                    <button
                      disabled={!cvsHasPending || cvsScoringInProgress || !!deletingCVId}
                      onClick={handleScorePending}
                      className={`inline-flex items-center gap-2 px-5 py-2 text-[11px] font-black rounded-xl transition-colors ${!cvsHasPending || cvsScoringInProgress || !!deletingCVId ? 'bg-slate-200 text-textMuted cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}
                    >
                      {cvsScoringInProgress
                        ? <><svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>{t.scoringBtnLabel}</>
                        : <><svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>{t.scoreBtnLabel}</>}
                    </button>
                  </div>
                </div>
              ) : (
                batchCompleteCount === null && <p className="text-[11px] text-indigo-400 italic">{t.noUploads}</p>
              )}
            </div>
          )}

        </div>
      </section>

      {/* F. AI Criteria ──────────────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" /></svg>
            {t.skillsAnalysis}
          </h3>
          {canEdit && details.criteria_extraction_status === 'completed' && !editingCriteria && (
            <button onClick={handleCriteriaEdit} className="text-[10px] font-black text-primary hover:text-primaryDark uppercase tracking-widest transition-colors">{t.editCriteria}</button>
          )}
        </div>
        <div className="px-6 py-5">
          {editingCriteria ? (
            <div className="animate-fade-in">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.requiredSkills} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={4} value={draftCriteria.required_skills} onChange={e => setDraftCriteria(p => ({ ...p, required_skills: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.preferredSkills} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={4} value={draftCriteria.preferred_skills} onChange={e => setDraftCriteria(p => ({ ...p, preferred_skills: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.minYears}</label>
                  <input type="number" min={0} value={draftCriteria.minimum_years} onChange={e => setDraftCriteria(p => ({ ...p, minimum_years: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.minLevel}</label>
                  <input type="text" value={draftCriteria.minimum_education} onChange={e => setDraftCriteria(p => ({ ...p, minimum_education: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.relevantRoles} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={3} value={draftCriteria.relevant_roles} onChange={e => setDraftCriteria(p => ({ ...p, relevant_roles: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.fieldsOfStudy} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={3} value={draftCriteria.fields_of_study} onChange={e => setDraftCriteria(p => ({ ...p, fields_of_study: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.certifications} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={3} value={draftCriteria.certifications} onChange={e => setDraftCriteria(p => ({ ...p, certifications: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.domainKnowledge} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={3} value={draftCriteria.domain_knowledge} onChange={e => setDraftCriteria(p => ({ ...p, domain_knowledge: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
                <div className="md:col-span-2 space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.otherRequirements} <span className="normal-case font-normal opacity-60">{t.criteriaListHint}</span></label>
                  <textarea rows={3} value={draftCriteria.other_requirements} onChange={e => setDraftCriteria(p => ({ ...p, other_requirements: e.target.value }))} className="w-full px-3 py-2 border border-border rounded-xl text-xs font-mono resize-none focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none" />
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-5 mt-3 border-t border-border">
                <button onClick={() => setEditingCriteria(false)} className="px-5 py-2.5 text-sm font-bold text-textMuted hover:text-textMain transition-colors">{t.cancelEdit}</button>
                <button onClick={() => setShowCriteriaWarning(true)} disabled={savingCriteria} className="flex items-center gap-2 px-6 py-2.5 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-50">
                  {savingCriteria && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                  {savingCriteria ? t.savingCriteria : t.saveCriteria}
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {/* Skills */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.requiredSkills}{isModified(a => a?.skills?.required) && modifiedBadge}</p>
                <div className="flex flex-wrap gap-2 mb-4">
                  {(analysis?.skills?.required || []).map((s, i) => (
                    <span key={i} className="px-3 py-1.5 bg-slate-100 rounded-lg text-xs font-bold text-textMain">{s}</span>
                  ))}
                </div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.preferredSkills}{isModified(a => a?.skills?.preferred) && modifiedBadge}</p>
                <div className="flex flex-wrap gap-2">
                  {(analysis?.skills?.preferred || []).map((s, i) => (
                    <span key={i} className="px-3 py-1.5 bg-blue-50 text-primary border border-blue-100 rounded-lg text-xs font-bold">{s}</span>
                  ))}
                </div>
              </div>
              {/* Experience & Education */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-slate-50 rounded-xl p-4 border border-border">
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.experience}</p>
                  <p className="text-lg font-black text-primary mb-2">{analysis?.experience?.minimum_years || 0}{t.years}</p>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.relevantRoles}{isModified(a => a?.experience?.relevant_roles) && modifiedBadge}</p>
                  <ul className="space-y-0.5">
                    {(analysis?.experience?.relevant_roles || []).map((r, i) => <li key={i} className="text-xs font-bold text-textMain">• {r}</li>)}
                  </ul>
                </div>
                <div className="bg-slate-50 rounded-xl p-4 border border-border">
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.education}</p>
                  <p className="text-sm font-black text-textMain mb-2">{analysis?.education?.minimum_level || 'N/A'}</p>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.fieldsOfStudy}{isModified(a => a?.education?.fields_of_study) && modifiedBadge}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(analysis?.education?.fields_of_study || []).map((f, i) => <span key={i} className="px-2 py-0.5 bg-white border border-border rounded text-[10px] font-bold text-textMuted">{f}</span>)}
                  </div>
                </div>
              </div>
              {/* Other categories */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {[
                  { label: t.certifications,    items: analysis?.certifications,    modKey: (a: any) => a?.certifications },
                  { label: t.domainKnowledge,   items: analysis?.domain_knowledge,  modKey: (a: any) => a?.domain_knowledge },
                  { label: t.otherRequirements, items: analysis?.other_requirements, modKey: (a: any) => a?.other_requirements },
                ].map((cat, idx) => (
                  <div key={idx} className="bg-slate-50 rounded-xl p-4 border border-border">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{cat.label}{isModified(cat.modKey) && modifiedBadge}</p>
                    <ul className="space-y-1">
                      {(cat.items || []).length > 0
                        ? (cat.items || []).map((item, i) => <li key={i} className="text-[11px] font-bold text-textMain leading-snug">• {item}</li>)
                        : <li className="text-[10px] text-textMuted italic">{t.noData}</li>}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      </div>
      {/* Right column — Evaluation Weights ────────────────────────────────── */}
      <div className="space-y-6">
        <div className="bg-white rounded-2xl border border-border shadow-sm p-6 lg:sticky lg:top-20">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
              {t.evalLogic}
            </h3>
            {canEdit && !editingWeights && analysis?.scoring_weights && (
              <button onClick={handleEditWeights} className="text-[10px] font-black text-indigo-600 hover:text-indigo-800 uppercase tracking-widest transition-colors">{t.editWeights}</button>
            )}
          </div>
          {editingWeights ? (
            <>
              <div className="space-y-3">
                {weightKeys.map((key, i) => {
                  const val = draftWeights[key] ?? 0;
                  const isOver = weightTotal > 100 && val > 0;
                  return (
                    <div key={i}>
                      <div className="flex justify-between text-[10px] font-black uppercase mb-1">
                        <span className="text-textMuted">{weightLabels[i]}</span>
                        <span className={isOver ? 'text-error' : 'text-textMuted'}>{val}%</span>
                      </div>
                      <input
                        type="number" min={0} max={100} value={val}
                        onChange={e => { const v = Math.max(0, Math.min(100, parseInt(e.target.value) || 0)); setDraftWeights(prev => ({ ...prev, [key]: v })); }}
                        className={`w-full px-3 py-1.5 text-sm font-bold rounded-lg border-2 focus:outline-none transition-colors ${isOver ? 'border-error bg-red-50 text-error' : 'border-border bg-slate-50 text-textMain focus:border-indigo-400'}`}
                      />
                    </div>
                  );
                })}
              </div>
              <div className={`mt-3 px-3 py-2 rounded-lg text-[10px] font-bold flex items-center justify-between ${weightTotal === 100 ? 'bg-green-50 text-success border border-green-200' : weightTotal > 100 ? 'bg-red-50 text-error border border-red-200' : 'bg-amber-50 text-warning border border-amber-200'}`}>
                <span>{weightTotal === 100 ? t.weightSuccess : weightTotal > 100 ? t.weightOver.replace('{e}', String(weightTotal - 100)) : t.weightUnder.replace('{r}', String(100 - weightTotal))}</span>
                <span className="font-black shrink-0 ml-2">{t.weightTotal}: {weightTotal}%</span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <button onClick={handleNormalizeWeights} className="px-2 py-1.5 text-[10px] font-black text-indigo-600 border border-indigo-200 rounded-lg hover:bg-indigo-50 uppercase tracking-widest transition-colors leading-tight">{t.normalizeWeights}</button>
                <button onClick={handleResetWeights} className="px-2 py-1.5 text-[10px] font-black text-textMuted border border-border rounded-lg hover:bg-slate-50 uppercase tracking-widest transition-colors leading-tight">{t.resetAiWeights}</button>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <button onClick={() => setEditingWeights(false)} className="px-3 py-2 text-xs font-bold text-textMuted border border-border rounded-lg hover:bg-slate-50 transition-colors">{t.cancelEdit}</button>
                <button onClick={handleSaveWeights} disabled={weightTotal !== 100 || savingWeights} className={`px-3 py-2 text-xs font-bold rounded-lg transition-colors ${weightTotal === 100 && !savingWeights ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-200 text-textMuted cursor-not-allowed'}`}>
                  {savingWeights ? '…' : t.saveWeights}
                </button>
              </div>
            </>
          ) : (
            <div className="space-y-4">
              {weightKeys.map((key, i) => {
                const weight = analysis?.scoring_weights?.[key];
                return weight ? (
                  <div key={i}>
                    <div className="flex justify-between text-[10px] font-black uppercase mb-1.5">
                      <span className="text-textMuted">{weightLabels[i]}</span>
                      <span className="text-textMain">{weight}%</span>
                    </div>
                    <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                      <div className="bg-indigo-600 h-full rounded-full" style={{ width: `${weight}%` }} />
                    </div>
                  </div>
                ) : null;
              })}
            </div>
          )}
        </div>
      </div>

      </div>

      {/* F2. Duplicate Submissions ───────────────────────────────────────────── */}
      {dupCount > 0 && (
        <div ref={duplicateSubmissionsRef} className="mt-6 bg-white rounded-2xl border border-orange-200 overflow-hidden shadow-sm scroll-mt-20">
          <button
            onClick={() => setDupSectionExpanded(p => !p)}
            className="w-full px-5 py-3.5 flex items-center justify-between hover:bg-orange-50 transition-colors"
          >
            <span className="text-[10px] font-black text-orange-700 uppercase tracking-widest flex items-center gap-2">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
              {t.dupSubmissionsTitle}
              <span className="ml-1 px-2 py-0.5 rounded-full bg-orange-100 text-orange-800 text-[9px] font-black">{dupCount}</span>
            </span>
            <svg className={`w-4 h-4 text-orange-400 transition-transform ${dupSectionExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" /></svg>
          </button>
          {dupSectionExpanded && (
            <div className="border-t border-orange-100 animate-fade-in">
              <p className="px-5 py-3 text-[10px] text-orange-600 bg-orange-50 border-b border-orange-100 leading-relaxed">
                {t.dupSubmissionsHint}
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="border-b border-border bg-slate-50">
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColName}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColEmail}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColReceived}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColSimilarity}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColOriginal}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColReason}</th>
                      <th className="px-4 py-2.5 text-left text-[9px] font-black text-textMuted uppercase tracking-widest">{t.dupColFile}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {(showAllDuplicates ? duplicateLogs : duplicateLogs.slice(0, 5)).map((log: any) => (
                      <tr key={log.log_id} className="hover:bg-slate-50 transition-colors">
                        <td className="px-4 py-2.5 font-medium text-textMain truncate max-w-[140px]">{log.duplicate_name || '—'}</td>
                        <td className="px-4 py-2.5 text-textMuted truncate max-w-[160px]">{log.duplicate_email || '—'}</td>
                        <td className="px-4 py-2.5 text-textMuted whitespace-nowrap">
                          {log.received_at ? new Date(log.received_at).toLocaleDateString() : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-textMuted whitespace-nowrap">
                          {log.duplicate_similarity_score != null
                            ? <span className={`font-bold ${log.duplicate_similarity_score >= 95 ? 'text-error' : log.duplicate_similarity_score >= 80 ? 'text-warning' : 'text-textMuted'}`}>{Math.round(log.duplicate_similarity_score)}%</span>
                            : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-textMuted truncate max-w-[160px]">
                          {log.original_candidate_name && log.original_application_id
                            ? <button onClick={() => onOpenApplication(jobId, log.original_application_id)} className="font-medium text-primary hover:underline transition-colors">{log.original_candidate_name}</button>
                            : log.original_candidate_name
                              ? <span className="font-medium text-textMain">{log.original_candidate_name}</span>
                              : '—'}
                        </td>
                        <td className="px-4 py-2.5 text-textMuted truncate max-w-[160px]">{log.duplicate_reason?.replace(/_/g, ' ') || log.notes || '—'}</td>
                        <td className="px-4 py-2.5 whitespace-nowrap">
                          {log.has_duplicate_cv
                            ? <button onClick={() => handleDownloadDupCV(log.log_id, log.duplicate_original_filename || log.raw_filename || 'cv')} className="text-[10px] font-black text-primary hover:underline">{t.dupDownloadCV}</button>
                            : <span className="text-textMuted">{log.raw_filename || '—'}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {duplicateLogs.length > 5 && (
                <div className="px-5 py-3 border-t border-border text-center">
                  <button
                    onClick={() => setShowAllDuplicates(p => !p)}
                    className="text-[10px] font-black text-orange-600 hover:text-orange-800 uppercase tracking-widest transition-colors"
                  >
                    {showAllDuplicates ? t.dupShowLess : `${t.dupShowAll} (${duplicateLogs.length})`}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* G. Reference Content ────────────────────────────────────────────────── */}
      <div className="mt-6 space-y-3">
        {originalAiCriteriaRef.current && (
          <div className="bg-white rounded-2xl border border-border overflow-hidden shadow-sm">
            <button
              onClick={() => setShowOriginalCriteria(p => !p)}
              className="w-full px-5 py-3.5 flex items-center justify-between hover:bg-slate-50 transition-colors"
            >
              <span className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                {showOriginalCriteria ? t.hideOriginalCriteria : t.viewOriginalCriteria}
              </span>
              <svg className={`w-4 h-4 text-textMuted transition-transform ${showOriginalCriteria ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" /></svg>
            </button>
            {showOriginalCriteria && (() => {
              const orig = originalAiCriteriaRef.current;
              return (
                <div className="px-5 py-4 border-t border-border animate-fade-in">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-[11px] text-textMuted">
                    {[
                      { label: t.requiredSkills,   items: orig?.skills?.required },
                      { label: t.preferredSkills,  items: orig?.skills?.preferred },
                      { label: t.relevantRoles,    items: orig?.experience?.relevant_roles },
                      { label: t.fieldsOfStudy,    items: orig?.education?.fields_of_study },
                      { label: t.certifications,   items: orig?.certifications },
                      { label: t.domainKnowledge,  items: orig?.domain_knowledge },
                      { label: t.otherRequirements,items: orig?.other_requirements },
                    ].map((sec, i) => (sec.items?.length ? (
                      <div key={i}>
                        <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-1">{sec.label}</p>
                        <p className="leading-relaxed">{sec.items.join(', ')}</p>
                      </div>
                    ) : null))}
                    {orig?.experience?.minimum_years != null && (
                      <div>
                        <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-1">{t.minYears}</p>
                        <p>{orig.experience.minimum_years}{t.years}</p>
                      </div>
                    )}
                    {orig?.education?.minimum_level && (
                      <div>
                        <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-1">{t.minLevel}</p>
                        <p>{orig.education.minimum_level}</p>
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}
          </div>
        )}
        <div className="bg-white rounded-2xl border border-border overflow-hidden shadow-sm">
          <button
            onClick={() => setDescExpanded(!descExpanded)}
            className="w-full px-5 py-3.5 flex items-center justify-between hover:bg-slate-50 transition-colors"
          >
            <span className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-2">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
              {t.jobDesc}
            </span>
            <svg className={`w-4 h-4 text-textMuted transition-transform ${descExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" /></svg>
          </button>
          {descExpanded && (
            <div className="px-5 pb-5 pt-3 border-t border-border animate-fade-in">
              <div className="prose prose-slate max-w-none text-textMain text-sm leading-relaxed opacity-80 whitespace-pre-wrap">
                {details.description}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Criteria save confirmation modal */}
      {showCriteriaWarning && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-3xl shadow-2xl max-w-md w-full mx-4 p-8 animate-fade-in">
            <h3 className="text-base font-black text-textMain mb-3">{t.criteriaWarningTitle}</h3>
            <p className="text-sm text-textMuted leading-relaxed mb-6">{t.criteriaWarningBody}</p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowCriteriaWarning(false)} className="px-5 py-2.5 text-sm font-bold text-textMuted hover:text-textMain transition-colors">{t.cancelEdit}</button>
              <button
                onClick={handleCriteriaSaveConfirmed}
                disabled={savingCriteria}
                className="flex items-center gap-2 px-6 py-2.5 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-50"
              >
                {savingCriteria && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />}
                {t.criteriaConfirm}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};
