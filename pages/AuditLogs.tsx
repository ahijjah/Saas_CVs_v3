import { useState, useEffect, useCallback } from 'react';
import { WEBHOOK_CONFIG } from '../config';
import { AuditLog } from '../types';

const T = {
  en: {
    title: 'Audit Logs',
    subtitle: 'Full audit trail of all admin and system actions',
    search: 'Filter by action…',
    searchEmail: 'Filter by user email…',
    resourceType: 'Resource type',
    allTypes: 'All types',
    refresh: 'Refresh',
    action: 'Action',
    user: 'User',
    tenant: 'Tenant',
    resource: 'Resource',
    when: 'When',
    details: 'Details',
    viewDetails: 'View',
    closeModal: 'Close',
    noLogs: 'No audit logs found.',
    page: 'Page',
    of: 'of',
    prev: 'Previous',
    next: 'Next',
    loading: 'Loading…',
    detailsModal: 'Log Details',
    logId: 'Log ID',
    ip: 'IP Address',
    detailsJson: 'Details',
  },
};

const RESOURCE_TYPES = [
  'job', 'application', 'user', 'tenant', 'platform_config',
  'platform_secret', 'ai_prompt', 'subscription',
];

export default function AuditLogs() {
  const lang = 'en';
  const t = T[lang];

  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [actionFilter, setActionFilter] = useState('');
  const [emailFilter, setEmailFilter] = useState('');
  const [resourceTypeFilter, setResourceTypeFilter] = useState('');
  const [selected, setSelected] = useState<AuditLog | null>(null);

  const fetchLogs = useCallback(async (pg = page) => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const params = new URLSearchParams({ page: String(pg) });
      if (actionFilter)      params.set('action', actionFilter);
      if (emailFilter)       params.set('user_email', emailFilter);
      if (resourceTypeFilter) params.set('resource_type', resourceTypeFilter);

      const res = await fetch(`${WEBHOOK_CONFIG.AUDIT_LOGS_URL}?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await res.json();
      if (data.success) {
        setLogs(data.logs ?? []);
        setTotal(data.total ?? 0);
        setPages(data.pages ?? 1);
      }
    } catch (err) {
      console.error('Failed to load audit logs', err);
    } finally {
      setLoading(false);
    }
  }, [page, actionFilter, emailFilter, resourceTypeFilter]);

  useEffect(() => { fetchLogs(1); setPage(1); }, [actionFilter, emailFilter, resourceTypeFilter]);
  useEffect(() => { fetchLogs(page); }, [page]);

  function formatAction(action: string) {
    return action.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  function formatDate(iso: string | null) {
    if (!iso) return '—';
    return new Date(iso).toLocaleString('en-GB', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  const actionBadgeColor = (action: string) => {
    if (action.startsWith('delete') || action.startsWith('remove')) return 'bg-red-100 text-red-700';
    if (action.startsWith('create') || action.startsWith('add'))    return 'bg-green-100 text-green-700';
    if (action.startsWith('update') || action.startsWith('change')) return 'bg-blue-100 text-blue-700';
    return 'bg-gray-100 text-gray-700';
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">{t.title}</h1>
        <p className="text-sm text-gray-500 mt-1">{t.subtitle}</p>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 mb-4 flex flex-wrap gap-3 items-end">
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t.search}</label>
          <input
            type="text"
            value={actionFilter}
            onChange={e => setActionFilter(e.target.value)}
            placeholder="e.g. create_job"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t.searchEmail}</label>
          <input
            type="text"
            value={emailFilter}
            onChange={e => setEmailFilter(e.target.value)}
            placeholder="user@example.com"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="min-w-[160px]">
          <label className="block text-xs font-medium text-gray-600 mb-1">{t.resourceType}</label>
          <select
            value={resourceTypeFilter}
            onChange={e => setResourceTypeFilter(e.target.value)}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{t.allTypes}</option>
            {RESOURCE_TYPES.map(rt => (
              <option key={rt} value={rt}>{rt.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => fetchLogs(page)}
          className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          {t.refresh}
        </button>
      </div>

      {/* Total */}
      <div className="text-xs text-gray-500 mb-3">{total.toLocaleString()} records</div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-gray-400 text-sm">{t.loading}</div>
        ) : logs.length === 0 ? (
          <div className="p-10 text-center text-gray-400 text-sm">{t.noLogs}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t.action}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t.user}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t.tenant}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t.resource}</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">{t.when}</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {logs.map(log => (
                  <tr key={log.log_id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${actionBadgeColor(log.action)}`}>
                        {formatAction(log.action)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-700">
                      {log.user_email || <span className="text-gray-400">system</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      {log.tenant_name || <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      {log.resource_type && (
                        <span className="font-medium text-gray-700">{log.resource_type}</span>
                      )}
                      {log.resource_id && (
                        <span className="ml-1 text-gray-400 font-mono truncate max-w-[80px] inline-block align-middle">
                          {log.resource_id.length > 12 ? log.resource_id.slice(0, 8) + '…' : log.resource_id}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">
                      {formatDate(log.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => setSelected(log)}
                        className="text-xs text-primary hover:underline font-medium"
                      >
                        {t.viewDetails}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <span className="text-gray-500">{t.page} {page} {t.of} {pages}</span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              {t.prev}
            </button>
            <button
              onClick={() => setPage(p => Math.min(pages, p + 1))}
              disabled={page === pages}
              className="px-3 py-1.5 border border-gray-300 rounded-lg text-gray-700 disabled:opacity-40 hover:bg-gray-50 transition-colors"
            >
              {t.next}
            </button>
          </div>
        </div>
      )}

      {/* Details Modal */}
      {selected && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h2 className="text-base font-semibold text-gray-900">{t.detailsModal}</h2>
              <button
                onClick={() => setSelected(null)}
                className="text-gray-400 hover:text-gray-600 text-xl leading-none"
              >
                ×
              </button>
            </div>
            <div className="overflow-y-auto p-6 space-y-3 text-sm">
              <Row label={t.logId}    value={selected.log_id} mono />
              <Row label={t.action}   value={formatAction(selected.action)} />
              <Row label={t.user}     value={selected.user_email || 'system'} />
              <Row label={t.tenant}   value={selected.tenant_name || selected.tenant_id || '—'} />
              <Row label={t.resource} value={`${selected.resource_type || '—'} / ${selected.resource_id || '—'}`} />
              <Row label={t.ip}       value={selected.ip_address || '—'} />
              <Row label={t.when}     value={formatDate(selected.created_at)} />
              {selected.details && (
                <div>
                  <div className="text-xs font-medium text-gray-500 mb-1">{t.detailsJson}</div>
                  <pre className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-xs overflow-x-auto whitespace-pre-wrap text-gray-700">
                    {JSON.stringify(selected.details, null, 2)}
                  </pre>
                </div>
              )}
            </div>
            <div className="px-6 py-4 border-t border-gray-200 flex justify-end">
              <button
                onClick={() => setSelected(null)}
                className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-200 transition-colors"
              >
                {t.closeModal}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className="text-gray-500 min-w-[110px] shrink-0">{label}</span>
      <span className={`text-gray-900 break-all ${mono ? 'font-mono text-xs' : ''}`}>{value}</span>
    </div>
  );
}
