
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { usePageTitle } from '../context/PageTitleContext';

interface DashboardProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface DashboardSummary {
  kpis: {
    awaiting_review: number;
    under_review: number;
    interviewing: number;
    offer_made: number;
    hired: number;
    rejected: number;
    withdrawn: number;
    on_hold: number;
    under_review_total: number;
    hired_this_month: number;
    active_jobs: number;
    active_campaigns: number;
  };
  plan_usage: {
    max_campaigns: number | null;
    active_campaigns: number;
    max_processed_cvs: number | null;
    processed_cvs_this_month: number;
  };
}

const T = {
  en: {
    dashboard: 'Dashboard',
    loading: 'Loading dashboard…',
    awaitingReview: 'Awaiting Review',
    underReview: 'Under Review',
    interviewing: 'Interviewing',
    hired: 'Hired',
    hiredThisMonth: 'Hired This Month',
    rejected: 'Rejected',
    activeJobs: 'Active Jobs',
    activeCampaigns: 'Active Campaigns',
    gettingStarted: 'Get Started',
    noJobs: 'No jobs yet. Create your first job to start recruiting.',
    createFirstJob: 'Create First Job',
    createCampaign: 'Create Campaign',
    viewAllApplications: 'View All Jobs',
  },
  ar: {
    dashboard: 'لوحة التحكم',
    loading: 'جارٍ تحميل لوحة التحكم…',
    awaitingReview: 'في انتظار المراجعة',
    underReview: 'قيد المراجعة',
    interviewing: 'مقابلة',
    hired: 'تم التعيين',
    hiredThisMonth: 'تم التعيين هذا الشهر',
    rejected: 'مرفوض',
    activeJobs: 'الوظائف النشطة',
    activeCampaigns: 'الحملات النشطة',
    gettingStarted: 'ابدأ الآن',
    noJobs: 'لا توجد وظائف حتى الآن. أنشئ وظيفتك الأولى لبدء التوظيف.',
    createFirstJob: 'إنشاء أول وظيفة',
    createCampaign: 'إنشاء حملة',
    viewAllApplications: 'عرض جميع الوظائف',
  },
};

interface KpiCardProps {
  label: string;
  value: number;
  color?: string;
  onClick?: () => void;
}

const KpiCard: React.FC<KpiCardProps> = ({ label, value, color = 'text-slate-800', onClick }) => (
  <div
    onClick={onClick}
    className={`bg-white rounded-xl border border-slate-200 p-5 flex flex-col items-center justify-center text-center min-h-[100px] ${
      onClick ? 'cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 transition-colors' : ''
    }`}
  >
    <p className={`text-3xl font-bold ${color}`}>{value}</p>
    <p className="text-xs text-slate-500 mt-2 leading-tight">{label}</p>
  </div>
);

export const Dashboard: React.FC<DashboardProps> = ({ auth, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const navigate = useNavigate();
  const { setPageTitle } = usePageTitle();

  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const tenantType = auth.user?.tenant_type ?? 'organization';
  const isAgency = tenantType === 'agency' || tenantType === 'individual_recruiter';

  useEffect(() => {
    setPageTitle(t.dashboard);
  }, [setPageTitle, t.dashboard]);

  useEffect(() => {
    const load = async () => {
      if (!auth.token) return;
      setLoading(true);
      try {
        const data = await apiService.get(WEBHOOK_CONFIG.DASHBOARD_SUMMARY_URL, {}, auth.token);
        setSummary(data);
      } catch (err: any) {
        addToast(err.message || 'Failed to load dashboard', 'error');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [auth.token, addToast]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="w-8 h-8 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="text-center py-16 text-slate-400 text-sm">
        {t.loading}
      </div>
    );
  }

  // Show getting started if no jobs
  if (summary.kpis.active_jobs === 0 && Object.values(summary.kpis).every(v => v === 0)) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="bg-white rounded-xl border border-slate-200 p-8 text-center space-y-4">
          <h2 className="text-xl font-bold text-slate-900">{t.gettingStarted}</h2>
          <p className="text-slate-600">{t.noJobs}</p>
          <div className="flex gap-3 justify-center pt-4">
            <button
              onClick={() => navigate('/jobs')}
              className="px-6 py-2 rounded-lg bg-indigo-600 text-white font-medium hover:bg-indigo-700 transition-colors"
            >
              {t.createFirstJob}
            </button>
            <button
              onClick={() => navigate('/campaigns')}
              className="px-6 py-2 rounded-lg border border-slate-200 text-slate-600 font-medium hover:bg-slate-50 transition-colors"
            >
              {t.createCampaign}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-6xl mx-auto">
      {/* KPI Cards Grid */}
      <div>
        <h2 className="text-lg font-semibold text-slate-900 mb-4">Key Metrics</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Workflow Status KPIs */}
          <KpiCard
            label={t.awaitingReview}
            value={summary.kpis.awaiting_review}
            color="text-sky-700"
          />
          <KpiCard
            label={t.underReview}
            value={summary.kpis.under_review}
            color="text-blue-700"
          />
          <KpiCard
            label={t.interviewing}
            value={summary.kpis.interviewing}
            color="text-purple-600"
          />

          {/* Outcomes */}
          <KpiCard
            label={t.hiredThisMonth}
            value={summary.kpis.hired_this_month}
            color="text-green-700"
          />
          <KpiCard
            label={t.rejected}
            value={summary.kpis.rejected}
            color="text-red-600"
          />

          {/* Operational KPIs */}
          <KpiCard
            label={t.activeJobs}
            value={summary.kpis.active_jobs}
            color="text-emerald-600"
            onClick={() => navigate('/jobs')}
          />

          {/* Show campaigns only if tenant uses them */}
          {isAgency || summary.kpis.active_campaigns > 0 ? (
            <KpiCard
              label={t.activeCampaigns}
              value={summary.kpis.active_campaigns}
              color="text-indigo-600"
              onClick={() => navigate('/campaigns')}
            />
          ) : null}
        </div>
      </div>

      {/* Quick Actions Card */}
      <div className="bg-white rounded-xl border border-slate-200 p-6">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Quick Actions</h3>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => navigate('/jobs')}
            className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 transition-colors"
          >
            {t.viewAllApplications}
          </button>
          {(isAgency || summary.kpis.active_campaigns > 0) && (
            <button
              onClick={() => navigate('/campaigns')}
              className="px-4 py-2 rounded-lg border border-slate-200 text-slate-600 text-sm font-medium hover:bg-slate-50 transition-colors"
            >
              {t.activeCampaigns}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
