
import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { ClientOrganization, AgencyUserClient, AuthState } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface ClientOrganizationsProps {
  auth: AuthState;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

const T = {
  en: {
    title: 'Client Organisations',
    sub: 'Manage the companies you recruit on behalf of',
    addClient: 'Add Client',
    loading: 'Loading...',
    noClients: 'No client organisations yet. Add your first client to get started.',
    statusActive: 'Active',
    statusInactive: 'Inactive',
    activeJobs: 'Active Jobs',
    assignedUsers: 'Assigned Users',
    industry: 'Industry',
    contact: 'Contact',
    email: 'Email',
    phone: 'Phone',
    notes: 'Notes',
    editBtn: 'Edit',
    deactivateBtn: 'Deactivate',
    manageUsers: 'Manage Users',
    saveBtn: 'Save',
    cancelBtn: 'Cancel',
    savingBtn: 'Saving...',
    orgName: 'Organisation Name',
    orgNameRequired: 'Organisation name is required',
    modalAddTitle: 'Add Client Organisation',
    modalEditTitle: 'Edit Client Organisation',
    modalUsersTitle: 'Assigned Users',
    assignUser: 'Assign User',
    removeUser: 'Remove',
    roleForClient: 'Role',
    roleAccountManager: 'Account Manager',
    roleRecruiter: 'Recruiter',
    roleViewer: 'Viewer',
    selectUser: 'Select user...',
    noUsers: 'No users assigned to this client yet.',
    confirmDeactivate: 'Deactivate this client organisation? Linked jobs will not be deleted.',
    createdSuccess: 'Client organisation created.',
    updatedSuccess: 'Client organisation updated.',
    deactivatedSuccess: 'Client organisation deactivated.',
    userAssigned: 'User assigned.',
    userRemoved: 'User removed.',
    errorLoad: 'Failed to load client organisations.',
  },
  ar: {
    title: 'منظمات العملاء',
    sub: 'إدارة الشركات التي توظف نيابة عنها',
    addClient: 'إضافة عميل',
    loading: 'جارٍ التحميل...',
    noClients: 'لا توجد منظمات عملاء بعد. أضف أول عميل للبدء.',
    statusActive: 'نشط',
    statusInactive: 'غير نشط',
    activeJobs: 'وظائف نشطة',
    assignedUsers: 'مستخدمون معيّنون',
    industry: 'القطاع',
    contact: 'جهة الاتصال',
    email: 'البريد',
    phone: 'الهاتف',
    notes: 'ملاحظات',
    editBtn: 'تعديل',
    deactivateBtn: 'تعطيل',
    manageUsers: 'إدارة المستخدمين',
    saveBtn: 'حفظ',
    cancelBtn: 'إلغاء',
    savingBtn: 'جارٍ الحفظ...',
    orgName: 'اسم المنظمة',
    orgNameRequired: 'اسم المنظمة مطلوب',
    modalAddTitle: 'إضافة منظمة عميل',
    modalEditTitle: 'تعديل منظمة عميل',
    modalUsersTitle: 'المستخدمون المعيّنون',
    assignUser: 'تعيين مستخدم',
    removeUser: 'إزالة',
    roleForClient: 'الدور',
    roleAccountManager: 'مدير الحساب',
    roleRecruiter: 'مسؤول توظيف',
    roleViewer: 'مشاهد',
    selectUser: 'اختر مستخدماً...',
    noUsers: 'لم يتم تعيين أي مستخدمين لهذا العميل بعد.',
    confirmDeactivate: 'تعطيل هذه المنظمة؟ لن يتم حذف الوظائف المرتبطة.',
    createdSuccess: 'تم إنشاء منظمة العميل.',
    updatedSuccess: 'تم تحديث منظمة العميل.',
    deactivatedSuccess: 'تم تعطيل منظمة العميل.',
    userAssigned: 'تم تعيين المستخدم.',
    userRemoved: 'تم إزالة المستخدم.',
    errorLoad: 'فشل تحميل منظمات العملاء.',
  },
};

const EMPTY_FORM = {
  organization_name: '',
  industry: '',
  contact_person: '',
  email: '',
  phone: '',
  logo_url: '',
  notes: '',
};

export const ClientOrganizationsPage: React.FC<ClientOrganizationsProps> = ({ auth, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];

  const isAdmin = auth.user?.role === 'admin' || auth.user?.role === 'super_admin';
  const canWrite = isAdmin || auth.user?.role === 'hr_manager';

  const [clients, setClients] = useState<ClientOrganization[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantUsers, setTenantUsers] = useState<any[]>([]);

  // Modal state
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState<ClientOrganization | null>(null);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // Users modal
  const [usersModalClient, setUsersModalClient] = useState<ClientOrganization | null>(null);
  const [assignedUsers, setAssignedUsers] = useState<AgencyUserClient[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [assignUserId, setAssignUserId] = useState('');
  const [assignRole, setAssignRole] = useState<string>('recruiter');
  const [assigning, setAssigning] = useState(false);

  const fetchClients = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, auth.token!);
      setClients(data.client_organizations || []);
    } catch {
      addToast(t.errorLoad, 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, t]);

  const fetchTenantUsers = useCallback(async () => {
    try {
      const data = await apiService.get(WEBHOOK_CONFIG.TENANT_USERS_URL, {}, auth.token!);
      setTenantUsers(data.users || []);
    } catch {}
  }, [auth.token]);

  useEffect(() => {
    fetchClients();
    if (isAdmin) fetchTenantUsers();
  }, [fetchClients, fetchTenantUsers, isAdmin]);

  const openAdd = () => {
    setEditTarget(null);
    setForm({ ...EMPTY_FORM });
    setFormError('');
    setShowForm(true);
  };

  const openEdit = (org: ClientOrganization) => {
    setEditTarget(org);
    setForm({
      organization_name: org.organization_name,
      industry:          org.industry || '',
      contact_person:    org.contact_person || '',
      email:             org.email || '',
      phone:             org.phone || '',
      logo_url:          org.logo_url || '',
      notes:             org.notes || '',
    });
    setFormError('');
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!form.organization_name.trim()) {
      setFormError(t.orgNameRequired);
      return;
    }
    setSaving(true);
    setFormError('');
    try {
      const payload = {
        organization_name: form.organization_name.trim(),
        industry:          form.industry || null,
        contact_person:    form.contact_person || null,
        email:             form.email || null,
        phone:             form.phone || null,
        logo_url:          form.logo_url || null,
        notes:             form.notes || null,
      };
      if (editTarget) {
        await apiService.patch(
          `${WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL}/${editTarget.client_organization_id}`,
          payload,
          auth.token!,
        );
        addToast(t.updatedSuccess, 'success');
      } else {
        await apiService.post(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, payload, auth.token!);
        addToast(t.createdSuccess, 'success');
      }
      setShowForm(false);
      fetchClients();
    } catch (err: any) {
      setFormError(err?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDeactivate = async (org: ClientOrganization) => {
    if (!window.confirm(t.confirmDeactivate)) return;
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL}/${org.client_organization_id}`,
        auth.token!,
      );
      addToast(t.deactivatedSuccess, 'success');
      fetchClients();
    } catch {
      addToast('Failed to deactivate.', 'error');
    }
  };

  const openUsersModal = async (org: ClientOrganization) => {
    setUsersModalClient(org);
    setAssignUserId('');
    setAssignRole('recruiter');
    setLoadingUsers(true);
    try {
      const data = await apiService.get(
        `${WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL}/${org.client_organization_id}/users`,
        {},
        auth.token!,
      );
      setAssignedUsers(data.users || []);
    } catch {} finally {
      setLoadingUsers(false);
    }
  };

  const handleAssignUser = async () => {
    if (!assignUserId || !usersModalClient) return;
    setAssigning(true);
    try {
      await apiService.post(
        `${WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL}/${usersModalClient.client_organization_id}/users`,
        { user_id: assignUserId, role_for_client: assignRole },
        auth.token!,
      );
      addToast(t.userAssigned, 'success');
      openUsersModal(usersModalClient);
    } catch {
      addToast('Assignment failed.', 'error');
    } finally {
      setAssigning(false);
    }
  };

  const handleRemoveUser = async (userId: string) => {
    if (!usersModalClient) return;
    try {
      await apiService.delete(
        `${WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL}/${usersModalClient.client_organization_id}/users/${userId}`,
        auth.token!,
      );
      addToast(t.userRemoved, 'success');
      openUsersModal(usersModalClient);
    } catch {
      addToast('Remove failed.', 'error');
    }
  };

  const roleLabels: Record<string, string> = {
    account_manager: t.roleAccountManager,
    recruiter:       t.roleRecruiter,
    viewer:          t.roleViewer,
  };

  // Unassigned users for the dropdown
  const assignedIds = assignedUsers.map(u => u.user_id);
  const unassignedUsers = tenantUsers.filter(u => !assignedIds.includes(u.user_id) && u.status === 'active');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-black text-textMain">{t.title}</h1>
          <p className="text-sm text-textMuted mt-0.5">{t.sub}</p>
        </div>
        {isAdmin && (
          <button
            onClick={openAdd}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors shadow-sm"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
            </svg>
            {t.addClient}
          </button>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex justify-center items-center p-12 text-textMuted">
          <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-primary mr-3" />
          {t.loading}
        </div>
      ) : clients.length === 0 ? (
        <div className="bg-white rounded-2xl border border-border p-12 text-center text-textMuted">
          {t.noClients}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {clients.map(org => (
            <div key={org.client_organization_id} className="bg-white rounded-2xl border border-border shadow-sm p-5 flex flex-col gap-3 hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="text-base font-black text-textMain truncate">{org.organization_name}</h3>
                  {org.industry && <p className="text-xs text-textMuted mt-0.5">{org.industry}</p>}
                </div>
                <span className={`shrink-0 text-[9px] font-black px-2 py-0.5 rounded-full uppercase tracking-wider ${
                  org.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'
                }`}>
                  {org.status === 'active' ? t.statusActive : t.statusInactive}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="bg-slate-50 rounded-xl px-3 py-2 text-center">
                  <div className="text-lg font-black text-primary">{org.active_jobs_count ?? 0}</div>
                  <div className="text-[9px] text-textMuted font-bold uppercase tracking-wider">{t.activeJobs}</div>
                </div>
                <div className="bg-slate-50 rounded-xl px-3 py-2 text-center">
                  <div className="text-lg font-black text-indigo-600">{org.assigned_users_count ?? 0}</div>
                  <div className="text-[9px] text-textMuted font-bold uppercase tracking-wider">{t.assignedUsers}</div>
                </div>
              </div>

              {(org.contact_person || org.email || org.phone) && (
                <div className="text-xs text-textMuted space-y-0.5 border-t border-border pt-2">
                  {org.contact_person && <p><span className="font-bold">{t.contact}:</span> {org.contact_person}</p>}
                  {org.email && <p><span className="font-bold">{t.email}:</span> {org.email}</p>}
                  {org.phone && <p><span className="font-bold">{t.phone}:</span> {org.phone}</p>}
                </div>
              )}

              <div className="flex gap-2 flex-wrap mt-auto pt-2 border-t border-border">
                {isAdmin && (
                  <button onClick={() => openUsersModal(org)}
                    className="flex-1 text-center text-[10px] font-bold px-2 py-1.5 border border-indigo-200 text-indigo-700 bg-indigo-50 rounded-lg hover:bg-indigo-100 transition-colors">
                    {t.manageUsers}
                  </button>
                )}
                {canWrite && (
                  <button onClick={() => openEdit(org)}
                    className="flex-1 text-center text-[10px] font-bold px-2 py-1.5 border border-border text-textMain bg-white rounded-lg hover:bg-slate-50 transition-colors">
                    {t.editBtn}
                  </button>
                )}
                {isAdmin && org.status === 'active' && (
                  <button onClick={() => handleDeactivate(org)}
                    className="text-[10px] font-bold px-2 py-1.5 border border-red-200 text-red-600 bg-red-50 rounded-lg hover:bg-red-100 transition-colors">
                    {t.deactivateBtn}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add/Edit Modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
            <div className="px-6 py-4 border-b border-border">
              <h2 className="text-lg font-black text-textMain">
                {editTarget ? t.modalEditTitle : t.modalAddTitle}
              </h2>
            </div>
            <div className="px-6 py-4 space-y-4">
              {[
                ['organization_name', t.orgName, true],
                ['industry', t.industry, false],
                ['contact_person', t.contact, false],
                ['email', t.email, false],
                ['phone', t.phone, false],
                ['logo_url', 'Logo URL', false],
              ].map(([key, label, required]) => (
                <div key={String(key)}>
                  <label className="block text-xs font-bold text-textMuted uppercase tracking-wider mb-1">
                    {String(label)}{required && ' *'}
                  </label>
                  <input
                    type="text"
                    value={(form as any)[key as string]}
                    onChange={e => setForm(f => ({ ...f, [key as string]: e.target.value }))}
                    className="w-full px-3 py-2 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                  />
                </div>
              ))}
              <div>
                <label className="block text-xs font-bold text-textMuted uppercase tracking-wider mb-1">{t.notes}</label>
                <textarea
                  rows={3}
                  value={form.notes}
                  onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                  className="w-full px-3 py-2 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
                />
              </div>
              {formError && <p className="text-sm text-error font-bold">{formError}</p>}
            </div>
            <div className="px-6 py-4 border-t border-border flex gap-3 justify-end">
              <button onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm font-bold border border-border rounded-xl hover:bg-slate-50 transition-colors">
                {t.cancelBtn}
              </button>
              <button onClick={handleSave} disabled={saving}
                className="px-5 py-2 text-sm font-bold bg-primary text-white rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-50">
                {saving ? t.savingBtn : t.saveBtn}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Users Modal */}
      {usersModalClient && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-border flex items-center justify-between">
              <div>
                <h2 className="text-lg font-black text-textMain">{t.modalUsersTitle}</h2>
                <p className="text-xs text-textMuted">{usersModalClient.organization_name}</p>
              </div>
              <button onClick={() => setUsersModalClient(null)}
                className="text-textMuted hover:text-textMain">
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
              {loadingUsers ? (
                <p className="text-center text-textMuted py-6">{t.loading}</p>
              ) : assignedUsers.length === 0 ? (
                <p className="text-center text-textMuted py-6 italic text-sm">{t.noUsers}</p>
              ) : (
                assignedUsers.map(u => (
                  <div key={u.user_id} className="flex items-center justify-between gap-3 p-3 bg-slate-50 rounded-xl border border-border">
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-textMain truncate">{u.full_name}</p>
                      <p className="text-xs text-textMuted truncate">{u.email}</p>
                      <span className="text-[9px] font-bold text-indigo-600 uppercase tracking-wider">
                        {roleLabels[u.role_for_client] || u.role_for_client}
                      </span>
                    </div>
                    {isAdmin && (
                      <button onClick={() => handleRemoveUser(u.user_id)}
                        className="shrink-0 text-[10px] font-bold text-red-600 hover:underline">
                        {t.removeUser}
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>

            {isAdmin && unassignedUsers.length > 0 && (
              <div className="px-6 py-4 border-t border-border space-y-3">
                <p className="text-xs font-bold text-textMuted uppercase tracking-wider">{t.assignUser}</p>
                <div className="flex gap-2">
                  <select
                    value={assignUserId}
                    onChange={e => setAssignUserId(e.target.value)}
                    className="flex-1 px-3 py-2 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                  >
                    <option value="">{t.selectUser}</option>
                    {unassignedUsers.map(u => (
                      <option key={u.user_id} value={u.user_id}>{u.full_name} ({u.email})</option>
                    ))}
                  </select>
                  <select
                    value={assignRole}
                    onChange={e => setAssignRole(e.target.value)}
                    className="px-3 py-2 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                  >
                    <option value="recruiter">{t.roleRecruiter}</option>
                    <option value="account_manager">{t.roleAccountManager}</option>
                    <option value="viewer">{t.roleViewer}</option>
                  </select>
                  <button
                    onClick={handleAssignUser}
                    disabled={!assignUserId || assigning}
                    className="px-4 py-2 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-50"
                  >
                    {assigning ? '…' : '+'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
