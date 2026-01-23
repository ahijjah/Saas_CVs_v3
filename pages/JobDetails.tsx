
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

  useEffect(() => {
    const fetchDetails = async () => {
      setLoading(true);
      try {
        const data = await apiService.get(WEBHOOK_CONFIG.GET_JOB_DETAILS_WEBHOOK_URL, { job_code: jobCode }, auth.token!);
        setDetails(data);
      } catch (err) {
        addToast("Failed to fetch job details. Showing mock details.", "error");
        setDetails({
          job_code: jobCode,
          job_title: 'Senior Frontend Engineer',
          job_client: 'Tech Corp',
          job_status: 'Active',
          applications_total: 45,
          applications_qualified: 12,
          applications_partial: 20,
          applications_rejected: 13,
          description: "We are looking for a Senior Frontend Engineer to join our growing team. You will be responsible for building high-quality web applications using modern technologies.",
          requirements: ["5+ years React experience", "TypeScript mastery", "Tailwind CSS expertise", "Strong UI/UX sensibility"],
          location: "Remote (Global)",
          salary_range: "$120k - $160k"
        });
      } finally {
        setLoading(false);
      }
    };
    fetchDetails();
  }, [jobCode]);

  if (loading) return <div className="p-8 text-center text-textMuted">Loading details...</div>;
  if (!details) return <div className="p-8 text-center text-error">Job not found.</div>;

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium">
        <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
        </svg>
        Back to Dashboard
      </button>

      <div className="bg-white rounded-xl shadow-sm border border-border p-8">
        <div className="flex justify-between items-start border-b border-border pb-6 mb-6">
          <div>
            <span className="text-primary text-xs font-bold uppercase tracking-widest">{details.job_code}</span>
            <h2 className="text-2xl font-bold text-textMain mt-1">{details.job_title}</h2>
            <p className="text-textMuted">{details.job_client} • {details.location}</p>
          </div>
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-bold bg-green-100 text-green-800">
            {details.job_status}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
          <div className="space-y-6">
            <section>
              <h4 className="text-sm font-bold text-textMain uppercase mb-3">Job Description</h4>
              <p className="text-sm text-textMain leading-relaxed">{details.description}</p>
            </section>
            
            <section>
              <h4 className="text-sm font-bold text-textMain uppercase mb-3">Salary Range</h4>
              <p className="text-sm text-primary font-semibold">{details.salary_range}</p>
            </section>
          </div>

          <div className="space-y-6">
            <section>
              <h4 className="text-sm font-bold text-textMain uppercase mb-3">Key Requirements</h4>
              <ul className="space-y-2">
                {details.requirements.map((req, i) => (
                  <li key={i} className="flex items-start text-sm text-textMain">
                    <svg className="w-4 h-4 text-success mr-2 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" />
                    </svg>
                    {req}
                  </li>
                ))}
              </ul>
            </section>

            <section className="bg-slate-50 rounded-lg p-4 border border-border">
              <h4 className="text-sm font-bold text-textMain mb-4">Application Statistics</h4>
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-white p-3 rounded border border-border">
                  <p className="text-xs text-textMuted uppercase">Qualified</p>
                  <p className="text-lg font-bold text-success">{details.applications_qualified}</p>
                </div>
                <div className="bg-white p-3 rounded border border-border">
                  <p className="text-xs text-textMuted uppercase">Partial</p>
                  <p className="text-lg font-bold text-warning">{details.applications_partial}</p>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};
