
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { JobDetails as JobDetailsType, AuthState } from '../types';

interface JobDetailsProps {
  jobId: string;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const JobDetails: React.FC<JobDetailsProps> = ({ jobId, auth, onBack, addToast }) => {
  const [details, setDetails] = useState<JobDetailsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [descExpanded, setDescExpanded] = useState(false);

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
          setDetails(data);
        } else {
          throw new Error("No data received for this job ID.");
        }
      } catch (err: any) {
        console.error("Fetch job details failed:", err);
        const errorMsg = err.name === 'TypeError' && err.message === 'Failed to fetch' 
          ? "Network connection error." : (err.message || "Failed to load job details.");
        
        setError(errorMsg);
        addToast(errorMsg, "error");
        
        // Mock Fallback matching the new schema
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
          salary_range: '£85k - £110k',
          applications_total: 142,
          applications_qualified: 24,
          applications_partial: 45,
          applications_rejected: 73,
          applications_evaluated: 120,
          applications_pending: 22,
          applications_above_threshold: 18,
          applications_below_threshold: 102,
          applications_recommended: 12,
          description: "We are seeking a highly skilled Senior Frontend Engineer to lead the development of our core product interface. You will work closely with design and product teams to create a seamless, high-performance experience for our enterprise users. The ideal candidate has deep expertise in React and a passion for crafting intuitive user interfaces. You will be responsible for defining frontend architecture, mentoring junior developers, and ensuring high code quality through rigorous peer review and testing processes.",
          analysis_json: {
            skills: {
              required: ["React", "TypeScript", "Tailwind CSS", "State Management (Redux/Zustand)"],
              preferred: ["Next.js", "GraphQL", "Jest/Cypress", "Web Accessibility (WCAG)"]
            },
            experience: {
              minimum_years: 5,
              relevant_roles: ["Senior Frontend Engineer", "Lead Developer", "React Specialist"],
              key_responsibilities: [
                "Architect scalable frontend components",
                "Optimizing application performance",
                "Mentoring junior engineering staff"
              ]
            },
            education: {
              minimum_level: "Bachelor's Degree",
              fields_of_study: ["Computer Science", "Software Engineering", "Information Technology"]
            },
            certifications: ["AWS Certified Developer", "Meta Frontend Professional"],
            domain_knowledge: ["FinTech", "Enterprise SaaS", "Data Visualization"],
            other_requirements: ["Strong communication skills", "Agile/Scrum experience"],
            scoring_weights: {
              skills: 40,
              experience: 30,
              education: 10,
              certifications: 5,
              soft_skills: 10,
              domain_knowledge: 5
            }
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
        <p className="text-textMuted font-medium animate-pulse tracking-wide">Syncing campaign data...</p>
      </div>
    );
  }

  if (error && !details) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] p-8 text-center">
        <div className="bg-red-50 text-error p-6 rounded-2xl mb-6 max-w-md">
          <p className="font-bold mb-2">Sync Error</p>
          <p className="text-sm opacity-80">{error}</p>
        </div>
        <button onClick={onBack} className="bg-primary text-white px-8 py-3 rounded-xl font-bold shadow-lg">Return to Dashboard</button>
      </div>
    );
  }

  if (!details) return null;

  // Fixed: Removed '|| {}' to ensure 'analysis' retains 'AnalysisJson' type and doesn't conflict with empty object type
  const analysis = details.analysis_json;

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12 animate-fade-in">
      {/* 1️⃣ Job Metadata (Summary Card) */}
      <section className="bg-white rounded-3xl shadow-sm border border-border overflow-hidden">
        <div className="px-8 py-6 bg-slate-50 border-b border-border flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-center space-x-4">
            <button onClick={onBack} className="p-2 hover:bg-white rounded-lg transition-colors text-textMuted hover:text-primary">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" /></svg>
            </button>
            <div>
              <div className="flex items-center space-x-2 text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">
                <span>{details.job_id}</span>
                <span className="w-1 h-1 rounded-full bg-border"></span>
                <span className={details.job_status === 'Active' ? 'text-success' : 'text-warning'}>{details.job_status}</span>
              </div>
              <h1 className="text-2xl font-black text-textMain tracking-tight">{details.job_title}</h1>
            </div>
          </div>
          <div className="flex items-center space-x-3">
            <button className="bg-primary text-white px-6 py-2.5 rounded-xl text-sm font-bold shadow-lg shadow-primary/20">Review Portal</button>
          </div>
        </div>
        <div className="p-8 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-6">
          {[
            { label: 'Client', value: details.job_client },
            { label: 'Type', value: details.job_type || 'Full-time' },
            { label: 'Location', value: details.location || 'Remote' },
            { label: 'Salary', value: details.salary_range || 'N/A' },
            { label: 'Posted', value: details.posted_date || '-' },
            { label: 'Closing', value: details.closing_date || '-' },
          ].map((item, idx) => (
            <div key={idx}>
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{item.label}</p>
              <p className="text-sm font-bold text-textMain truncate">{item.value}</p>
            </div>
          ))}
        </div>
      </section>

      {/* 4️⃣ Applications Overview (KPIs) */}
      <section className="grid grid-cols-2 lg:grid-cols-6 gap-4">
        {[
          { label: 'Total', value: details.applications_total, color: 'text-textMain' },
          { label: 'Evaluated', value: details.applications_evaluated || 0, color: 'text-primary' },
          { label: 'Pending', value: details.applications_pending || 0, color: 'text-warning' },
          { label: 'Qualifying', value: details.applications_above_threshold || 0, color: 'text-success' },
          { label: 'Failing', value: details.applications_below_threshold || 0, color: 'text-error' },
          { label: 'Recommended', value: details.applications_recommended || 0, color: 'text-indigo-600' },
        ].map((kpi, idx) => (
          <div key={idx} className="bg-white p-5 rounded-2xl border border-border shadow-sm flex flex-col items-center justify-center text-center">
            <span className={`text-2xl font-black ${kpi.color}`}>{kpi.value}</span>
            <span className="text-[10px] font-black text-textMuted uppercase tracking-widest mt-1">{kpi.label}</span>
          </div>
        ))}
      </section>

      {/* Main Grid: Analysis & Scoring */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* 2️⃣ Job Description Analysis */}
        <div className="lg:col-span-2 space-y-6">
          
          {/* Skills Section */}
          <div className="bg-white rounded-3xl border border-border p-8 shadow-sm">
            <h3 className="text-sm font-black text-textMain uppercase tracking-widest mb-6 flex items-center">
              <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> Skills Analysis
            </h3>
            <div className="space-y-6">
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">Required Skills</p>
                <div className="flex flex-wrap gap-2">
                  {(analysis?.skills?.required || []).map((s, i) => (
                    <span key={i} className="px-4 py-1.5 bg-slate-100 rounded-lg text-xs font-bold text-textMain">{s}</span>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">Preferred Skills</p>
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
                <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> Experience
              </h3>
              <div className="space-y-4">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">Minimum Years</p>
                  <p className="text-lg font-black text-primary">{analysis?.experience?.minimum_years || 0}+ Years</p>
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">Relevant Roles</p>
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
                <span className="w-2 h-4 bg-primary rounded-full mr-3"></span> Education
              </h3>
              <div className="space-y-4">
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">Minimum Level</p>
                  <p className="text-sm font-black text-textMain">{analysis?.education?.minimum_level || 'N/A'}</p>
                </div>
                <div>
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-2">Fields of Study</p>
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
            {[
              { label: 'Certifications', items: analysis?.certifications },
              { label: 'Domain Knowledge', items: analysis?.domain_knowledge },
              { label: 'Other Requirements', items: analysis?.other_requirements }
            ].map((cat, idx) => (
              <div key={idx} className="bg-white rounded-3xl border border-border p-6 shadow-sm">
                <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-4">{cat.label}</h4>
                <ul className="space-y-2">
                  {(cat.items || []).length > 0 ? (cat.items || []).map((item, i) => (
                    <li key={i} className="text-[11px] font-bold text-textMain leading-snug">• {item}</li>
                  )) : <li className="text-[10px] text-textMuted italic">No specific data.</li>}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {/* 3️⃣ Scoring & Evaluation Logic */}
        <div className="space-y-6">
          <div className="bg-white rounded-3xl border border-border p-8 shadow-sm sticky top-8">
            <h3 className="text-sm font-black text-textMain uppercase tracking-widest mb-8 flex items-center">
              <span className="w-2 h-4 bg-indigo-600 rounded-full mr-3"></span> Evaluation Logic
            </h3>
            <div className="space-y-6">
              {[
                { label: 'Skills', weight: analysis?.scoring_weights?.skills },
                { label: 'Experience', weight: analysis?.scoring_weights?.experience },
                { label: 'Education', weight: analysis?.scoring_weights?.education },
                { label: 'Certifications', weight: analysis?.scoring_weights?.certifications },
                { label: 'Soft Skills', weight: analysis?.scoring_weights?.soft_skills },
                { label: 'Domain Knowledge', weight: analysis?.scoring_weights?.domain_knowledge },
                { label: 'Other', weight: analysis?.scoring_weights?.other_requirements },
              ].map((item, i) => item.weight ? (
                <div key={i}>
                  <div className="flex justify-between text-[10px] font-black uppercase mb-2">
                    <span className="text-textMuted">{item.label}</span>
                    <span className="text-textMain">{item.weight}%</span>
                  </div>
                  <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                    <div className="bg-indigo-600 h-full rounded-full" style={{ width: `${item.weight}%` }}></div>
                  </div>
                </div>
              ) : null)}
            </div>
            <div className="mt-8 pt-8 border-t border-border">
              <p className="text-[10px] text-textMuted italic leading-relaxed">
                Weights are read-only and were defined during the job creation workflow. They represent the AI's priority ranking during candidate evaluation.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 5️⃣ Job Description (Collapsed by default) */}
      <section className="bg-white rounded-3xl border border-border overflow-hidden">
        <button 
          onClick={() => setDescExpanded(!descExpanded)}
          className="w-full px-8 py-5 flex items-center justify-between hover:bg-slate-50 transition-colors"
        >
          <h3 className="text-sm font-black text-textMain uppercase tracking-widest flex items-center">
            <span className="w-2 h-4 bg-slate-400 rounded-full mr-3"></span> Original Job Description
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