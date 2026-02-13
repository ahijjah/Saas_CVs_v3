
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, AdminOverview, Tenant } from '../types';

interface AdminDashboardProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

export const AdminDashboard: React.FC<AdminDashboardProps> = ({ auth, addToast }) => {
  const [data, setData] = useState<{ overview: AdminOverview; tenants: Tenant[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchDashboard = async () => {
    setLoading(true);
    try {
      const response = await apiService.get(WEBHOOK_CONFIG.ADMIN_DASHBOARD_URL, {}, auth.token!);
      setData(response);
    } catch (err: any) {
      addToast(err.message || "Failed to load admin dashboard.", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboard();
  }, [auth.token]);

  const toggleTenantStatus = async (tenant: Tenant) => {
    const newStatus = tenant.status === 'active' ? 'suspended' : 'active';
    setActionLoading(tenant.tenant_id);
    try {
      await apiService.post(WEBHOOK_CONFIG.ADMIN_TENANT_STATUS_URL, {
        tenant_id: tenant.tenant_id,
        status: newStatus
      }, auth.token!);
      
      addToast(`Tenant ${tenant.tenant_name} is now ${newStatus}.`, "success");
      fetchDashboard();
    } catch (err: any) {
      addToast(err.message || "Failed to update tenant status.", "error");
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600"></div>
        <p className="text-textMuted animate-pulse font-bold uppercase tracking-widest text-xs">Loading system overview...</p>
      </div>
    );
  }

  const stats = data?.overview;

  return (
    <div className="space-y-8">
      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6">
        {[
          { label: 'Total Tenants', value: stats?.total_tenants, color: 'text-indigo-600', icon: '🏢' },
          { label: 'Active Users', value: stats?.total_users, color: 'text-primary', icon: '👤' },
          { label: 'Total Applications', value: stats?.total_applications, color: 'text-success', icon: '📄' },
          { label: 'Ingest Failures (24h)', value: stats?.failed_ingest_24h, color: stats?.failed_ingest_24h ? 'text-error' : 'text-textMuted', icon: '⚠️' }
        ].map((stat, i) => (
          <div key={i} className="bg-white p-5 sm:p-6 rounded-2xl border border-border shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xl">{stat.icon}</span>
              <span className={`text-2xl font-black ${stat.color}`}>{stat.value ?? 0}</span>
            </div>
            <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{stat.label}</p>
          </div>
        ))}
      </div>

      {/* Tenants Table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-6 py-4 border-b border-border flex justify-between items-center">
          <h3 className="text-sm font-black text-textMain uppercase tracking-widest">Platform Tenants</h3>
          <span className="text-xs text-textMuted font-bold">{data?.tenants?.length || 0} Registered Organizations</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left min-w-[800px]">
            <thead className="bg-slate-50 border-b border-border">
              <tr>
                <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Organization</th>
                <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Status</th>
                <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest text-center">Resources</th>
                <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Joined</th>
                <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data?.tenants?.map((tenant) => (
                <tr key={tenant.tenant_id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-6 py-4">
                    <div className="text-sm font-bold text-textMain">{tenant.tenant_name}</div>
                    <div className="text-[10px] font-bold text-textMuted uppercase tracking-tighter">Code: {tenant.tenant_code}</div>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-tighter ${
                      tenant.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                    }`}>
                      {tenant.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center justify-center space-x-3">
                      <div className="text-center">
                        <div className="text-xs font-bold text-textMain">{tenant.users_count}</div>
                        <div className="text-[8px] font-black text-textMuted uppercase">Users</div>
                      </div>
                      <div className="text-center">
                        <div className="text-xs font-bold text-textMain">{tenant.jobs_count}</div>
                        <div className="text-[8px] font-black text-textMuted uppercase">Jobs</div>
                      </div>
                      <div className="text-center">
                        <div className="text-xs font-bold text-textMain">{tenant.applications_count}</div>
                        <div className="text-[8px] font-black text-textMuted uppercase">Apps</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-xs font-medium text-textMuted">
                    {new Date(tenant.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button
                      disabled={actionLoading === tenant.tenant_id}
                      onClick={() => toggleTenantStatus(tenant)}
                      className={`text-xs font-bold px-4 py-1.5 rounded-lg border transition-all ${
                        tenant.status === 'active' 
                        ? 'border-error text-error hover:bg-error hover:text-white' 
                        : 'border-success text-success hover:bg-success hover:text-white'
                      } disabled:opacity-50`}
                    >
                      {actionLoading === tenant.tenant_id ? 'Wait...' : tenant.status === 'active' ? 'Suspend' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
