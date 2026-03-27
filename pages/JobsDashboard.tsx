
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Job, AuthState } from '../types';

interface JobsDashboardProps {
  auth: AuthState;
  onViewDetails: (jobId: string) => void;
  onViewApplications: (jobId: string, filter: string) => void;
  onAddJob: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const JobsDashboard: React.FC<JobsDashboardProps> = ({ 
  auth, 
  onViewDetails, 
  onViewApplications, 
  onAddJob,
  addToast
}) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchJobs = async () => {
      setLoading(true);
      try {
        const data = await apiService.get(
          WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL, 
          {}, 
          auth.token!
        );
        // Ensure data is array
        setJobs(Array.isArray(data) ? data : []);
      } catch (err: any) {
        console.error(err);
        addToast("Failed to fetch jobs. Using placeholder data for demonstration.", "error");
        // Fallback for demo - added job_id as UUID to satisfy Job type and rules
        setJobs([
          { job_id: 'JOB-2026-00074', job_code: 'JB001', job_title: 'Senior Frontend Engineer', job_client: 'Tech Corp', job_status: 'Active', applications_total: 45, applications_qualified: 12, applications_partial: 20, applications_rejected: 13 },
          { job_id: 'JOB-2026-00075', job_code: 'JB002', job_title: 'Backend Developer', job_client: 'Data Systems', job_status: 'Active', applications_total: 30, applications_qualified: 5, applications_partial: 10, applications_rejected: 15 },
          { job_id: 'JOB-2026-00076', job_code: 'JB003', job_title: 'UX Designer', job_client: 'Creative Lab', job_status: 'Closed', applications_total: 15, applications_qualified: 8, applications_partial: 4, applications_rejected: 3 },
        ]);
      } finally {
        setLoading(false);
      }
    };

    fetchJobs();
  }, [auth.token, addToast]);

  // Logging wrappers to ensure job_id vs job_code is correctly tracked
  const handleViewApplicationsClick = (job: Job, filter: string) => {
    console.log(`[JobsDashboard] onViewApplications click. job_id (UUID): ${job.job_id}, job_code: ${job.job_code}, filter: ${filter}`);
    onViewApplications(job.job_id, filter);
  };

  const handleViewDetailsClick = (job: Job) => {
    console.log(`[JobsDashboard] onViewDetails click. job_id (UUID): ${job.job_id}, job_code: ${job.job_code}`);
    onViewDetails(job.job_id);
  };

  return (
    <div className="space-y-6 p-1 sm:p-0">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h3 className="text-lg font-medium text-textMain">Active Recruitment Campaigns</h3>
          <p className="text-sm text-textMuted">Overview of your open roles and candidate pipeline</p>
        </div>
        <button
          onClick={onAddJob}
          className="w-full sm:w-auto bg-primary hover:bg-primaryDark text-white px-5 py-2.5 rounded-xl font-bold flex items-center justify-center transition-all shadow-lg shadow-primary/20"
        >
          <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          Add New Job
        </button>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 space-y-4">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary"></div>
            <p className="text-textMuted animate-pulse font-medium uppercase tracking-widest text-xs">Loading jobs...</p>
          </div>
        ) : (
          <>
            {/* Mobile View: Cards */}
            <div className="block sm:hidden divide-y divide-border">
              {jobs.length === 0 ? (
                <div className="p-8 text-center text-textMuted">
                  No jobs found. Create your first campaign.
                </div>
              ) : (
                jobs.map((job) => (
                  <div key={job.job_id} className="p-4 space-y-4">
                    <div className="flex justify-between items-start">
                      <div className="min-w-0 flex-1 pr-2">
                        <button 
                          onClick={() => handleViewDetailsClick(job)}
                          className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1 hover:text-primary hover:underline transition-colors text-left block"
                        >
                          {job.job_code}
                        </button>
                        <button 
                          onClick={() => handleViewDetailsClick(job)}
                          className="text-sm font-bold text-textMain truncate hover:text-primary hover:underline transition-colors text-left block w-full"
                        >
                          {job.job_title}
                        </button>
                        <div className="text-xs text-textMuted truncate">{job.job_client}</div>
                      </div>
                      <span className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-tighter ${
                        job.job_status === 'Active' ? 'bg-green-100 text-green-800' :
                        job.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
                        'bg-amber-100 text-amber-800'
                      }`}>
                        {job.job_status}
                      </span>
                    </div>
                    
                    <div className="grid grid-cols-4 gap-2 text-center bg-slate-50 p-3 rounded-xl border border-slate-100">
                      <div>
                        <div className="text-[9px] font-black text-textMuted uppercase tracking-tighter mb-1">Total</div>
                        <button 
                          onClick={() => handleViewApplicationsClick(job, 'all')} 
                          className="text-sm font-bold text-primary underline decoration-primary/20"
                        >
                          {job.applications_total}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-success uppercase tracking-tighter mb-1">Qual.</div>
                        <button 
                          onClick={() => handleViewApplicationsClick(job, 'qualified')} 
                          className="text-sm font-bold text-success underline decoration-success/20"
                        >
                          {job.applications_qualified}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-warning uppercase tracking-tighter mb-1">Part.</div>
                        <button 
                          onClick={() => handleViewApplicationsClick(job, 'partial')} 
                          className="text-sm font-bold text-warning underline decoration-warning/20"
                        >
                          {job.applications_partial}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-error uppercase tracking-tighter mb-1">Rej.</div>
                        <button 
                          onClick={() => handleViewApplicationsClick(job, 'rejected')} 
                          className="text-sm font-bold text-error underline decoration-error/20"
                        >
                          {job.applications_rejected}
                        </button>
                      </div>
                    </div>

                    <button 
                      onClick={() => handleViewDetailsClick(job)}
                      className="w-full py-2.5 bg-white border border-border rounded-xl text-xs font-bold text-textMain hover:bg-slate-50 transition-colors shadow-sm"
                    >
                      View Campaign Details
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* Desktop View: Table */}
            <div className="hidden sm:block overflow-x-auto w-full">
              <table className="w-full text-left min-w-[900px]">
                <thead className="bg-slate-50 border-b border-border sticky top-0">
                  <tr>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">Job Code</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">Title / Client</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">Status</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider text-center whitespace-nowrap">Total</th>
                    <th className="px-6 py-4 text-xs font-semibold text-success uppercase tracking-wider text-center whitespace-nowrap">Qualified</th>
                    <th className="px-6 py-4 text-xs font-semibold text-warning uppercase tracking-wider text-center whitespace-nowrap">Partial</th>
                    <th className="px-6 py-4 text-xs font-semibold text-error uppercase tracking-wider text-center whitespace-nowrap">Rejected</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider text-right whitespace-nowrap">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {jobs.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-6 py-12 text-center text-textMuted">
                        No jobs found. Create your first job campaign to get started.
                      </td>
                    </tr>
                  ) : (
                    jobs.map((job) => (
                      <tr key={job.job_id} className="hover:bg-slate-50 transition-colors group">
                        <td className="px-6 py-4 text-sm font-medium whitespace-nowrap">
                          <button 
                            onClick={() => handleViewDetailsClick(job)}
                            className="text-primary hover:underline transition-colors font-medium"
                          >
                            {job.job_code}
                          </button>
                        </td>
                        <td className="px-6 py-4">
                          <button 
                            onClick={() => handleViewDetailsClick(job)}
                            className="text-sm font-semibold text-primary hover:underline transition-colors text-left block"
                          >
                            {job.job_title}
                          </button>
                          <div className="text-xs text-textMuted whitespace-nowrap">{job.job_client}</div>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            job.job_status === 'Active' ? 'bg-green-100 text-green-800' :
                            job.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
                            'bg-amber-100 text-amber-800'
                          }`}>
                            {job.job_status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-center">
                          <button 
                            onClick={() => handleViewApplicationsClick(job, 'all')}
                            className="text-sm font-semibold text-primary hover:underline"
                          >
                            {job.applications_total}
                          </button>
                        </td>
                        <td className="px-6 py-4 text-center">
                          <button 
                            onClick={() => handleViewApplicationsClick(job, 'qualified')}
                            className="text-sm font-semibold text-success hover:underline"
                          >
                            {job.applications_qualified}
                          </button>
                        </td>
                        <td className="px-6 py-4 text-center">
                          <button 
                            onClick={() => handleViewApplicationsClick(job, 'partial')}
                            className="text-sm font-semibold text-warning hover:underline"
                          >
                            {job.applications_partial}
                          </button>
                        </td>
                        <td className="px-6 py-4 text-center">
                          <button 
                            onClick={() => handleViewApplicationsClick(job, 'rejected')}
                            className="text-sm font-semibold text-error hover:underline"
                          >
                            {job.applications_rejected}
                          </button>
                        </td>
                        <td className="px-6 py-4 text-right whitespace-nowrap">
                          <button 
                            onClick={() => handleViewDetailsClick(job)}
                            className="text-primary hover:text-primaryDark text-sm font-medium"
                          >
                            View Details
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
