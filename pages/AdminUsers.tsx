
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, AdminUser } from '../types';

interface AdminUsersProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

export const AdminUsers: React.FC<AdminUsersProps> = ({ auth, addToast }) => {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [search, setSearch] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const response = await apiService.get(WEBHOOK_CONFIG.ADMIN_USERS_URL, {
        page: page.toString(),
        limit: '15',
        search,
        role,
        status
      }, auth.token!);
      
      setUsers(response.users || []);
      setTotalPages(response.total_pages || 1);
    } catch (err: any) {
      addToast(err.message || "Failed to load users.", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchUsers();
    }, 300); // Debounce search
    return () => clearTimeout(timer);
  }, [page, search, role, status]);

  const toggleUserStatus = async (user: AdminUser) => {
    const newStatus = user.status === 'active' ? 'disabled' : 'active';
    setActionLoading(user.user_id);
    try {
      await apiService.post(WEBHOOK_CONFIG.ADMIN_USER_STATUS_URL, {
        user_id: user.user_id,
        status: newStatus
      }, auth.token!);
      
      addToast(`User ${user.email} is now ${newStatus}.`, "success");
      fetchUsers();
    } catch (err: any) {
      addToast(err.message || "Failed to update user status.", "error");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Filters Header */}
      <div className="bg-white p-6 rounded-2xl border border-border shadow-sm flex flex-wrap gap-4 items-end">
        <div className="flex-1 min-w-[200px] space-y-1.5">
          <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Search Users</label>
          <div className="relative">
            <input
              type="text"
              placeholder="Email, name..."
              className="w-full pl-10 pr-4 py-2 border border-border rounded-xl text-sm focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-600 outline-none transition-all"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <svg className="w-4 h-4 text-textMuted absolute left-3.5 top-1/2 -translate-y-1/2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
        </div>
        <div className="w-full sm:w-40 space-y-1.5">
          <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Role</label>
          <select
            className="w-full px-4 py-2 border border-border rounded-xl text-sm bg-white outline-none focus:border-indigo-600"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="">All Roles</option>
            <option value="super_admin">Super Admin</option>
            <option value="admin">Admin</option>
            <option value="user">User</option>
          </select>
        </div>
        <div className="w-full sm:w-40 space-y-1.5">
          <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Status</label>
          <select
            className="w-full px-4 py-2 border border-border rounded-xl text-sm bg-white outline-none focus:border-indigo-600"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">All Statuses</option>
            <option value="active">Active</option>
            <option value="disabled">Disabled</option>
          </select>
        </div>
      </div>

      {/* Users Table */}
      <div className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 space-y-4">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
            <p className="text-textMuted font-bold uppercase tracking-widest text-[10px]">Filtering users...</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left min-w-[900px]">
                <thead className="bg-slate-50 border-b border-border">
                  <tr>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">User Details</th>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Role</th>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Status</th>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Tenant</th>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest">Last Login</th>
                    <th className="px-6 py-4 text-[10px] font-black text-textMuted uppercase tracking-widest text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {users.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-6 py-12 text-center text-textMuted text-sm italic">
                        No users match your criteria.
                      </td>
                    </tr>
                  ) : (
                    users.map((user) => (
                      <tr key={user.user_id} className="hover:bg-slate-50 transition-colors">
                        <td className="px-6 py-4">
                          <div className="text-sm font-bold text-textMain">{user.full_name || 'N/A'}</div>
                          <div className="text-xs text-textMuted">{user.email}</div>
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-0.5 rounded text-[9px] font-black uppercase tracking-widest ${
                            user.role?.toLowerCase().includes('admin') ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-700'
                          }`}>
                            {user.role}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-black uppercase tracking-tighter ${
                            user.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                          }`}>
                            {user.status}
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <div className="text-xs font-bold text-textMain">{user.tenant_name}</div>
                          <div className="text-[9px] text-textMuted font-black uppercase tracking-tighter">{user.tenant_code}</div>
                        </td>
                        <td className="px-6 py-4 text-xs font-medium text-textMuted">
                          {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : 'Never'}
                        </td>
                        <td className="px-6 py-4 text-right">
                          <button
                            disabled={actionLoading === user.user_id}
                            onClick={() => toggleUserStatus(user)}
                            className={`text-xs font-bold px-4 py-1.5 rounded-lg border transition-all ${
                              user.status === 'active' 
                              ? 'border-error text-error hover:bg-error hover:text-white' 
                              : 'border-success text-success hover:bg-success hover:text-white'
                            } disabled:opacity-50`}
                          >
                            {actionLoading === user.user_id ? '...' : user.status === 'active' ? 'Disable' : 'Activate'}
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="px-6 py-4 bg-slate-50 border-t border-border flex items-center justify-between">
              <span className="text-xs text-textMuted font-medium">Page {page} of {totalPages}</span>
              <div className="flex space-x-2">
                <button
                  disabled={page <= 1}
                  onClick={() => setPage(p => p - 1)}
                  className="px-3 py-1.5 rounded-lg border border-border bg-white text-xs font-bold text-textMain hover:bg-slate-100 transition-colors disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  disabled={page >= totalPages}
                  onClick={() => setPage(p => p + 1)}
                  className="px-3 py-1.5 rounded-lg border border-border bg-white text-xs font-bold text-textMain hover:bg-slate-100 transition-colors disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
