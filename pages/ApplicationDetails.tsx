
import React, { useState } from 'react';
import { ApplicationDetailedAnalysis, ScoreDimension, ScoreDetail } from '../types';
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
    securityCheck: 'Security Check',
    securityPassed: 'Passed',
    securityWarning: 'Warning',
    securityBlocked: 'Blocked',
    securityRiskLevel: 'Risk Level',
    securityRiskScore: 'Risk Score',
    securityReasonCodes: 'Reason Codes',
    securityPatternCategories: 'Pattern Categories',
    securityCheckedAt: 'Checked At',
    securitySummary: 'Summary',
    securityBlockedNote: 'This application was blocked before scoring due to suspicious content detected in the CV.',
    securityWarningNote: 'Suspicious content was detected in this CV. The application was allowed through for review.',
    securityDetectedStatements: 'Detected Suspicious Statements',
    securitySnippetsNote: 'Short excerpts shown for reviewer context. Full CV content is not displayed here.',
    securityPatternLabels: {
      override_instructions: 'Instruction Override',
      score_manipulation:    'Score Manipulation',
      reveal_prompt:         'Prompt Reveal Attempt',
      jailbreak:             'Jailbreak Pattern',
      scoring_rule_change:   'Scoring Rule Change',
      auto_qualify:          'Auto-Qualify Attempt',
      obfuscated:            'Obfuscated Content',
      encoded_payload:       'Encoded Payload',
      unicode_spam:          'Unicode Anomaly',
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
    securityCheck: 'فحص الأمان',
    securityPassed: 'اجتاز',
    securityWarning: 'تحذير',
    securityBlocked: 'محجوب',
    securityRiskLevel: 'مستوى المخاطر',
    securityRiskScore: 'درجة المخاطر',
    securityReasonCodes: 'رموز الأسباب',
    securityPatternCategories: 'فئات الأنماط',
    securityCheckedAt: 'وقت الفحص',
    securitySummary: 'الملخص',
    securityBlockedNote: 'تم حجب هذا الطلب قبل التقييم بسبب محتوى مشبوه تم اكتشافه في السيرة الذاتية.',
    securityWarningNote: 'تم اكتشاف محتوى مشبوه في هذه السيرة الذاتية. تم السماح بمرور الطلب للمراجعة.',
    securityDetectedStatements: 'العبارات المشبوهة المكتشفة',
    securitySnippetsNote: 'مقاطع قصيرة تُعرض لأغراض المراجعة. لا يُعرض النص الكامل للسيرة الذاتية هنا.',
    securityPatternLabels: {
      override_instructions: 'محاولة تجاوز التعليمات',
      score_manipulation:    'محاولة التلاعب بالدرجات',
      reveal_prompt:         'محاولة كشف النظام',
      jailbreak:             'نمط تجاوز القيود',
      scoring_rule_change:   'محاولة تغيير معايير التقييم',
      auto_qualify:          'محاولة الإجازة التلقائية',
      obfuscated:            'محتوى مخفي',
      encoded_payload:       'حمولة مشفرة',
      unicode_spam:          'تشوه يونيكود',
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

export const ApplicationDetails: React.FC<ApplicationDetailsProps> = ({ data, onBack, jobMeta, onDownloadCV, downloadingCV }) => {
  const { lang, isAr } = useLanguage() as { lang: 'en' | 'ar'; isAr: boolean };
  const t = T[lang];

  const [showRaw, setShowRaw] = useState(false);
  const [intelligenceExpanded, setIntelligenceExpanded] = useState(
    data.gatekeeper_passed === false
  );

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

      {/* Low Match Banner */}
      {isLowMatch && (
        <div className={`bg-slate-50 border ${isAr ? 'border-r-4' : 'border-l-4'} border-slate-400 rounded-2xl p-5 flex items-start gap-4`}>
          <svg className="w-5 h-5 text-slate-500 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
          <p className="text-sm text-slate-600 leading-relaxed">{t.gatekeeperFiltered}</p>
        </div>
      )}

      {/* Possible Duplicate Banner */}
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

      {/* ── Security Check Banner ───────────────────────────────────────────── */}
      {data.security_check_status && data.security_check_status !== 'passed' && (() => {
        const secStatus   = data.security_check_status!;
        const secLevel    = data.security_risk_level || '';
        const secScore    = data.security_risk_score ?? 0;
        const secCodes    = data.security_reason_codes || [];
        const secPatterns = data.security_detected_patterns || [];
        const secSnippets = data.security_detected_snippets || [];
        const secAt: string | null = data.security_checked_at || null;
        const isBlocked = secStatus === 'blocked';
        const patternLabels = (t as any).securityPatternLabels as Record<string, string>;
        return (
          <div className={`rounded-2xl p-5 border-l-4 ${isBlocked ? 'bg-red-50 border-red-500' : 'bg-amber-50 border-amber-400'}`}>
            <div className="flex items-start gap-3 mb-3">
              <svg className={`w-5 h-5 mt-0.5 shrink-0 ${isBlocked ? 'text-red-500' : 'text-amber-500'}`} fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              <div className="flex-1">
                <p className={`text-sm font-bold ${isBlocked ? 'text-red-800' : 'text-amber-800'}`}>
                  {isBlocked ? (t as any).securityBlockedNote : (t as any).securityWarningNote}
                </p>
              </div>
              <span className={`shrink-0 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${isBlocked ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                {isBlocked ? (t as any).securityBlocked : (t as any).securityWarning}
              </span>
            </div>
            <div className="bg-white rounded-xl border border-slate-200 px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
              <span className="text-textMuted">{(t as any).securityRiskLevel}</span>
              <span className={`font-bold uppercase ${secLevel === 'high' ? 'text-red-600' : secLevel === 'medium' ? 'text-amber-600' : 'text-green-600'}`}>{secLevel}</span>
              <span className="text-textMuted">{(t as any).securityRiskScore}</span>
              <span className="font-semibold text-textMain">{secScore}</span>
              {secPatterns.length > 0 && (
                <>
                  <span className="text-textMuted">{(t as any).securityPatternCategories}</span>
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
            {secSnippets.length > 0 && (
              <div className="mt-3">
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

      {/* Profile & Scoring */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
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
      </div>

      {/* Intelligence Analysis Panel */}
      {hasIntelligence && (
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
      {hasEvidenceSection && (
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
      {!isLowMatch && (
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
      {hasRedFlags && (
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
      {!isLowMatch && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {renderSection(t.sections.strengths, analysis.strengths)}
          {renderSection(t.sections.risks, analysis.risks)}
        </div>
      )}

      {/* Interview Support */}
      {!isLowMatch && ((analysis.interview_focus_points?.length ?? 0) > 0 || (analysis.interview_suggested_questions?.length ?? 0) > 0) && (
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
      {(data.ai_comparisons?.length ?? 0) > 0 && (
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
      {(data.scoring_provider || data.ai_model || data.scoring_prompt_code || data.level2_prompt_code) && (
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

      {/* Raw Data */}
      <section className="bg-white rounded-3xl border border-border overflow-hidden">
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
      </section>
    </div>
  );
};
