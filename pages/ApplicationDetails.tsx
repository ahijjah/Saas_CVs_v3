
import React, { useState } from 'react';
import { ApplicationDetailedAnalysis, ScoreDimension } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface ApplicationDetailsProps {
  data: ApplicationDetailedAnalysis;
  onBack: () => void;
}

const T = {
  en: {
    back: 'Back to Applications',
    points: 'Points',
    noDecision: 'No Decision',
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
    interviewPrep: 'Interview Preparation',
    focusAreas: 'Focus Areas',
    suggestedQs: 'Suggested Questions',
    rawPayload: 'Developer: Raw AI Payload',
  },
  ar: {
    back: 'العودة إلى الطلبات',
    points: 'نقطة',
    noDecision: 'لا يوجد قرار',
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
    interviewPrep: 'التحضير للمقابلة',
    focusAreas: 'مجالات التركيز',
    suggestedQs: 'أسئلة مقترحة',
    rawPayload: 'المطور: البيانات الخام للذكاء الاصطناعي',
  },
};

export const ApplicationDetails: React.FC<ApplicationDetailsProps> = ({ data, onBack }) => {
  const { lang } = useLanguage();
  const t = T[lang];

  const [showRaw, setShowRaw] = useState(false);

  const getScoreColor = (achieved: number, max: number) => {
    if (!max || max === 0) return 'bg-slate-200';
    const percentage = (achieved / max) * 100;
    if (percentage >= 80) return 'bg-success';
    if (percentage >= 60) return 'bg-warning';
    return 'bg-error';
  };

  const renderScoreBar = (label: string, dimension?: ScoreDimension) => {
    if (!dimension || dimension.max === 0) return null;
    const percentage = Math.min(100, (dimension.achieved / dimension.max) * 100);
    const colorClass = getScoreColor(dimension.achieved, dimension.max);

    return (
      <div className="space-y-1">
        <div className="flex justify-between text-[10px] font-black uppercase tracking-wider">
          <span className="text-textMuted">{label}</span>
          <span className="text-textMain font-mono">{dimension.achieved} / {dimension.max}</span>
        </div>
        <div className="w-full bg-slate-100 h-2 rounded-full overflow-hidden border border-slate-200/50">
          <div
            className={`h-full transition-all duration-1000 ${colorClass}`}
            style={{ width: `${percentage}%` }}
          />
        </div>
      </div>
    );
  };

  const renderSection = (title: string, content: any, icon?: React.ReactNode) => {
    if (!content || (Array.isArray(content) && content.length === 0)) return null;

    return (
      <div className="bg-white p-6 rounded-2xl border border-border shadow-sm flex flex-col h-full">
        <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4 flex items-center">
          {icon && <span className="mr-2">{icon}</span>}
          {title}
        </h4>
        <div className="flex-1">
          {typeof content === 'string' ? (
            <p className="text-sm text-textMain leading-relaxed whitespace-pre-wrap">{content}</p>
          ) : Array.isArray(content) ? (
            <ul className="space-y-2">
              {content.map((item, i) => (
                <li key={i} className="text-sm text-textMain flex items-start">
                  <span className="text-primary mr-2 font-bold">•</span>
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

  const scoreDims: [string, ScoreDimension | undefined][] = [
    [t.scoreBars[0], scores.skills],
    [t.scoreBars[1], scores.experience],
    [t.scoreBars[2], scores.education],
    [t.scoreBars[3], scores.certifications],
    [t.scoreBars[4], scores.soft_skills],
    [t.scoreBars[5], scores.domain_knowledge],
    [t.scoreBars[6], scores.other_requirements],
  ];

  return (
    <div className="space-y-8 animate-fade-in max-w-6xl mx-auto pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="flex items-center text-primary hover:text-primaryDark transition-colors text-sm font-bold group gap-2">
          <svg className="w-4 h-4 transform group-hover:-translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t.back}
        </button>
      </div>

      {/* Profile & Scoring */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 bg-white rounded-3xl border border-border shadow-sm overflow-hidden">
          <div className="p-8 bg-slate-50 border-b border-border flex items-center gap-8">
            <div className={`w-24 h-24 rounded-3xl flex flex-col items-center justify-center font-black text-white shadow-xl rotate-3 transform hover:rotate-0 transition-transform ${
              score >= 80 ? 'bg-success' : score >= 60 ? 'bg-warning' : 'bg-error'
            }`}>
              <span className="text-3xl">{score}</span>
              <span className="text-[10px] opacity-80 uppercase leading-none tracking-tighter">{t.points}</span>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-widest ${
                  data.decision === 'qualified' ? 'bg-green-100 text-green-800' :
                  data.decision === 'partial' ? 'bg-amber-100 text-amber-800' :
                  'bg-red-100 text-red-800'
                }`}>
                  {data.decision || t.noDecision}
                </span>
                <span className="text-[10px] font-black text-textMuted uppercase tracking-widest">• REF: {data.application_id}</span>
              </div>
              <h1 className="text-3xl font-black text-textMain tracking-tight">{candidateName}</h1>
            </div>
          </div>

          <div className="p-8">
            <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">{t.execSummary}</h4>
            <p className="text-base text-textMain leading-relaxed italic border-l-4 border-primary/20 pl-6 py-1">
              {analysis.summary || t.noSummary}
            </p>
          </div>
        </div>

        <div className="bg-white rounded-3xl border border-border shadow-sm p-8 space-y-6">
          <h4 className="text-[10px] font-black text-textMain uppercase tracking-widest flex items-center">
            <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> {t.dimScoring}
          </h4>
          <div className="space-y-4">
            {scoreDims.map(([label, dim]) => renderScoreBar(label, dim))}
          </div>
          <div className="pt-6 border-t border-slate-100">
            <p className="text-[10px] text-textMuted italic leading-relaxed">{t.scoringNote}</p>
          </div>
        </div>
      </div>

      {/* HR Evaluation Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {renderSection(t.sections.matchedSkills, analysis.cv_skills_matched)}
        {renderSection(t.sections.expFit, analysis.cv_experience_summary)}
        {renderSection(t.sections.academicFit, analysis.cv_education_summary)}
        {renderSection(t.sections.certifications, analysis.cv_certifications_found)}
        {renderSection(t.sections.gaps, analysis.gaps_identified, <svg className="w-3 h-3 text-error" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" /></svg>)}
        {renderSection(t.sections.evalNotes, analysis.evaluation_notes)}
      </div>

      {/* Strengths & Risks */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {renderSection(t.sections.strengths, analysis.strengths)}
        {renderSection(t.sections.risks, analysis.risks)}
      </div>

      {/* Interview Support */}
      <div className="bg-indigo-900 rounded-3xl p-8 text-white shadow-xl shadow-indigo-200">
        <h3 className="text-sm font-black uppercase tracking-widest mb-8 flex items-center">
          <svg className="w-5 h-5 mr-3 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
          {t.interviewPrep}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
          <div className="space-y-6">
            <h4 className="text-[10px] font-black text-indigo-300 uppercase tracking-widest">{t.focusAreas}</h4>
            <ul className="space-y-3">
              {(analysis.interview_focus_points || []).map((point, i) => (
                <li key={i} className="flex items-start text-sm bg-indigo-800/50 p-3 rounded-xl border border-indigo-700/50">
                  <span className="text-indigo-400 mr-3 font-bold">#</span>
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

      {/* Raw Data */}
      <section className="bg-white rounded-3xl border border-border overflow-hidden">
        <button
          onClick={() => setShowRaw(!showRaw)}
          className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <h3 className="text-[10px] font-black text-textMuted uppercase tracking-widest flex items-center">
            <span className="w-2 h-4 bg-slate-400 rounded-full mr-3"></span> {t.rawPayload}
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
