
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, AuthState, ApplicationFilter } from '../types';

interface ApplicationsListProps {
  jobCode: string;
  initialFilter: ApplicationFilter;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const ApplicationsList: React.FC<ApplicationsListProps> = ({ 
  jobCode, initialFilter, auth, onBack, addToast 
}) => {
  const [applications, setApplications] = useState<Application[]>([]);
  const [filter, setFilter] = useState<ApplicationFilter>(initialFilter);
  const [loading, setLoading] = useState(true);

  const fetchApplications = async (activeFilter: ApplicationFilter) => {
    setLoading(true);
    try {
      const data = await apiService.get(
        WEBHOOK_CONFIG.GET_APPLICATIONS_WEBHOOK_URL, 
        { job_code: jobCode, filter: activeFilter }, 
        auth.token!
      );
      setApplications(Array.isArray(data) ? data : []);
    } catch (err) {
      addToast("Failed to fetch applications. Showing mock data.", "error");
      // Fix: Explicitly type mock data array as Application[] to satisfy status literal type requirements
      const mockApplications: Application[] = [
        { id: 'APP1', candidate_name: 'John Smith', score: 92, status: 'qualified', applied_date: '2023-10-21', summary: 'Strong React experience, excellent culture fit.' },
        { id: 'APP2', candidate_name: 'Jane Doe', score: 85, status: 'qualified', applied_date: '2023-10-20', summary: 'Great portfolio, background in SaaS.' },
        { id: 'APP3', candidate_name: 'Mike Johnson', score: 65, status: 'partial', applied_date: '2023-10-19', summary: 'Missing direct TypeScript experience.' },
        { id: 'APP4', candidate_name: 'Sarah Wilson', score: 32, status: 'rejected', applied_date: '2023-10-18', summary: 'Incomplete application, low technical alignment.' },
      ];
      setApplications(mockApplications.filter(a => activeFilter === 'all' || a.status === activeFilter));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchApplications(filter);
  }, [jobCode, filter]);

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
              onClick={() => setFilter(f)}
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
          <div className="p-12 text-center text-textMuted">Loading applications...</div>
        ) : applications.length === 0 ? (
          <div className="bg-white p-12 rounded-xl border border-border text-center text-textMuted">
            No applications found matching this criteria.
          </div>
        ) : (
          applications.map((app) => (
            <div key={app.id} className="bg-white p-6 rounded-xl border border-border shadow-sm flex flex-col md:flex-row md:items-center justify-between hover:border-primary/30 transition-all">
              <div className="flex items-center space-x-6">
                <div className={`w-14 h-14 rounded-full flex flex-col items-center justify-center font-bold text-white shadow-sm ${
                  app.score >= 80 ? 'bg-success' : app.score >= 60 ? 'bg-warning' : 'bg-error'
                }`}>
                  <span className="text-lg">{app.score}</span>
                  <span className="text-[10px] opacity-80 uppercase leading-none">PTS</span>
                </div>
                <div>
                  <h4 className="text-lg font-bold text-textMain">{app.candidate_name}</h4>
                  <p className="text-xs text-textMuted">Applied on {app.applied_date} • {app.id}</p>
                  <p className="text-sm text-textMain mt-2 max-w-xl italic">"{app.summary}"</p>
                </div>
              </div>

              <div className="mt-4 md:mt-0 flex flex-col items-end space-y-2">
                 <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase ${
                  app.status === 'qualified' ? 'bg-green-100 text-green-800' :
                  app.status === 'partial' ? 'bg-amber-100 text-amber-800' :
                  'bg-red-100 text-red-800'
                }`}>
                  {app.status}
                </span>
                <button className="text-primary hover:text-primaryDark text-sm font-semibold">
                  View full analysis →
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
