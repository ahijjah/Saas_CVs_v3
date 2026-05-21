
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { Job, AuthState } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface JobsDashboardProps {
  auth: AuthState;
  onViewDetails: (jobId: string) => void;
  onViewApplications: (jobId: string, filter: string) => void;
  onAddJob: () => void;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

const isSuperAdmin = (auth: AuthState) =>
  (auth.user?.role || '').toLowerCase() === 'super_admin';

const T = {
  en: {
    title: 'Active Recruitment Campaigns',
    sub: 'Overview of your open roles and candidate pipeline',
    addJob: 'Add New Job',
    refresh: 'Refresh',
    loading: 'Loading jobs...',
    noJobs: 'No jobs found. Create your first campaign.',
    noJobsTable: 'No jobs found. Create your first job campaign to get started.',
    totalLabel: 'Total',
    qualLabel: 'Qual.',
    partLabel: 'Part.',
    rejLabel: 'Rej.',
    viewDetails: 'View Campaign Details',
    colCode: 'Job Code',
    colTitle: 'Title',
    colClient: 'Client',
    colTenant: 'Tenant',
    colStatus: 'Status',
    colTotal: 'Total',
    colQualified: 'Qualified',
    colPartial: 'Partial',
    colRejected: 'Rejected',
    colActions: 'Actions',
    viewDetailsLink: 'View Details',
    filterByTenant: 'Filter by tenant',
    allTenants: 'All tenants',
    filterByClient: 'Filter by client',
    allClients: 'All clients',
    campaignsUsage: 'Active campaigns',
    cvsUsage: 'CVs (last 30 days)',
    planLimitReached: 'Limit reached',
    trialBadge: 'Trial',
    scoringInProgress: 'Scoring in progress',
  },
  ar: {
    title: 'حملات التوظيف النشطة',
    sub: 'نظرة عامة على وظائفك المفتوحة وخط أنابيب المرشحين',
    addJob: 'إضافة وظيفة جديدة',
    refresh: 'تحديث',
    loading: 'جارٍ تحميل الوظائف...',
    noJobs: 'لا توجد وظائف. أنشئ حملتك الأولى.',
    noJobsTable: 'لا توجد وظائف. أنشئ حملة التوظيف الأولى للبدء.',
    totalLabel: 'الإجمالي',
    qualLabel: 'مؤهل',
    partLabel: 'جزئي',
    rejLabel: 'مرفوض',
    viewDetails: 'عرض تفاصيل الحملة',
    colCode: 'رمز الوظيفة',
    colTitle: 'المسمى',
    colClient: 'العميل',
    colTenant: 'المستأجر',
    colStatus: 'الحالة',
    colTotal: 'الإجمالي',
    colQualified: 'مؤهلون',
    colPartial: 'جزئيون',
    colRejected: 'مرفوضون',
    colActions: 'الإجراءات',
    viewDetailsLink: 'عرض التفاصيل',
    filterByTenant: 'تصفية حسب المستأجر',
    allTenants: 'جميع المستأجرين',
    campaignsUsage: 'الحملات النشطة',
    cvsUsage: 'سير ذاتية (آخر 30 يوم)',
    planLimitReached: 'الحد الأقصى',
    trialBadge: 'تجريبي',
    scoringInProgress: 'جاري التقييم',
    filterByClient: 'تصفية حسب العميل',
    allClients: 'جميع العملاء',
  },
};

export const JobsDashboard: React.FC<JobsDashboardProps> = ({
  auth,
  onViewDetails,
  onViewApplications,
  onAddJob,
  addToast
}) => {
  const { lang } = useLanguage();
  const t = T[lang];

  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tenantFilter, setTenantFilter] = useState('');
  const [clientOrgFilter, setClientOrgFilter] = useState('');
  const superAdmin = isSuperAdmin(auth);

  // Plan usage (tenant users only — not shown for super admin)
  const [planUsage, setPlanUsage] = useState<{
    active_campaigns: number; max_campaigns: number;
    processed_cvs: number; max_cvs: number;
    subscription_status: string; plan_name: string;
    tenant_type: string;
    at_limit_campaigns: boolean; at_limit_cvs: boolean;
    near_limit_campaigns: boolean; near_limit_cvs: boolean;
  } | null>(null);

  const fetchJobs = async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.GET_JOBS_WEBHOOK_URL, {}, auth.token!);
      setJobs(Array.isArray(data) ? data : []);
    } catch (err: any) {
      console.error(err);
      if (!silent) {
        addToast("Failed to fetch jobs.", "error");
        setJobs([
          { job_id: 'JOB-2026-00074', job_code: 'JB001', job_title: 'Senior Frontend Engineer', job_client: 'Tech Corp', job_status: 'Active', applications_total: 45, applications_qualified: 12, applications_partial: 20, applications_rejected: 13 },
          { job_id: 'JOB-2026-00075', job_code: 'JB002', job_title: 'Backend Developer', job_client: 'Data Systems', job_status: 'Active', applications_total: 30, applications_qualified: 5, applications_partial: 10, applications_rejected: 15 },
          { job_id: 'JOB-2026-00076', job_code: 'JB003', job_title: 'UX Designer', job_client: 'Creative Lab', job_status: 'Closed', applications_total: 15, applications_qualified: 8, applications_partial: 4, applications_rejected: 3 },
        ]);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const fetchUsage = async () => {
    if (superAdmin) return;
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.TENANT_USAGE_URL, {}, auth.token!);
      if (data?.usage && data?.limits) {
        setPlanUsage({
          active_campaigns: data.usage.active_campaigns,
          max_campaigns: data.limits.max_campaigns,
          processed_cvs: data.usage.processed_cvs_this_month,
          max_cvs: data.limits.max_processed_cvs_per_month,
          subscription_status: data.subscription_status || 'active',
          plan_name: data.plan_name || data.plan_code || '',
          tenant_type: data.tenant_type || 'organization',
          at_limit_campaigns: data.at_limit?.campaigns ?? false,
          at_limit_cvs: data.at_limit?.cvs ?? false,
          near_limit_campaigns: data.near_limit?.campaigns ?? false,
          near_limit_cvs: data.near_limit?.cvs ?? false,
        });
      }
    } catch { /* silently ignore */ }
  };

  useEffect(() => {
    fetchJobs();
    fetchUsage();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.token]);

  const handleViewApplicationsClick = (job: Job, filter: string) => {
    onViewApplications(job.job_id, filter);
  };

  const handleViewDetailsClick = (job: Job) => {
    onViewDetails(job.job_id);
  };

  const tenantOptions = superAdmin
    ? Array.from(new Set(jobs.map(j => j.tenant_name).filter(Boolean))) as string[]
    : [];

  const isAgencyTenant = !superAdmin && (
    planUsage?.tenant_type === 'agency' || planUsage?.tenant_type === 'individual_recruiter'
  );

  const clientOrgOptions = isAgencyTenant
    ? Array.from(new Set(jobs.map(j => j.client_org_name).filter(Boolean))) as string[]
    : [];
  const hasGeneralJobs = isAgencyTenant && jobs.some(j => !j.client_org_name);

  const filteredJobs = jobs.filter(j => {
    if (tenantFilter && j.tenant_name !== tenantFilter) return false;
    if (clientOrgFilter === '__general__') return !j.client_org_name;
    if (clientOrgFilter && j.client_org_name !== clientOrgFilter) return false;
    return true;
  });

  return (
    <div className="space-y-6 p-1 sm:p-0">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h3 className="text-lg font-medium text-textMain">{t.title}</h3>
          <p className="text-sm text-textMuted">{t.sub}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
          {superAdmin && tenantOptions.length > 0 && (
            <select
              value={tenantFilter}
              onChange={e => setTenantFilter(e.target.value)}
              className="w-full sm:w-48 border border-border rounded-xl px-3 py-2 text-sm text-textMain bg-white focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              <option value="">{t.allTenants}</option>
              {tenantOptions.map(tn => (
                <option key={tn} value={tn}>{tn}</option>
              ))}
            </select>
          )}
          {isAgencyTenant && (clientOrgOptions.length > 0 || hasGeneralJobs) && (
            <select
              value={clientOrgFilter}
              onChange={e => setClientOrgFilter(e.target.value)}
              className="w-full sm:w-48 border border-border rounded-xl px-3 py-2 text-sm text-textMain bg-white focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              <option value="">{t.allClients}</option>
              {hasGeneralJobs && <option value="__general__">General</option>}
              {clientOrgOptions.map(co => (
                <option key={co} value={co}>{co}</option>
              ))}
            </select>
          )}
          <button
            onClick={() => { fetchJobs(true); fetchUsage(); }}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-xl text-sm font-bold text-textMuted hover:text-textMain hover:bg-slate-50 transition-colors"
          >
            <svg className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {t.refresh}
          </button>
          <button
            onClick={onAddJob}
            className="sm:w-auto bg-primary hover:bg-primaryDark text-white px-5 py-2.5 rounded-xl font-bold flex items-center justify-center transition-all shadow-lg shadow-primary/20 gap-2"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
            </svg>
            {t.addJob}
          </button>
        </div>
      </div>

      {/* Plan usage strip — tenant users only */}
      {!superAdmin && planUsage && (
        <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-white rounded-xl border border-border shadow-sm">
          {planUsage.subscription_status === 'trial' && (
            <span className="px-2.5 py-1 rounded-full bg-amber-100 text-amber-800 text-[10px] font-black uppercase tracking-widest shrink-0">
              {t.trialBadge} — {planUsage.plan_name}
            </span>
          )}
          {planUsage.subscription_status !== 'trial' && (
            <span className="px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-800 text-[10px] font-black uppercase tracking-widest shrink-0">
              {planUsage.plan_name}
            </span>
          )}
          {/* Campaigns */}
          <div className="flex items-center gap-2 flex-1 min-w-[160px]">
            <div className="flex-1">
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-[9px] font-black text-textMuted uppercase tracking-widest">{t.campaignsUsage}</span>
                <span className={`text-[10px] font-black ${planUsage.at_limit_campaigns ? 'text-error' : planUsage.near_limit_campaigns ? 'text-amber-600' : 'text-textMain'}`}>
                  {planUsage.active_campaigns} / {planUsage.max_campaigns > 0 ? planUsage.max_campaigns : '∞'}
                  {planUsage.at_limit_campaigns && <span className="ml-1 text-[9px]">{t.planLimitReached}</span>}
                </span>
              </div>
              <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${planUsage.at_limit_campaigns ? 'bg-error' : planUsage.near_limit_campaigns ? 'bg-amber-500' : 'bg-primary'}`}
                  style={{ width: planUsage.max_campaigns > 0 ? `${Math.min((planUsage.active_campaigns / planUsage.max_campaigns) * 100, 100)}%` : '0%' }}
                />
              </div>
            </div>
          </div>
          {/* CVs last 30 days */}
          <div className="flex items-center gap-2 flex-1 min-w-[160px]">
            <div className="flex-1">
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-[9px] font-black text-textMuted uppercase tracking-widest">{t.cvsUsage}</span>
                <span className={`text-[10px] font-black ${planUsage.at_limit_cvs ? 'text-error' : planUsage.near_limit_cvs ? 'text-amber-600' : 'text-textMain'}`}>
                  {planUsage.processed_cvs.toLocaleString()} / {planUsage.max_cvs > 0 ? planUsage.max_cvs.toLocaleString() : '∞'}
                  {planUsage.at_limit_cvs && <span className="ml-1 text-[9px]">{t.planLimitReached}</span>}
                </span>
              </div>
              <div className="w-full bg-slate-100 h-1.5 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${planUsage.at_limit_cvs ? 'bg-error' : planUsage.near_limit_cvs ? 'bg-amber-500' : 'bg-indigo-500'}`}
                  style={{ width: planUsage.max_cvs > 0 ? `${Math.min((planUsage.processed_cvs / planUsage.max_cvs) * 100, 100)}%` : '0%' }}
                />
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="bg-white rounded-2xl shadow-sm border border-border overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 space-y-4">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary"></div>
            <p className="text-textMuted animate-pulse font-medium uppercase tracking-widest text-xs">{t.loading}</p>
          </div>
        ) : (
          <>
            {/* Mobile View: Cards */}
            <div className="block sm:hidden divide-y divide-border">
              {filteredJobs.length === 0 ? (
                <div className="p-8 text-center text-textMuted">
                  {t.noJobs}
                </div>
              ) : (
                filteredJobs.map((job) => (
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
                        <div className="text-xs text-textMuted truncate">
                          {job.job_client}
                          {isAgencyTenant && (
                            <span className={`ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold ${job.client_org_name ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
                              {job.client_org_name || 'General'}
                            </span>
                          )}
                          {!isAgencyTenant && job.client_org_name && (
                            <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-indigo-50 text-indigo-700">
                              {job.client_org_name}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <span className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-tighter ${
                          job.job_status === 'Active' ? 'bg-green-100 text-green-800' :
                          job.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
                          'bg-amber-100 text-amber-800'
                        }`}>
                          {job.job_status}
                        </span>
                        {(job.applications_in_progress ?? 0) > 0 && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-blue-100 text-blue-700">
                            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                            {t.scoringInProgress}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="grid grid-cols-4 gap-2 text-center bg-slate-50 p-3 rounded-xl border border-slate-100">
                      <div>
                        <div className="text-[9px] font-black text-textMuted uppercase tracking-tighter mb-1">{t.totalLabel}</div>
                        <button
                          onClick={() => handleViewApplicationsClick(job, 'all')}
                          className="text-sm font-bold text-primary underline decoration-primary/20"
                        >
                          {job.applications_total}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-success uppercase tracking-tighter mb-1">{t.qualLabel}</div>
                        <button
                          onClick={() => handleViewApplicationsClick(job, 'qualified')}
                          className="text-sm font-bold text-success underline decoration-success/20"
                        >
                          {job.applications_qualified}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-warning uppercase tracking-tighter mb-1">{t.partLabel}</div>
                        <button
                          onClick={() => handleViewApplicationsClick(job, 'partial')}
                          className="text-sm font-bold text-warning underline decoration-warning/20"
                        >
                          {job.applications_partial}
                        </button>
                      </div>
                      <div>
                        <div className="text-[9px] font-black text-error uppercase tracking-tighter mb-1">{t.rejLabel}</div>
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
                      {t.viewDetails}
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
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">{t.colCode}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">{t.colTitle}</th>
                    {isAgencyTenant && <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">{t.colClient}</th>}
                    {superAdmin && <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">{t.colTenant}</th>}
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider whitespace-nowrap">{t.colStatus}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider text-center whitespace-nowrap">{t.colTotal}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-success uppercase tracking-wider text-center whitespace-nowrap">{t.colQualified}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-warning uppercase tracking-wider text-center whitespace-nowrap">{t.colPartial}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-error uppercase tracking-wider text-center whitespace-nowrap">{t.colRejected}</th>
                    <th className="px-6 py-4 text-xs font-semibold text-textMuted uppercase tracking-wider text-right whitespace-nowrap">{t.colActions}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {filteredJobs.length === 0 ? (
                    <tr>
                      <td colSpan={superAdmin ? 9 : isAgencyTenant ? 9 : 8} className="px-6 py-12 text-center text-textMuted">
                        {t.noJobsTable}
                      </td>
                    </tr>
                  ) : (
                    filteredJobs.map((job) => (
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
                          {job.job_client && (
                            <div className="text-xs text-textMuted whitespace-nowrap">{job.job_client}</div>
                          )}
                        </td>
                        {isAgencyTenant && (
                          <td className="px-6 py-4 whitespace-nowrap">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${job.client_org_name ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
                              {job.client_org_name || 'General'}
                            </span>
                          </td>
                        )}
                        {superAdmin && (
                          <td className="px-6 py-4 text-xs text-textMuted whitespace-nowrap">{job.tenant_name || '—'}</td>
                        )}
                        <td className="px-6 py-4 whitespace-nowrap">
                          <div className="flex flex-col gap-1">
                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                              job.job_status === 'Active' ? 'bg-green-100 text-green-800' :
                              job.job_status === 'Closed' ? 'bg-slate-100 text-slate-800' :
                              'bg-amber-100 text-amber-800'
                            }`}>
                              {job.job_status}
                            </span>
                            {(job.applications_in_progress ?? 0) > 0 && (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-blue-100 text-blue-700">
                                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
                                {t.scoringInProgress}
                              </span>
                            )}
                          </div>
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
                            {t.viewDetailsLink}
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
