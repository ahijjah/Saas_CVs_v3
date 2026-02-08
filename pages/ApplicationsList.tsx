
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, AuthState, ApplicationFilter } from '../types';
import { ApplicationDetails } from './ApplicationDetails';

interface ApplicationsListProps {
  jobId: string;
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
    setLoading(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL, 
        { job_id: jobId }, 
        auth.token!
      );
      const arr = Array.isArray(data) ? data : [];
      setApplicationsAll(arr);
    } catch (err) {
      console.error("[ApplicationsList] Fetch error:", err);
      addToast("Failed to fetch applications.", "error");
      setApplicationsAll([]);
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

      // Unwrap array if returned as [{...}]
      const detailsObj = Array.isArray(detailsRaw) ? detailsRaw[0] : detailsRaw;

      if (!detailsObj) {
        throw new Error("Application data not found");
      }

      // Normalize backend response for ApplicationDetails component
      const normalized = {
        application_id: detailsObj?.application_id,
        candidate_name: detailsObj?.candidate_name,
        overall_score: Number(detailsObj?.overall_score || detailsObj?.score || 0),
        decision: (detailsObj?.decision || detailsObj?.status || "").toLowerCase(),
        scores: detailsObj?.scores,
        analysis: detailsObj?.analysis || {},
        raw_ai_response: detailsObj?.raw_ai_response,
        // Map common fields to root for convenience
        summary: detailsObj?.analysis?.summary,
        strengths: detailsObj?.analysis?.strengths || detailsObj?.raw_ai_response?.evidence_skills,
        risks: detailsObj?.analysis?.gaps_identified || detailsObj?.analysis?.risks
      };

      setSelectedDetails(normalized);
      setView("details");
    } catch (err: any) {
      console.error("[ApplicationsList] Details fetch error:", err);
      addToast(err.message || "Failed to load application analysis.", "error");
    } finally {
      setDetailsLoading(false);
    }
  };

  useEffect(() => {
    fetchApplications();
  }, [jobId]);

  const filteredApplications = filter === 'all' 
    ? applicationsAll 
    : applicationsAll.filter(a => (a.status ?? '').toLowerCase().trim() === filter);

  if (view === 'details' && selectedDetails) {
    return (
      <ApplicationDetails 
        data={selectedDetails} 
        onBack={() => setView('list')} 
      />
    );
  }

  const handleFilterClick = (f: ApplicationFilter) => {
    setFilter(f);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium">
          <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Back to Campaigns
        </button>

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
                  <p className="text-xs text-textMuted">Applied on {app.applied_date}</p>
                  <p className="text-sm text-textMain mt-2 max-w-xl italic">"{app.summary}"</p>
                </div>
              </div>

              <div className="mt-4 md:mt-0 flex flex-col items-end space-y-2">
                 <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${
                  (app.status ?? '').toLowerCase().trim() === 'qualified' ? 'bg-green-100 text-green-800' :
                  (app.status ?? '').toLowerCase().trim() === 'partial' ? 'bg-amber-100 text-amber-800' :
                  'bg-red-100 text-red-800'
                }`}>
                  {app.status}
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
