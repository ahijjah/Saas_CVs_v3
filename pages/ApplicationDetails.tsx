
import React, { useState } from 'react';
import { ApplicationDetailedAnalysis, ScoreDimension, ScoreDetail, KnockoutAnswerRecord, PassingCriteria, WorkflowStatus } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface JobMetaStrip {
  job_title: string;
  job_client: string | null;
  job_code: string;
  job_status: string;
  job_type: string | null;
  location: string | null;
  client_org_name: string | null;
}

interface ApplicationDetailsProps {
  data: ApplicationDetailedAnalysis;
  onBack: () => void;
  jobMeta?: JobMetaStrip | null;
  onDownloadCV?: () => void;
  downloadingCV?: boolean;
  token?: string;
  onWorkflowStatusChange?: (appId: string, newStatus: WorkflowStatus, note?: string) => void;
  onRecruiterNotesChange?: (appId: string, notes: string | null) => void;
}

const T = {
  en: {
    back: 'Back to Applications',
    points: 'Points',
    noDecision: 'No Decision',
    lowMatch: 'Low Match',
    gatekeeperFiltered: 'Pre-filtered by local AI — below semantic similarity threshold',
    execSummary: 'Executive Summary',
    noSummary: 'No executive summary available for this candidate.',
    dimScoring: 'Dimension Scoring',
    scoreBars: ['Technical Skills', 'Relevant Experience', 'Education Alignment', 'Certifications', 'Soft Skills', 'Domain Knowledge', 'Other Criteria'],
    scoringNote: 'Scoring is calculated based on the job evaluation profile weights.',
    sections: {
      matchedSkills: 'Matched Skills',
      expFit: 'Experience Fit',
      academicFit: 'Academic Fit',
      certifications: 'Certifications',
      gaps: 'Gaps Identified',
      evalNotes: 'Evaluation Notes',
      strengths: 'Strengths',
      risks: 'Identified Risks',
    },
    intelligence: 'Intelligence Analysis',
    cvLanguage: 'CV Language',
    semanticSimilarity: 'Semantic Similarity',
    skillMatch: 'Skill Match',
    gatekeeperPassed: 'Gatekeeper',
    passed: 'Passed',
    filtered: 'Filtered',
    matchedSkillsLabel: 'Matched Skills',
    missingSkillsLabel: 'Missing Skills',
    noMatchedSkills: 'No matched skills recorded.',
    redFlags: 'Red Flags',
    reasoning: 'AI Reasoning',
    interviewPrep: 'Interview Preparation',
    focusAreas: 'Focus Areas',
    suggestedQs: 'Suggested Questions',
    aiComparison: 'AI Comparison Results',
    compProvider: 'Provider',
    compModel: 'Model',
    compScore: 'Score',
    compDelta: 'Delta vs primary',
    promptTracking: 'Scoring Audit',
    primaryProvider: 'Primary Provider',
    primaryModel: 'Primary Model',
    scoringPrompt: 'Scoring Prompt',
    level2Prompt: 'Level 2 Prompt',
    promptVersion: 'v',
    rawPayload: 'Developer: Raw AI Payload',
    possibleDuplicate: 'Possible Duplicate',
    dupBannerTitle: 'This application may be a duplicate',
    dupContentSimilarity: 'High content similarity',
    dupIdentityMatch: 'Identity match',
    dupRefLabel: 'Earlier matching application',
    dupCandidateName: 'Candidate',
    dupSubmittedOn: 'Submitted on',
    dupRefId: 'Reference ID',
    dupSimilarityScore: 'Similarity score',
    dupReason: 'Reason',
    dupCheckedAt: 'Checked at',
    intakeTitle: 'Application Intake',
    intakeSource: 'Source',
    intakeReceived: 'Received',
    intakeSender: 'Sender / Uploader',
    intakeFile: 'Original File',
    intakeSourceLabels: {
      manual_upload:    'Upload',
      email_forwarding: 'Email Intake',
      platform_email:   'Dedicated Email',
      public_apply:     'Link',
    } as Record<string, string>,
    evidenceAnalysis: 'Evidence-Based Analysis',
    matchedEvidence: 'Matched Evidence',
    missingEvidence: 'Missing / Weak Evidence',
    additionalStrengths: 'Additional Relevant Strengths',
    notInFinalScore: 'Not included in final score',
    additionalInsights: 'Additional Recruiter Insights',
    scoreOverview: 'Score Overview',
    securityBlockedPageTitle: 'Application Security Review',
    securityBlockedBadge: 'Blocked Before AI Scoring',
    securityCandidateRef: 'Candidate Reference',
    securityCheck: 'Security Check',
    securityPassed: 'Passed',
    securityWarning: 'Warning',
    securityBlocked: 'Blocked',
    securityAlertTitle: 'Suspicious Content Detected — Application Blocked',
    securityWarningTitle: 'Suspicious Content Detected — Under Review',
    securityWhyFlagged: 'Why was this flagged?',
    securityBlockedSummary: 'This CV contains content that appears designed to manipulate the automated evaluation process. The application was blocked and was not scored.',
    securityWarningSummary: 'This CV contains content that may be attempting to influence the automated evaluation process. The application was allowed through but should be reviewed carefully.',
    securityGuidanceNote: 'Please review this application carefully before proceeding.',
    securityRiskLevel: 'Risk Level',
    securitySeverityScore: 'Severity Score',
    securityIssueTypes: 'Issue Types Detected',
    securityCheckedAt: 'Checked At',
    stoppedReasonTitle: 'Stopped Before AI Scoring',
    stoppedReasonCategory: 'Reason Category',
    stoppedReasonDetail: 'Detailed Reason',
    stoppedReasonNoDetail: 'No detailed reason was recorded.',
    stoppedReasonUnknown: 'Failed / Unknown',
    stoppedTechnicalStatus: 'Technical Status',
    stoppedProcessingStatus: 'Processing Status',
    stoppedEvalStage: 'Evaluation Stage',
    stoppedMeaningfulReasonLabel: 'Meaningful Reason',
    stoppedTechnicalReason: 'Technical Reason',
    stoppedNoTechnicalReason: 'No technical reason was recorded.',
    stoppedMeaningfulReasons: {
      security_blocked:  'The CV was blocked because it contains suspicious content that may try to manipulate the automated evaluation.',
      extraction_failed: 'The CV could not be read reliably. The extracted text was missing, corrupted, or too short for scoring.',
      processing_error:  'The application could not be processed due to a system or scoring error. It may require technical review.',
      duplicate_blocked: 'This CV was detected as an exact duplicate of a previously submitted application. It was blocked automatically and was not scored.',
      other:             'The application stopped before AI scoring and requires review.',
    } as Record<string, string>,
    stoppedLabels: {
      security_blocked:  'Security Blocked',
      extraction_failed: 'Extraction Failed',
      processing_error:  'Processing Error',
      duplicate_blocked: 'Duplicate Blocked',
      other:             'Failed',
    } as Record<string, string>,
    dupDetailTitle: 'Duplicate Application Details',
    dupDetailReason: 'Match Type',
    dupDetailOriginalCandidate: 'Original Candidate',
    dupDetailOriginalRef: 'Reference Application ID',
    dupDetailOriginalSubmitted: 'Original Submitted',
    dupDetailSimilarity: 'Similarity Score',
    dupDetailCheckedAt: 'Detected At',
    dupDetailReasonLabels: {
      file_hash:                  'Exact File Match (byte-for-byte identical)',
      normalized_text_hash:       'Exact Content Match (identical extracted text)',
      canonical_text_fingerprint: 'Cross-Format Match (same CV, different file format)',
      content_similarity_fallback:'High Content Similarity',
    } as Record<string, string>,
    dupDetailUnknownRef: 'Reference application not available',
    knockoutSectionTitle: 'Knockout Questions',
    knockoutNoAnswers: 'No knockout answers were submitted for this application.',
    knockoutQuestion: 'Question',
    knockoutAnswer: 'Answer',
    knockoutRequired: 'Required',
    knockoutPassingCriteria: 'Passing Criteria',
    knockoutStatus: 'Result',
    knockoutPassed: 'Passed',
    knockoutFailed: 'Failed',
    knockoutNoCriteria: 'No Criteria',
    knockoutNotEvaluated: 'Not Evaluated',
    knockoutCriteriaPass: (op: string, val: string) => `Pass if answer is ${op} ${val}`,
    knockoutCriteriaAnswers: (answers: string[]) => `Must answer: ${answers.join(' or ')}`,
    securityDetectedStatements: 'Detected Suspicious Statements',
    securitySnippetsNote: 'Short excerpts shown for reviewer context. Full CV content is not displayed here.',
    securityPatternLabels: {
      override_instructions:          'Instruction Override',
      score_manipulation:             'Score Manipulation',
      reveal_prompt:                  'Prompt Reveal Attempt',
      jailbreak:                      'Jailbreak Pattern',
      scoring_rule_change:            'Scoring Rule Change',
      auto_qualify:                   'Auto-Qualify Attempt',
      forced_ranking:                 'Forced Ranking Attempt',
      automatic_pass:                 'Automatic Pass Attempt',
      prompt_disclosure_attempt:      'Evaluation Logic Disclosure',
      bypass_evaluation_rules:        'Evaluation Bypass Attempt',
      false_requirement_satisfaction: 'False Requirement Satisfaction',
      obfuscated:                     'Obfuscated Content',
      encoded_payload:                'Encoded Payload',
      unicode_spam:                   'Unicode Anomaly',
    } as Record<string, string>,
    securityReasonExplanations: {
      override_instructions:          'The CV attempted to override or ignore the system\'s evaluation instructions.',
      score_manipulation:             'The CV attempted to artificially influence or increase the candidate\'s score.',
      reveal_prompt:                  'The CV attempted to expose internal evaluation logic or hidden system instructions.',
      jailbreak:                      'The CV attempted to bypass or disable the AI evaluation system\'s rules.',
      scoring_rule_change:            'The CV attempted to alter the scoring criteria or evaluation rules.',
      auto_qualify:                   'The CV attempted to automatically mark the candidate as qualified.',
      forced_ranking:                 'The CV attempted to force the candidate to be ranked among top applicants.',
      automatic_pass:                 'The CV attempted to automatically pass HR or technical screening stages.',
      prompt_disclosure_attempt:      'The CV attempted to reveal hidden recruiter scoring logic or internal evaluation criteria.',
      bypass_evaluation_rules:        'The CV attempted to bypass standard evaluation limitations or criteria.',
      false_requirement_satisfaction: 'The CV attempted to mark missing requirements as satisfied regardless of actual evidence.',
      obfuscated:                     'The CV contains deliberately obscured or hidden text to evade detection.',
      encoded_payload:                'The CV contains encoded or encrypted content that conceals suspicious instructions.',
      unicode_spam:                   'The CV contains unusual or invisible characters that may be used to hide content.',
    } as Record<string, string>,
  },
  ar: {
    back: 'العودة إلى الطلبات',
    points: 'نقطة',
    noDecision: 'لا يوجد قرار',
    lowMatch: 'تطابق منخفض',
    gatekeeperFiltered: 'تمت تصفيته بالذكاء المحلي — أقل من حد التشابه الدلالي',
    execSummary: 'الملخص التنفيذي',
    noSummary: 'لا يوجد ملخص تنفيذي متاح لهذا المرشح.',
    dimScoring: 'تقييم الأبعاد',
    scoreBars: ['المهارات التقنية', 'الخبرة ذات الصلة', 'التوافق الأكاديمي', 'الشهادات', 'المهارات الناعمة', 'معرفة المجال', 'معايير أخرى'],
    scoringNote: 'يُحسب التقييم بناءً على أوزان ملف تقييم الوظيفة.',
    sections: {
      matchedSkills: 'المهارات المطابقة',
      expFit: 'ملاءمة الخبرة',
      academicFit: 'الملاءمة الأكاديمية',
      certifications: 'الشهادات',
      gaps: 'الفجوات المحددة',
      evalNotes: 'ملاحظات التقييم',
      strengths: 'نقاط القوة',
      risks: 'المخاطر المحددة',
    },
    intelligence: 'تحليل الذكاء',
    cvLanguage: 'لغة السيرة الذاتية',
    semanticSimilarity: 'التشابه الدلالي',
    skillMatch: 'تطابق المهارات',
    gatekeeperPassed: 'الحارس الذكي',
    passed: 'اجتاز',
    filtered: 'مُصفَّى',
    matchedSkillsLabel: 'المهارات المتطابقة',
    missingSkillsLabel: 'المهارات المفقودة',
    noMatchedSkills: 'لا توجد مهارات متطابقة مسجلة.',
    redFlags: 'مؤشرات الخطر',
    reasoning: 'استنتاج الذكاء الاصطناعي',
    interviewPrep: 'التحضير للمقابلة',
    focusAreas: 'مجالات التركيز',
    suggestedQs: 'أسئلة مقترحة',
    aiComparison: 'نتائج المقارنة بالذكاء الاصطناعي',
    compProvider: 'المزود',
    compModel: 'النموذج',
    compScore: 'النتيجة',
    compDelta: 'الفارق عن الأساسي',
    promptTracking: 'تدقيق التقييم',
    primaryProvider: 'المزود الأساسي',
    primaryModel: 'النموذج الأساسي',
    scoringPrompt: 'نموذج التقييم',
    level2Prompt: 'نموذج المستوى 2',
    promptVersion: 'إ',
    rawPayload: 'المطور: البيانات الخام للذكاء الاصطناعي',
    possibleDuplicate: 'مكرر محتمل',
    dupBannerTitle: 'قد يكون هذا الطلب مكرراً',
    dupContentSimilarity: 'تشابه محتوى عالي',
    dupIdentityMatch: 'تطابق هوية',
    dupRefLabel: 'الطلب المرجعي السابق',
    dupCandidateName: 'المرشح',
    dupSubmittedOn: 'تاريخ التقديم',
    dupRefId: 'رقم المرجع',
    dupSimilarityScore: 'درجة التشابه',
    dupReason: 'السبب',
    dupCheckedAt: 'وقت الفحص',
    intakeTitle: 'مصدر الطلب',
    intakeSource: 'المصدر',
    intakeReceived: 'تاريخ الاستلام',
    intakeSender: 'المُرسِل / الرافع',
    intakeFile: 'الملف الأصلي',
    intakeSourceLabels: {
      manual_upload:    'رفع',
      email_forwarding: 'بريد إلكتروني',
      platform_email:   'بريد مخصص',
      public_apply:     'الرابط',
    } as Record<string, string>,
    evidenceAnalysis: 'تحليل قائم على الأدلة',
    matchedEvidence: 'الأدلة المطابقة',
    missingEvidence: 'الأدلة الناقصة / الضعيفة',
    additionalStrengths: 'نقاط قوة إضافية ذات صلة',
    notInFinalScore: 'غير محتسب في النتيجة النهائية',
    additionalInsights: 'رؤى إضافية للمسؤول',
    scoreOverview: 'نظرة عامة على النتيجة',
    securityBlockedPageTitle: 'مراجعة أمان الطلب',
    securityBlockedBadge: 'محجوب قبل التقييم بالذكاء الاصطناعي',
    securityCandidateRef: 'مرجع المرشح',
    securityCheck: 'فحص الأمان',
    securityPassed: 'اجتاز',
    securityWarning: 'تحذير',
    securityBlocked: 'محجوب',
    securityAlertTitle: 'محتوى مشبوه — تم حجب الطلب',
    securityWarningTitle: 'محتوى مشبوه — قيد المراجعة',
    securityWhyFlagged: 'لماذا تم تحديد هذا الطلب؟',
    securityBlockedSummary: 'تحتوي هذه السيرة الذاتية على محتوى يبدو مصمماً للتلاعب بعملية التقييم الآلي. تم حجب الطلب ولم يُقيَّم.',
    securityWarningSummary: 'تحتوي هذه السيرة الذاتية على محتوى قد يُحاول التأثير على عملية التقييم الآلي. تم السماح بمرور الطلب ويجب مراجعته بعناية.',
    securityGuidanceNote: 'يرجى مراجعة هذا الطلب بعناية قبل المتابعة.',
    securityRiskLevel: 'مستوى المخاطر',
    securitySeverityScore: 'درجة الخطورة',
    securityIssueTypes: 'أنواع المشكلات المكتشفة',
    securityCheckedAt: 'وقت الفحص',
    stoppedReasonTitle: 'توقف قبل التقييم بالذكاء الاصطناعي',
    stoppedReasonCategory: 'فئة السبب',
    stoppedReasonDetail: 'السبب التفصيلي',
    stoppedReasonNoDetail: 'لم يُسجَّل أي سبب تفصيلي.',
    stoppedReasonUnknown: 'فشل / غير معروف',
    stoppedTechnicalStatus: 'الحالة التقنية',
    stoppedProcessingStatus: 'حالة المعالجة',
    stoppedEvalStage: 'مرحلة التقييم',
    stoppedMeaningfulReasonLabel: 'السبب المفهوم',
    stoppedTechnicalReason: 'السبب التقني',
    stoppedNoTechnicalReason: 'لم يُسجَّل أي سبب تقني.',
    stoppedMeaningfulReasons: {
      security_blocked:  'تم حجب السيرة الذاتية لاحتوائها على محتوى مشبوه قد يُحاول التلاعب بعملية التقييم الآلي.',
      extraction_failed: 'تعذّر قراءة السيرة الذاتية بشكل موثوق. النص المستخرج مفقود أو تالف أو قصير جداً للتقييم.',
      processing_error:  'تعذّرت معالجة الطلب بسبب خطأ في النظام أو في عملية التقييم. قد يتطلب مراجعة تقنية.',
      duplicate_blocked: 'تم اكتشاف أن هذه السيرة الذاتية مكررة بشكل مطابق لطلب تقديم سابق. تم حجبها تلقائياً ولم تُقيَّم.',
      other:             'توقّف الطلب قبل التقييم بالذكاء الاصطناعي ويتطلب مراجعة.',
    } as Record<string, string>,
    stoppedLabels: {
      security_blocked:  'محجوب أمنياً',
      extraction_failed: 'فشل الاستخراج',
      processing_error:  'خطأ في المعالجة',
      duplicate_blocked: 'مكرر موقوف',
      other:             'فشل',
    } as Record<string, string>,
    dupDetailTitle: 'تفاصيل الطلب المكرر',
    dupDetailReason: 'نوع التطابق',
    dupDetailOriginalCandidate: 'المرشح الأصلي',
    dupDetailOriginalRef: 'معرف الطلب المرجعي',
    dupDetailOriginalSubmitted: 'تاريخ التقديم الأصلي',
    dupDetailSimilarity: 'درجة التشابه',
    dupDetailCheckedAt: 'وقت الاكتشاف',
    dupDetailReasonLabels: {
      file_hash:                  'تطابق مطابق للملف (بايت بايت)',
      normalized_text_hash:       'تطابق مطابق للمحتوى (نص مستخرج متطابق)',
      canonical_text_fingerprint: 'تطابق بين صيغ مختلفة (نفس السيرة الذاتية بتنسيق آخر)',
      content_similarity_fallback:'تشابه عالٍ في المحتوى',
    } as Record<string, string>,
    dupDetailUnknownRef: 'الطلب المرجعي غير متاح',
    knockoutSectionTitle: 'أسئلة الفرز المسبق',
    knockoutNoAnswers: 'لم يُقدِّم المتقدم أي إجابات على أسئلة الفرز.',
    knockoutQuestion: 'السؤال',
    knockoutAnswer: 'الإجابة',
    knockoutRequired: 'إلزامي',
    knockoutPassingCriteria: 'معايير الاجتياز',
    knockoutStatus: 'النتيجة',
    knockoutPassed: 'اجتاز',
    knockoutFailed: 'لم يجتز',
    knockoutNoCriteria: 'لا معايير',
    knockoutNotEvaluated: 'غير مُقيَّم',
    knockoutCriteriaPass: (op: string, val: string) => `ينجح إذا كانت الإجابة ${op} ${val}`,
    knockoutCriteriaAnswers: (answers: string[]) => `يجب الإجابة بـ: ${answers.join(' أو ')}`,
    securityDetectedStatements: 'العبارات المشبوهة المكتشفة',
    securitySnippetsNote: 'مقاطع قصيرة تُعرض لأغراض المراجعة. لا يُعرض النص الكامل للسيرة الذاتية هنا.',
    securityPatternLabels: {
      override_instructions:          'محاولة تجاوز التعليمات',
      score_manipulation:             'محاولة التلاعب بالدرجات',
      reveal_prompt:                  'محاولة كشف النظام',
      jailbreak:                      'نمط تجاوز القيود',
      scoring_rule_change:            'محاولة تغيير معايير التقييم',
      auto_qualify:                   'محاولة الإجازة التلقائية',
      forced_ranking:                 'محاولة ترتيب إجباري',
      automatic_pass:                 'محاولة الاجتياز التلقائي',
      prompt_disclosure_attempt:      'محاولة كشف منطق التقييم',
      bypass_evaluation_rules:        'محاولة تجاوز قواعد التقييم',
      false_requirement_satisfaction: 'محاولة تلبية متطلبات وهمية',
      obfuscated:                     'محتوى مخفي',
      encoded_payload:                'حمولة مشفرة',
      unicode_spam:                   'تشوه يونيكود',
    } as Record<string, string>,
    securityReasonExplanations: {
      override_instructions:          'حاولت السيرة الذاتية تجاوز أو تجاهل تعليمات التقييم.',
      score_manipulation:             'حاولت السيرة الذاتية التأثير اصطناعياً على درجة المرشح.',
      reveal_prompt:                  'حاولت السيرة الذاتية كشف المنطق الداخلي لعملية التقييم.',
      jailbreak:                      'حاولت السيرة الذاتية تعطيل قواعد نظام التقييم الذكي.',
      scoring_rule_change:            'حاولت السيرة الذاتية تغيير معايير أو قواعد التقييم.',
      auto_qualify:                   'حاولت السيرة الذاتية تصنيف المرشح تلقائياً كمؤهل.',
      forced_ranking:                 'حاولت السيرة الذاتية إجبار النظام على تصنيف المرشح ضمن الأفضل.',
      automatic_pass:                 'حاولت السيرة الذاتية اجتياز مراحل الفرز الأولي تلقائياً.',
      prompt_disclosure_attempt:      'حاولت السيرة الذاتية الكشف عن منطق التقييم السري أو المعايير الداخلية.',
      bypass_evaluation_rules:        'حاولت السيرة الذاتية تجاوز القيود والمعايير المعتادة للتقييم.',
      false_requirement_satisfaction: 'حاولت السيرة الذاتية اعتبار المتطلبات المفقودة مستوفاة بغض النظر عن الأدلة.',
      obfuscated:                     'تحتوي السيرة الذاتية على نص مخفي أو مبهم للتحايل على الفحص.',
      encoded_payload:                'تحتوي السيرة الذاتية على محتوى مشفر يُخفي تعليمات مشبوهة.',
      unicode_spam:                   'تحتوي السيرة الذاتية على أحرف غير مرئية قد تُستخدم لإخفاء محتوى.',
    } as Record<string, string>,
  },
};

// Maps score bar index to reasoning key
const REASONING_KEYS = ['skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other'];

const LANG_LABELS: Record<string, { en: string; ar: string }> = {
  ar:    { en: 'Arabic',  ar: 'عربي'  },
  en:    { en: 'English', ar: 'إنجليزي' },
  mixed: { en: 'Mixed',   ar: 'مختلط' },
};

export const ApplicationDetails: React.FC<ApplicationDetailsProps> = ({ data, onBack, jobMeta, onDownloadCV, downloadingCV, onWorkflowStatusChange, onRecruiterNotesChange }) => {
  const { lang, isAr } = useLanguage() as { lang: 'en' | 'ar'; isAr: boolean };
  const t = T[lang];

  const [showRaw, setShowRaw] = useState(false);
  const [intelligenceExpanded, setIntelligenceExpanded] = useState(
    data.gatekeeper_passed === false
  );
  const [noteInput, setNoteInput] = useState('');
  const [showNoteInput, setShowNoteInput] = useState(false);
  const [pendingStatus, setPendingStatus] = useState<WorkflowStatus | null>(null);
  const [recruiterNotesText, setRecruiterNotesText] = useState(data.recruiter_notes ?? '');
  const [notesDirty, setNotesDirty] = useState(false);

  const currentWorkflowStatus: WorkflowStatus = (data.workflow_status as WorkflowStatus) || 'awaiting_review';

  const VALID_TRANSITIONS: Record<WorkflowStatus, WorkflowStatus[]> = {
    awaiting_review: ['under_review', 'on_hold', 'rejected', 'withdrawn'],
    under_review:    ['shortlisted', 'on_hold', 'rejected', 'withdrawn'],
    shortlisted:     ['interviewing', 'under_review', 'on_hold', 'rejected', 'withdrawn'],
    interviewing:    ['offer_made', 'shortlisted', 'on_hold', 'rejected', 'withdrawn'],
    offer_made:      ['hired', 'interviewing', 'on_hold', 'rejected', 'withdrawn'],
    hired:           [],
    rejected:        ['awaiting_review', 'under_review'],
    withdrawn:       ['awaiting_review', 'under_review'],
    on_hold:         ['awaiting_review', 'under_review', 'shortlisted', 'interviewing', 'offer_made', 'rejected', 'withdrawn'],
  };

  const WF_LABELS: Record<WorkflowStatus, string> = {
    awaiting_review: 'Awaiting Review',
    under_review:    'Under Review',
    shortlisted:     'Shortlisted',
    interviewing:    'Interviewing',
    offer_made:      'Offer Made',
    hired:           'Hired',
    rejected:        'Rejected',
    withdrawn:       'Withdrawn',
    on_hold:         'On Hold',
  };

  const WF_STYLES: Record<WorkflowStatus, string> = {
    awaiting_review: 'bg-sky-100 text-sky-700 border-sky-200',
    under_review:    'bg-blue-100 text-blue-700 border-blue-200',
    shortlisted:     'bg-indigo-100 text-indigo-700 border-indigo-200',
    interviewing:    'bg-purple-100 text-purple-700 border-purple-200',
    offer_made:      'bg-amber-100 text-amber-700 border-amber-200',
    hired:           'bg-green-100 text-green-800 border-green-200',
    rejected:        'bg-red-100 text-red-700 border-red-200',
    withdrawn:       'bg-orange-100 text-orange-700 border-orange-200',
    on_hold:         'bg-yellow-100 text-yellow-700 border-yellow-200',
  };

  const WF_ACTION_LABELS: Record<WorkflowStatus, string> = {
    awaiting_review: 'Awaiting Review',
    under_review:    'Start Review',
    shortlisted:     'Shortlist',
    interviewing:    'Move to Interview',
    offer_made:      'Make Offer',
    hired:           'Mark Hired',
    rejected:        'Reject',
    withdrawn:       'Mark Withdrawn',
    on_hold:         'Put On Hold',
  };

  const handleTransitionClick = (target: WorkflowStatus) => {
    if (!onWorkflowStatusChange) return;
    if (target === 'hired' || target === 'rejected' || target === 'withdrawn') {
      setPendingStatus(target);
      setShowNoteInput(true);
    } else {
      onWorkflowStatusChange(data.application_id, target);
    }
  };

  const confirmTransition = () => {
    if (!pendingStatus || !onWorkflowStatusChange) return;
    onWorkflowStatusChange(data.application_id, pendingStatus, noteInput.trim() || undefined);
    setPendingStatus(null);
    setNoteInput('');
    setShowNoteInput(false);
  };

  const handleSaveNotes = () => {
    if (!onRecruiterNotesChange) return;
    onRecruiterNotesChange(data.application_id, recruiterNotesText.trim() || null);
    setNotesDirty(false);
  };

  const getScoreBadgeColor = (achieved: number, max: number) => {
    if (!max || max === 0) return 'bg-slate-100 text-slate-600';
    const pct = (achieved / max) * 100;
    if (pct >= 80) return 'bg-green-100 text-green-800';
    if (pct >= 60) return 'bg-amber-100 text-amber-800';
    return 'bg-red-100 text-red-800';
  };

  const renderEvidenceCard = (
    label: string,
    dim: ScoreDimension | undefined,
    detail: ScoreDetail | undefined,
    reasoning: string,
    isInsight = false,
  ) => {
    if (!dim && !detail) return null;
    const hasDim = dim && dim.max > 0;
    const pct = hasDim ? Math.round((dim!.achieved / dim!.max) * 100) : 0;
    const badgeColor = hasDim ? getScoreBadgeColor(dim!.achieved, dim!.max) : 'bg-slate-100 text-slate-500';
    const hasEvidence = (detail?.positive?.length ?? 0) > 0 || (detail?.negative?.length ?? 0) > 0 || (detail?.additional_strengths?.length ?? 0) > 0;

    return (
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden flex flex-col">
        <div className="px-5 py-3 border-b border-slate-100 flex items-center justify-between gap-2 bg-slate-50 flex-wrap">
          <h5 className="text-[10px] font-black text-textMain uppercase tracking-wider">{label}</h5>
          <div className={`flex items-center gap-2 ${isAr ? 'flex-row-reverse' : ''}`}>
            {hasDim && (
              <span className={`px-2 py-0.5 rounded-full text-[10px] font-black ${badgeColor}`}>
                {dim!.achieved} / {dim!.max}
              </span>
            )}
            {hasDim && !isInsight && dim!.weight != null && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-black bg-indigo-50 text-indigo-700">
                {dim!.weight}%
              </span>
            )}
            {isInsight && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-500">
                {t.notInFinalScore}
              </span>
            )}
          </div>
        </div>
        <div className="px-5 py-4 space-y-3 flex-1">
          {(detail?.positive?.length ?? 0) > 0 && (
            <div>
              <p className="text-[9px] font-black text-green-700 uppercase tracking-widest mb-1.5 flex items-center gap-1">
                <span>✅</span> {t.matchedEvidence}
              </p>
              <ul className="space-y-1">
                {detail!.positive.map((item, i) => (
                  <li key={i} className="text-xs text-textMain leading-relaxed flex items-start gap-1.5">
                    <span className="text-green-500 mt-0.5 shrink-0">•</span><span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(detail?.negative?.length ?? 0) > 0 && (
            <div>
              <p className="text-[9px] font-black text-red-600 uppercase tracking-widest mb-1.5 flex items-center gap-1">
                <span>❌</span> {t.missingEvidence}
              </p>
              <ul className="space-y-1">
                {detail!.negative.map((item, i) => (
                  <li key={i} className="text-xs text-textMain leading-relaxed flex items-start gap-1.5">
                    <span className="text-red-400 mt-0.5 shrink-0">•</span><span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(detail?.additional_strengths?.length ?? 0) > 0 && (
            <div>
              <p className="text-[9px] font-black text-blue-700 uppercase tracking-widest mb-1.5 flex items-center gap-1">
                <span>➕</span> {t.additionalStrengths}
              </p>
              <ul className="space-y-1">
                {detail!.additional_strengths!.map((item, i) => (
                  <li key={i} className="text-xs text-textMain leading-relaxed flex items-start gap-1.5">
                    <span className="text-blue-400 mt-0.5 shrink-0">•</span><span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!hasEvidence && reasoning && (
            <p className="text-xs text-textMuted italic leading-relaxed">{reasoning}</p>
          )}
          {detail?.summary && (
            <p className="text-[10px] text-textMuted italic leading-relaxed pt-2 border-t border-slate-100">{detail.summary}</p>
          )}
        </div>
      </div>
    );
  };

  const renderSection = (title: string, content: any, icon?: React.ReactNode) => {
    if (!content || (Array.isArray(content) && content.length === 0)) return null;

    return (
      <div className="bg-white p-6 rounded-2xl border border-border shadow-sm flex flex-col h-full">
        <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4 flex items-center">
          {icon && <span className={isAr ? 'ml-2' : 'mr-2'}>{icon}</span>}
          {title}
        </h4>
        <div className="flex-1">
          {typeof content === 'string' ? (
            <p className="text-sm text-textMain leading-relaxed whitespace-pre-wrap">{content}</p>
          ) : Array.isArray(content) ? (
            <ul className="space-y-2">
              {content.map((item, i) => (
                <li key={i} className="text-sm text-textMain flex items-start">
                  <span className={`text-primary font-bold ${isAr ? 'ml-2' : 'mr-2'}`}>•</span>
                  <span className="leading-relaxed">{item}</span>
                </li>
              ))}
            </ul>
          ) : (
            <pre className="text-[10px] text-textMain bg-slate-50 p-3 rounded-lg overflow-x-auto">
              {JSON.stringify(content, null, 2)}
            </pre>
          )}
        </div>
      </div>
    );
  };

  const candidateName = data.candidate_name || 'Candidate Analysis';
  const score = data.overall_score || 0;
  const analysis = data.analysis || ({} as ApplicationDetailedAnalysis['analysis']);
  const scores = data.scores || {};
  const isLowMatch = data.decision === 'low_match';
  const isSecurityBlocked =
    data.stopped_reason === 'security_blocked' ||
    data.security_check_status === 'blocked' ||
    (data.evaluation_exit_reason?.startsWith('[security_check]') ?? false);

  const scoreDims: [string, ScoreDimension | undefined, string][] = [
    [t.scoreBars[0], scores.skills,            data.reasoning?.skills            || ''],
    [t.scoreBars[1], scores.experience,        data.reasoning?.experience        || ''],
    [t.scoreBars[2], scores.education,         data.reasoning?.education         || ''],
    [t.scoreBars[3], scores.certifications,    data.reasoning?.certifications    || ''],
    [t.scoreBars[4], scores.soft_skills,       data.reasoning?.soft_skills       || ''],
    [t.scoreBars[5], scores.domain_knowledge,  data.reasoning?.domain_knowledge  || ''],
    [t.scoreBars[6], scores.other_requirements, data.reasoning?.other            || ''],
  ];

  // Evidence dims: pairs label+dim+detail+reasoning for each scoring dimension
  const DETAIL_KEYS = ['skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other'] as const;
  const SCORES_KEYS: (keyof typeof scores)[] = ['skills', 'experience', 'education', 'certifications', 'soft_skills', 'domain_knowledge', 'other_requirements'];
  const evidenceDims = DETAIL_KEYS.map((dk, i) => ({
    label: t.scoreBars[i],
    dim: scores[SCORES_KEYS[i]],
    detail: data.score_details?.[dk],
    reasoning: scoreDims[i][2],
  }));
  const weightedEvidenceDims = evidenceDims.filter(d => !d.dim || (d.dim.weight ?? 1) > 0);
  const zeroWeightEvidenceDims = evidenceDims.filter(d => d.dim && d.dim.weight === 0);
  const hasEvidenceSection = !isLowMatch && (data.score_details != null || evidenceDims.some(d => d.dim));

  const hasIntelligence = data.local_similarity_score != null || data.cv_language || (data.matched_skills?.length ?? 0) > 0 || (data.missing_skills?.length ?? 0) > 0;
  const hasRedFlags = (data.red_flags?.length ?? 0) > 0;
  const cvLangKey = (data.cv_language || 'en') as keyof typeof LANG_LABELS;
  const cvLangLabel = (LANG_LABELS[cvLangKey] || LANG_LABELS['en'])[lang];

  const decisionStyles: Record<string, string> = {
    qualified: 'bg-green-100 text-green-800',
    partial:   'bg-amber-100 text-amber-800',
    rejected:  'bg-red-100 text-red-800',
    low_match: 'bg-slate-100 text-slate-600',
  };
  const decisionLabel: Record<string, string> = {
    qualified: lang === 'ar' ? 'مؤهل' : 'Qualified',
    partial:   lang === 'ar' ? 'جزئي' : 'Partial',
    rejected:  lang === 'ar' ? 'مرفوض' : 'Rejected',
    low_match: t.lowMatch,
  };

  return (
    <div className="space-y-8 animate-fade-in max-w-6xl mx-auto pb-12">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <button onClick={onBack} className="flex items-center text-primary hover:text-primaryDark transition-colors text-sm font-bold group gap-2">
          <svg className={`w-4 h-4 transform transition-transform ${isAr ? 'group-hover:translate-x-1 rotate-180' : 'group-hover:-translate-x-1'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t.back}
        </button>
        {onDownloadCV && (
          <button
            onClick={onDownloadCV}
            disabled={downloadingCV}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded-lg text-xs font-bold text-textMuted hover:text-primary hover:border-primary transition-colors disabled:opacity-50"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
            {downloadingCV ? (lang === 'ar' ? 'جارٍ التحميل…' : 'Downloading…') : (lang === 'ar' ? 'تحميل السيرة' : 'Download CV')}
          </button>
        )}
      </div>

      {/* Job metadata strip */}
      {jobMeta && (
        <div className="bg-white rounded-xl border border-border px-5 py-3.5 flex flex-wrap items-center gap-x-5 gap-y-2 shadow-sm">
          <div className="min-w-0">
            <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-0.5">{jobMeta.job_code}</p>
            <p className="text-sm font-bold text-textMain truncate">{jobMeta.job_title}</p>
          </div>
          {jobMeta.client_org_name !== undefined && (
            <span className={`shrink-0 text-xs font-bold px-2 py-0.5 rounded-full ${jobMeta.client_org_name ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
              {jobMeta.client_org_name || (lang === 'ar' ? 'عام' : 'General')}
            </span>
          )}
          {jobMeta.job_type && <span className="shrink-0 text-xs text-textMuted font-medium">{jobMeta.job_type}</span>}
          {jobMeta.location && <span className="shrink-0 text-xs text-textMuted">{jobMeta.location}</span>}
          <span className={`shrink-0 ml-auto px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase ${
            jobMeta.job_status === 'Active' ? 'bg-green-100 text-green-800' :
            jobMeta.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
            'bg-amber-100 text-amber-800'
          }`}>{jobMeta.job_status}</span>
        </div>
      )}

      {/* ── Security-Blocked: page heading + candidate card ─────────────────── */}
      {isSecurityBlocked && (() => {
        const secStatus   = data.security_check_status!;
        const secLevel    = data.security_risk_level || '';
        const secScore    = data.security_risk_score ?? 0;
        const secCodes    = data.security_reason_codes || [];
        const secPatterns = data.security_detected_patterns || [];
        const secSnippets = data.security_detected_snippets || [];
        const secAt: string | null = data.security_checked_at || null;
        const patternLabels      = (t as any).securityPatternLabels as Record<string, string>;
        const reasonExplanations = (t as any).securityReasonExplanations as Record<string, string>;
        return (
          <div className="space-y-6">
            {/* Page heading */}
            <div className="flex items-center gap-3 flex-wrap">
              <h2 className="text-xl font-black text-red-700 tracking-tight">
                {(t as any).securityBlockedPageTitle}
              </h2>
              <span className="px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-red-600 text-white">
                {(t as any).securityBlockedBadge}
              </span>
            </div>

            {/* Candidate identification card — no score, no summary */}
            <div className="bg-white rounded-3xl border border-red-200 shadow-sm overflow-hidden">
              <div className="p-8 bg-red-50 border-b border-red-100 flex items-center gap-6 flex-wrap">
                {/* Blocked icon in place of score circle */}
                <div className="w-24 h-24 rounded-3xl flex flex-col items-center justify-center bg-red-600 text-white shadow-xl shrink-0">
                  <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <span className="px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-red-100 text-red-800">
                      {(t as any).securityBlocked}
                    </span>
                    {data.duplicate_status === 'possible_duplicate' && (
                      <span className="px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-orange-100 text-orange-700">
                        {t.possibleDuplicate}
                      </span>
                    )}
                    <span className="text-[10px] font-black text-red-400 uppercase tracking-widest">• REF: {data.application_id}</span>
                  </div>
                  <h1 className="text-3xl font-black text-textMain tracking-tight">{candidateName}</h1>
                </div>
              </div>
            </div>

            {/* Duplicate banner (if applicable) */}
            {data.duplicate_status === 'possible_duplicate' && (
              <div className={`bg-orange-50 border ${isAr ? 'border-r-4' : 'border-l-4'} border-orange-400 rounded-2xl p-5`}>
                <div className="flex items-start gap-3 mb-3">
                  <svg className="w-5 h-5 text-orange-500 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  <div className="flex-1">
                    <p className="text-sm font-bold text-orange-800">{t.dupBannerTitle}</p>
                    {data.duplicate_reason && (
                      <p className="text-xs text-orange-600 mt-0.5">
                        {data.duplicate_reason === 'high_content_similarity' ? t.dupContentSimilarity : t.dupIdentityMatch}
                        {data.duplicate_similarity_score != null && ` — ${data.duplicate_similarity_score.toFixed(1)}%`}
                      </p>
                    )}
                  </div>
                  <span className="shrink-0 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-orange-100 text-orange-700">
                    {t.possibleDuplicate}
                  </span>
                </div>
                {data.duplicate_reference && (
                  <div className="mt-3 bg-white rounded-xl border border-orange-200 px-4 py-3">
                    <p className="text-[10px] font-black text-orange-500 uppercase tracking-widest mb-2">{t.dupRefLabel}</p>
                    <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                      <span className="text-textMuted">{t.dupCandidateName}</span>
                      <span className="font-semibold text-textMain">{data.duplicate_reference.candidate_name}</span>
                      {data.duplicate_reference.applied_at && (
                        <>
                          <span className="text-textMuted">{t.dupSubmittedOn}</span>
                          <span className="font-semibold text-textMain">{new Date(data.duplicate_reference.applied_at).toLocaleDateString()}</span>
                        </>
                      )}
                      <span className="text-textMuted">{t.dupRefId}</span>
                      <span className="font-mono text-[10px] text-textMuted">{data.duplicate_reference.application_id.slice(0, 8)}…</span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Security detail panel — full expanded as primary content */}
            <div className="rounded-2xl border-l-4 border-red-500 overflow-hidden">
              <div className="flex items-start gap-3 px-5 py-4 bg-red-600">
                <svg className="w-5 h-5 mt-0.5 shrink-0 text-white" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                </svg>
                <p className="flex-1 text-sm font-bold text-white leading-snug">
                  {(t as any).securityAlertTitle}
                </p>
                <span className="shrink-0 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-white/20 text-white">
                  {(t as any).securityBlocked}
                </span>
              </div>
              <div className="px-5 py-4 space-y-4 bg-red-50">
                {/* Why flagged */}
                <div className="rounded-xl p-4 border bg-red-100/60 border-red-200">
                  <p className="text-[10px] font-black uppercase tracking-widest mb-2 text-red-700">
                    {(t as any).securityWhyFlagged}
                  </p>
                  <p className="text-xs mb-2 leading-relaxed text-red-900">
                    {(t as any).securityBlockedSummary}
                  </p>
                  {secCodes.length > 0 && (
                    <ul className="space-y-1 mt-2">
                      {secCodes.map(code => {
                        const explanation = reasonExplanations[code];
                        return explanation ? (
                          <li key={code} className="flex items-start gap-2 text-xs text-red-800">
                            <span className="mt-1 shrink-0 w-1.5 h-1.5 rounded-full bg-current opacity-70" />
                            {explanation}
                          </li>
                        ) : null;
                      })}
                    </ul>
                  )}
                </div>
                {/* Guidance */}
                <p className="text-xs font-semibold text-red-700">⚠ {(t as any).securityGuidanceNote}</p>
                {/* Details grid */}
                <div className="bg-white rounded-xl border border-slate-200 px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
                  <span className="text-textMuted">{(t as any).securityRiskLevel}</span>
                  <span className="font-bold uppercase text-red-600">{secLevel}</span>
                  <span className="text-textMuted">{(t as any).securitySeverityScore}</span>
                  <span className="font-semibold text-textMain">{secScore}</span>
                  {secPatterns.length > 0 && (
                    <>
                      <span className="text-textMuted">{(t as any).securityIssueTypes}</span>
                      <span className="font-semibold text-textMain">{secPatterns.map(p => patternLabels[p] || p).join(', ')}</span>
                    </>
                  )}
                  {secAt && (
                    <>
                      <span className="text-textMuted">{(t as any).securityCheckedAt}</span>
                      <span className="text-textMain">{new Date(secAt).toLocaleString()}</span>
                    </>
                  )}
                  {data.evaluation_exit_reason && (
                    <>
                      <span className="text-textMuted col-span-2 mt-1 pt-1 border-t border-slate-100">{(t as any).stoppedTechnicalReason}</span>
                      <span className="text-textMain col-span-2 text-xs leading-relaxed break-words font-mono">{data.evaluation_exit_reason}</span>
                    </>
                  )}
                </div>
                {/* Snippets */}
                {secSnippets.length > 0 && (
                  <div>
                    <p className="text-[10px] font-black uppercase tracking-widest mb-2 text-red-700">
                      {(t as any).securityDetectedStatements}
                    </p>
                    <ul className="space-y-1.5">
                      {secSnippets.map((snippet, i) => (
                        <li key={i} className="text-xs rounded-lg px-3 py-2 font-mono leading-relaxed bg-red-100 text-red-900">
                          &ldquo;{snippet}&rdquo;
                        </li>
                      ))}
                    </ul>
                    <p className="text-[10px] text-textMuted mt-2 italic">{(t as any).securitySnippetsNote}</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}

      {/* Low Match Banner */}
      {!isSecurityBlocked && isLowMatch && (
        <div className={`bg-slate-50 border ${isAr ? 'border-r-4' : 'border-l-4'} border-slate-400 rounded-2xl p-5 flex items-start gap-4`}>
          <svg className="w-5 h-5 text-slate-500 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
          <p className="text-sm text-slate-600 leading-relaxed">{t.gatekeeperFiltered}</p>
        </div>
      )}

      {/* Possible Duplicate Banner — only in normal layout; blocked layout renders its own */}
      {!isSecurityBlocked && data.duplicate_status === 'possible_duplicate' && (
        <div className={`bg-orange-50 border ${isAr ? 'border-r-4' : 'border-l-4'} border-orange-400 rounded-2xl p-5`}>
          <div className="flex items-start gap-3 mb-3">
            <svg className="w-5 h-5 text-orange-500 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <div className="flex-1">
              <p className="text-sm font-bold text-orange-800">{t.dupBannerTitle}</p>
              {data.duplicate_reason && (
                <p className="text-xs text-orange-600 mt-0.5">
                  {data.duplicate_reason === 'high_content_similarity' ? t.dupContentSimilarity : t.dupIdentityMatch}
                  {data.duplicate_similarity_score != null && ` — ${data.duplicate_similarity_score.toFixed(1)}%`}
                </p>
              )}
            </div>
            <span className="shrink-0 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-orange-100 text-orange-700">
              {t.possibleDuplicate}
            </span>
          </div>
          {data.duplicate_reference && (
            <div className="mt-3 bg-white rounded-xl border border-orange-200 px-4 py-3">
              <p className="text-[10px] font-black text-orange-500 uppercase tracking-widest mb-2">{t.dupRefLabel}</p>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                <span className="text-textMuted">{t.dupCandidateName}</span>
                <span className="font-semibold text-textMain">{data.duplicate_reference.candidate_name}</span>
                {data.duplicate_reference.applied_at && (
                  <>
                    <span className="text-textMuted">{t.dupSubmittedOn}</span>
                    <span className="font-semibold text-textMain">
                      {new Date(data.duplicate_reference.applied_at).toLocaleDateString()}
                    </span>
                  </>
                )}
                <span className="text-textMuted">{t.dupRefId}</span>
                <span className="font-mono text-[10px] text-textMuted">{data.duplicate_reference.application_id.slice(0, 8)}…</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Security Check Banner — warning state only; blocked uses dedicated layout above ── */}
      {!isSecurityBlocked && data.security_check_status && data.security_check_status !== 'passed' && (() => {
        const secStatus   = data.security_check_status!;
        const secLevel    = data.security_risk_level || '';
        const secScore    = data.security_risk_score ?? 0;
        const secCodes    = data.security_reason_codes || [];
        const secPatterns = data.security_detected_patterns || [];
        const secSnippets = data.security_detected_snippets || [];
        const secAt: string | null = data.security_checked_at || null;
        const isBlocked = secStatus === 'blocked';
        const patternLabels = (t as any).securityPatternLabels as Record<string, string>;
        const reasonExplanations = (t as any).securityReasonExplanations as Record<string, string>;
        return (
          <div className={`rounded-2xl border-l-4 overflow-hidden ${isBlocked ? 'border-red-500' : 'border-amber-400'}`}>
            {/* Header */}
            <div className={`flex items-start gap-3 px-5 py-4 ${isBlocked ? 'bg-red-600' : 'bg-amber-500'}`}>
              <svg className="w-5 h-5 mt-0.5 shrink-0 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              <p className="flex-1 text-sm font-bold text-white leading-snug">
                {isBlocked ? (t as any).securityAlertTitle : (t as any).securityWarningTitle}
              </p>
              <span className="shrink-0 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-white/20 text-white">
                {isBlocked ? (t as any).securityBlocked : (t as any).securityWarning}
              </span>
            </div>

            <div className={`px-5 py-4 space-y-4 ${isBlocked ? 'bg-red-50' : 'bg-amber-50'}`}>
              {/* Why flagged explanation */}
              <div className={`rounded-xl p-4 border ${isBlocked ? 'bg-red-100/60 border-red-200' : 'bg-amber-100/60 border-amber-200'}`}>
                <p className={`text-[10px] font-black uppercase tracking-widest mb-2 ${isBlocked ? 'text-red-700' : 'text-amber-700'}`}>
                  {(t as any).securityWhyFlagged}
                </p>
                <p className={`text-xs mb-2 leading-relaxed ${isBlocked ? 'text-red-900' : 'text-amber-900'}`}>
                  {isBlocked ? (t as any).securityBlockedSummary : (t as any).securityWarningSummary}
                </p>
                {secCodes.length > 0 && (
                  <ul className="space-y-1 mt-2">
                    {secCodes.map(code => {
                      const explanation = reasonExplanations[code];
                      return explanation ? (
                        <li key={code} className={`flex items-start gap-2 text-xs ${isBlocked ? 'text-red-800' : 'text-amber-800'}`}>
                          <span className="mt-1 shrink-0 w-1.5 h-1.5 rounded-full bg-current opacity-70" />
                          {explanation}
                        </li>
                      ) : null;
                    })}
                  </ul>
                )}
              </div>

              {/* Guidance note */}
              <p className={`text-xs font-semibold ${isBlocked ? 'text-red-700' : 'text-amber-700'}`}>
                ⚠ {(t as any).securityGuidanceNote}
              </p>

              {/* Technical details grid */}
              <div className="bg-white rounded-xl border border-slate-200 px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
                <span className="text-textMuted">{(t as any).securityRiskLevel}</span>
                <span className={`font-bold uppercase ${secLevel === 'high' ? 'text-red-600' : secLevel === 'medium' ? 'text-amber-600' : 'text-green-600'}`}>{secLevel}</span>
                <span className="text-textMuted">{(t as any).securitySeverityScore}</span>
                <span className="font-semibold text-textMain">{secScore}</span>
                {secPatterns.length > 0 && (
                  <>
                    <span className="text-textMuted">{(t as any).securityIssueTypes}</span>
                    <span className="font-semibold text-textMain">{secPatterns.map(p => patternLabels[p] || p).join(', ')}</span>
                  </>
                )}
                {secAt && (
                  <>
                    <span className="text-textMuted">{(t as any).securityCheckedAt}</span>
                    <span className="text-textMain">{new Date(secAt).toLocaleString()}</span>
                  </>
                )}
              </div>

              {/* Detected snippets */}
              {secSnippets.length > 0 && (
                <div>
                  <p className={`text-[10px] font-black uppercase tracking-widest mb-2 ${isBlocked ? 'text-red-700' : 'text-amber-700'}`}>
                    {(t as any).securityDetectedStatements}
                  </p>
                  <ul className="space-y-1.5">
                    {secSnippets.map((snippet, i) => (
                      <li key={i} className={`text-xs rounded-lg px-3 py-2 font-mono leading-relaxed ${isBlocked ? 'bg-red-100 text-red-900' : 'bg-amber-100 text-amber-900'}`}>
                        &ldquo;{snippet}&rdquo;
                      </li>
                    ))}
                  </ul>
                  <p className="text-[10px] text-textMuted mt-2 italic">
                    {(t as any).securitySnippetsNote}
                  </p>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* ── Application Intake Metadata ─────────────────────────────────────── */}
      {(data.submission_source || data.applied_at || data.email_sender_address ||
        data.submitted_by_name || data.submitted_by_email || data.original_filename) && (() => {
        const sourceLabel = data.submission_source
          ? (t.intakeSourceLabels[data.submission_source] || data.submission_source)
          : null;
        const senderDisplay = (data.submission_source === 'manual_upload')
          ? (data.submitted_by_name || data.submitted_by_email)
          : (data.submission_source === 'public_apply')
            ? null
            : (data.email_sender_address || data.submitted_by_email);
        const rows: [string, string | null | undefined][] = [
          [t.intakeSource,   sourceLabel],
          [t.intakeReceived, data.applied_at ? new Date(data.applied_at).toLocaleString() : null],
          [t.intakeSender,   senderDisplay],
          [t.intakeFile,     data.original_filename],
        ].filter(([, v]) => v) as [string, string][];
        if (rows.length === 0) return null;
        return (
          <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="px-6 py-3 border-b border-border flex items-center gap-2 bg-slate-50">
              <svg className="w-3.5 h-3.5 text-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.intakeTitle}</h4>
            </div>
            <div className="px-6 py-4 grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-3">
              {rows.map(([label, value]) => (
                <div key={label}>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{label}</p>
                  <p className="text-sm font-medium text-textMain break-all">{value}</p>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Stopped Before AI — full detail card for non-security-blocked failed applications */}
      {!isSecurityBlocked && data.processing_status === 'failed' && (() => {
        const st = t as any;
        const sr = data.stopped_reason;
        const labels = st.stoppedLabels as Record<string, string>;
        const meaningfulReasons = st.stoppedMeaningfulReasons as Record<string, string>;
        const categoryLabel = sr ? (labels[sr] || sr) : st.stoppedReasonUnknown;
        const badgeStyle = sr === 'extraction_failed'
          ? 'bg-amber-100 text-amber-700'
          : sr === 'duplicate_blocked'
          ? 'bg-orange-100 text-orange-700'
          : sr === 'processing_error' || !sr
          ? 'bg-slate-100 text-slate-600'
          : 'bg-red-100 text-red-700';
        const meaningfulText = sr ? (meaningfulReasons[sr] || meaningfulReasons['other']) : meaningfulReasons['other'];
        const technicalText = data.evaluation_exit_reason || null;
        const evalStageLabel = data.evaluation_stage != null ? `Stage ${data.evaluation_stage}` : '—';
        return (
          <div className="bg-white rounded-2xl border border-amber-200 shadow-sm overflow-hidden">
            <div className="px-6 py-3 border-b border-amber-200 bg-amber-50 flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-amber-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <h4 className="text-[10px] font-black text-amber-700 uppercase tracking-widest">{st.stoppedReasonTitle}</h4>
            </div>
            <div className="px-6 py-5 grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-4">
              {/* Reason Category */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{st.stoppedReasonCategory}</p>
                <span className={`inline-block px-2.5 py-0.5 rounded-full text-[11px] font-black ${badgeStyle}`}>{categoryLabel}</span>
              </div>
              {/* Meaningful Reason */}
              <div className="md:col-span-2">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{st.stoppedMeaningfulReasonLabel}</p>
                <p className="text-sm leading-relaxed text-textMain">{meaningfulText}</p>
              </div>
              {/* Technical Status */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{st.stoppedTechnicalStatus}</p>
                <div className="space-y-1 text-xs text-textMuted">
                  <div className="flex gap-2">
                    <span className="font-semibold">{st.stoppedProcessingStatus}:</span>
                    <span className="font-mono">{data.processing_status}</span>
                  </div>
                  <div className="flex gap-2">
                    <span className="font-semibold">{st.stoppedEvalStage}:</span>
                    <span className="font-mono">{evalStageLabel}</span>
                  </div>
                </div>
              </div>
              {/* Technical Reason */}
              <div className="md:col-span-2">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{st.stoppedTechnicalReason}</p>
                <p className={`text-sm leading-relaxed font-mono ${technicalText ? 'text-textMain' : 'text-textMuted italic'}`}>
                  {technicalText || st.stoppedNoTechnicalReason}
                </p>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Duplicate Application Details — shown for exact_duplicate and possible_duplicate */}
      {(data.duplicate_status === 'exact_duplicate' || data.duplicate_status === 'possible_duplicate') && (() => {
        const dt = t as any;
        const reasonLabels = dt.dupDetailReasonLabels as Record<string, string>;
        const reasonLabel = data.duplicate_reason
          ? (reasonLabels[data.duplicate_reason] || data.duplicate_reason.replace(/_/g, ' '))
          : '—';
        const refInfo = data.duplicate_reference;
        const isExact = data.duplicate_status === 'exact_duplicate';
        const borderColor = isExact ? 'border-orange-200' : 'border-orange-200';
        const headerBg   = isExact ? 'bg-orange-50'     : 'bg-orange-50';
        const headerText = isExact ? 'text-orange-700'  : 'text-orange-700';
        return (
          <div className={`bg-white rounded-2xl border ${borderColor} shadow-sm overflow-hidden`}>
            <div className={`px-6 py-3 border-b ${borderColor} ${headerBg} flex items-center gap-2`}>
              <svg className={`w-3.5 h-3.5 ${headerText} shrink-0`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <h4 className={`text-[10px] font-black ${headerText} uppercase tracking-widest`}>{dt.dupDetailTitle}</h4>
            </div>
            <div className="px-6 py-5 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
              {/* Match Type */}
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{dt.dupDetailReason}</p>
                <p className="text-sm text-textMain">{reasonLabel}</p>
              </div>
              {/* Detected At */}
              {data.duplicate_checked_at && (
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{dt.dupDetailCheckedAt}</p>
                  <p className="text-sm text-textMain">{new Date(data.duplicate_checked_at).toLocaleString()}</p>
                </div>
              )}
              {/* Similarity Score (for possible_duplicate) */}
              {data.duplicate_similarity_score != null && (
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{dt.dupDetailSimilarity}</p>
                  <p className={`text-sm font-bold ${data.duplicate_similarity_score >= 95 ? 'text-red-600' : data.duplicate_similarity_score >= 80 ? 'text-amber-600' : 'text-textMain'}`}>
                    {Math.round(data.duplicate_similarity_score)}%
                  </p>
                </div>
              )}
              {/* Reference Application */}
              <div className="md:col-span-2">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{dt.dupDetailOriginalCandidate}</p>
                {refInfo ? (
                  <div className="bg-orange-50 rounded-xl border border-orange-100 px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
                    <span className="text-textMuted">{dt.dupDetailOriginalCandidate}</span>
                    <span className="font-semibold text-textMain">{refInfo.candidate_name || '—'}</span>
                    {refInfo.applied_at && (
                      <>
                        <span className="text-textMuted">{dt.dupDetailOriginalSubmitted}</span>
                        <span className="font-semibold text-textMain">{new Date(refInfo.applied_at).toLocaleDateString()}</span>
                      </>
                    )}
                    <span className="text-textMuted">{dt.dupDetailOriginalRef}</span>
                    <span className="font-mono text-[10px] text-textMuted">{refInfo.application_id.slice(0, 8)}…</span>
                  </div>
                ) : (
                  <p className="text-sm text-textMuted italic">{dt.dupDetailUnknownRef}</p>
                )}
              </div>
            </div>
          </div>
        );
      })()}

      {/* Knockout Questions Section */}
      {(() => {
        const koAnswers: KnockoutAnswerRecord[] = data.knockout_answers ?? [];
        const kt = t as any;

        type EvalResult = 'passed' | 'failed' | 'no_criteria' | 'not_evaluated';

        const evaluateAnswer = (qa: KnockoutAnswerRecord): EvalResult => {
          const pc: PassingCriteria | null | undefined = qa.passing_criteria;
          if (!pc) return 'no_criteria';
          const raw = qa.answer_value ?? '';

          if ((qa.question_type === 'yes_no' || qa.question_type === 'single_choice') && pc.passing_answers?.length) {
            const match = pc.passing_answers.some(a => a.toLowerCase() === raw.toLowerCase());
            return match ? 'passed' : 'failed';
          }

          if (qa.question_type === 'number' && pc.operator != null && pc.value != null) {
            const num = parseFloat(raw);
            if (isNaN(num)) return 'not_evaluated';
            const threshold = pc.value;
            const ops: Record<string, boolean> = {
              '>=': num >= threshold,
              '>':  num >  threshold,
              '=':  num === threshold,
              '<=': num <= threshold,
              '<':  num <  threshold,
            };
            const result = ops[pc.operator];
            if (result === undefined) return 'not_evaluated';
            return result ? 'passed' : 'failed';
          }

          return 'not_evaluated';
        };

        const evalBadge = (result: EvalResult) => {
          if (result === 'passed') return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-green-100 text-green-700">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" /></svg>
              {kt.knockoutPassed}
            </span>
          );
          if (result === 'failed') return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-red-100 text-red-700">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
              {kt.knockoutFailed}
            </span>
          );
          if (result === 'no_criteria') return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-slate-100 text-slate-500">
              {kt.knockoutNoCriteria}
            </span>
          );
          return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-amber-100 text-amber-700">
              {kt.knockoutNotEvaluated}
            </span>
          );
        };

        const formatCriteria = (qa: KnockoutAnswerRecord): string | null => {
          const pc: PassingCriteria | null | undefined = qa.passing_criteria;
          if (!pc) return null;
          if (qa.question_type === 'number' && pc.operator != null && pc.value != null) {
            return kt.knockoutCriteriaPass(pc.operator, String(pc.value));
          }
          if ((qa.question_type === 'yes_no' || qa.question_type === 'single_choice') && pc.passing_answers?.length) {
            return kt.knockoutCriteriaAnswers(pc.passing_answers);
          }
          return null;
        };

        return (
          <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="px-6 py-3 border-b border-border flex items-center gap-2 bg-slate-50">
              <svg className="w-3.5 h-3.5 text-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{kt.knockoutSectionTitle}</h4>
            </div>
            {koAnswers.length === 0 ? (
              <p className="px-6 py-4 text-sm text-textMuted italic">{kt.knockoutNoAnswers}</p>
            ) : (
              <div className="divide-y divide-slate-100">
                {koAnswers.map((qa, idx) => {
                  const evalResult = evaluateAnswer(qa);
                  const criteriaLabel = formatCriteria(qa);
                  return (
                    <div key={qa.answer_id} className="px-6 py-4 grid grid-cols-1 md:grid-cols-4 gap-x-8 gap-y-1.5">
                      <div className="md:col-span-2">
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{idx + 1}. {kt.knockoutQuestion}</p>
                        <p className="text-sm font-medium text-textMain">
                          {qa.question_text}
                          {qa.is_required && (
                            <span className="ml-1.5 px-1.5 py-0.5 bg-slate-100 text-textMuted text-[9px] font-black uppercase rounded">{kt.knockoutRequired}</span>
                          )}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{kt.knockoutAnswer}</p>
                        <p className="text-sm font-semibold text-textMain capitalize">{qa.answer_value}</p>
                      </div>
                      <div>
                        <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{kt.knockoutPassingCriteria}</p>
                        {criteriaLabel ? (
                          <p className="text-sm text-slate-600 mb-1">{criteriaLabel}</p>
                        ) : (
                          <p className="text-sm text-slate-400 italic mb-1">—</p>
                        )}
                        {evalBadge(evalResult)}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })()}

      {/* Profile & Scoring — hidden for security-blocked applications */}
      {!isSecurityBlocked && <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <div className="p-8 bg-slate-50 border-b border-border flex items-center gap-8">
            <div className={`w-24 h-24 rounded-3xl flex flex-col items-center justify-center font-black text-white shadow-xl rotate-3 transform hover:rotate-0 transition-transform ${
              isLowMatch ? 'bg-slate-400'
                : data.decision === 'qualified' ? 'bg-success'
                : data.decision === 'partial'   ? 'bg-warning'
                : data.decision === 'rejected'  ? 'bg-error'
                : score >= 80 ? 'bg-success' : score >= 60 ? 'bg-warning' : 'bg-error'
            }`}>
              <span className="text-3xl">{isLowMatch ? '—' : score}</span>
              <span className="text-[10px] opacity-80 uppercase leading-none tracking-tighter">{t.points}</span>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${decisionStyles[data.decision] || 'bg-slate-100 text-slate-600'}`}>
                  {decisionLabel[data.decision] || data.decision || t.noDecision}
                </span>
                {data.duplicate_status === 'possible_duplicate' && (
                  <span className="px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest bg-orange-100 text-orange-700">
                    {t.possibleDuplicate}
                  </span>
                )}
                <span className="text-[10px] font-black text-textMuted uppercase tracking-widest">• REF: {data.application_id}</span>
              </div>
              <h1 className="text-3xl font-black text-textMain tracking-tight">{candidateName}</h1>
            </div>
          </div>

          {!isLowMatch && (
            <div className="p-8">
              <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">{t.execSummary}</h4>
              <p className={`text-base text-textMain leading-relaxed italic ${isAr ? 'border-r-4 pr-6' : 'border-l-4 pl-6'} border-primary/20 py-1`}>
                {analysis.summary || t.noSummary}
              </p>
            </div>
          )}
        </div>

        <div className="bg-white rounded-3xl border border-border shadow-sm p-6 flex flex-col">
          <h4 className="text-[10px] font-black text-textMain uppercase tracking-widest flex items-center mb-5">
            <span className={`w-2 h-4 bg-primary rounded-full ${isAr ? 'ml-3' : 'mr-3'}`}></span> {t.scoreOverview}
          </h4>
          <div className="flex-1 divide-y divide-slate-50">
            {scoreDims.map(([label, dim]) => {
              if (!dim || dim.max === 0) return null;
              const pct = Math.round((dim.achieved / dim.max) * 100);
              const badge = getScoreBadgeColor(dim.achieved, dim.max);
              return (
                <div key={label} className="flex items-center gap-2 py-2.5">
                  <span className="flex-1 text-[10px] font-bold text-textMuted uppercase tracking-wide truncate">{label}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-black ${badge}`}>{dim.achieved}<span className="opacity-60">/{dim.max}</span></span>
                  {dim.weight != null && dim.weight > 0 && (
                    <span className="px-1.5 py-0.5 bg-indigo-50 text-indigo-600 rounded text-[9px] font-bold w-8 text-center">{dim.weight}%</span>
                  )}
                  {dim.weight === 0 && (
                    <span className="px-1.5 py-0.5 bg-slate-100 text-slate-400 rounded text-[9px] font-bold w-8 text-center">—</span>
                  )}
                </div>
              );
            })}
          </div>
          <div className="pt-4 mt-4 border-t border-slate-100">
            <p className="text-[10px] text-textMuted italic leading-relaxed">{t.scoringNote}</p>
          </div>
        </div>
      </div>}

      {/* Intelligence Analysis Panel */}
      {!isSecurityBlocked && hasIntelligence && (
        <div className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <button
            onClick={() => setIntelligenceExpanded(v => !v)}
            className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
          >
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-3">
              <span className="w-2 h-4 bg-indigo-500 rounded-full"></span>
              {t.intelligence}
            </h3>
            <svg className={`w-5 h-5 text-textMuted transform transition-transform ${intelligenceExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {intelligenceExpanded && (
            <div className="px-8 pb-8 pt-2 border-t border-slate-50 space-y-6 animate-fade-in">
              {/* Stats row */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {data.cv_language && (
                  <div className="bg-slate-50 rounded-2xl p-4 text-center">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.cvLanguage}</p>
                    <span className="inline-block px-3 py-1 rounded-full bg-indigo-100 text-indigo-700 text-xs font-bold uppercase">{cvLangLabel}</span>
                  </div>
                )}
                {data.local_similarity_score != null && (
                  <div className="bg-slate-50 rounded-2xl p-4 text-center">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.semanticSimilarity}</p>
                    <p className="text-2xl font-black text-textMain">{Math.round(data.local_similarity_score)}%</p>
                  </div>
                )}
                {data.skill_match_ratio != null && (
                  <div className="bg-slate-50 rounded-2xl p-4 text-center">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.skillMatch}</p>
                    <p className="text-2xl font-black text-textMain">{Math.round(data.skill_match_ratio)}%</p>
                  </div>
                )}
                {data.gatekeeper_passed != null && (
                  <div className="bg-slate-50 rounded-2xl p-4 text-center">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.gatekeeperPassed}</p>
                    <span className={`inline-block px-3 py-1 rounded-full text-xs font-bold uppercase ${data.gatekeeper_passed ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                      {data.gatekeeper_passed ? t.passed : t.filtered}
                    </span>
                  </div>
                )}
              </div>

              {/* Matched / Missing skills */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {(data.matched_skills?.length ?? 0) > 0 && (
                  <div>
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.matchedSkillsLabel}</p>
                    <div className="flex flex-wrap gap-2">
                      {data.matched_skills!.map((skill, i) => (
                        <span key={i} className="px-3 py-1 bg-green-50 text-green-700 border border-green-200 rounded-full text-xs font-semibold">
                          {skill}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {(data.missing_skills?.length ?? 0) > 0 && (
                  <div>
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.missingSkillsLabel}</p>
                    <div className="flex flex-wrap gap-2">
                      {data.missing_skills!.map((skill, i) => (
                        <span key={i} className="px-3 py-1 bg-red-50 text-red-700 border border-red-200 rounded-full text-xs font-semibold">
                          {skill}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Evidence-Based Analysis */}
      {!isSecurityBlocked && hasEvidenceSection && (
        <section className="space-y-6">
          <div className="flex items-center gap-3">
            <span className="w-2 h-4 bg-primary rounded-full shrink-0"></span>
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.evidenceAnalysis}</h3>
          </div>
          {/* Weighted dimensions */}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {weightedEvidenceDims.map(({ label, dim, detail, reasoning }) =>
              renderEvidenceCard(label, dim, detail, reasoning, false)
            )}
          </div>
          {/* Zero-weight dimensions → Additional Recruiter Insights */}
          {zeroWeightEvidenceDims.length > 0 && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 flex-wrap">
                <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.additionalInsights}</h4>
                <span className="px-2.5 py-0.5 bg-slate-100 text-slate-500 text-[10px] font-bold rounded-full">{t.notInFinalScore}</span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {zeroWeightEvidenceDims.map(({ label, dim, detail, reasoning }) =>
                  renderEvidenceCard(label, dim, detail, reasoning, true)
                )}
              </div>
            </div>
          )}
        </section>
      )}

      {/* HR Evaluation Grid */}
      {!isSecurityBlocked && !isLowMatch && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {renderSection(t.sections.matchedSkills, analysis.cv_skills_matched)}
          {renderSection(t.sections.expFit, analysis.cv_experience_summary)}
          {renderSection(t.sections.academicFit, analysis.cv_education_summary)}
          {renderSection(t.sections.certifications, analysis.cv_certifications_found)}
          {renderSection(t.sections.gaps, analysis.gaps_identified, <svg className="w-3 h-3 text-error" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" /></svg>)}
          {renderSection(t.sections.evalNotes, analysis.evaluation_notes)}
        </div>
      )}

      {/* Red Flags */}
      {!isSecurityBlocked && hasRedFlags && (
        <div className={`bg-amber-50 border ${isAr ? 'border-r-4' : 'border-l-4'} border-amber-400 rounded-2xl p-6`}>
          <h4 className="text-[10px] font-black text-amber-700 uppercase tracking-widest mb-4 flex items-center gap-2">
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            {t.redFlags}
          </h4>
          <ul className="space-y-2">
            {data.red_flags!.map((flag, i) => (
              <li key={i} className="text-sm text-amber-800 flex items-start gap-2">
                <span className="font-bold mt-0.5">⚠</span>
                <span className="leading-relaxed">{flag}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Strengths & Risks */}
      {!isSecurityBlocked && !isLowMatch && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {renderSection(t.sections.strengths, analysis.strengths)}
          {renderSection(t.sections.risks, analysis.risks)}
        </div>
      )}

      {/* Interview Support */}
      {!isSecurityBlocked && !isLowMatch && ((analysis.interview_focus_points?.length ?? 0) > 0 || (analysis.interview_suggested_questions?.length ?? 0) > 0) && (
        <div className="bg-indigo-900 rounded-3xl p-8 text-white shadow-xl shadow-indigo-200">
          <h3 className="text-sm font-black uppercase tracking-widest mb-8 flex items-center">
            <svg className={`w-5 h-5 ${isAr ? 'ml-3' : 'mr-3'} text-indigo-400`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
            {t.interviewPrep}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
            <div className="space-y-6">
              <h4 className="text-[10px] font-black text-indigo-300 uppercase tracking-widest">{t.focusAreas}</h4>
              <ul className="space-y-3">
                {(analysis.interview_focus_points || []).map((point, i) => (
                  <li key={i} className="flex items-start text-sm bg-indigo-800/50 p-3 rounded-xl border border-indigo-700/50">
                    <span className={`text-indigo-400 ${isAr ? 'ml-3' : 'mr-3'} font-bold`}>#</span>
                    {point}
                  </li>
                ))}
              </ul>
            </div>
            <div className="space-y-6">
              <h4 className="text-[10px] font-black text-indigo-300 uppercase tracking-widest">{t.suggestedQs}</h4>
              <div className="space-y-4">
                {(analysis.interview_suggested_questions || []).map((q, i) => (
                  <div key={i} className="bg-white/5 border border-white/10 p-4 rounded-xl text-sm leading-relaxed">
                    <span className="text-indigo-400 font-bold block mb-1">Q{i + 1}:</span>
                    "{q}"
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* AI Comparison Results */}
      {!isSecurityBlocked && (data.ai_comparisons?.length ?? 0) > 0 && (
        <section className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <div className="px-8 py-5 border-b border-slate-100">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-3">
              <span className="w-2 h-4 bg-violet-500 rounded-full"></span>
              {t.aiComparison}
            </h3>
          </div>
          <div className="p-8 space-y-6">
            {data.ai_comparisons!.map((cmp, i) => {
              const delta = cmp.final_score - score;
              const deltaLabel = delta > 0 ? `+${delta}` : String(delta);
              const deltaColor = delta > 5 ? 'text-green-600' : delta < -5 ? 'text-red-600' : 'text-gray-500';
              return (
                <div key={i} className="bg-slate-50 rounded-2xl p-5 space-y-3">
                  <div className="flex flex-wrap gap-4 items-center">
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.compProvider}</p>
                      <p className="text-sm font-semibold text-textMain capitalize">{cmp.provider}</p>
                    </div>
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.compModel}</p>
                      <p className="text-sm font-mono text-textMain">{cmp.model}</p>
                    </div>
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.compScore}</p>
                      <p className="text-2xl font-black text-textMain">{cmp.final_score}</p>
                    </div>
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.compDelta}</p>
                      <p className={`text-lg font-black ${deltaColor}`}>{deltaLabel}</p>
                    </div>
                  </div>
                  {/* Per-dimension comparison bars */}
                  {cmp.score_skills != null && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2">
                      {([
                        [t.scoreBars[0], cmp.score_skills,           data.scores?.skills?.achieved],
                        [t.scoreBars[1], cmp.score_experience,       data.scores?.experience?.achieved],
                        [t.scoreBars[2], cmp.score_education,        data.scores?.education?.achieved],
                        [t.scoreBars[3], cmp.score_certifications,   data.scores?.certifications?.achieved],
                        [t.scoreBars[4], cmp.score_soft_skills,      data.scores?.soft_skills?.achieved],
                        [t.scoreBars[5], cmp.score_domain_knowledge, data.scores?.domain_knowledge?.achieved],
                      ] as [string, number | undefined, number | undefined][]).map(([label, compScore, primScore], idx) => {
                        if (compScore == null) return null;
                        const d = primScore != null ? compScore - primScore : null;
                        return (
                          <div key={idx} className="bg-white rounded-xl p-2 text-center">
                            <p className="text-[9px] font-bold text-textMuted uppercase tracking-wider truncate">{label}</p>
                            <p className="text-lg font-black text-textMain">{compScore}</p>
                            {d != null && (
                              <p className={`text-[10px] font-bold ${d > 0 ? 'text-green-600' : d < 0 ? 'text-red-600' : 'text-gray-400'}`}>
                                {d > 0 ? `+${d}` : String(d)}
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Scoring Audit — provider, model, prompt versions */}
      {!isSecurityBlocked && (data.scoring_provider || data.ai_model || data.scoring_prompt_code || data.level2_prompt_code) && (
        <section className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <div className="px-8 py-5 border-b border-slate-100">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-3">
              <span className="w-2 h-4 bg-slate-400 rounded-full"></span>
              {t.promptTracking}
            </h3>
          </div>
          <div className="px-8 py-5 flex flex-wrap gap-6 text-sm">
            {data.scoring_provider && (
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.primaryProvider}</p>
                <p className="text-textMain font-semibold capitalize">{data.scoring_provider}</p>
              </div>
            )}
            {data.ai_model && (
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.primaryModel}</p>
                <p className="text-textMain font-mono">{data.ai_model}</p>
              </div>
            )}
            {data.scoring_prompt_code && (
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.scoringPrompt}</p>
                <p className="text-textMain font-mono">
                  {data.scoring_prompt_code}
                  {data.scoring_prompt_version != null && (
                    <span className="ml-1 text-xs text-textMuted">{t.promptVersion}{data.scoring_prompt_version}</span>
                  )}
                </p>
              </div>
            )}
            {data.level2_prompt_code && (
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-wider mb-0.5">{t.level2Prompt}</p>
                <p className="text-textMain font-mono">
                  {data.level2_prompt_code}
                  {data.level2_prompt_version != null && (
                    <span className="ml-1 text-xs text-textMuted">{t.promptVersion}{data.level2_prompt_version}</span>
                  )}
                </p>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Recruiter Workflow Panel */}
      {onWorkflowStatusChange && (
        <section className="bg-white rounded-3xl border border-border overflow-hidden">
          <div className="px-8 py-5 border-b border-slate-100 flex items-center justify-between">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center">
              <span className={`w-2 h-4 bg-indigo-400 rounded-full ${isAr ? 'ml-3' : 'mr-3'}`}></span>
              Recruiter Workflow
            </h3>
            <span className={`px-3 py-1 rounded-full text-xs font-bold border ${WF_STYLES[currentWorkflowStatus]}`}>
              {WF_LABELS[currentWorkflowStatus]}
            </span>
          </div>
          <div className="px-8 py-6 space-y-4">
            {VALID_TRANSITIONS[currentWorkflowStatus].length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {VALID_TRANSITIONS[currentWorkflowStatus].map(target => (
                  <button
                    key={target}
                    onClick={() => handleTransitionClick(target)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-bold border transition-all hover:opacity-80 ${WF_STYLES[target]}`}
                  >
                    {WF_ACTION_LABELS[target]}
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-xs text-textMuted italic">No further transitions available.</p>
            )}

            {showNoteInput && pendingStatus && (
              <div className="mt-3 bg-slate-50 rounded-xl border border-slate-200 p-4 space-y-3">
                <p className="text-xs font-bold text-textMain">
                  Confirm: {WF_ACTION_LABELS[pendingStatus]}
                  <span className="text-textMuted font-normal ml-1">(optional note)</span>
                </p>
                <textarea
                  value={noteInput}
                  onChange={e => setNoteInput(e.target.value)}
                  placeholder="Add a note for this transition (optional)..."
                  className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-none"
                  rows={2}
                />
                <div className="flex gap-2">
                  <button
                    onClick={confirmTransition}
                    className={`px-4 py-1.5 rounded-lg text-xs font-bold ${WF_STYLES[pendingStatus]} hover:opacity-80 transition-all`}
                  >
                    Confirm
                  </button>
                  <button
                    onClick={() => { setShowNoteInput(false); setPendingStatus(null); setNoteInput(''); }}
                    className="px-4 py-1.5 rounded-lg text-xs font-bold bg-slate-100 text-slate-600 hover:bg-slate-200 transition-all"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Workflow History */}
            {data.workflow_history && data.workflow_history.length > 0 && (
              <div className="mt-4">
                <p className="text-[9px] font-black text-textMuted uppercase tracking-widest mb-2">History</p>
                <div className="space-y-1.5">
                  {data.workflow_history.map(h => (
                    <div key={h.history_id} className="flex items-start gap-2 text-xs text-textMuted">
                      <span className="shrink-0 mt-0.5 text-slate-300">→</span>
                      <span>
                        <span className="font-semibold text-textMain">{WF_LABELS[h.to_status as WorkflowStatus] || h.to_status}</span>
                        {h.from_status && <span className="text-slate-400"> from {WF_LABELS[h.from_status as WorkflowStatus] || h.from_status}</span>}
                        {h.changed_by_name && <span className="text-slate-400"> by {h.changed_by_name}</span>}
                        {h.note && <span className="italic ml-1">"{h.note}"</span>}
                        {h.created_at && <span className="text-slate-300 ml-1">· {new Date(h.created_at).toLocaleDateString()}</span>}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Recruiter Notes */}
      {onRecruiterNotesChange && (
        <section className="bg-white rounded-3xl border border-border overflow-hidden">
          <div className="px-8 py-5 border-b border-slate-100">
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center">
              <span className={`w-2 h-4 bg-amber-400 rounded-full ${isAr ? 'ml-3' : 'mr-3'}`}></span>
              Recruiter Notes
            </h3>
          </div>
          <div className="px-8 py-6 space-y-3">
            <textarea
              value={recruiterNotesText}
              onChange={e => { setRecruiterNotesText(e.target.value); setNotesDirty(true); }}
              placeholder="Add private recruiter notes about this candidate..."
              className="w-full text-sm border border-slate-200 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-amber-300 resize-none"
              rows={4}
            />
            <div className="flex justify-end">
              <button
                onClick={handleSaveNotes}
                disabled={!notesDirty}
                className="px-5 py-2 rounded-xl text-xs font-bold bg-amber-100 text-amber-800 hover:bg-amber-200 transition-all disabled:opacity-40"
              >
                Save Notes
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Raw Data */}
      {!isSecurityBlocked && <section className="bg-white rounded-3xl border border-border overflow-hidden">
        <button
          onClick={() => setShowRaw(!showRaw)}
          className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center">
            <span className={`w-2 h-4 bg-slate-400 rounded-full ${isAr ? 'ml-3' : 'mr-3'}`}></span> {t.rawPayload}
          </h3>
          <svg className={`w-5 h-5 text-textMuted transform transition-transform ${showRaw ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
        {showRaw && (
          <div className="px-8 pb-10 pt-4 animate-fade-in border-t border-slate-50">
            <div className="bg-slate-900 rounded-2xl p-6 overflow-hidden">
              <pre className="text-[10px] text-blue-300 font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
                {JSON.stringify(data.raw_ai_response || data, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </section>}
    </div>
  );
};
