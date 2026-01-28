
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { JobDetails as JobDetailsType, AuthState } from '../types';

interface JobDetailsProps {
  jobCode: string;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const JobDetails: React.FC<JobDetailsProps> = ({ jobCode, auth, onBack, addToast }) => {
  const [details, setDetails] = useState<JobDetailsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchDetails = async () => {
      setLoading(true);
      setError(null);
      try {
        // As per instructions: send job_id as query parameter
        const data = await apiService.get(
          WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL, 
          { job_id: jobCode }, 
          auth.token!
        );
        
        if (data) {
          setDetails(data);
        } else {
          throw new Error("No data received for this job.");
        }
      } catch (err: any) {
        console.error("Fetch job details failed:", err);
        setError(err.message || "Failed to load job details.");
        addToast("Error fetching job data. Showing reference mockups.", "error");
        
        // Fallback for demonstration/error state handling
        setDetails({
          job_code: jobCode,
          job_title: 'Senior Frontend Engineer',
          job_client: 'Tech Corp',
          job_status: 'Active',
          applications_total: 45,
          applications_qualified: 12,
          applications_partial: 20,
          applications_rejected: 13,
          description: "We are seeking a highly skilled Senior Frontend Engineer to lead the development of our core product interface. You will work closely with design and product teams to create a seamless, high-performance experience for our enterprise users. The ideal candidate has deep expertise in React and a passion for crafting intuitive user interfaces.",
          requirements: [
            "Expertise in modern React and TypeScript (5+ years)",
            "Deep understanding of browser performance and optimization",
            "Experience with complex state management patterns",
            "Strong advocate for accessibility and clean code standards",
            "Proven track record of building scalable SaaS applications"
          ],
          location: "London, UK (Hybrid)",
          salary_range: "£85,000 - £110,000",
          skills_required: ["React", "TypeScript", "Tailwind CSS", "GraphQL", "Next.js", "Jest"],
          experience_min_years: 5,
          education_requirement: "Bachelor's in Computer Science or related field",
          scoring_weights: {
            technical: 40,
            experience: 30,
            education: 15,
            culture: 15
          }
        });
      } finally {
        setLoading(false);
      }
    };
    fetchDetails();
  }, [jobCode, auth.token]);

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
      <div className="flex flex-col items-center justify-center min-h-[400px] p-8">
        <div className="bg-red-50 border border-red-100 rounded-3xl p-10 text-center max-w-lg shadow-sm">
          <div className="bg-red-100 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-6">
            <svg className="w-8 h-8 text-error" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-2xl font-black text-textMain mb-2">Job Retrieval Failed</h2>
          <p className="text-textMuted mb-8 leading-relaxed">We encountered an issue fetching the details for job campaign <span className="font-bold text-textMain">{jobCode}</span>. This might be due to a network error or the campaign no longer exists.</p>
          <button 
            onClick={onBack}
            className="bg-primary hover:bg-primaryDark text-white px-8 py-3 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all active:scale-95"
          >
            Return to Dashboard
          </button>
        </div>
      </div>
    );
  }

  if (!details) return null;

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12 animate-fade-in">
      {/* Navigation & Actions Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center space-x-4">
          <button 
            onClick={onBack}
            className="p-2 hover:bg-white hover:shadow-sm rounded-lg transition-all text-textMuted hover:text-primary group"
          >
            <svg className="w-6 h-6 transform group-hover:-translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div>
            <div className="flex items-center space-x-2 mb-1">
              <span className="text-[10px] font-black text-textMuted uppercase tracking-[0.2em]">{details.job_code}</span>
              <span className="w-1 h-1 rounded-full bg-border"></span>
              <span className={`text-[10px] font-black uppercase tracking-widest ${details.job_status === 'Active' ? 'text-success' : 'text-warning'}`}>
                {details.job_status}
              </span>
            </div>
            <h1 className="text-3xl font-black text-textMain tracking-tight leading-none">{details.job_title}</h1>
          </div>
        </div>
        <div className="flex items-center space-x-3">
          <button className="px-5 py-2.5 rounded-xl border border-border bg-white text-sm font-bold text-textMain hover:bg-slate-50 transition-colors shadow-sm">
            Edit Details
          </button>
          <button className="px-5 py-2.5 rounded-xl bg-primary text-white text-sm font-bold hover:bg-primaryDark transition-all shadow-lg shadow-primary/20">
            Share Portal
          </button>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
        
        {/* Left Column: JOB DESCRIPTION and KEY REQUIREMENTS */}
        <div className="lg:col-span-2 space-y-8">
          
          {/* Job Description */}
          <section className="bg-white rounded-3xl shadow-sm border border-border p-10">
            <h3 className="text-[11px] font-black text-textMuted uppercase tracking-[0.3em] mb-8 flex items-center">
              <span className="w-8 h-1 bg-primary rounded-full mr-3"></span>
              Job Description
            </h3>
            <div className="prose prose-slate max-w-none">
              <p className="text-textMain text-lg leading-relaxed font-medium opacity-90">
                {details.description}
              </p>
            </div>
          </section>

          {/* Key Requirements Checklist */}
          <section className="bg-white rounded-3xl shadow-sm border border-border p-10">
            <h3 className="text-[11px] font-black text-textMuted uppercase tracking-[0.3em] mb-8 flex items-center">
              <span className="w-8 h-1 bg-primary rounded-full mr-3"></span>
              Key Requirements
            </h3>
            <div className="grid grid-cols-1 gap-4">
              {details.requirements.map((req, index) => (
                <div key={index} className="flex items-start group p-4 rounded-2xl hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100">
                  <div className="bg-success/10 rounded-xl p-2 mr-4 group-hover:bg-success/20 transition-colors">
                    <svg className="w-5 h-5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
                    </svg>
                  </div>
                  <span className="text-textMain font-bold leading-relaxed">{req}</span>
                </div>
              ))}
            </div>
          </section>

          {/* Technical Skills Alignment */}
          <section className="bg-white rounded-3xl shadow-sm border border-border p-10">
            <h3 className="text-[11px] font-black text-textMuted uppercase tracking-[0.3em] mb-8 flex items-center">
              <span className="w-8 h-1 bg-primary rounded-full mr-3"></span>
              Core Skills Required
            </h3>
            <div className="flex flex-wrap gap-3">
              {details.skills_required?.map((skill, index) => (
                <div key={index} className="px-6 py-2.5 bg-slate-50 border border-border rounded-2xl text-sm font-black text-textMain hover:border-primary transition-all cursor-default hover:shadow-sm">
                  {skill}
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* Right Column: Metadata and Application Statistics */}
        <div className="space-y-8">
          
          {/* Metadata Card: Experience & Education */}
          <div className="bg-white rounded-3xl shadow-sm border border-border overflow-hidden">
            <div className="p-8 space-y-8">
              <h3 className="text-[11px] font-black text-textMuted uppercase tracking-[0.2em] mb-2">Campaign Metadata</h3>
              
              <div className="space-y-6">
                <div className="flex items-start space-x-4">
                  <div className="bg-primary/10 p-3 rounded-2xl">
                    <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">Min. Experience</p>
                    <p className="text-lg font-black text-textMain">{details.experience_min_years}+ Years</p>
                  </div>
                </div>

                <div className="flex items-start space-x-4">
                  <div className="bg-primary/10 p-3 rounded-2xl">
                    <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 14l9-5-9-5-9 5 9 5z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 14l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">Education Requirement</p>
                    <p className="text-sm font-black text-textMain leading-snug">{details.education_requirement}</p>
                  </div>
                </div>

                <div className="flex items-start space-x-4">
                  <div className="bg-primary/10 p-3 rounded-2xl">
                    <svg className="w-6 h-6 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                    </svg>
                  </div>
                  <div>
                    <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">Location</p>
                    <p className="text-lg font-black text-textMain">{details.location}</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="bg-slate-50 border-t border-border p-6 flex items-center justify-between">
              <span className="text-xs font-bold text-textMuted">Budget Reference</span>
              <span className="text-sm font-black text-primary">{details.salary_range}</span>
            </div>
          </div>

          {/* Application Statistics Card */}
          <div className="bg-white rounded-3xl shadow-sm border border-border p-8">
            <h3 className="text-[11px] font-black text-textMuted uppercase tracking-[0.2em] mb-8">Application Pipeline</h3>
            <div className="grid grid-cols-1 gap-4">
              <div className="bg-green-50 border border-green-100 rounded-2xl p-6 flex justify-between items-center group cursor-pointer hover:bg-green-100 transition-colors">
                <div>
                  <p className="text-xs font-black text-success uppercase tracking-widest">Qualified</p>
                  <p className="text-2xl font-black text-success mt-1">{details.applications_qualified}</p>
                </div>
                <div className="bg-success text-white p-2 rounded-xl shadow-lg shadow-success/20 group-hover:scale-110 transition-transform">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              </div>

              <div className="bg-amber-50 border border-amber-100 rounded-2xl p-6 flex justify-between items-center group cursor-pointer hover:bg-amber-100 transition-colors">
                <div>
                  <p className="text-xs font-black text-warning uppercase tracking-widest">Partial Fit</p>
                  <p className="text-2xl font-black text-warning mt-1">{details.applications_partial}</p>
                </div>
                <div className="bg-warning text-white p-2 rounded-xl shadow-lg shadow-warning/20 group-hover:scale-110 transition-transform">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M12 9v2m0 4h.01" />
                  </svg>
                </div>
              </div>
            </div>
            
            <div className="mt-8 pt-8 border-t border-border">
              <h4 className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-6">AI Scoring Weights</h4>
              <div className="space-y-4">
                {details.scoring_weights && Object.entries(details.scoring_weights).map(([key, value]) => (
                  <div key={key}>
                    <div className="flex justify-between text-[11px] font-black uppercase mb-1.5">
                      <span className="text-textMuted">{key}</span>
                      <span className="text-textMain">{value}%</span>
                    </div>
                    <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                      <div className="bg-primary h-full rounded-full" style={{ width: `${value}%` }}></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Quick Action */}
          <button className="w-full bg-slate-900 text-white py-5 rounded-3xl font-black text-lg hover:bg-black transition-all shadow-xl shadow-slate-900/20 active:scale-[0.98]">
            Review All Candidates
          </button>
        </div>

      </div>
    </div>
  );
};
