
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Application, AuthState, ApplicationFilter } from '../types';
import { ApplicationDetails } from './ApplicationDetails';
import { useLanguage } from '../context/LanguageContext';

interface ApplicationsListProps {
  jobId: string;
  initialFilter: ApplicationFilter;
  initialApplicationId?: string | null;
  auth: AuthState;
  onBack: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

const T = {
  en: {
    backToCampaigns: 'Back to Campaigns',
    filterAll: 'All',
    filterQualified: 'Qualified',
    filterPartial: 'Partial',
    filterRejected: 'Rejected',
    filterLowMatch: 'Low Match',
    filterPossibleDuplicate: 'Possible Duplicates',
    loading: 'Loading applications...',
    noApps: 'No applications found matching this criteria.',
    appliedOn: 'Applied on',
    viewAnalysis: 'View full analysis →',
    loadingAnalysis: 'Loading analysis...',
    pts: 'PTS',
    possibleDuplicate: 'Possible Duplicate',
  },
  ar: {
    backToCampaigns: 'العودة إلى الحملات',
    filterAll: 'الكل',
    filterQualified: 'مؤهلون',
    filterPartial: 'جزئيون',
    filterRejected: 'مرفوضون',
    filterLowMatch: 'تطابق منخفض',
    filterPossibleDuplicate: 'مكررات محتملة',
    loading: 'جارٍ تحميل الطلبات...',
    noApps: 'لا توجد طلبات مطابقة لهذه المعايير.',
    appliedOn: 'تاريخ التقديم',
    viewAnalysis: '← عرض التحليل الكامل',
    loadingAnalysis: 'جارٍ تحميل التحليل...',
    pts: 'نقطة',
    possibleDuplicate: 'مكرر محتمل',
  },
};

// low_match is an internal status; it maps to 'rejected' for display purposes
const FILTER_KEYS: ApplicationFilter[] = ['all', 'qualified', 'partial', 'rejected', 'possible_duplicate'];

export const ApplicationsList: React.FC<ApplicationsListProps> = ({
  jobId, initialFilter, initialApplicationId, auth, onBack, addToast
}) => {
  const { lang } = useLanguage();
  const t = T[lang];

  const [view, setView] = useState<'list' | 'details'>('list');
  const [applicationsAll, setApplicationsAll] = useState<Application[]>([]);
  const [selectedDetails, setSelectedDetails] = useState<any | null>(null);
  const [filter, setFilter] = useState<ApplicationFilter>(initialFilter);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);

  const filterLabels: Record<ApplicationFilter, string> = {
    all: t.filterAll,
    qualified: t.filterQualified,
    partial: t.filterPartial,
    rejected: t.filterRejected,
    low_match: t.filterLowMatch,
    possible_duplicate: t.filterPossibleDuplicate,
  };

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

      const detailsObj = Array.isArray(detailsRaw) ? detailsRaw[0] : detailsRaw;

      if (!detailsObj) {
        throw new Error("Application data not found");
      }

      const normalized = {
        application_id:        detailsObj?.application_id,
        candidate_name:        detailsObj?.candidate_name,
        overall_score:         Number(detailsObj?.overall_score || detailsObj?.score || 0),
        decision:              (detailsObj?.decision || detailsObj?.status || "").toLowerCase(),
        scores:                detailsObj?.scores,
        analysis:              detailsObj?.analysis || {},
        raw_ai_response:       detailsObj?.raw_ai_response,
        summary:               detailsObj?.analysis?.summary,
        strengths:             detailsObj?.analysis?.strengths || detailsObj?.raw_ai_response?.evidence_skills,
        risks:                 detailsObj?.analysis?.gaps_identified || detailsObj?.analysis?.risks,
        // Intelligence pipeline fields
        cv_language:           detailsObj?.cv_language,
        local_similarity_score: detailsObj?.local_similarity_score,
        skill_match_ratio:     detailsObj?.skill_match_ratio,
        gatekeeper_passed:     detailsObj?.gatekeeper_passed,
        matched_skills:        detailsObj?.matched_skills || [],
        missing_skills:        detailsObj?.missing_skills || [],
        red_flags:             detailsObj?.red_flags || detailsObj?.analysis?.red_flags || [],
        reasoning:             detailsObj?.reasoning || {},
        // Duplicate detection fields
        duplicate_status:                   detailsObj?.duplicate_status,
        duplicate_reference_application_id: detailsObj?.duplicate_reference_application_id,
        duplicate_similarity_score:         detailsObj?.duplicate_similarity_score,
        duplicate_reason:                   detailsObj?.duplicate_reason,
        duplicate_checked_at:               detailsObj?.duplicate_checked_at,
        duplicate_reference:                detailsObj?.duplicate_reference,
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

  // Auto-open a specific application when navigated from JobDetails duplicate section
  useEffect(() => {
    if (!initialApplicationId || loading || applicationsAll.length === 0) return;
    const target = applicationsAll.find(
      (a) => (a.application_id || a.id) === initialApplicationId
    );
    if (target) handleViewAnalysis(target);
  }, [initialApplicationId, loading, applicationsAll]);

  // low_match is treated as rejected for all display/filter purposes
  const normaliseStatus = (s: string) => s === 'low_match' ? 'rejected' : s;
  const filteredApplications = (() => {
    if (filter === 'all') return applicationsAll;
    if (filter === 'possible_duplicate') {
      return applicationsAll.filter(a => a.duplicate_status === 'possible_duplicate');
    }
    return applicationsAll.filter(a => normaliseStatus((a.status ?? '').toLowerCase().trim()) === filter);
  })();

  if (view === 'details' && selectedDetails) {
    return (
      <ApplicationDetails
        data={selectedDetails}
        onBack={() => setView('list')}
      />
    );
  }

  const getStatusStyles = (status: string) => {
    const s = normaliseStatus((status || '').toLowerCase().trim());
    if (s === 'qualified')  return { pill: 'bg-green-100 text-green-800',  badge: 'bg-success',  label: t.filterQualified };
    if (s === 'partial')    return { pill: 'bg-amber-100 text-amber-800',  badge: 'bg-warning',  label: t.filterPartial };
    return                         { pill: 'bg-red-100 text-red-800',      badge: 'bg-error',    label: t.filterRejected };
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <button onClick={onBack} className="flex items-center text-primary hover:underline text-sm font-medium gap-1">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t.backToCampaigns}
        </button>

        <div className="flex bg-slate-100 p-1 rounded-lg">
          {FILTER_KEYS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-md text-xs font-bold uppercase transition-all ${
                filter === f ? 'bg-white text-primary shadow-sm' : 'text-textMuted hover:text-textMain'
              }`}
            >
              {filterLabels[f]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4">
        {loading ? (
          <div className="p-12 text-center text-textMuted flex flex-col items-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4"></div>
            {t.loading}
          </div>
        ) : filteredApplications.length === 0 ? (
          <div className="bg-white p-12 rounded-xl border border-border text-center text-textMuted">
            {t.noApps}
          </div>
        ) : (
          filteredApplications.map((app) => {
            const styles = getStatusStyles(app.status);
            return (
              <div key={app.id || app.application_id} className="bg-white p-6 rounded-xl border border-border shadow-sm flex flex-col md:flex-row md:items-center justify-between hover:border-primary/30 transition-all">
                <div className="flex items-center gap-6">
                  <div className={`w-14 h-14 aspect-square shrink-0 flex-none rounded-full flex flex-col items-center justify-center font-bold text-white shadow-sm ${styles.badge}`}>
                    <span className="text-lg leading-none">{app.score}</span>
                    <span className="text-[10px] opacity-80 uppercase leading-none mt-0.5">{t.pts}</span>
                  </div>
                  <div>
                    <h4 className="text-lg font-bold text-textMain">{app.candidate_name}</h4>
                    <p className="text-xs text-textMuted">{t.appliedOn} {app.applied_date}</p>
                    <p className="text-sm text-textMain mt-2 max-w-xl italic">"{app.summary}"</p>
                  </div>
                </div>

                <div className="mt-4 md:mt-0 flex flex-col items-end gap-2">
                  <span className={`px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider ${styles.pill}`}>
                    {styles.label}
                  </span>
                  {app.duplicate_status === 'possible_duplicate' && (
                    <span className="px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-wider bg-orange-100 text-orange-700">
                      {t.possibleDuplicate}
                    </span>
                  )}
                  <button
                    disabled={detailsLoading}
                    onClick={() => handleViewAnalysis(app)}
                    className="text-primary hover:text-primaryDark text-sm font-semibold flex items-center disabled:opacity-50"
                  >
                    {detailsLoading ? t.loadingAnalysis : t.viewAnalysis}
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
