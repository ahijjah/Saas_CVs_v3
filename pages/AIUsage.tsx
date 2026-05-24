import React, { useState, useEffect, useCallback } from 'react';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface AIUsageProps {
  auth: AuthState;
  addToast?: (msg: string, type?: string) => void;
}

const T = {
  en: {
    title: 'AI Usage & Cost',
    subtitle: 'Track AI API calls, token consumption, and estimated costs across all tenants',
    refresh: 'Refresh',
    loading: 'Loading…',
    noData: 'No data available.',
    from: 'From',
    to: 'To',
    allTenants: 'All Tenants',
    allModels: 'All Models',
    allStages: 'All Stages',
    apply: 'Apply Filters',
    reset: 'Reset',
    tabs: {
      overview: 'Overview',
      byTenant: 'By Tenant',
      byJob: 'By Job',
      byModel: 'By Model',
      logs: 'Detailed Logs',
      pricing: 'Pricing Config',
    },
    kpi: {
      totalCost: 'Total AI Cost',
      totalTokens: 'Total Tokens',
      totalCalls: 'Total Calls',
      failedCalls: 'Failed Calls',
      avgCost: 'Avg Cost / Call',
      avgLatency: 'Avg Latency',
      uniqueApps: 'Unique Applications',
    },
    table: {
      tenant: 'Tenant',
      job: 'Job',
      model: 'Model',
      provider: 'Provider',
      calls: 'Calls',
      tokens: 'Tokens',
      cost: 'Est. Cost (USD)',
      failed: 'Failed',
      latency: 'Avg Latency',
      apps: 'Applications',
      stage: 'Stage',
      status: 'Status',
      promptTokens: 'Prompt Tokens',
      completionTokens: 'Completion Tokens',
      createdAt: 'Created At',
      actions: 'Actions',
    },
    logs: {
      page: 'Page',
      of: 'of',
      prev: 'Prev',
      next: 'Next',
      exportCsv: 'Export CSV',
      filterStage: 'Stage',
      filterStatus: 'Status',
      filterModel: 'Model',
      allStatuses: 'All Statuses',
      allStages: 'All Stages',
      metadata: 'Metadata',
      close: 'Close',
    },
    pricing: {
      inputPrice: 'Input Price (per 1M tokens)',
      outputPrice: 'Output Price (per 1M tokens)',
      active: 'Active',
      save: 'Save',
      saved: 'Saved',
      updatedAt: 'Updated',
    },
  },
  ar: {
    title: 'استخدام الذكاء الاصطناعي والتكلفة',
    subtitle: 'تتبع استدعاءات واجهة برمجة تطبيقات الذكاء الاصطناعي واستهلاك الرموز والتكاليف المقدرة',
    refresh: 'تحديث',
    loading: 'جارٍ التحميل…',
    noData: 'لا توجد بيانات.',
    from: 'من',
    to: 'إلى',
    allTenants: 'جميع المستأجرين',
    allModels: 'جميع النماذج',
    allStages: 'جميع المراحل',
    apply: 'تطبيق الفلاتر',
    reset: 'إعادة تعيين',
    tabs: {
      overview: 'نظرة عامة',
      byTenant: 'حسب المستأجر',
      byJob: 'حسب الوظيفة',
      byModel: 'حسب النموذج',
      logs: 'السجلات التفصيلية',
      pricing: 'إعدادات التسعير',
    },
    kpi: {
      totalCost: 'إجمالي التكلفة',
      totalTokens: 'إجمالي الرموز',
      totalCalls: 'إجمالي الاستدعاءات',
      failedCalls: 'الاستدعاءات الفاشلة',
      avgCost: 'متوسط التكلفة / استدعاء',
      avgLatency: 'متوسط زمن الاستجابة',
      uniqueApps: 'الطلبات الفريدة',
    },
    table: {
      tenant: 'المستأجر',
      job: 'الوظيفة',
      model: 'النموذج',
      provider: 'المزود',
      calls: 'الاستدعاءات',
      tokens: 'الرموز',
      cost: 'التكلفة المقدرة (USD)',
      failed: 'فاشل',
      latency: 'متوسط الزمن',
      apps: 'الطلبات',
      stage: 'المرحلة',
      status: 'الحالة',
      promptTokens: 'رموز الطلب',
      completionTokens: 'رموز الإجابة',
      createdAt: 'تاريخ الإنشاء',
      actions: 'إجراءات',
    },
    logs: {
      page: 'صفحة',
      of: 'من',
      prev: 'السابق',
      next: 'التالي',
      exportCsv: 'تصدير CSV',
      filterStage: 'المرحلة',
      filterStatus: 'الحالة',
      filterModel: 'النموذج',
      allStatuses: 'جميع الحالات',
      allStages: 'جميع المراحل',
      metadata: 'البيانات الوصفية',
      close: 'إغلاق',
    },
    pricing: {
      inputPrice: 'سعر المدخلات (لكل مليون رمز)',
      outputPrice: 'سعر المخرجات (لكل مليون رمز)',
      active: 'نشط',
      save: 'حفظ',
      saved: 'تم الحفظ',
      updatedAt: 'تاريخ التحديث',
    },
  },
};

type TabKey = 'overview' | 'byTenant' | 'byJob' | 'byModel' | 'logs' | 'pricing';

interface Overview {
  total_calls: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  failed_calls: number;
  successful_calls: number;
  avg_latency_ms: number;
  avg_cost_per_call: number;
  unique_applications: number;
  unique_tenants: number;
}

interface TenantRow {
  tenant_id: string | null;
  tenant_name: string | null;
  total_calls: number;
  total_tokens: number;
  total_cost_usd: number;
  failed_calls: number;
  avg_latency_ms: number;
  unique_applications: number;
}

interface JobRow {
  job_id: string | null;
  job_title: string | null;
  tenant_name: string | null;
  total_calls: number;
  total_tokens: number;
  total_cost_usd: number;
  unique_applications: number;
  failed_calls: number;
}

interface ModelRow {
  provider: string;
  model: string;
  total_calls: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_cost_usd: number;
  failed_calls: number;
  avg_latency_ms: number;
}

interface LogRow {
  usage_id: string;
  tenant_id: string | null;
  job_id: string | null;
  application_id: string | null;
  stage: string;
  provider: string;
  model: string;
  prompt_key: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  request_status: string;
  latency_ms: number | null;
  retry_count: number;
  error_type: string | null;
  error_message: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

interface PricingRow {
  pricing_id: string;
  provider: string;
  model: string;
  input_price_per_1m_tokens: number;
  output_price_per_1m_tokens: number;
  active: boolean;
  updated_at: string | null;
}

function fmtCost(v: number | null | undefined): string {
  if (v == null) return '—';
  if (v < 0.000001) return '$0.00';
  if (v < 0.01) return `$${v.toFixed(6)}`;
  return `$${v.toFixed(4)}`;
}
function fmtTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}
function fmtMs(v: number | null | undefined): string {
  if (v == null || v === 0) return '—';
  return `${Math.round(v)}ms`;
}

const STATUS_BADGE: Record<string, string> = {
  success:   'bg-emerald-100 text-emerald-700',
  failed:    'bg-red-100 text-red-700',
  timeout:   'bg-amber-100 text-amber-700',
  cancelled: 'bg-slate-100 text-slate-600',
};

const STAGES = ['cv_scoring', 'cv_comparison', 'criteria_extraction', 'level2_screening'];
const STATUSES = ['success', 'failed', 'timeout', 'cancelled'];

export const AIUsagePage: React.FC<AIUsageProps> = ({ auth }) => {
  const { lang } = useLanguage();
  const t = T[lang];
  const token = auth?.token || localStorage.getItem('token') || '';

  const [tab, setTab] = useState<TabKey>('overview');
  const [loading, setLoading] = useState(false);

  // Filters
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate]     = useState('');
  const [filterModel, setFilterModel] = useState('');
  const [filterStage, setFilterStage] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  // Data
  const [overview, setOverview]     = useState<Overview | null>(null);
  const [tenants, setTenants]       = useState<TenantRow[]>([]);
  const [jobs, setJobs]             = useState<JobRow[]>([]);
  const [models, setModels]         = useState<ModelRow[]>([]);
  const [logs, setLogs]             = useState<LogRow[]>([]);
  const [logTotal, setLogTotal]     = useState(0);
  const [logPages, setLogPages]     = useState(1);
  const [logPage, setLogPage]       = useState(1);
  const [pricing, setPricing]       = useState<PricingRow[]>([]);
  const [pricingEdits, setPricingEdits] = useState<Record<string, Partial<PricingRow>>>({});
  const [savedPricing, setSavedPricing] = useState<Record<string, boolean>>({});

  const [expandedLog, setExpandedLog] = useState<LogRow | null>(null);

  const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  const base = (WEBHOOK_CONFIG as Record<string, string>)['AI_USAGE_URL'] || '/admin/ai-usage';
  const pricingUrl = (WEBHOOK_CONFIG as Record<string, string>)['AI_PRICING_URL'] || '/admin/ai-usage/pricing';

  function buildParams(extra: Record<string, string> = {}): string {
    const p = new URLSearchParams();
    if (fromDate)   p.set('from_date', fromDate);
    if (toDate)     p.set('to_date', toDate);
    if (filterModel) p.set('model', filterModel);
    if (filterStage) p.set('stage', filterStage);
    Object.entries(extra).forEach(([k, v]) => v && p.set(k, v));
    return p.toString() ? `?${p}` : '';
  }

  const fetchOverview = useCallback(async () => {
    const res = await fetch(`${base}/overview${buildParams()}`, { headers });
    if (res.ok) setOverview(await res.json());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate, filterModel, filterStage]);

  const fetchTenants = useCallback(async () => {
    const res = await fetch(`${base}/by-tenant${buildParams()}`, { headers });
    if (res.ok) setTenants(await res.json());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate, filterModel, filterStage]);

  const fetchJobs = useCallback(async () => {
    const res = await fetch(`${base}/by-job${buildParams()}`, { headers });
    if (res.ok) setJobs(await res.json());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate]);

  const fetchModels = useCallback(async () => {
    const res = await fetch(`${base}/by-model${buildParams()}`, { headers });
    if (res.ok) setModels(await res.json());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate]);

  const fetchLogs = useCallback(async (pg = logPage) => {
    const params = new URLSearchParams();
    if (fromDate)     params.set('from_date', fromDate);
    if (toDate)       params.set('to_date', toDate);
    if (filterModel)  params.set('model', filterModel);
    if (filterStage)  params.set('stage', filterStage);
    if (filterStatus) params.set('request_status', filterStatus);
    params.set('page', String(pg));
    params.set('limit', '50');
    const res = await fetch(`${base}/logs?${params}`, { headers });
    if (res.ok) {
      const data = await res.json();
      setLogs(data.results || []);
      setLogTotal(data.total || 0);
      setLogPages(data.pages || 1);
      setLogPage(pg);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDate, toDate, filterModel, filterStage, filterStatus, logPage]);

  const fetchPricing = useCallback(async () => {
    const res = await fetch(pricingUrl, { headers });
    if (res.ok) setPricing(await res.json());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      if (tab === 'overview') await fetchOverview();
      else if (tab === 'byTenant') await fetchTenants();
      else if (tab === 'byJob') await fetchJobs();
      else if (tab === 'byModel') await fetchModels();
      else if (tab === 'logs') await fetchLogs(1);
      else if (tab === 'pricing') await fetchPricing();
    } finally {
      setLoading(false);
    }
  }, [tab, fetchOverview, fetchTenants, fetchJobs, fetchModels, fetchLogs, fetchPricing]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleApplyFilters = () => { fetchAll(); };
  const handleResetFilters = () => {
    setFromDate(''); setToDate(''); setFilterModel(''); setFilterStage(''); setFilterStatus('');
  };

  const handleExportCsv = () => {
    if (!logs.length) return;
    const cols = ['usage_id','stage','provider','model','prompt_tokens','completion_tokens','total_tokens','estimated_cost_usd','request_status','latency_ms','created_at'];
    const rows = [cols.join(','), ...logs.map(r => cols.map(c => JSON.stringify((r as Record<string,unknown>)[c] ?? '')).join(','))];
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'ai_usage_logs.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const handleSavePricing = async (row: PricingRow) => {
    const edits = pricingEdits[row.pricing_id] || {};
    const body = {
      input_price_per_1m_tokens:  edits.input_price_per_1m_tokens  ?? row.input_price_per_1m_tokens,
      output_price_per_1m_tokens: edits.output_price_per_1m_tokens ?? row.output_price_per_1m_tokens,
      active: edits.active ?? row.active,
    };
    const res = await fetch(`${pricingUrl}/${row.pricing_id}`, {
      method: 'PUT', headers, body: JSON.stringify(body),
    });
    if (res.ok) {
      setSavedPricing(s => ({ ...s, [row.pricing_id]: true }));
      setTimeout(() => setSavedPricing(s => ({ ...s, [row.pricing_id]: false })), 2000);
      await fetchPricing();
    }
  };

  // ── Bar chart helper ──────────────────────────────────────────────────────
  function BarChart({ data, labelKey, valueKey, colorClass = 'bg-indigo-500', fmtValue = String }: {
    data: Record<string, unknown>[];
    labelKey: string;
    valueKey: string;
    colorClass?: string;
    fmtValue?: (v: number) => string;
  }) {
    const max = Math.max(...data.map(r => Number(r[valueKey]) || 0), 0.000001);
    return (
      <div className="space-y-2">
        {data.map((r, i) => {
          const val = Number(r[valueKey]) || 0;
          const pct = (val / max) * 100;
          return (
            <div key={i} className="flex items-center gap-2 text-sm">
              <span className="w-32 truncate text-slate-600 shrink-0">{String(r[labelKey] || '—')}</span>
              <div className="flex-1 bg-slate-100 rounded h-4 overflow-hidden">
                <div className={`h-full rounded ${colorClass}`} style={{ width: `${pct}%` }} />
              </div>
              <span className="w-24 text-right text-slate-700 font-medium shrink-0">{fmtValue(val)}</span>
            </div>
          );
        })}
      </div>
    );
  }

  // ── KPI cards ─────────────────────────────────────────────────────────────
  function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <p className="text-xs text-slate-500 font-medium uppercase tracking-wide mb-1">{label}</p>
        <p className="text-2xl font-bold text-slate-800">{value}</p>
        {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
      </div>
    );
  }

  // ── Tabs ──────────────────────────────────────────────────────────────────
  const tabKeys: TabKey[] = ['overview', 'byTenant', 'byJob', 'byModel', 'logs', 'pricing'];

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">{t.title}</h1>
          <p className="text-sm text-slate-500 mt-1">{t.subtitle}</p>
        </div>
        <button
          onClick={fetchAll}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors"
        >
          {loading ? t.loading : t.refresh}
        </button>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-slate-500 mb-1">{t.from}</label>
            <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">{t.to}</label>
            <input type="date" value={toDate} onChange={e => setToDate(e.target.value)}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">{t.allModels}</label>
            <input placeholder={t.allModels} value={filterModel} onChange={e => setFilterModel(e.target.value)}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-36 focus:outline-none focus:ring-2 focus:ring-indigo-300" />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">{t.allStages}</label>
            <select value={filterStage} onChange={e => setFilterStage(e.target.value)}
              className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300">
              <option value="">{t.allStages}</option>
              {STAGES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          {tab === 'logs' && (
            <div>
              <label className="block text-xs text-slate-500 mb-1">{t.logs.allStatuses}</label>
              <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
                className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300">
                <option value="">{t.logs.allStatuses}</option>
                {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          )}
          <button onClick={handleApplyFilters}
            className="px-4 py-1.5 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700">
            {t.apply}
          </button>
          <button onClick={handleResetFilters}
            className="px-4 py-1.5 bg-slate-100 text-slate-600 rounded-lg text-sm font-medium hover:bg-slate-200">
            {t.reset}
          </button>
        </div>
      </div>

      {/* Tab nav */}
      <div className="flex gap-1 bg-slate-100 p-1 rounded-xl w-fit">
        {tabKeys.map(k => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              tab === k ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
            }`}>
            {t.tabs[k]}
          </button>
        ))}
      </div>

      {loading && (
        <div className="text-center py-10 text-slate-400">{t.loading}</div>
      )}

      {/* ── Overview ─────────────────────────────────────────────────────── */}
      {!loading && tab === 'overview' && overview && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <KpiCard label={t.kpi.totalCost}   value={fmtCost(overview.total_cost_usd)} />
            <KpiCard label={t.kpi.totalTokens} value={fmtTokens(overview.total_tokens)} sub={`${fmtTokens(overview.total_prompt_tokens)} in / ${fmtTokens(overview.total_completion_tokens)} out`} />
            <KpiCard label={t.kpi.totalCalls}  value={String(overview.total_calls)} sub={`${overview.successful_calls} successful`} />
            <KpiCard label={t.kpi.failedCalls} value={String(overview.failed_calls)} />
            <KpiCard label={t.kpi.avgCost}     value={fmtCost(overview.avg_cost_per_call)} />
            <KpiCard label={t.kpi.avgLatency}  value={fmtMs(overview.avg_latency_ms)} />
            <KpiCard label={t.kpi.uniqueApps}  value={String(overview.unique_applications)} />
          </div>
        </div>
      )}

      {/* ── By Tenant ────────────────────────────────────────────────────── */}
      {!loading && tab === 'byTenant' && (
        <div className="space-y-6">
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-700 mb-4">Cost by Tenant</h3>
            <BarChart data={tenants} labelKey="tenant_name" valueKey="total_cost_usd" colorClass="bg-indigo-500" fmtValue={v => fmtCost(v)} />
          </div>
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {[t.table.tenant, t.table.calls, t.table.tokens, t.table.cost, t.table.failed, t.table.latency, t.table.apps].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {tenants.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-400">{t.noData}</td></tr>
                )}
                {tenants.map(r => (
                  <tr key={r.tenant_id || 'null'} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-700">{r.tenant_name || '—'}</td>
                    <td className="px-4 py-3 text-slate-600">{r.total_calls}</td>
                    <td className="px-4 py-3 text-slate-600">{fmtTokens(r.total_tokens)}</td>
                    <td className="px-4 py-3 font-medium text-slate-700">{fmtCost(r.total_cost_usd)}</td>
                    <td className="px-4 py-3 text-slate-600">{r.failed_calls}</td>
                    <td className="px-4 py-3 text-slate-600">{fmtMs(r.avg_latency_ms)}</td>
                    <td className="px-4 py-3 text-slate-600">{r.unique_applications}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── By Job ───────────────────────────────────────────────────────── */}
      {!loading && tab === 'byJob' && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {[t.table.job, t.table.tenant, t.table.calls, t.table.tokens, t.table.cost, t.table.apps, t.table.failed].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-400">{t.noData}</td></tr>
              )}
              {jobs.map(r => (
                <tr key={r.job_id || 'null'} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-700 max-w-[200px] truncate">{r.job_title || '—'}</td>
                  <td className="px-4 py-3 text-slate-500">{r.tenant_name || '—'}</td>
                  <td className="px-4 py-3 text-slate-600">{r.total_calls}</td>
                  <td className="px-4 py-3 text-slate-600">{fmtTokens(r.total_tokens)}</td>
                  <td className="px-4 py-3 font-medium text-slate-700">{fmtCost(r.total_cost_usd)}</td>
                  <td className="px-4 py-3 text-slate-600">{r.unique_applications}</td>
                  <td className="px-4 py-3 text-slate-600">{r.failed_calls}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── By Model ─────────────────────────────────────────────────────── */}
      {!loading && tab === 'byModel' && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-700 mb-4">Cost by Model</h3>
              <BarChart data={models} labelKey="model" valueKey="total_cost_usd" colorClass="bg-violet-500" fmtValue={v => fmtCost(v)} />
            </div>
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-700 mb-4">Token Usage by Model</h3>
              <BarChart data={models} labelKey="model" valueKey="total_tokens" colorClass="bg-sky-500" fmtValue={fmtTokens} />
            </div>
          </div>
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {[t.table.provider, t.table.model, t.table.calls, t.table.tokens, t.table.cost, t.table.failed, t.table.latency].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {models.length === 0 && (
                  <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-400">{t.noData}</td></tr>
                )}
                {models.map(r => (
                  <tr key={`${r.provider}-${r.model}`} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-500">{r.provider}</td>
                    <td className="px-4 py-3 font-medium text-slate-700">{r.model}</td>
                    <td className="px-4 py-3 text-slate-600">{r.total_calls}</td>
                    <td className="px-4 py-3 text-slate-600">
                      <span title={`${r.total_prompt_tokens} prompt / ${r.total_completion_tokens} completion`}>
                        {fmtTokens(r.total_tokens)}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-medium text-slate-700">{fmtCost(r.total_cost_usd)}</td>
                    <td className="px-4 py-3 text-slate-600">{r.failed_calls}</td>
                    <td className="px-4 py-3 text-slate-600">{fmtMs(r.avg_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Detailed Logs ─────────────────────────────────────────────────── */}
      {!loading && tab === 'logs' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">{logTotal} records</p>
            <button onClick={handleExportCsv}
              className="px-3 py-1.5 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium hover:bg-slate-200">
              {t.logs.exportCsv}
            </button>
          </div>
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-x-auto">
            <table className="w-full text-sm whitespace-nowrap">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {[t.table.stage, t.table.model, t.table.promptTokens, t.table.completionTokens, t.table.cost, t.table.status, t.table.latency, t.table.createdAt, t.table.actions].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {logs.length === 0 && (
                  <tr><td colSpan={9} className="px-4 py-8 text-center text-slate-400">{t.noData}</td></tr>
                )}
                {logs.map(r => (
                  <tr key={r.usage_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-600">{r.stage}</td>
                    <td className="px-4 py-3 text-slate-700 font-medium">{r.model}</td>
                    <td className="px-4 py-3 text-slate-600">{r.prompt_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-slate-600">{r.completion_tokens.toLocaleString()}</td>
                    <td className="px-4 py-3 text-slate-700 font-medium">{fmtCost(r.estimated_cost_usd)}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_BADGE[r.request_status] || 'bg-slate-100 text-slate-600'}`}>
                        {r.request_status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{fmtMs(r.latency_ms)}</td>
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                    </td>
                    <td className="px-4 py-3">
                      {r.metadata && (
                        <button onClick={() => setExpandedLog(r)}
                          className="text-indigo-600 hover:underline text-xs">
                          {t.logs.metadata}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {/* Pagination */}
          <div className="flex items-center justify-between pt-1">
            <button disabled={logPage <= 1} onClick={() => fetchLogs(logPage - 1)}
              className="px-3 py-1.5 text-sm bg-slate-100 rounded-lg disabled:opacity-40 hover:bg-slate-200">
              {t.logs.prev}
            </button>
            <span className="text-sm text-slate-500">
              {t.logs.page} {logPage} {t.logs.of} {logPages}
            </span>
            <button disabled={logPage >= logPages} onClick={() => fetchLogs(logPage + 1)}
              className="px-3 py-1.5 text-sm bg-slate-100 rounded-lg disabled:opacity-40 hover:bg-slate-200">
              {t.logs.next}
            </button>
          </div>
        </div>
      )}

      {/* ── Pricing Config ────────────────────────────────────────────────── */}
      {!loading && tab === 'pricing' && (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {[t.table.provider, t.table.model, t.pricing.inputPrice, t.pricing.outputPrice, t.pricing.active, t.pricing.updatedAt, t.table.actions].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {pricing.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-400">{t.noData}</td></tr>
              )}
              {pricing.map(row => {
                const edits = pricingEdits[row.pricing_id] || {};
                const inputVal  = edits.input_price_per_1m_tokens  ?? row.input_price_per_1m_tokens;
                const outputVal = edits.output_price_per_1m_tokens ?? row.output_price_per_1m_tokens;
                const activeVal = edits.active ?? row.active;
                const isSaved   = savedPricing[row.pricing_id];
                return (
                  <tr key={row.pricing_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-slate-500">{row.provider}</td>
                    <td className="px-4 py-3 font-medium text-slate-700">{row.model}</td>
                    <td className="px-4 py-3">
                      <input type="number" step="0.001" value={inputVal}
                        onChange={e => setPricingEdits(prev => ({
                          ...prev,
                          [row.pricing_id]: { ...prev[row.pricing_id], input_price_per_1m_tokens: parseFloat(e.target.value) },
                        }))}
                        className="w-24 border border-slate-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300" />
                    </td>
                    <td className="px-4 py-3">
                      <input type="number" step="0.001" value={outputVal}
                        onChange={e => setPricingEdits(prev => ({
                          ...prev,
                          [row.pricing_id]: { ...prev[row.pricing_id], output_price_per_1m_tokens: parseFloat(e.target.value) },
                        }))}
                        className="w-24 border border-slate-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300" />
                    </td>
                    <td className="px-4 py-3">
                      <input type="checkbox" checked={activeVal}
                        onChange={e => setPricingEdits(prev => ({
                          ...prev,
                          [row.pricing_id]: { ...prev[row.pricing_id], active: e.target.checked },
                        }))}
                        className="w-4 h-4 accent-indigo-600" />
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {row.updated_at ? new Date(row.updated_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <button onClick={() => handleSavePricing(row)}
                        className={`px-3 py-1 rounded text-xs font-medium ${isSaved ? 'bg-emerald-100 text-emerald-700' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}>
                        {isSaved ? t.pricing.saved : t.pricing.save}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Metadata drawer */}
      {expandedLog && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-slate-800">{t.logs.metadata}</h3>
              <button onClick={() => setExpandedLog(null)}
                className="text-slate-400 hover:text-slate-600 text-sm">{t.logs.close}</button>
            </div>
            <pre className="bg-slate-50 rounded-lg p-4 text-xs overflow-auto max-h-96 text-slate-700">
              {JSON.stringify(expandedLog.metadata, null, 2)}
            </pre>
            <p className="text-xs text-slate-400">usage_id: {expandedLog.usage_id}</p>
          </div>
        </div>
      )}
    </div>
  );
};
