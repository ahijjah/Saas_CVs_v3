
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, AuthState, ApplicationFilter } from '../types';
import { ApplicationDetails } from './ApplicationDetails';

interface ApplicationsListProps {
  jobId: string; // This MUST be the job_id (UUID), not job_code
  initialFilter: ApplicationFilter;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const ApplicationsList: React.FC<ApplicationsListProps> = ({ 
  jobId, initialFilter, auth, onBack, addToast 
}) => {
  const [view, setView] = useState<'list' | 'details'>('list');
  const [applicationsAll, setApplicationsAll] = useState<Application[]>([]);
  const [selectedDetails, setSelectedDetails] = useState<any | null>(null);
  const [filter, setFilter] = useState<ApplicationFilter>(initialFilter);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const fetchApplications = async () => {
    console.log('[ApplicationsList] fetchApplications triggered. job_id (UUID):', jobId);
    console.log('[ApplicationsList] calling GET applications:', WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL);
    console.log('[ApplicationsList] params:', { job_id: jobId });
    
    setLoading(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL, 
        { job_id: jobId }, 
        auth.token!
      );

      console.log('[ApplicationsList] raw API response:', data);
      console.log('[ApplicationsList] response type:', typeof data, 'isArray:', Array.isArray(data));

      const arr = Array.isArray(data) ? data : [];
      console.log('[ApplicationsList] applicationsAll length:', arr.length);
      console.log('[ApplicationsList] first row:', arr[0]);
      console.log('[ApplicationsList] sample statuses:', arr.slice(0, 15).map(a => (a as any)?.status));

      setApplicationsAll(arr);
    } catch (err) {
      console.error("[ApplicationsList] Fetch error:", err);
      addToast("Failed to fetch applications. Showing mock data.", "error");
      const mockApplications: Application[] = [
        { id: 'APP1', application_id: 'uuid-1', candidate_name: 'John Smith', score: 92, status: 'qualified', applied_date: '2023-10-21', summary: 'Strong React experience, excellent culture fit.' },
        { id: 'APP2', application_id: 'uuid-2', candidate_name: 'Jane Doe', score: 85, status: 'qualified', applied_date: '2023-10-20', summary: 'Great portfolio, background in SaaS.' },
        { id: 'APP3', application_id: 'uuid-3', candidate_name: 'Mike Johnson', score: 65, status: 'partial', applied_date: '2023-10-19', summary: 'Missing direct TypeScript experience.' },
        { id: 'APP4', application_id: 'uuid-4', candidate_name: 'Sarah Wilson', score: 32, status: 'rejected', applied_date: '2023-10-18', summary: 'Incomplete application, low technical alignment.' },
      ];
      setApplicationsAll(mockApplications);
    } finally {
      setLoading(false);
    }
  };

  const handleViewAnalysis = async (app: Application) => {
    setDetailsLoading(true);
    try {
      const detailsRaw = await apiService.get(
        WEBHOOK_CONFIG.APPLICATION_DETAILS_WEBHOOK_URL,
        { application_id: app.application_id || app.id },
        auth.token!
      );

      console.log("[ApplicationsList] Raw backend details received:", detailsRaw);

      const detailsObj = Array.isArray(detailsRaw) ? detailsRaw[0] : detailsRaw;

      console.log("[ApplicationsList] detailsObj (unwrapped):", detailsObj);

      const normalized = {
        application_id: detailsObj?.application_id,
        candidate_name: detailsObj?.candidate_name,
        score: Number(detailsObj?.overall_score || detailsObj?.score || 0),
        status: detailsObj?.decision || detailsObj?.status || "",

        summary: detailsObj?.analysis?.summary,
        skills_match: detailsObj?.analysis?.cv_skills_matched,
        experience_fit: detailsObj?.analysis?.cv_experience_summary,
        education_check: detailsObj?.analysis?.cv_education_summary,
        strengths: detailsObj?.raw_ai_response?.evidence_skills,
        risks: detailsObj?.analysis?.gaps_identified,

        scores: detailsObj?.scores,
        analysis: detailsObj?.analysis,
        raw_ai_response: detailsObj?.raw_ai_response,

        // Root properties required by ApplicationDetails component
        overall_score: Number(detailsObj?.overall_score || detailsObj?.score || 0),
        decision: (detailsObj?.decision || detailsObj?.status || "").toLowerCase(),
      };

      console.log("[ApplicationsList] Normalized application details:", normalized);

      setSelectedDetails(normalized);
      setView("details");
    } catch (err) {
      console.error("[ApplicationsList] Details fetch error:", err);
      addToast("Failed to load application analysis.", "error");
      // Fallback for demo
      setSelectedDetails({
        ...app,
        overall_score: app.score,
        decision: app.status,
        analysis: {
          summary: "This candidate shows exceptional promise in frontend architecture...",
          skills: ["React Expert", "Modern CSS", "Architecture Design"],
          strengths: ["Clean code enthusiast", "Strong communicator"],
          risks: ["Hasn't worked in banking sector before"]
        }
      });
      setView('details');
    } finally {
      setDetailsLoading(false);
    }
  };

  useEffect(() => {
    fetchApplications();
  }, [jobId]);

  // Compute filtered applications with normalized status check
  const filteredApplications = filter === 'all' 
    ? applicationsAll 
    : applicationsAll.filter(a => (a.status ?? '').toLowerCase().trim() === filter);

  // REQUIRED RENDER LOGS
  console.log('[ApplicationsList] current filter:', filter);
  console.log('[ApplicationsList] counts:', { all: applicationsAll.length, filtered: filteredApplications.length });
  console.log('[ApplicationsList] sample statuses:', applicationsAll.slice(0, 10).map(a => a.status));

  if (view === 'details' && selectedDetails) {
    return (
      <ApplicationDetails 
        data={selectedDetails} 
        onBack={() => setView('list')} 
      />
    );
  }

  const handleFilterClick = (f: ApplicationFilter) => {
    console.log('[ApplicationsList] filter click:', f);
    setFilter(f);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex flex-col">
          <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium">
            <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Back to Campaigns
          </button>
        </div>

        <div className="flex bg-slate-100 p-1 rounded-lg">
          {(['all', 'qualified', 'partial', 'rejected'] as ApplicationFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => handleFilterClick(f)}
              className={`px-4 py-1.5 rounded-md text-xs font-bold uppercase transition-all ${
                filter === f ? 'bg-white text-primary shadow-sm' : 'text-textMuted hover:text-textMain'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded p-2 font-mono">
        Debug: items={applicationsAll.length} | filtered={filteredApplications.length} | filter={filter} | firstStatus={(applicationsAll[0] as any)?.status ?? 'none'} | jobId={jobId}
      </div>

      <div className="grid grid-cols-1 gap-4">
        {loading ? (
          <div className="p-12 text-center text-textMuted flex flex-col items-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4"></div>
            Loading applications...
          </div>
        ) : filteredApplications.length === 0 ? (
          <div className="bg-white p-12 rounded-xl border border-border text-center text-textMuted">
            No applications found matching this criteria.
          </div>
        ) : (
          filteredApplications.map((app) => (
            <div key={app.id || app.application_id} className="bg-white p-6 rounded-xl border border-border shadow-sm flex flex-col md:flex-row md:items-center justify-between hover:border-primary/30 transition-all">
              <div className="flex items-center space-x-6">
                <div className={`w-14 h-14 rounded-full flex flex-col items-center justify-center font-bold text-white shadow-sm ${
                  app.score >= 80 ? 'bg-success' : app.score >= 60 ? 'bg-warning' : 'bg-error'
                }`}>
                  <span className="text-lg">{app.score}</span>
                  <span className="text-[10px] opacity-80 uppercase leading-none">PTS</span>
                </div>
                <div>
                  <h4 className="text-lg font-bold text-textMain">{app.candidate_name}</h4>
                  <p className="text-xs text-textMuted">Applied on {app.applied_date} • {app.id || app.application_id}</p>
                  <p className="text-sm text-textMain mt-2 max-w-xl italic">"{app.summary}"</p>
                </div>
              </div>

              <div className="mt-4 md:mt-0 flex flex-col items-end space-y-2">
                 <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase ${
                  (app.status ?? '').toLowerCase().trim() === 'qualified' ? 'bg-green-100 text-green-800' :
                  (app.status ?? '').toLowerCase().trim() === 'partial' ? 'bg-amber-100 text-amber-800' :
                  'bg-red-100 text-red-800'
                }`}>
                  {app.status || 'NO_STATUS'}
                </span>
                <button 
                  disabled={detailsLoading}
                  onClick={() => handleViewAnalysis(app)}
                  className="text-primary hover:text-primaryDark text-sm font-semibold flex items-center disabled:opacity-50"
                >
                  {detailsLoading ? 'Loading analysis...' : 'View full analysis →'}
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
