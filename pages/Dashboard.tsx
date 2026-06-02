
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

interface DashboardKpis {
  // Workspace overview
  total_applications: number;
  in_recruitment_process: number;
  hired_this_month: number;
  active_jobs: number;
  active_campaigns: number;
  // Needs attention
  awaiting_review: number;
  failed_or_blocked: number;
  // Pipeline breakdown
  under_review: number;
  interviewing: number;
  offer_made: number;
  hired: number;
  rejected: number;
  withdrawn: number;
  on_hold: number;
  under_review_total: number;
}

interface DashboardSummary {
  kpis: DashboardKpis;
}

const T = {
  en: {
    dashboard: 'Home',
    loading: 'Loading…',
    gettingStarted: 'Get Started',
    noJobs: 'No jobs yet. Create your first job to start recruiting.',
    createFirstJob: 'Create First Job',
    createCampaign: 'Create Campaign',
    sectionOverview: 'Workspace Overview',
    sectionAttention: 'Needs Attention',
    sectionPipeline: 'Recruitment Pipeline',
    activeCampaigns: 'Active Campaigns',
    activeJobs: 'Active Jobs',
    totalApplications: 'Total Applications',
    inProcess: 'In Recruitment Process',
    hiredThisMonth: 'Hired This Month',
    attAwaitingReview: 'Awaiting Review',
    attFailedBlocked: 'Failed / Blocked',
    attNone: 'All caught up — nothing needs immediate attention.',
    openJobs: 'Open related jobs',
    openCampaigns: 'Open campaigns',
    underReview: 'Under Review',
    interviewing: 'Interviewing',
    offerMade: 'Offer Made',
    hired: 'Hired',
    rejected: 'Rejected',
    withdrawn: 'Withdrawn',
    onHold: 'On Hold',
  },
  ar: {
    dashboard: 'الرئيسية',
    loading: 'جارٍ التحميل…',
    gettingStarted: 'ابدأ الآن',
    noJobs: 'لا توجد وظائف حتى الآن. أنشئ وظيفتك الأولى لبدء التوظيف.',
    createFirstJob: 'إنشاء أول وظيفة',
    createCampaign: 'إنشاء حملة',
    sectionOverview: 'نظرة عامة على مساحة العمل',
    sectionAttention: 'يحتاج إلى انتباه',
    sectionPipeline: 'خط التوظيف',
    activeCampaigns: 'الحملات النشطة',
    activeJobs: 'الوظائف النشطة',
    totalApplications: 'إجمالي الطلبات',
    inProcess: 'في عملية التوظيف',
    hiredThisMonth: 'تم التعيين هذا الشهر',
    attAwaitingReview: 'في انتظار المراجعة',
    attFailedBlocked: 'فشل / محظور',
    attNone: 'كل شيء على ما يرام — لا يوجد شيء يحتاج إلى انتباه فوري.',
    openJobs: 'فتح الوظائف ذات الصلة',
    openCampaigns: 'فتح الحملات',
    underReview: 'قيد المراجعة',
    interviewing: 'مقابلة',
    offerMade: 'تم تقديم العرض',
    hired: 'تم التعيين',
    rejected: 'مرفوض',
    withdrawn: 'منسحب',
    onHold: 'معلق',
  },
};

// ── Card primitives ────────────────────────────────────────────────────────

interface OverviewCardProps {
  label: string;
  value: number;
  color: string;
  onClick?: () => void;
}

const OverviewCard: React.FC<OverviewCardProps> = ({ label, value, color, onClick }) => (
  <div
    onClick={onClick}
    className={`bg-white rounded-xl border border-slate-200 p-5 flex flex-col items-center justify-center text-center min-h-[100px] transition-all ${
      onClick ? 'cursor-pointer hover:border-indigo-400 hover:bg-indigo-50 hover:shadow-md' : ''
    }`}
  >
    <p className={`text-3xl font-bold ${color}`}>{value}</p>
    <p className="text-xs text-slate-500 mt-2 leading-tight">{label}</p>
  </div>
);

interface AttentionCardProps {
  label: string;
  value: number;
  color: string;
  bgColor: string;
  borderColor: string;
  description: string;
  helperText: string;
  onClick?: () => void;
}

const AttentionCard: React.FC<AttentionCardProps> = ({
  label, value, color, bgColor, borderColor, description, helperText, onClick,
}) => (
  <div
    onClick={onClick}
    className={`rounded-xl border ${borderColor} ${bgColor} p-4 transition-all ${
      onClick ? 'cursor-pointer hover:opacity-80 hover:shadow-md' : ''
    }`}
  >
    <div className="flex items-center gap-4">
      <div className={`text-3xl font-bold ${color} min-w-[3rem] text-center tabular-nums`}>{value}</div>
      <div className="flex-1">
        <p className={`text-sm font-semibold ${color}`}>{label}</p>
        <p className="text-xs text-slate-500 mt-0.5 leading-tight">{description}</p>
      </div>
    </div>
    {onClick && (
      <p className="text-xs text-slate-400 mt-3 italic">{helperText}</p>
    )}
  </div>
);

interface PipelineRowProps {
  label: string;
  value: number;
  total: number;
  color: string;
  barColor: string;
}

const PipelineRow: React.FC<PipelineRowProps> = ({ label, value, total, color, barColor }) => {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-slate-500 w-36 shrink-0 text-right">{label}</span>
      <div className="flex-1 bg-slate-100 rounded-full h-2 overflow-hidden">
        <div
          className={`h-2 rounded-full ${barColor} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-sm font-semibold ${color} w-8 text-right tabular-nums`}>{value}</span>
    </div>
  );
};

// ── Main component ─────────────────────────────────────────────────────────

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
      <div className="text-center py-16 text-slate-400 text-sm">{t.loading}</div>
    );
  }

  const kpis = summary.kpis;
  const isEmpty = kpis.active_jobs === 0 && kpis.total_applications === 0;

  if (isEmpty) {
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

  const showCampaigns = isAgency || kpis.active_campaigns > 0;

  const pipelineTotal =
    kpis.awaiting_review + kpis.under_review + kpis.interviewing +
    kpis.offer_made + kpis.hired + kpis.rejected + kpis.withdrawn + kpis.on_hold;

  const attentionItems: AttentionCardProps[] = [
    kpis.awaiting_review > 0 && {
      label: t.attAwaitingReview,
      value: kpis.awaiting_review,
      description: 'Applications scored by AI, ready for your review.',
      helperText: t.openJobs,
      color: 'text-sky-700',
      bgColor: 'bg-sky-50',
      borderColor: 'border-sky-200',
      onClick: () => navigate('/candidates?view=awaiting_review'),
    },
    kpis.failed_or_blocked > 0 && {
      label: t.attFailedBlocked,
      value: kpis.failed_or_blocked,
      description: 'Applications where AI scoring failed or is stuck. May need manual review.',
      helperText: t.openJobs,
      color: 'text-red-700',
      bgColor: 'bg-red-50',
      borderColor: 'border-red-200',
      onClick: () => navigate('/candidates?view=failed_blocked'),
    },
  ].filter(Boolean) as AttentionCardProps[];

  return (
    <div className="space-y-8 max-w-5xl mx-auto">

      {/* ── Section A: Workspace Overview ────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
          {t.sectionOverview}
        </h2>
        <div className={`grid gap-4 ${showCampaigns ? 'grid-cols-2 sm:grid-cols-3' : 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4'}`}>
          {showCampaigns && (
            <OverviewCard
              label={t.activeCampaigns}
              value={kpis.active_campaigns}
              color="text-indigo-600"
              onClick={() => navigate('/campaigns')}
            />
          )}
          <OverviewCard
            label={t.activeJobs}
            value={kpis.active_jobs}
            color="text-emerald-600"
            onClick={() => navigate('/jobs')}
          />
          <OverviewCard
            label={t.totalApplications}
            value={kpis.total_applications}
            color="text-slate-800"
          />
          <OverviewCard
            label={t.inProcess}
            value={kpis.in_recruitment_process}
            color="text-blue-700"
            onClick={() => navigate('/candidates?view=in_process')}
          />
          <OverviewCard
            label={t.hiredThisMonth}
            value={kpis.hired_this_month}
            color="text-green-700"
          />
        </div>
      </section>

      {/* ── Section B: Needs Attention ───────────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
          {t.sectionAttention}
        </h2>
        {attentionItems.length === 0 ? (
          <div className="bg-green-50 border border-green-200 rounded-xl px-5 py-4 text-sm text-green-700">
            {t.attNone}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {attentionItems.map((item, i) => (
              <AttentionCard key={i} {...item} />
            ))}
          </div>
        )}
      </section>

      {/* ── Section C: Recruitment Pipeline ─────────────────────────────── */}
      <section>
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
          {t.sectionPipeline}
        </h2>
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-3">
          <PipelineRow label={t.awaitingReview} value={kpis.awaiting_review} total={pipelineTotal} color="text-sky-700"    barColor="bg-sky-400" />
          <PipelineRow label={t.underReview}    value={kpis.under_review}    total={pipelineTotal} color="text-blue-700"   barColor="bg-blue-400" />
          <PipelineRow label={t.interviewing}   value={kpis.interviewing}    total={pipelineTotal} color="text-purple-700" barColor="bg-purple-400" />
          <PipelineRow label={t.offerMade}      value={kpis.offer_made}      total={pipelineTotal} color="text-amber-700"  barColor="bg-amber-400" />
          <PipelineRow label={t.hired}          value={kpis.hired}           total={pipelineTotal} color="text-green-700"  barColor="bg-green-400" />
          <div className="border-t border-slate-100 my-1" />
          <PipelineRow label={t.rejected}       value={kpis.rejected}        total={pipelineTotal} color="text-red-500"    barColor="bg-red-300" />
          <PipelineRow label={t.withdrawn}      value={kpis.withdrawn}       total={pipelineTotal} color="text-slate-400"  barColor="bg-slate-300" />
          <PipelineRow label={t.onHold}         value={kpis.on_hold}         total={pipelineTotal} color="text-orange-500" barColor="bg-orange-300" />
        </div>
      </section>

    </div>
  );
};
