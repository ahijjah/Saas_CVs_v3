
import React, { useState } from 'react';
import { ApplicationDetailedAnalysis, ScoreDimension, ScoreDetail, KnockoutAnswerRecord, KnockoutSuggestionRecord, KnockoutValidationRecord, KnockoutValidationRun, PassingCriteria, WorkflowStatus, DeterministicScore, DetDimensionScore, DetCriterionScore } from '../types';
import { apiService } from '../services/api';
import { useLanguage } from '../context/LanguageContext';
import {
  WORKFLOW_STATUS_STYLES_BORDERED,
  WORKFLOW_STATUS_LABELS_EN,
  WORKFLOW_STATUS_LABELS_AR,
} from '../constants/workflow';
import { WorkflowActionMenu } from '../components/WorkflowActionMenu';

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
  userRole?: string;
  advancedMoveEnabled?: boolean;
  onWorkflowStatusChange?: (appId: string, newStatus: WorkflowStatus, note?: string, isAdvancedMove?: boolean) => void;
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
    genderTitle: 'Gender Metadata',
    genderLabel: 'Gender',
    genderConfidence: 'Confidence',
    genderBasis: 'Basis',
    genderBasisLabels: {
      title:            'Title (Mr./Ms.)',
      pronoun:          'Stated pronoun',
      explicit_cv_text: 'Explicit statement',
      name:             'Name heuristic',
      unknown:          'No signal',
    } as Record<string, string>,
    genderValues: { male: 'Male', female: 'Female', unknown: 'Unknown' } as Record<string, string>,
    genderNote: 'Metadata only — not used in scoring or ranking.',
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
    explainabilityTitle: 'Candidate Assessment',
    additionalDetailsTitle: 'Additional Details',
    developerTitle: 'Developer / Diagnostic',
    coverageSummary: 'Coverage Summary',
    requiredRequirements: 'Required Requirements',
    preferredRequirements: 'Preferred Requirements',
    xMatched: 'Matched',
    xPartial: 'Partial',
    xMissing: 'Missing',
    xCoverage: 'Coverage',
    recruiterSignalLabel: 'Recruiter Signal',
    recruiterSignalLabels: {
      STRONG_FULL_MATCH:             'Excellent Match',
      STRONG_REQUIRED_WEAK_PREFERRED: 'Strong Match',
      STRONG_REQUIRED_NO_PREFERRED:  'Strong Core Match',
      NEAR_MATCH:                    'Near Match',
      MOSTLY_MET:                    'Mostly Met',
      PARTIAL_REQUIRED:              'Significant Gaps',
      POOR_REQUIRED_COVERAGE:        'Does Not Meet Requirements',
      NO_REQUIRED_CRITERIA:          'No Required Criteria',
    } as Record<string, string>,
    recruiterSignalDescriptions: {
      STRONG_FULL_MATCH:             'All core and preferred requirements satisfied.',
      STRONG_REQUIRED_WEAK_PREFERRED: 'Core requirements met. Preferred requirements partially met.',
      STRONG_REQUIRED_NO_PREFERRED:  'All core requirements met. No preferred requirements specified.',
      NEAR_MATCH:                    'Core requirements mostly satisfied. Some gaps remain.',
      MOSTLY_MET:                    'All or nearly all requirements have evidence. Minor gaps in match quality.',
      PARTIAL_REQUIRED:              'Significant gaps in core requirements.',
      POOR_REQUIRED_COVERAGE:        'Most core requirements are missing.',
      NO_REQUIRED_CRITERIA:          'No mandatory requirements were defined for this job.',
    } as Record<string, string>,
    xDimensionLabels: {
      skills:           'Technical Skills',
      experience:       'Experience',
      education:        'Education',
      certifications:   'Certifications',
      soft_skills:      'Soft Skills',
      domain_knowledge: 'Domain Knowledge',
      other:            'Other Requirements',
    } as Record<string, string>,
    badgeRequired:    'Required',
    badgePreferred:   'Preferred',
    statusMatched:    'Matched',
    statusPartial:    'Partial',
    statusAbsent:     'Missing',
    matchTypeLabels: {
      direct:       'Direct match',
      equivalent:   'Equivalent',
      transferable: 'Transferable',
      inferred:     'Inferred',
      missing:      'Not found',
    } as Record<string, string>,
    xConfidence:       'Confidence',
    xEffectiveCredit:  'Credit',
    xEvidence:         'Evidence',
    xNoEvidence:       'No supporting evidence recorded.',
    xDimensionScore:   'Dimension Score',
    xWeight:           'Weight',
    xNoCriteriaData:   'No criteria data available.',
    qualitativeSummaryTitle: 'Evaluation Summary',
    gapsIdentified: 'Gaps Identified',
    suggestedInterviewQuestions: 'Suggested Interview Questions',
    xNoExplainability: 'Detailed evidence analysis is not available for this application.',
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
    knockoutNotAnswered: 'Not Answered',
    knockoutSource: 'Source',
    knockoutSourceCandidate: 'Candidate (Form)',
    knockoutSourceCandidateEmail: 'Candidate (Email)',
    knockoutSourceRecruiter: 'Recruiter Entered',
    knockoutSourceAI: 'AI Extracted',
    knockoutSourceAICV: 'AI (CV)',
    knockoutSourceAIEmail: 'AI (Email)',
    knockoutSourceAICVEmail: 'AI (CV + Email)',
    knockoutSourceAISuggested: 'AI Suggested (Accepted)',
    knockoutMethodDirect: 'Direct Statement',
    knockoutMethodInferred: 'AI Inference',
    knockoutMethodApproved: 'Recruiter Approved',
    knockoutMethodManual: 'Manual Entry',
    knockoutMethodNotFound: 'Not Found',
    knockoutEditAnswer: 'Edit',
    knockoutSaveAnswer: 'Save',
    knockoutCancelEdit: 'Cancel',
    knockoutEditPlaceholder: 'Enter answer',
    knockoutGenerateSuggestions: 'Run Screening Validation',
    knockoutValidationCompleted: '✓ Screening Validation Completed',
    knockoutValidationOutdated: '⚠ Validation Outdated',
    knockoutGenerating: 'Validating…',
    knockoutAISuggested: 'Suggested from CV',
    knockoutAllAnswered: 'All screening questions have answers.',
    knockoutNoContentForAI: 'No CV/email content available for screening validation.',
    knockoutAlreadyCompleted: 'Screening validation already completed. No changes detected since last run.',
    knockoutSuggestionEvidence: 'Evidence',
    knockoutSuggestionConfidence: 'Confidence',
    knockoutSuggestionAccept: 'Accept',
    knockoutSuggestionEditAccept: 'Edit & Accept',
    knockoutSuggestionIgnore: 'Ignore',
    knockoutVerified: 'Direct evidence',
    knockoutInferred: 'Inferred',
    knockoutNoEvidence: 'No evidence',
    knockoutContradiction: 'Contradictory',
    knockoutSuggestionSourceCV: 'From CV',
    knockoutSuggestionSourceEmail: 'From Email',
    knockoutSuggestionSourceBoth: 'From CV & Email',
    knockoutNoSuggestionsFound: 'No suggestions were generated. Ensure the CV has been processed and extracted text is available.',
    knockoutNoContent: 'No CV text or email content is available for this application. Ensure the CV has been processed successfully before running screening validation.',
    knockoutAnalysisFailed: 'Screening validation failed',
    knockoutCvValidation: 'Screening Validation',
    knockoutSupportedByCv: 'Supported by CV',
    knockoutContradictedByCv: 'Contradicted by CV',
    knockoutNotSupportedByCv: 'Not in CV',
    knockoutCannotValidate: 'Cannot Validate',
    knockoutCannotDetermine: 'Cannot Determine',
    knockoutReasoning: 'Reasoning',
    knockoutValidationSummary: 'Screening Validation Summary',
    knockoutValidationLastRun: 'Last Run',
    knockoutValidationQuestionsProcessed: 'Questions Processed',
    knockoutValidationPromptVersion: 'Prompt Version',
    knockoutValidationModel: 'Model',
    knockoutValidationStatusLabel: 'Status',
    knockoutValidationAnswersValidated: 'answers validated',
    knockoutValidationSuggested: 'answer suggested',
    knockoutValidationSuggestedPlural: 'answers suggested',
    knockoutValidationCannotDetermine: 'cannot determine',
    knockoutSeeMore: 'See more',
    knockoutSeeLess: 'See less',
    knockoutFinalAnswer: 'Final Answer',
    knockoutScreeningValidation: 'Screening Validation',
    knockoutCannotDetermineHelper: 'The AI could not find sufficient evidence in the CV or email to determine an answer.',
    knockoutCannotValidateHelper: 'The AI could not validate the existing answer using available CV evidence.',
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
    genderTitle: 'بيانات الجنس',
    genderLabel: 'الجنس',
    genderConfidence: 'مستوى الثقة',
    genderBasis: 'الأساس',
    genderBasisLabels: {
      title:            'اللقب (السيد/السيدة)',
      pronoun:          'ضمير مُصرَّح به',
      explicit_cv_text: 'تصريح صريح',
      name:             'قرينة الاسم',
      unknown:          'لا إشارة',
    } as Record<string, string>,
    genderValues: { male: 'ذكر', female: 'أنثى', unknown: 'غير محدد' } as Record<string, string>,
    genderNote: 'بيانات وصفية فقط — لا تُستخدم في التقييم أو الترتيب.',
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
    explainabilityTitle: 'تقييم المرشح',
    additionalDetailsTitle: 'تفاصيل إضافية',
    developerTitle: 'المطور / التشخيص',
    coverageSummary: 'ملخص التغطية',
    requiredRequirements: 'المتطلبات الإلزامية',
    preferredRequirements: 'المتطلبات المفضلة',
    xMatched: 'مطابق',
    xPartial: 'جزئي',
    xMissing: 'غائب',
    xCoverage: 'التغطية',
    recruiterSignalLabel: 'إشارة المسؤول',
    recruiterSignalLabels: {
      STRONG_FULL_MATCH:             'تطابق ممتاز',
      STRONG_REQUIRED_WEAK_PREFERRED: 'تطابق قوي',
      STRONG_REQUIRED_NO_PREFERRED:  'تطابق أساسي قوي',
      NEAR_MATCH:                    'تطابق شبه تام',
      MOSTLY_MET:                    'محقق في الغالب',
      PARTIAL_REQUIRED:              'ثغرات جوهرية',
      POOR_REQUIRED_COVERAGE:        'لا يلبي المتطلبات',
      NO_REQUIRED_CRITERIA:          'لا توجد معايير إلزامية',
    } as Record<string, string>,
    recruiterSignalDescriptions: {
      STRONG_FULL_MATCH:             'جميع المتطلبات الأساسية والمفضلة محققة.',
      STRONG_REQUIRED_WEAK_PREFERRED: 'المتطلبات الأساسية محققة. المتطلبات المفضلة محققة جزئياً.',
      STRONG_REQUIRED_NO_PREFERRED:  'جميع المتطلبات الأساسية محققة. لا توجد متطلبات مفضلة محددة.',
      NEAR_MATCH:                    'المتطلبات الأساسية محققة في معظمها مع بعض الثغرات.',
      MOSTLY_MET:                    'جميع أو معظم المتطلبات لديها أدلة. ثغرات طفيفة في جودة التطابق.',
      PARTIAL_REQUIRED:              'ثغرات جوهرية في المتطلبات الأساسية.',
      POOR_REQUIRED_COVERAGE:        'معظم المتطلبات الأساسية غائبة.',
      NO_REQUIRED_CRITERIA:          'لم يتم تحديد أي متطلبات إلزامية لهذه الوظيفة.',
    } as Record<string, string>,
    xDimensionLabels: {
      skills:           'المهارات التقنية',
      experience:       'الخبرة',
      education:        'التعليم',
      certifications:   'الشهادات',
      soft_skills:      'المهارات الشخصية',
      domain_knowledge: 'المعرفة المتخصصة',
      other:            'متطلبات أخرى',
    } as Record<string, string>,
    badgeRequired:    'إلزامي',
    badgePreferred:   'مفضل',
    statusMatched:    'مطابق',
    statusPartial:    'جزئي',
    statusAbsent:     'غائب',
    matchTypeLabels: {
      direct:       'تطابق مباشر',
      equivalent:   'مكافئ',
      transferable: 'قابل للنقل',
      inferred:     'مستنتج',
      missing:      'غير موجود',
    } as Record<string, string>,
    xConfidence:       'الثقة',
    xEffectiveCredit:  'الائتمان',
    xEvidence:         'الأدلة',
    xNoEvidence:       'لم يُسجَّل أي دليل داعم.',
    xDimensionScore:   'درجة البُعد',
    xWeight:           'الوزن',
    xNoCriteriaData:   'لا تتوفر بيانات معايير.',
    qualitativeSummaryTitle: 'ملخص التقييم',
    gapsIdentified: 'الفجوات المحددة',
    suggestedInterviewQuestions: 'أسئلة المقابلة المقترحة',
    xNoExplainability: 'تحليل الأدلة التفصيلي غير متاح لهذا الطلب.',
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
    knockoutNotAnswered: 'لم تُجب',
    knockoutSource: 'المصدر',
    knockoutSourceCandidate: 'المتقدم (نموذج)',
    knockoutSourceCandidateEmail: 'المتقدم (بريد إلكتروني)',
    knockoutSourceRecruiter: 'أدخله المسؤول',
    knockoutSourceAI: 'مستخرج بالذكاء الاصطناعي',
    knockoutSourceAICV: 'ذكاء اصطناعي (السيرة)',
    knockoutSourceAIEmail: 'ذكاء اصطناعي (البريد)',
    knockoutSourceAICVEmail: 'ذكاء اصطناعي (السيرة + البريد)',
    knockoutSourceAISuggested: 'مقترح بالذكاء الاصطناعي (مقبول)',
    knockoutMethodDirect: 'تصريح مباشر',
    knockoutMethodInferred: 'استنتاج ذكاء اصطناعي',
    knockoutMethodApproved: 'موافقة المسؤول',
    knockoutMethodManual: 'إدخال يدوي',
    knockoutMethodNotFound: 'غير موجود',
    knockoutEditAnswer: 'تعديل',
    knockoutSaveAnswer: 'حفظ',
    knockoutCancelEdit: 'إلغاء',
    knockoutEditPlaceholder: 'أدخل الإجابة',
    knockoutGenerateSuggestions: 'تشغيل التحقق من الفرز',
    knockoutValidationCompleted: '✓ اكتمل التحقق من الفرز',
    knockoutValidationOutdated: '⚠ التحقق قديم',
    knockoutGenerating: 'جارٍ التحقق…',
    knockoutAISuggested: 'مقترح من السيرة الذاتية',
    knockoutAllAnswered: 'جميع أسئلة الفرز لها إجابات.',
    knockoutNoContentForAI: 'لا يوجد محتوى CV/بريد إلكتروني للتحقق.',
    knockoutAlreadyCompleted: 'اكتمل التحقق من الفرز. لم يُكتشف أي تغيير منذ آخر تشغيل.',
    knockoutSuggestionEvidence: 'الدليل',
    knockoutSuggestionConfidence: 'الثقة',
    knockoutSuggestionAccept: 'قبول',
    knockoutSuggestionEditAccept: 'تعديل وقبول',
    knockoutSuggestionIgnore: 'تجاهل',
    knockoutVerified: 'دليل مباشر',
    knockoutInferred: 'مستنتج',
    knockoutNoEvidence: 'لا دليل',
    knockoutContradiction: 'متناقض',
    knockoutSuggestionSourceCV: 'من السيرة الذاتية',
    knockoutSuggestionSourceEmail: 'من البريد الإلكتروني',
    knockoutSuggestionSourceBoth: 'من السيرة الذاتية والبريد',
    knockoutNoSuggestionsFound: 'لم يتم توليد أي اقتراحات. تأكد من معالجة السيرة الذاتية واستخراج النص منها.',
    knockoutNoContent: 'لا يوجد نص سيرة ذاتية أو بريد إلكتروني متاح. تأكد من اكتمال معالجة السيرة الذاتية قبل التحقق.',
    knockoutAnalysisFailed: 'فشل التحقق من الفرز',
    knockoutCvValidation: 'التحقق من الفرز',
    knockoutSupportedByCv: 'مدعوم بالسيرة الذاتية',
    knockoutContradictedByCv: 'متناقض مع السيرة الذاتية',
    knockoutNotSupportedByCv: 'غير مذكور في السيرة الذاتية',
    knockoutCannotValidate: 'لا يمكن التحقق',
    knockoutCannotDetermine: 'لا يمكن التحديد',
    knockoutReasoning: 'التفسير',
    knockoutValidationSummary: 'ملخص التحقق من الفرز',
    knockoutValidationLastRun: 'آخر تشغيل',
    knockoutValidationQuestionsProcessed: 'الأسئلة المعالجة',
    knockoutValidationPromptVersion: 'إصدار النظام',
    knockoutValidationModel: 'النموذج',
    knockoutValidationStatusLabel: 'الحالة',
    knockoutValidationAnswersValidated: 'إجابة تم التحقق منها',
    knockoutValidationSuggested: 'إجابة مقترحة',
    knockoutValidationSuggestedPlural: 'إجابات مقترحة',
    knockoutValidationCannotDetermine: 'لا يمكن التحديد',
    knockoutSeeMore: 'عرض المزيد',
    knockoutSeeLess: 'عرض أقل',
    knockoutFinalAnswer: 'الإجابة النهائية',
    knockoutScreeningValidation: 'التحقق من الفرز',
    knockoutCannotDetermineHelper: 'لم يتمكن الذكاء الاصطناعي من إيجاد أدلة كافية في السيرة الذاتية أو البريد لتحديد إجابة.',
    knockoutCannotValidateHelper: 'لم يتمكن الذكاء الاصطناعي من التحقق من الإجابة الموجودة باستخدام أدلة السيرة الذاتية المتاحة.',
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

export const ApplicationDetails: React.FC<ApplicationDetailsProps> = ({ data, onBack, jobMeta, onDownloadCV, downloadingCV, token, userRole, advancedMoveEnabled, onWorkflowStatusChange, onRecruiterNotesChange }) => {
  const { lang, isAr } = useLanguage() as { lang: 'en' | 'ar'; isAr: boolean };
  const t = T[lang];

  const [showRaw, setShowRaw] = useState(false);
  const [explainabilityExpanded, setExplainabilityExpanded] = useState(true);
  const [expandedDimensions, setExpandedDimensions] = useState<Record<string, boolean>>({});
  const [interviewPrepExpanded, setInterviewPrepExpanded] = useState(false);
  const [intelligenceExpanded, setIntelligenceExpanded] = useState(
    data.gatekeeper_passed === false
  );
  const [recruiterNotesText, setRecruiterNotesText] = useState(data.recruiter_notes ?? '');
  const [notesDirty, setNotesDirty] = useState(false);

  // Knockout inline-edit state (hoisted here to satisfy Rules of Hooks)
  const [koEditingQid, setKoEditingQid] = useState<string | null>(null);
  const [koEditingValue, setKoEditingValue] = useState('');
  const [koEditingSaving, setKoEditingSaving] = useState(false);
  const [koLocalAnswers, setKoLocalAnswers] = useState<Record<string, string>>({});
  // Knockout suggestion + validation state
  const [koSuggestions, setKoSuggestions] = useState<KnockoutSuggestionRecord[]>(
    data.knockout_suggestions ?? []
  );
  const [koValidations, setKoValidations] = useState<KnockoutValidationRecord[]>(
    data.knockout_validations ?? []
  );
  const [koValidationRun, setKoValidationRun] = useState<KnockoutValidationRun | null>(
    data.latest_validation_run ?? null
  );
  const [koValidationLocalStale, setKoValidationLocalStale] = useState(false);
  const [koEvidenceExpanded, setKoEvidenceExpanded] = useState<Set<string>>(new Set());
  const [koAnalyzing, setKoAnalyzing] = useState(false);
  const [koSuggestionsError, setKoSuggestionsError] = useState<string | null>(null);
  const [koEditingSuggestionQid, setKoEditingSuggestionQid] = useState<string | null>(null);
  const [koEditingSuggestionValue, setKoEditingSuggestionValue] = useState('');

  const currentWorkflowStatus: WorkflowStatus = (data.workflow_status as WorkflowStatus) || 'awaiting_review';
  const WF_LABELS = lang === 'ar' ? WORKFLOW_STATUS_LABELS_AR : WORKFLOW_STATUS_LABELS_EN;
  const WF_STYLES = WORKFLOW_STATUS_STYLES_BORDERED;

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

      {/* ── Candidate Header Card ─────────────────────────────────────────────── */}
      {!isSecurityBlocked && (
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
            {!isLowMatch && analysis.summary && (
              <div className="px-8 py-5">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">{t.execSummary}</p>
                <p className={`text-sm text-textMain leading-relaxed italic ${isAr ? 'border-r-4 pr-4' : 'border-l-4 pl-4'} border-primary/20 py-1`}>
                  {analysis.summary}
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
      )}

      {/* Knockout Questions Section */}
      {(() => {
        const koAnswers: KnockoutAnswerRecord[] = data.knockout_answers ?? [];
        const kt = t as any;

        // State is hoisted to component body (koEditingQid etc.) — aliased here for locality
        const editingQid = koEditingQid;
        const setEditingQid = setKoEditingQid;
        const editingValue = koEditingValue;
        const setEditingValue = setKoEditingValue;
        const editingSaving = koEditingSaving;
        const setEditingSaving = setKoEditingSaving;
        const localAnswers = koLocalAnswers;
        const setLocalAnswers = setKoLocalAnswers;
        const suggestions = koSuggestions;
        const setSuggestions = setKoSuggestions;
        const validations = koValidations;
        const setValidations = setKoValidations;
        const validationRun = koValidationRun;
        const setValidationRun = setKoValidationRun;
        const validationLocalStale = koValidationLocalStale;
        const setValidationLocalStale = setKoValidationLocalStale;
        const evidenceExpanded = koEvidenceExpanded;
        const toggleEvidence = (qid: string) => setKoEvidenceExpanded(prev => {
          const next = new Set(prev);
          next.has(qid) ? next.delete(qid) : next.add(qid);
          return next;
        });
        const analyzing = koAnalyzing;
        const setAnalyzing = setKoAnalyzing;
        const suggestionsError = koSuggestionsError;
        const setSuggestionsError = setKoSuggestionsError;
        const editingSuggestionQid = koEditingSuggestionQid;
        const setEditingSuggestionQid = setKoEditingSuggestionQid;
        const editingSuggestionValue = koEditingSuggestionValue;
        const setEditingSuggestionValue = setKoEditingSuggestionValue;

        // Validation freshness: fresh = run exists + server says not stale + no local actions since
        const isValidationFresh = Boolean(validationRun && !validationRun.is_stale && !validationLocalStale);

        // Map validations by question_id for quick lookup
        const validationByQid = Object.fromEntries(
          validations.map(v => [v.question_id, v])
        );

        if (koAnswers.length === 0) return null;

        type EvalResult = 'passed' | 'failed' | 'no_criteria' | 'not_evaluated' | 'not_answered';

        const evaluateAnswer = (qa: KnockoutAnswerRecord, rawVal: string | null | undefined): EvalResult => {
          if (rawVal == null || rawVal === '') return 'not_answered';
          const pc: PassingCriteria | null | undefined = qa.passing_criteria;
          if (!pc) return 'no_criteria';

          if ((qa.question_type === 'yes_no' || qa.question_type === 'single_choice') && pc.passing_answers?.length) {
            const match = pc.passing_answers.some(a => a.toLowerCase() === rawVal.toLowerCase());
            return match ? 'passed' : 'failed';
          }

          if (qa.question_type === 'number' && pc.operator != null && pc.value != null) {
            const num = parseFloat(rawVal);
            if (isNaN(num)) return 'not_evaluated';
            const ops: Record<string, boolean> = {
              '>=': num >= pc.value,
              '>':  num >  pc.value,
              '=':  num === pc.value,
              '<=': num <= pc.value,
              '<':  num <  pc.value,
            };
            const result = ops[pc.operator];
            return result === undefined ? 'not_evaluated' : (result ? 'passed' : 'failed');
          }

          return 'not_evaluated';
        };

        const evalBadge = (result: EvalResult) => {
          if (result === 'not_answered') return (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-black bg-slate-100 text-slate-400 italic">
              {kt.knockoutNotAnswered}
            </span>
          );
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

        const sourceLabel = (src: string | null | undefined) => {
          if (!src || src === 'candidate_provided' || src === 'candidate_form') return kt.knockoutSourceCandidate;
          if (src === 'candidate_email') return kt.knockoutSourceCandidateEmail;
          if (src === 'recruiter_entered') return kt.knockoutSourceRecruiter;
          if (src === 'ai_extracted' || src === 'ai_cv') return kt.knockoutSourceAICV;
          if (src === 'ai_email') return kt.knockoutSourceAIEmail;
          if (src === 'ai_cv_email') return kt.knockoutSourceAICVEmail;
          if (src === 'ai_suggested_accepted') return kt.knockoutSourceAISuggested;
          return src;
        };

        const methodBadge = (method: string | null | undefined) => {
          if (!method || method === 'direct_statement') {
            return (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-green-50 text-green-700 text-[9px] font-black uppercase tracking-widest">
                {kt.knockoutMethodDirect}
              </span>
            );
          }
          if (method === 'ai_inference') {
            return (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600 text-[9px] font-black uppercase tracking-widest">
                {kt.knockoutMethodInferred}
              </span>
            );
          }
          if (method === 'recruiter_approved') {
            return (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-50 text-teal-700 text-[9px] font-black uppercase tracking-widest">
                {kt.knockoutMethodApproved}
              </span>
            );
          }
          if (method === 'manual_entry') {
            return (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-500 text-[9px] font-black uppercase tracking-widest">
                {kt.knockoutMethodManual}
              </span>
            );
          }
          if (method === 'not_found') {
            return (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 text-[9px] font-black uppercase tracking-widest">
                {kt.knockoutMethodNotFound}
              </span>
            );
          }
          return null;
        };

        const handleStartEdit = (qa: KnockoutAnswerRecord) => {
          const cur = localAnswers[qa.question_id] ?? qa.answer_value ?? '';
          setEditingQid(qa.question_id);
          setEditingValue(cur);
        };

        const handleSaveEdit = async (qa: KnockoutAnswerRecord) => {
          if (!token) return;
          setEditingSaving(true);
          try {
            await apiService.saveKnockoutAnswer(data.application_id, qa.question_id, editingValue.trim(), token);
            setLocalAnswers(prev => ({ ...prev, [qa.question_id]: editingValue.trim() }));
            setEditingQid(null);
            setValidationLocalStale(true);
          } catch {
            // leave edit mode open so user can retry
          } finally {
            setEditingSaving(false);
          }
        };

        const handleGenerateSuggestions = async () => {
          if (!token || analyzing) return;
          setAnalyzing(true);
          setSuggestionsError(null);
          try {
            const result = await apiService.triggerScreeningValidation(data.application_id, token);
            if (result?.status === 'ok') {
              if (Array.isArray(result.validations)) setValidations(result.validations);
              if (Array.isArray(result.suggestions)) setSuggestions(result.suggestions);
              if (result.run) { setValidationRun(result.run); setValidationLocalStale(false); }
              if (!result.validations_count && !result.suggestions_generated) {
                setSuggestionsError(kt.knockoutNoSuggestionsFound);
              }
            } else if (result?.status === 'already_completed') {
              if (Array.isArray(result.validations)) setValidations(result.validations);
              if (result.run) setValidationRun(result.run);
              setSuggestionsError(kt.knockoutAlreadyCompleted);
            } else if (result?.status === 'no_content') {
              setSuggestionsError(result.reason || kt.knockoutNoContentForAI);
            } else if (result?.status === 'error') {
              setSuggestionsError(result.reason || kt.knockoutAnalysisFailed);
            }
          } catch (err: any) {
            setSuggestionsError(err?.message ? `${kt.knockoutAnalysisFailed}: ${err.message}` : kt.knockoutAnalysisFailed);
          } finally {
            setAnalyzing(false);
          }
        };

        const handleAcceptSuggestion = async (questionId: string, overrideAnswer?: string) => {
          if (!token) return;
          try {
            await apiService.acceptKnockoutSuggestion(data.application_id, questionId, overrideAnswer ?? null, token);
            const accepted = suggestions.find(s => s.question_id === questionId);
            const finalAnswer = overrideAnswer ?? accepted?.suggested_answer ?? '';
            setLocalAnswers(prev => ({ ...prev, [questionId]: finalAnswer }));
            setSuggestions(prev => prev.map(s => s.question_id === questionId ? { ...s, status: 'accepted' } : s));
            setValidationLocalStale(true);
          } catch {
            // silently fail
          }
        };

        const handleIgnoreSuggestion = async (questionId: string) => {
          if (!token) return;
          try {
            await apiService.ignoreKnockoutSuggestion(data.application_id, questionId, token);
            setSuggestions(prev => prev.map(s => s.question_id === questionId ? { ...s, status: 'ignored' } : s));
            setValidationLocalStale(true);
          } catch {
            // silently fail
          }
        };

        const renderAnswerCell = (qa: KnockoutAnswerRecord) => {
          const displayVal = localAnswers[qa.question_id] ?? qa.answer_value;
          const isEditing = editingQid === qa.question_id;

          if (isEditing) {
            if (qa.question_type === 'yes_no') {
              return (
                <div className="flex flex-col gap-1.5">
                  <div className="flex gap-2">
                    {['yes', 'no'].map(opt => (
                      <button
                        key={opt}
                        onClick={() => setEditingValue(opt)}
                        className={`px-3 py-1 rounded-lg text-xs font-semibold border ${editingValue === opt ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-slate-600 border-slate-300 hover:border-indigo-400'}`}
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                  <div className="flex gap-1.5 mt-0.5">
                    <button onClick={() => handleSaveEdit(qa)} disabled={editingSaving} className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg disabled:opacity-50">
                      {kt.knockoutSaveAnswer}
                    </button>
                    <button onClick={() => setEditingQid(null)} disabled={editingSaving} className="px-2.5 py-1 bg-slate-100 text-slate-600 text-[11px] font-semibold rounded-lg">
                      {kt.knockoutCancelEdit}
                    </button>
                  </div>
                </div>
              );
            }
            if (qa.question_type === 'single_choice' && qa.options?.length) {
              return (
                <div className="flex flex-col gap-1.5">
                  <select
                    value={editingValue}
                    onChange={e => setEditingValue(e.target.value)}
                    className="text-sm border border-slate-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-300"
                  >
                    <option value="">—</option>
                    {qa.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                  </select>
                  <div className="flex gap-1.5">
                    <button onClick={() => handleSaveEdit(qa)} disabled={editingSaving} className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg disabled:opacity-50">
                      {kt.knockoutSaveAnswer}
                    </button>
                    <button onClick={() => setEditingQid(null)} disabled={editingSaving} className="px-2.5 py-1 bg-slate-100 text-slate-600 text-[11px] font-semibold rounded-lg">
                      {kt.knockoutCancelEdit}
                    </button>
                  </div>
                </div>
              );
            }
            // number or fallback
            return (
              <div className="flex flex-col gap-1.5">
                <input
                  type={qa.question_type === 'number' ? 'number' : 'text'}
                  value={editingValue}
                  onChange={e => setEditingValue(e.target.value)}
                  placeholder={kt.knockoutEditPlaceholder}
                  className="text-sm border border-slate-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-300 w-32"
                />
                <div className="flex gap-1.5">
                  <button onClick={() => handleSaveEdit(qa)} disabled={editingSaving} className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg disabled:opacity-50">
                    {kt.knockoutSaveAnswer}
                  </button>
                  <button onClick={() => setEditingQid(null)} disabled={editingSaving} className="px-2.5 py-1 bg-slate-100 text-slate-600 text-[11px] font-semibold rounded-lg">
                    {kt.knockoutCancelEdit}
                  </button>
                </div>
              </div>
            );
          }

          // Show suggestion panel when there's no final answer, a pending suggestion exists,
          // AND no validation result (validation panel handles the suggested case instead).
          const suggestion = suggestions.find(s => s.question_id === qa.question_id && s.status === 'pending');
          const hasValidation = Boolean(validationByQid[qa.question_id]);
          if ((displayVal == null || displayVal === '') && suggestion && !hasValidation) {
            const isEditingSuggestion = editingSuggestionQid === qa.question_id;
            const confPct = Math.round(suggestion.confidence * 100);
            const verificationLabel: Record<string, string> = {
              verified: kt.knockoutVerified,
              inferred: kt.knockoutInferred,
              no_evidence: kt.knockoutNoEvidence,
              contradiction: kt.knockoutContradiction,
              not_found: kt.knockoutNoEvidence,
            };
            const suggSourceLabel: Record<string, string> = {
              candidate_email: kt.knockoutSourceCandidateEmail,
              ai_cv: kt.knockoutSuggestionSourceCV,
              ai_email: kt.knockoutSuggestionSourceEmail,
              ai_cv_email: kt.knockoutSuggestionSourceBoth,
              cv: kt.knockoutSuggestionSourceCV,
              email_body: kt.knockoutSuggestionSourceEmail,
              cv_and_email: kt.knockoutSuggestionSourceBoth,
              not_found: '',
            };
            return (
              <div className="flex flex-col gap-2 border border-indigo-100 bg-indigo-50/60 rounded-xl p-3">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 text-[9px] font-black uppercase tracking-widest">
                    <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
                    {kt.knockoutAISuggested}
                  </span>
                  {suggestion.suggested_source !== 'not_found' && (
                    <span className="text-[9px] text-indigo-500 font-semibold">{suggSourceLabel[suggestion.suggested_source]}</span>
                  )}
                  {suggestion.answer_method && suggestion.answer_method !== 'ai_inference' && (
                    <span className="inline-flex items-center px-1 py-0.5 rounded-full bg-green-50 text-green-700 text-[8px] font-black uppercase tracking-widest">
                      {kt.knockoutMethodDirect}
                    </span>
                  )}
                  <span className="ml-auto text-[9px] text-slate-500 font-semibold">{kt.knockoutSuggestionConfidence}: {confPct}%</span>
                </div>
                {suggestion.suggested_answer && (
                  isEditingSuggestion ? (
                    <input
                      type="text"
                      value={editingSuggestionValue}
                      onChange={e => setEditingSuggestionValue(e.target.value)}
                      className="text-sm border border-slate-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-indigo-300 w-full"
                    />
                  ) : (
                    <p className="text-sm font-semibold text-indigo-900 capitalize">{suggestion.suggested_answer}</p>
                  )
                )}
                {suggestion.evidence_text && (
                  <p className="text-[10px] text-slate-500 leading-snug">
                    <span className="font-semibold text-slate-600">{kt.knockoutSuggestionEvidence}:</span> {suggestion.evidence_text}
                  </p>
                )}
                {suggestion.verification_status && (
                  <span className={`inline-flex self-start items-center px-1.5 py-0.5 rounded-full text-[9px] font-black uppercase tracking-widest ${
                    suggestion.verification_status === 'verified' ? 'bg-green-100 text-green-700' :
                    suggestion.verification_status === 'inferred' ? 'bg-amber-100 text-amber-700' :
                    suggestion.verification_status === 'contradiction' ? 'bg-red-100 text-red-700' :
                    'bg-slate-100 text-slate-500'
                  }`}>
                    {verificationLabel[suggestion.verification_status] ?? suggestion.verification_status}
                  </span>
                )}
                {token && (
                  <div className="flex gap-1.5 mt-0.5 flex-wrap">
                    {isEditingSuggestion ? (
                      <>
                        <button
                          onClick={() => { handleAcceptSuggestion(qa.question_id, editingSuggestionValue); setEditingSuggestionQid(null); }}
                          className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg hover:bg-indigo-700"
                        >
                          {kt.knockoutSuggestionAccept}
                        </button>
                        <button
                          onClick={() => setEditingSuggestionQid(null)}
                          className="px-2.5 py-1 bg-slate-100 text-slate-600 text-[11px] font-semibold rounded-lg"
                        >
                          {kt.knockoutCancelEdit}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => handleAcceptSuggestion(qa.question_id)}
                          className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg hover:bg-indigo-700"
                        >
                          {kt.knockoutSuggestionAccept}
                        </button>
                        <button
                          onClick={() => { setEditingSuggestionQid(qa.question_id); setEditingSuggestionValue(suggestion.suggested_answer ?? ''); }}
                          className="px-2.5 py-1 bg-white border border-indigo-300 text-indigo-700 text-[11px] font-semibold rounded-lg hover:bg-indigo-50"
                        >
                          {kt.knockoutSuggestionEditAccept}
                        </button>
                        <button
                          onClick={() => handleIgnoreSuggestion(qa.question_id)}
                          className="px-2.5 py-1 bg-slate-100 text-slate-500 text-[11px] font-semibold rounded-lg hover:bg-slate-200"
                        >
                          {kt.knockoutSuggestionIgnore}
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          }

          return (
            <div className="flex items-start gap-2">
              {displayVal != null && displayVal !== '' ? (
                <p className="text-sm font-semibold text-textMain capitalize">{displayVal}</p>
              ) : (
                <p className="text-sm text-slate-400 italic">{kt.knockoutNotAnswered}</p>
              )}
              {token && (
                <button
                  onClick={() => handleStartEdit(qa)}
                  className="ml-auto flex-shrink-0 text-[10px] text-indigo-500 hover:text-indigo-700 font-semibold underline"
                >
                  {kt.knockoutEditAnswer}
                </button>
              )}
            </div>
          );
        };

        return (
          <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
            <div className="px-6 py-3 border-b border-border flex items-center gap-2 bg-slate-50">
              <svg className="w-3.5 h-3.5 text-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest">{kt.knockoutSectionTitle}</h4>
              {token && data.submission_source !== 'public_apply' && (
                isValidationFresh ? (
                  <button
                    disabled
                    className="ml-auto flex items-center gap-1.5 px-3 py-1 bg-green-50 border border-green-200 text-green-700 text-[10px] font-bold rounded-lg opacity-80 cursor-default"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                    {kt.knockoutValidationCompleted}
                  </button>
                ) : (
                  <button
                    onClick={handleGenerateSuggestions}
                    disabled={analyzing}
                    className={`ml-auto flex items-center gap-1.5 px-3 py-1 border text-[10px] font-bold rounded-lg disabled:opacity-50 transition-colors ${validationRun && !isValidationFresh ? 'bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100' : 'bg-indigo-50 border-indigo-200 text-indigo-700 hover:bg-indigo-100'}`}
                  >
                    {analyzing ? (
                      <><span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />{kt.knockoutGenerating}</>
                    ) : validationRun && !isValidationFresh ? (
                      <><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>{kt.knockoutValidationOutdated}</>
                    ) : (
                      <><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>{kt.knockoutGenerateSuggestions}</>
                    )}
                  </button>
                )
              )}
            </div>

            {/* Validation Summary — shown when a completed run exists */}
            {validationRun && (
              <div className={`px-6 py-3 border-b flex flex-wrap gap-x-6 gap-y-1.5 ${validationRun.is_stale || validationLocalStale ? 'bg-amber-50 border-amber-100' : 'bg-green-50 border-green-100'}`}>
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">{kt.knockoutValidationStatusLabel}:</span>
                  {validationRun.is_stale || validationLocalStale ? (
                    <span className="text-[9px] font-black text-amber-600">⚠ {kt.knockoutValidationOutdated.replace('⚠ ', '')}</span>
                  ) : (
                    <span className="text-[9px] font-black text-green-600">✓ Completed</span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">{kt.knockoutValidationLastRun}:</span>
                  <span className="text-[9px] text-slate-500">{new Date(validationRun.created_at).toLocaleString()}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">{kt.knockoutValidationQuestionsProcessed}:</span>
                  <span className="text-[9px] text-slate-500">{validationRun.questions_count}</span>
                </div>
                {validationRun.prompt_version && (
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">{kt.knockoutValidationPromptVersion}:</span>
                    <span className="text-[9px] text-slate-500">v{validationRun.prompt_version}</span>
                  </div>
                )}
                {validationRun.model && (
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">{kt.knockoutValidationModel}:</span>
                    <span className="text-[9px] text-slate-500 font-mono">{validationRun.model}</span>
                  </div>
                )}
                <div className="w-full flex flex-wrap gap-2 mt-0.5">
                  {validationRun.validated_count > 0 && (
                    <span className="text-[9px] text-green-700">✓ {validationRun.validated_count} {kt.knockoutValidationAnswersValidated}</span>
                  )}
                  {validationRun.suggested_count > 0 && (
                    <span className="text-[9px] text-indigo-600">● {validationRun.suggested_count} {validationRun.suggested_count === 1 ? kt.knockoutValidationSuggested : kt.knockoutValidationSuggestedPlural}</span>
                  )}
                  {validationRun.cannot_count > 0 && (
                    <span className="text-[9px] text-slate-400">— {validationRun.cannot_count} {kt.knockoutValidationCannotDetermine}</span>
                  )}
                </div>
              </div>
            )}

            {suggestionsError && suggestionsError !== kt.knockoutAlreadyCompleted && (
              <div className="px-6 py-2 bg-amber-50 border-b border-amber-100 flex items-start gap-2">
                <svg className="w-3.5 h-3.5 text-amber-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" /></svg>
                <p className="text-[11px] text-amber-700 leading-snug">{suggestionsError}</p>
              </div>
            )}
            <div className="divide-y divide-slate-100">
              {koAnswers.map((qa, idx) => {
                const displayVal = localAnswers[qa.question_id] ?? qa.answer_value;
                const evalResult = evaluateAnswer(qa, displayVal);
                const criteriaLabel = formatCriteria(qa);
                const src = editingQid === qa.question_id ? 'recruiter_entered' : (localAnswers[qa.question_id] != null ? 'recruiter_entered' : qa.answer_source);
                const validation = validationByQid[qa.question_id];
                const validationStatusBadge = (vstatus: string) => {
                  const map: Record<string, string> = {
                    supported_by_cv:     'bg-green-50 text-green-700',
                    contradicted_by_cv:  'bg-red-50 text-red-700',
                    not_supported_by_cv: 'bg-slate-100 text-slate-500',
                    cannot_validate:     'bg-amber-50 text-amber-600',
                    suggested:           'bg-indigo-50 text-indigo-600',
                    cannot_determine:    'bg-slate-100 text-slate-400',
                  };
                  const statusLabel: Record<string, string> = {
                    supported_by_cv:     kt.knockoutSupportedByCv,
                    contradicted_by_cv:  kt.knockoutContradictedByCv,
                    not_supported_by_cv: kt.knockoutNotSupportedByCv,
                    cannot_validate:     kt.knockoutCannotValidate,
                    suggested:           kt.knockoutAISuggested,
                    cannot_determine:    kt.knockoutCannotDetermine,
                  };
                  return (
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[9px] font-black uppercase tracking-widest ${map[vstatus] ?? 'bg-slate-100 text-slate-500'}`}>
                      {statusLabel[vstatus] ?? vstatus}
                    </span>
                  );
                };

                return (
                  <div key={qa.question_id} className="px-6 py-4 grid grid-cols-1 md:grid-cols-4 gap-x-8 gap-y-1.5">
                    <div className="md:col-span-2">
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{idx + 1}. {kt.knockoutQuestion}</p>
                      <p className="text-sm font-medium text-textMain">
                        {qa.question_text}
                        {qa.is_required && (
                          <span className="ml-1.5 px-1.5 py-0.5 bg-slate-100 text-textMuted text-[9px] font-black uppercase rounded">{kt.knockoutRequired}</span>
                        )}
                      </p>
                      {displayVal != null && displayVal !== '' && src && (
                        <div className="flex flex-col gap-0.5 mt-0.5">
                          <div className="flex items-center gap-1 flex-wrap">
                            <p className="text-[10px] text-slate-400">{kt.knockoutSource}: {sourceLabel(src)}</p>
                            {qa.answer_method && methodBadge(qa.answer_method)}
                            {qa.confidence != null && (
                              <span className="text-[9px] text-slate-400 font-mono">
                                {kt.knockoutSuggestionConfidence}: {Math.round(qa.confidence * 100)}%
                              </span>
                            )}
                          </div>
                          {qa.evidence_text && src === 'candidate_email' && qa.answer_method === 'direct_statement' && (
                            <p className="text-[10px] text-slate-400 italic leading-snug line-clamp-2">
                              {kt.knockoutSuggestionEvidence}: "{qa.evidence_text}"
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                    <div>
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-0.5">{kt.knockoutFinalAnswer}</p>
                      {renderAnswerCell(qa)}
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

                    {/* Screening Validation panel — full width */}
                    {validation && (() => {
                      const EVIDENCE_PREVIEW = 120;
                      const ev = validation.evidence_text ?? '';
                      const isExpanded = evidenceExpanded.has(qa.question_id);
                      const showToggle = ev.length > EVIDENCE_PREVIEW;
                      const displayEvidence = showToggle && !isExpanded ? ev.slice(0, EVIDENCE_PREVIEW) + '…' : ev;

                      return (
                        <div className={`md:col-span-4 mt-1 rounded-lg border px-4 py-3 space-y-2 ${
                            validation.validation_status === 'contradicted_by_cv' ? 'border-red-200 bg-red-50' :
                            validation.validation_status === 'suggested' ? 'border-indigo-100 bg-indigo-50' :
                            'border-slate-100 bg-slate-50'
                          }`}>
                          {/* Header row */}
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[9px] font-black text-textMuted uppercase tracking-widest">{kt.knockoutCvValidation}</span>
                            {validationStatusBadge(validation.validation_status)}
                            {validation.confidence != null && validation.confidence > 0 && (
                              <span className="text-[9px] text-slate-400 font-mono">{kt.knockoutSuggestionConfidence}: {Math.round(validation.confidence * 100)}%</span>
                            )}
                          </div>

                          {/* Helper text for cannot_* statuses */}
                          {validation.validation_status === 'cannot_determine' && (
                            <p className="text-[10px] text-slate-500 italic">{kt.knockoutCannotDetermineHelper}</p>
                          )}
                          {validation.validation_status === 'cannot_validate' && (
                            <p className="text-[10px] text-slate-500 italic">{kt.knockoutCannotValidateHelper}</p>
                          )}

                          {/* Suggested answer block */}
                          {validation.validation_status === 'suggested' && validation.suggested_answer && (() => {
                            const valSuggestion = suggestions.find(s => s.question_id === qa.question_id && s.status === 'pending');
                            if (!valSuggestion) return null;
                            const isEditingThis = editingSuggestionQid === qa.question_id;
                            return (
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-xs font-semibold text-indigo-700">{kt.knockoutAISuggested}: <span className="capitalize">{validation.suggested_answer}</span></span>
                                {!isEditingThis ? (
                                  <>
                                    <button onClick={() => handleAcceptSuggestion(qa.question_id)} className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg hover:bg-indigo-700">{kt.knockoutSuggestionAccept}</button>
                                    <button onClick={() => { setEditingSuggestionQid(qa.question_id); setEditingSuggestionValue(validation.suggested_answer ?? ''); }} className="px-2.5 py-1 bg-white border border-indigo-300 text-indigo-700 text-[11px] font-semibold rounded-lg hover:bg-indigo-50">{kt.knockoutSuggestionEditAccept}</button>
                                    <button onClick={() => handleIgnoreSuggestion(qa.question_id)} className="px-2.5 py-1 bg-slate-100 text-slate-500 text-[11px] font-semibold rounded-lg hover:bg-slate-200">{kt.knockoutSuggestionIgnore}</button>
                                  </>
                                ) : (
                                  <>
                                    <input type="text" value={editingSuggestionValue} onChange={e => setEditingSuggestionValue(e.target.value)} className="border border-indigo-300 rounded-lg px-2 py-1 text-xs text-textMain w-40 focus:outline-none focus:ring-1 focus:ring-indigo-400" />
                                    <button onClick={() => handleAcceptSuggestion(qa.question_id, editingSuggestionValue)} className="px-2.5 py-1 bg-indigo-600 text-white text-[11px] font-semibold rounded-lg">{kt.knockoutSuggestionAccept}</button>
                                    <button onClick={() => setEditingSuggestionQid(null)} className="px-2.5 py-1 bg-slate-100 text-slate-500 text-[11px] font-semibold rounded-lg">{kt.knockoutCancelEdit}</button>
                                  </>
                                )}
                              </div>
                            );
                          })()}

                          {/* Evidence — collapsible */}
                          {ev && (
                            <div>
                              <p className="text-[10px] text-slate-500 italic leading-snug">
                                <span className="not-italic font-semibold text-slate-400">{kt.knockoutSuggestionEvidence}: </span>
                                "{displayEvidence}"
                                {showToggle && (
                                  <button onClick={() => toggleEvidence(qa.question_id)} className="ml-1 not-italic text-indigo-500 hover:text-indigo-700 font-semibold">
                                    {isExpanded ? kt.knockoutSeeLess : kt.knockoutSeeMore}
                                  </button>
                                )}
                              </p>
                            </div>
                          )}

                          {/* Reasoning */}
                          {validation.reasoning_summary && (
                            <p className="text-[10px] text-slate-500 leading-snug">
                              <span className="font-semibold">{kt.knockoutReasoning}:</span> {validation.reasoning_summary}
                            </p>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* Candidate Assessment — deterministic evidence panel (primary) */}
      {!isSecurityBlocked && data.det_score && (() => {
        const ds: DeterministicScore = data.det_score!;
        const req = ds.required_summary;
        const pref = ds.preferred_summary;
        const signal = ds.recruiter_signal;

        const signalColors: Record<string, string> = {
          STRONG_FULL_MATCH:            'bg-emerald-100 text-emerald-800 border-emerald-200',
          STRONG_REQUIRED_WEAK_PREFERRED:'bg-emerald-100 text-emerald-800 border-emerald-200',
          STRONG_REQUIRED_NO_PREFERRED: 'bg-emerald-100 text-emerald-800 border-emerald-200',
          NEAR_MATCH:                   'bg-amber-100 text-amber-800 border-amber-200',
          MOSTLY_MET:                   'bg-amber-100 text-amber-800 border-amber-200',
          PARTIAL_REQUIRED:             'bg-orange-100 text-orange-800 border-orange-200',
          POOR_REQUIRED_COVERAGE:       'bg-red-100 text-red-800 border-red-200',
          NO_REQUIRED_CRITERIA:         'bg-slate-100 text-slate-700 border-slate-200',
        };

        const statusIcon = (status: string) => {
          if (status === 'MATCHED') return <span className="text-emerald-600 font-black text-sm">✓</span>;
          if (status === 'PARTIAL') return <span className="text-amber-500 font-black text-sm">△</span>;
          return <span className="text-red-500 font-black text-sm">✗</span>;
        };

        const statusBg = (status: string) => {
          if (status === 'MATCHED') return 'border-l-2 border-emerald-400 bg-emerald-50/40';
          if (status === 'PARTIAL') return 'border-l-2 border-amber-400 bg-amber-50/40';
          return 'border-l-2 border-red-300 bg-red-50/30';
        };

        // Safety guard: effective_credit=0 must never render a green check.
        // The scoring engine can set status='MATCHED' while applying quality_factor=0,
        // producing effective_credit=0. The display should reflect the credit, not
        // the raw status, to avoid showing contradictory signals to recruiters.
        const displayStatus = (c: DetCriterionScore): 'MATCHED' | 'PARTIAL' | 'ABSENT' => {
          if ((c.effective_credit ?? 0) === 0) return 'ABSENT';
          return c.status;
        };

        const dimOrder = ['skills','experience','education','certifications','soft_skills','domain_knowledge','other'];
        const orderedDims = dimOrder
          .filter(k => ds.dimensions[k])
          .concat(Object.keys(ds.dimensions).filter(k => !dimOrder.includes(k)));

        return (
          <div className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
            <button
              onClick={() => setExplainabilityExpanded(v => !v)}
              className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
            >
              <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-3">
                <span className="w-2 h-4 bg-emerald-500 rounded-full"></span>
                {(t as any).explainabilityTitle}
              </h3>
              <svg className={`w-5 h-5 text-textMuted transform transition-transform ${explainabilityExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {explainabilityExpanded && (
              <div className="px-8 pb-8 pt-2 border-t border-slate-50 space-y-6 animate-fade-in">
                {/* Coverage Summary + Signal */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="sm:col-span-1 bg-slate-50 rounded-2xl p-5">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.requiredRequirements}</p>
                    <div className="flex gap-4 mb-2 flex-wrap">
                      <span className="flex items-center gap-1 text-sm font-bold text-emerald-700"><span className="text-base">✓</span> {t.xMatched}: <span className="ml-0.5">{req.matched}</span></span>
                      <span className="flex items-center gap-1 text-sm font-bold text-amber-600"><span className="text-base">△</span> {t.xPartial}: <span className="ml-0.5">{req.partial}</span></span>
                      <span className="flex items-center gap-1 text-sm font-bold text-red-600"><span className="text-base">✗</span> {t.xMissing}: <span className="ml-0.5">{req.absent}</span></span>
                    </div>
                    {req.coverage_pct != null && (
                      <div className="mt-3">
                        <div className="flex justify-between items-center mb-1">
                          <span className="text-[10px] text-textMuted font-bold uppercase tracking-wider">{t.xCoverage}</span>
                          <span className="text-sm font-black text-textMain">{Math.round(req.coverage_pct)}%</span>
                        </div>
                        <div className="w-full bg-slate-200 rounded-full h-1.5">
                          <div className={`h-1.5 rounded-full ${req.coverage_pct >= 80 ? 'bg-emerald-500' : req.coverage_pct >= 50 ? 'bg-amber-400' : 'bg-red-400'}`} style={{ width: `${Math.min(100, req.coverage_pct)}%` }} />
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="sm:col-span-1 bg-slate-50 rounded-2xl p-5">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.preferredRequirements}</p>
                    <div className="flex gap-4 mb-2 flex-wrap">
                      <span className="flex items-center gap-1 text-sm font-bold text-emerald-700"><span className="text-base">✓</span> {t.xMatched}: <span className="ml-0.5">{pref.matched}</span></span>
                      <span className="flex items-center gap-1 text-sm font-bold text-amber-600"><span className="text-base">△</span> {t.xPartial}: <span className="ml-0.5">{pref.partial}</span></span>
                      <span className="flex items-center gap-1 text-sm font-bold text-red-600"><span className="text-base">✗</span> {t.xMissing}: <span className="ml-0.5">{pref.absent}</span></span>
                    </div>
                    {pref.coverage_pct != null && (
                      <div className="mt-3">
                        <div className="flex justify-between items-center mb-1">
                          <span className="text-[10px] text-textMuted font-bold uppercase tracking-wider">{t.xCoverage}</span>
                          <span className="text-sm font-black text-textMain">{Math.round(pref.coverage_pct)}%</span>
                        </div>
                        <div className="w-full bg-slate-200 rounded-full h-1.5">
                          <div className={`h-1.5 rounded-full ${pref.coverage_pct >= 80 ? 'bg-emerald-500' : pref.coverage_pct >= 50 ? 'bg-amber-400' : 'bg-red-400'}`} style={{ width: `${Math.min(100, pref.coverage_pct)}%` }} />
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="sm:col-span-1 flex flex-col justify-center">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.recruiterSignalLabel}</p>
                    <div className={`rounded-2xl border px-5 py-4 ${signalColors[signal] || 'bg-slate-100 text-slate-700 border-slate-200'}`}>
                      <p className="text-base font-black mb-1">{(t.recruiterSignalLabels?.[signal]) || signal.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                      <p className="text-xs leading-relaxed opacity-80">{(t.recruiterSignalDescriptions?.[signal]) || ''}</p>
                    </div>
                  </div>
                </div>
                {/* Qualitative Summary */}
                {ds.qualitative_summary && (
                  <div className="bg-slate-50 rounded-2xl p-6 space-y-4">
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.qualitativeSummaryTitle || 'Evaluation Summary'}</p>
                    {ds.qualitative_summary.evaluation_notes && (
                      <p className="text-sm text-textMain leading-relaxed">{ds.qualitative_summary.evaluation_notes}</p>
                    )}
                    {ds.qualitative_summary.strengths && ds.qualitative_summary.strengths.length > 0 && (
                      <div>
                        <p className="text-[10px] font-black text-emerald-700 uppercase tracking-wider mb-2">{t.sections?.strengths || 'Strengths'}</p>
                        <ul className="space-y-1">
                          {ds.qualitative_summary.strengths.map((s: string, i: number) => (
                            <li key={i} className="text-sm text-textMain flex items-start gap-2">
                              <span className="text-emerald-500 mt-0.5">•</span>
                              <span>{s}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {ds.qualitative_summary.gaps_identified && ds.qualitative_summary.gaps_identified.length > 0 && (
                      <div>
                        <p className="text-[10px] font-black text-amber-700 uppercase tracking-wider mb-2">{t.gapsIdentified || 'Gaps Identified'}</p>
                        <ul className="space-y-1">
                          {ds.qualitative_summary.gaps_identified.map((g: string, i: number) => (
                            <li key={i} className="text-sm text-textMain flex items-start gap-2">
                              <span className="text-amber-500 mt-0.5">•</span>
                              <span>{g}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {ds.qualitative_summary.suggested_interview_questions && ds.qualitative_summary.suggested_interview_questions.length > 0 && (
                      <div>
                        <p className="text-[10px] font-black text-indigo-700 uppercase tracking-wider mb-2">{t.suggestedInterviewQuestions || 'Suggested Interview Questions'}</p>
                        <ul className="space-y-1">
                          {ds.qualitative_summary.suggested_interview_questions.map((q: string, i: number) => (
                            <li key={i} className="text-sm text-textMain flex items-start gap-2">
                              <span className="text-indigo-500 mt-0.5">{i + 1}.</span>
                              <span>{q}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}

                {/* Per-dimension breakdown */}
                <div className="space-y-3">
                  {orderedDims.map(dimKey => {
                    const dim: DetDimensionScore = ds.dimensions[dimKey];
                    const isOpen = expandedDimensions[dimKey] ?? false;
                    const dimLabel = (t.xDimensionLabels?.[dimKey]) || dimKey.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                    const scorePercent = Math.round(dim.dimension_score * 100);
                    const requiredCriteria = dim.criteria.filter(c => c.required);
                    const preferredCriteria = dim.criteria.filter(c => !c.required);
                    const hasCriteria = dim.criteria.length > 0;
                    return (
                      <div key={dimKey} className="border border-slate-200 rounded-2xl overflow-hidden">
                        <button
                          onClick={() => setExpandedDimensions(prev => ({ ...prev, [dimKey]: !prev[dimKey] }))}
                          className="w-full px-5 py-4 flex items-center gap-4 hover:bg-slate-50 transition-colors text-left"
                        >
                          <div className="flex-1 flex items-center gap-3 flex-wrap">
                            <span className="text-sm font-black text-textMain">{dimLabel}</span>
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-black ${scorePercent >= 80 ? 'bg-emerald-100 text-emerald-700' : scorePercent >= 50 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'}`}>{scorePercent}%</span>
                            <span className="text-[10px] text-textMuted bg-slate-100 rounded px-1.5 py-0.5 font-bold">{Math.round(dim.weight_pct * 100)}% {t.xWeight}</span>
                          </div>
                          <div className="flex items-center gap-3 text-[10px] font-bold text-textMuted">
                            {dim.n_required > 0 && (
                              <span>
                                <span className="text-emerald-600">{dim.n_required_matched}✓</span>
                                {dim.n_required_partial > 0 && <span className="text-amber-500"> {dim.n_required_partial}△</span>}
                                {dim.n_required_absent > 0 && <span className="text-red-500"> {dim.n_required_absent}✗</span>}
                                <span className="ml-1 text-textMuted">req</span>
                              </span>
                            )}
                            {dim.n_preferred > 0 && (
                              <span>
                                <span className="text-emerald-600">{dim.n_preferred_matched}✓</span>
                                {dim.n_preferred_partial > 0 && <span className="text-amber-500"> {dim.n_preferred_partial}△</span>}
                                {dim.n_preferred_absent > 0 && <span className="text-red-500"> {dim.n_preferred_absent}✗</span>}
                                <span className="ml-1 text-textMuted">pref</span>
                              </span>
                            )}
                          </div>
                          <svg className={`w-4 h-4 text-textMuted transform transition-transform flex-shrink-0 ${isOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                          </svg>
                        </button>
                        {isOpen && (
                          <div className="px-5 pb-5 pt-1 border-t border-slate-100 space-y-2">
                            {!hasCriteria && <p className="text-sm text-textMuted italic py-3">{t.xNoCriteriaData}</p>}
                            {requiredCriteria.length > 0 && (
                              <div className="space-y-2">
                                {requiredCriteria.map((c: DetCriterionScore, i: number) => {
                                  const ds = displayStatus(c);
                                  return (
                                  <div key={i} className={`rounded-xl p-3 ${statusBg(ds)}`}>
                                    <div className="flex items-start gap-2 flex-wrap">
                                      {statusIcon(ds)}
                                      <span className="flex-1 text-sm font-semibold text-textMain leading-snug">{c.criterion_text}</span>
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-black bg-indigo-100 text-indigo-700 uppercase tracking-wide flex-shrink-0">{t.badgeRequired}</span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-textMuted">
                                      <span>{t.matchTypeLabels?.[c.match_type] || (c.match_type || '').replace(/_/g, ' ')}</span>
                                      <span>{t.xConfidence}: {Math.round(c.confidence * 100)}%</span>
                                      <span>{t.xEffectiveCredit}: {Math.round(c.effective_credit * 100)}%</span>
                                    </div>
                                    {c.supporting_evidence.length > 0 && (
                                      <div className="mt-2 space-y-1">
                                        <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.xEvidence}</p>
                                        {c.supporting_evidence.map((ev, ei) => (
                                          <p key={ei} className="text-xs text-textMain bg-white rounded-lg px-3 py-1.5 border border-slate-100 leading-relaxed">{ev}</p>
                                        ))}
                                      </div>
                                    )}
                                    {ds !== 'ABSENT' && c.supporting_evidence.length === 0 && (
                                      <p className="mt-2 text-[10px] text-textMuted italic">{t.xNoEvidence}</p>
                                    )}
                                  </div>
                                  );
                                })}
                              </div>
                            )}
                            {preferredCriteria.length > 0 && (
                              <div className="space-y-2 pt-1">
                                {preferredCriteria.map((c: DetCriterionScore, i: number) => {
                                  const ds = displayStatus(c);
                                  return (
                                  <div key={i} className={`rounded-xl p-3 ${statusBg(ds)}`}>
                                    <div className="flex items-start gap-2 flex-wrap">
                                      {statusIcon(ds)}
                                      <span className="flex-1 text-sm font-semibold text-textMain leading-snug">{c.criterion_text}</span>
                                      <span className="px-1.5 py-0.5 rounded text-[9px] font-black bg-slate-100 text-slate-600 uppercase tracking-wide flex-shrink-0">{t.badgePreferred}</span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-textMuted">
                                      <span>{t.matchTypeLabels?.[c.match_type] || (c.match_type || '').replace(/_/g, ' ')}</span>
                                      <span>{t.xConfidence}: {Math.round(c.confidence * 100)}%</span>
                                      <span>{t.xEffectiveCredit}: {Math.round(c.effective_credit * 100)}%</span>
                                    </div>
                                    {c.supporting_evidence.length > 0 && (
                                      <div className="mt-2 space-y-1">
                                        <p className="text-[10px] font-black text-textMuted uppercase tracking-wider">{t.xEvidence}</p>
                                        {c.supporting_evidence.map((ev, ei) => (
                                          <p key={ei} className="text-xs text-textMain bg-white rounded-lg px-3 py-1.5 border border-slate-100 leading-relaxed">{ev}</p>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                  );
                                })}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* Evidence-Based Analysis — fallback when no det_score */}
      {!isSecurityBlocked && !data.det_score && hasEvidenceSection && (
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

      {/* Interview Preparation — collapsible */}
      {!isSecurityBlocked && !isLowMatch && ((analysis.interview_focus_points?.length ?? 0) > 0 || (analysis.interview_suggested_questions?.length ?? 0) > 0) && (
        <div className="bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <button
            onClick={() => setInterviewPrepExpanded(v => !v)}
            className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
          >
            <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center gap-3">
              <span className="w-2 h-4 bg-indigo-500 rounded-full"></span>
              {t.interviewPrep}
            </h3>
            <svg className={`w-5 h-5 text-textMuted transform transition-transform ${interviewPrepExpanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          {interviewPrepExpanded && (
            <div className="border-t border-slate-100 animate-fade-in">
              <div className="bg-indigo-900 m-6 rounded-3xl p-8 text-white shadow-xl shadow-indigo-200">
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
            </div>
          )}
        </div>
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
            <WorkflowActionMenu
              applicationId={data.application_id}
              currentStatus={currentWorkflowStatus}
              candidateName={data.candidate_name}
              processingStatus={data.processing_status ?? 'ai_scored'}
              isUpdating={false}
              lang={lang}
              userRole={userRole}
              advancedMoveEnabled={advancedMoveEnabled}
              onTransition={(appId, toStatus, note, isAdvancedMove) => onWorkflowStatusChange(appId, toStatus, note, isAdvancedMove)}
            />

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
                        {h.is_advanced_move && (
                          <span className="ml-1.5 text-[9px] font-bold uppercase tracking-wide bg-amber-100 text-amber-700 px-1 py-0.5 rounded border border-amber-200">
                            Exceptional
                          </span>
                        )}
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

    </div>
  );
};
