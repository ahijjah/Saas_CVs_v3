
import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';

interface TenantFeaturesProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

interface TenantRow {
  tenant_id: string;
  tenant_name: string;
  tenant_code: string;
  status: string;
  ai_recruitment_enabled: boolean;
  ats_management_enabled: boolean;
  enabled_features_count: number;
  configured_features_count: number;
}

interface Summary {
  total_tenants: number;
  ai_enabled: number;
  ats_enabled: number;
  custom_configs: number;
}

interface FeatureItem {
  feature_code: string;
  is_enabled: boolean;
}

interface ModuleDetail {
  module_code: string;
  module_enabled: boolean;
  features: FeatureItem[];
  enabled_count: number;
  total_count: number;
}

interface TenantDetail {
  tenant_id: string;
  tenant_name: string;
  tenant_code: string;
  status: string;
  modules: ModuleDetail[];
}

const FEATURE_LABELS: Record<string, string> = {
  AI_RECRUITMENT:   'AI Recruitment',
  AI_JOB_ANALYSIS:  'Job Analysis',
  AI_SCORING:       'AI Scoring',
  AI_KNOCKOUTS:     'Knockout Questions',
  AI_EXPLAINABILITY: 'Explainability',
  AI_COMPARISON:    'Candidate Comparison',
  AI_BULK_UPLOAD:   'Bulk Upload',
  AI_INTAKE_EMAIL:  'Email Intake',
  AI_INTAKE_PUBLIC: 'Public Apply',
  AI_INTAKE_MANUAL: 'Manual Upload',
  ATS_MANAGEMENT:   'ATS Management',
  ATS_WORKFLOWS:    'Workflows',
  ATS_NOTES:        'Recruiter Notes',
  ATS_COMMUNICATIONS: 'Communications',
  ATS_ASSIGNMENTS:  'Assignments',
  ATS_EMAIL_TEMPLATES: 'Email Templates',
  ATS_REPORTING:    'Reporting',
};

const MODULE_COLORS: Record<string, { bg: string; text: string; badge: string }> = {
  AI_RECRUITMENT: { bg: 'bg-indigo-50', text: 'text-indigo-700', badge: 'bg-indigo-100 text-indigo-800' },
  ATS_MANAGEMENT: { bg: 'bg-teal-50',   text: 'text-teal-700',   badge: 'bg-teal-100 text-teal-800'   },
};

function ToggleSwitch({ enabled, loading, onChange }: { enabled: boolean; loading: boolean; onChange: () => void }) {
  return (
    <button
      onClick={onChange}
      disabled={loading}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
        enabled ? 'bg-primary' : 'bg-slate-300'
      }`}
    >
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${
        enabled ? 'translate-x-5' : 'translate-x-1'
      }`} />
    </button>
  );
}

export const TenantFeaturesPage: React.FC<TenantFeaturesProps> = ({ auth, addToast }) => {
  const BASE = WEBHOOK_CONFIG.PLATFORM_FEATURES_BASE_URL;
  const token = auth.token!;

  const [summary, setSummary] = useState<Summary | null>(null);
  const [tenants, setTenants] = useState<TenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [totalTenants, setTotalTenants] = useState(0);
  const PAGE_SIZE = 20;

  // Detail drawer state
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TenantDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [togglingCode, setTogglingCode] = useState<string | null>(null);

  const fetchOverview = useCallback(async (searchVal = search, pageVal = page) => {
    setLoading(true);
    try {
      const resp = await apiService.get(
        `${BASE}/tenants/features`,
        { search: searchVal, page: String(pageVal), page_size: String(PAGE_SIZE) },
        token,
      );
      setSummary(resp.summary);
      setTenants(resp.tenants || []);
      setTotalTenants(resp.pagination?.total || 0);
    } catch (err: any) {
      addToast(err.message || 'Failed to load tenant features', 'error');
    } finally {
      setLoading(false);
    }
  }, [BASE, token, search, page, addToast]);

  useEffect(() => { fetchOverview(); }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchOverview(search, 1);
  };

  const openDetail = async (tenantId: string) => {
    if (expandedId === tenantId) { setExpandedId(null); setDetail(null); return; }
    setExpandedId(tenantId);
    setDetail(null);
    setDetailLoading(true);
    try {
      const resp = await apiService.get(`${BASE}/tenants/${tenantId}/features`, {}, token);
      setDetail(resp);
    } catch (err: any) {
      addToast(err.message || 'Failed to load tenant detail', 'error');
      setExpandedId(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const toggleFeature = async (tenantId: string, code: string, currentlyEnabled: boolean) => {
    const action = currentlyEnabled ? 'disable' : 'enable';
    setTogglingCode(code);
    try {
      await apiService.post(`${BASE}/tenants/${tenantId}/features/${code}/${action}`, {}, token);
      addToast(`${FEATURE_LABELS[code] || code} ${action}d`, 'success');
      // Refresh detail + overview
      const [resp] = await Promise.all([
        apiService.get(`${BASE}/tenants/${tenantId}/features`, {}, token),
        fetchOverview(),
      ]);
      setDetail(resp);
    } catch (err: any) {
      addToast(err.message || 'Toggle failed', 'error');
    } finally {
      setTogglingCode(null);
    }
  };

  const toggleModule = async (tenantId: string, code: string, currentlyEnabled: boolean) => {
    const action = currentlyEnabled ? 'disable' : 'enable';
    setTogglingCode(code);
    try {
      await apiService.post(`${BASE}/tenants/${tenantId}/modules/${code}/${action}`, {}, token);
      addToast(`${FEATURE_LABELS[code] || code} module ${action}d`, 'success');
      const [resp] = await Promise.all([
        apiService.get(`${BASE}/tenants/${tenantId}/features`, {}, token),
        fetchOverview(),
      ]);
      setDetail(resp);
    } catch (err: any) {
      addToast(err.message || 'Module toggle failed', 'error');
    } finally {
      setTogglingCode(null);
    }
  };

  const totalPages = Math.ceil(totalTenants / PAGE_SIZE);

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Total Tenants',         value: summary?.total_tenants  ?? 0, color: 'text-indigo-600',   icon: '🏢' },
          { label: 'AI Recruitment On',      value: summary?.ai_enabled     ?? 0, color: 'text-primary',      icon: '🤖' },
          { label: 'ATS Management On',      value: summary?.ats_enabled    ?? 0, color: 'text-teal-600',     icon: '📋' },
          { label: 'Custom Configurations',  value: summary?.custom_configs ?? 0, color: 'text-amber-600',    icon: '⚙️' },
        ].map((s, i) => (
          <div key={i} className="bg-white p-5 rounded-2xl border border-border shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xl">{s.icon}</span>
              <span className={`text-2xl font-black ${s.color}`}>{s.value}</span>
            </div>
            <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Search + table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-black text-textMain uppercase tracking-widest flex-1">Tenant Feature Configuration</h3>
          <form onSubmit={handleSearch} className="flex gap-2">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search tenants…"
              className="text-sm border border-border rounded-lg px-3 py-1.5 text-textMain focus:outline-none focus:ring-1 focus:ring-primary w-52"
            />
            <button
              type="submit"
              className="text-xs font-bold px-3 py-1.5 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
            >
              Search
            </button>
          </form>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-40 space-x-3">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary" />
            <span className="text-textMuted text-sm font-bold uppercase tracking-widest">Loading…</span>
          </div>
        ) : tenants.length === 0 ? (
          <div className="text-center py-12 text-textMuted text-sm font-bold">No tenants found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left min-w-[700px]">
              <thead className="bg-slate-50 border-b border-border">
                <tr>
                  <th className="px-6 py-3 text-[10px] font-black text-textMuted uppercase tracking-widest">Organization</th>
                  <th className="px-6 py-3 text-[10px] font-black text-textMuted uppercase tracking-widest text-center">AI Recruitment</th>
                  <th className="px-6 py-3 text-[10px] font-black text-textMuted uppercase tracking-widest text-center">ATS Management</th>
                  <th className="px-6 py-3 text-[10px] font-black text-textMuted uppercase tracking-widest text-center">Features Enabled</th>
                  <th className="px-6 py-3 text-[10px] font-black text-textMuted uppercase tracking-widest text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {tenants.map(t => (
                  <React.Fragment key={t.tenant_id}>
                    <tr className="hover:bg-slate-50 transition-colors cursor-pointer" onClick={() => openDetail(t.tenant_id)}>
                      <td className="px-6 py-4">
                        <div className="text-sm font-bold text-textMain">{t.tenant_name}</div>
                        <div className="text-[10px] font-bold text-textMuted uppercase tracking-tighter">
                          {t.tenant_code} · <span className={t.status === 'active' ? 'text-success' : 'text-error'}>{t.status}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 text-center">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${
                          t.ai_recruitment_enabled ? 'bg-indigo-100 text-indigo-800' : 'bg-slate-100 text-slate-500'
                        }`}>
                          {t.ai_recruitment_enabled ? '✓ On' : '✗ Off'}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-center">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase ${
                          t.ats_management_enabled ? 'bg-teal-100 text-teal-800' : 'bg-slate-100 text-slate-500'
                        }`}>
                          {t.ats_management_enabled ? '✓ On' : '✗ Off'}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-center text-sm font-bold text-textMuted">
                        {t.enabled_features_count > 0
                          ? `${t.enabled_features_count} / ${t.configured_features_count}`
                          : 'All (default)'}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <button
                          onClick={e => { e.stopPropagation(); openDetail(t.tenant_id); }}
                          className="text-xs font-bold text-primary hover:underline"
                        >
                          {expandedId === t.tenant_id ? 'Collapse ▲' : 'Manage →'}
                        </button>
                      </td>
                    </tr>

                    {/* Expanded feature detail */}
                    {expandedId === t.tenant_id && (
                      <tr>
                        <td colSpan={5} className="bg-slate-50 border-t border-border px-6 py-5">
                          {detailLoading || !detail ? (
                            <div className="flex items-center gap-3 text-textMuted text-sm">
                              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-primary" />
                              Loading feature details…
                            </div>
                          ) : (
                            <div className="grid md:grid-cols-2 gap-4">
                              {detail.modules.map(mod => {
                                const colors = MODULE_COLORS[mod.module_code] || { bg: 'bg-slate-50', text: 'text-slate-700', badge: 'bg-slate-100 text-slate-700' };
                                return (
                                  <div key={mod.module_code} className={`rounded-xl border border-border ${colors.bg} p-4`}>
                                    {/* Module header */}
                                    <div className="flex items-center justify-between mb-3">
                                      <div>
                                        <span className={`text-xs font-black uppercase tracking-widest ${colors.text}`}>
                                          {FEATURE_LABELS[mod.module_code] || mod.module_code}
                                        </span>
                                        <span className={`ml-2 px-1.5 py-0.5 rounded text-[9px] font-black uppercase ${colors.badge}`}>
                                          {mod.enabled_count}/{mod.total_count} features
                                        </span>
                                      </div>
                                      <ToggleSwitch
                                        enabled={mod.module_enabled}
                                        loading={togglingCode === mod.module_code}
                                        onChange={() => toggleModule(detail.tenant_id, mod.module_code, mod.module_enabled)}
                                      />
                                    </div>

                                    {/* Feature list */}
                                    <div className="space-y-1.5">
                                      {mod.features.map(feat => (
                                        <div key={feat.feature_code} className="flex items-center justify-between bg-white/70 rounded-lg px-3 py-1.5">
                                          <span className="text-xs font-medium text-textMuted">
                                            {FEATURE_LABELS[feat.feature_code] || feat.feature_code}
                                          </span>
                                          <ToggleSwitch
                                            enabled={feat.is_enabled}
                                            loading={togglingCode === feat.feature_code}
                                            onChange={() => toggleFeature(detail.tenant_id, feat.feature_code, feat.is_enabled)}
                                          />
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-6 py-4 border-t border-border flex items-center justify-between">
            <span className="text-xs text-textMuted font-bold">
              Page {page} of {totalPages} ({totalTenants} tenants)
            </span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => { const p = page - 1; setPage(p); fetchOverview(search, p); }}
                className="text-xs font-bold px-3 py-1.5 border border-border rounded-lg hover:bg-slate-50 disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => { const p = page + 1; setPage(p); fetchOverview(search, p); }}
                className="text-xs font-bold px-3 py-1.5 border border-border rounded-lg hover:bg-slate-50 disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* No enforcement notice */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl px-5 py-3 flex items-start gap-3">
        <span className="text-amber-500 text-lg mt-0.5">ℹ️</span>
        <div>
          <p className="text-xs font-black text-amber-800 uppercase tracking-widest">Configuration Mode Only</p>
          <p className="text-xs text-amber-700 mt-0.5">
            Feature flags are currently in configuration-only mode. Disabling a feature does not yet restrict access for tenant users.
            Enforcement will be activated in a future release.
          </p>
        </div>
      </div>
    </div>
  );
};
