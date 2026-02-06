
import React from 'react';

interface ApplicationDetailsProps {
  data: any;
  onBack: () => void;
}

export const ApplicationDetails: React.FC<ApplicationDetailsProps> = ({ data, onBack }) => {
  // Generic helper to render sections from the analysis payload
  const renderAnalysisSection = (title: string, content: any) => {
    if (!content) return null;
    
    return (
      <div className="bg-white p-6 rounded-2xl border border-border shadow-sm">
        <h4 className="text-xs font-black text-textMuted uppercase tracking-widest mb-4">{title}</h4>
        {typeof content === 'string' ? (
          <p className="text-sm text-textMain leading-relaxed">{content}</p>
        ) : Array.isArray(content) ? (
          <ul className="space-y-2">
            {content.map((item, i) => (
              <li key={i} className="text-sm text-textMain flex items-start">
                <span className="text-primary mr-2">•</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        ) : (
          <pre className="text-xs text-textMain bg-slate-50 p-3 rounded-lg overflow-x-auto">
            {JSON.stringify(content, null, 2)}
          </pre>
        )}
      </div>
    );
  };

  const candidateName = data.candidate_name || data.details?.candidate_name || 'Candidate Analysis';
  const score = data.score || data.details?.score || 0;

  return (
    <div className="space-y-6 animate-fade-in max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium">
          <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Back to Applications
        </button>
      </div>

      <div className="bg-white rounded-3xl border border-border overflow-hidden shadow-sm">
        <div className="p-8 bg-slate-50 border-b border-border flex items-center justify-between">
          <div className="flex items-center space-x-6">
            <div className={`w-20 h-20 rounded-full flex flex-col items-center justify-center font-bold text-white shadow-lg ${
              score >= 80 ? 'bg-success' : score >= 60 ? 'bg-warning' : 'bg-error'
            }`}>
              <span className="text-2xl">{score}</span>
              <span className="text-[10px] opacity-80 uppercase leading-none">Score</span>
            </div>
            <div>
              <h1 className="text-2xl font-black text-textMain tracking-tight">{candidateName}</h1>
              <p className="text-sm text-textMuted mt-1">Application Reference: {data.application_id || data.id || 'N/A'}</p>
            </div>
          </div>
        </div>

        <div className="p-8 grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Detailed breakdown based on typical n8n analysis response structure */}
          {renderAnalysisSection("Executive Summary", data.summary || data.analysis?.summary)}
          {renderAnalysisSection("Key Skills Matching", data.skills_match || data.analysis?.skills)}
          {renderAnalysisSection("Experience Assessment", data.experience_fit || data.analysis?.experience)}
          {renderAnalysisSection("Education & Certifications", data.education_check || data.analysis?.education)}
          {renderAnalysisSection("Strengths", data.strengths || data.analysis?.strengths)}
          {renderAnalysisSection("Potential Risks", data.risks || data.analysis?.risks)}
        </div>

        {/* Catch-all for extra data */}
        <div className="p-8 bg-slate-50 border-t border-border">
          <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">Raw AI Analysis Payload</h4>
          <div className="bg-white p-4 rounded-xl border border-border">
             <pre className="text-[10px] text-textMuted font-mono overflow-x-auto whitespace-pre-wrap leading-relaxed">
               {JSON.stringify(data.analysis || data, null, 2)}
             </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
