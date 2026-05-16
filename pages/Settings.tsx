
import React, { useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, UserProfile, TenantUser } from '../types';
import { ToastType } from '../components/Toast';
import { useLanguage } from '../context/LanguageContext';

interface SettingsProps {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
}

const T = {
  en: {
    loading: 'Fetching profile...',
    profileTitle: 'Profile Settings',
    profileSub: 'Your personal and organizational details',
    editProfile: 'Edit Profile',
    tenantName: 'Company Name',
    emailDomain: 'Email Domain',
    emailReadOnly: 'Email Address',
    adminName: 'Admin Name',
    cvIngestionMode: 'CV Ingestion Mode',
    tenantStatus: 'Tenant Status',
    userRole: 'User Role',
    forwardingEmail: 'Forwarding Email',
    platformEmail: 'Platform Email',
    forwarding: 'Forwarding',
    plan: 'Plan',
    maxUsers: 'Max Users',
    maxJobs: 'Max Campaigns',
    activeUsers: 'Active Users',
    memberSince: 'Member Since',
    emailROLabel: 'Email Address (Read-only)',
    statusROLabel: 'Tenant Status (Read-only)',
    roleROLabel: 'User Role (Read-only)',
    cancel: 'Cancel',
    save: 'Save Changes',
    saving: 'Saving...',
    securityTitle: 'Security',
    securitySub: 'Manage your account password',
    changePassword: 'Change Password',
    currentPass: 'Current Password',
    newPass: 'New Password',
    confirmPass: 'Confirm New Password',
    cancelPass: 'Cancel',
    updatePass: 'Update Password',
    changingPass: 'Changing...',
    passHintMain: 'Your password was last changed recently.',
    passHintSub: 'We recommend changing your password every 90 days to keep your account secure.',
    // Users section
    usersTitle: 'Team Members',
    usersSub: 'Manage users within your organization',
    addUser: 'Add User',
    userLimitReached: 'Plan limit reached',
    usersOf: 'of',
    active: 'Active',
    disabled: 'Disabled',
    activate: 'Activate',
    deactivate: 'Deactivate',
    noUsers: 'No team members found.',
    addUserTitle: 'Add New User',
    fullName: 'Full Name',
    email: 'Email',
    password: 'Password',
    role: 'Role',
    roles: { admin: 'Admin', hr_manager: 'HR Manager', recruiter: 'Recruiter', viewer: 'Viewer' },
    rolePermissionsTitle: 'Role Permissions',
    rolePermissions: {
      admin: [
        'Manage account settings and subscription',
        'Manage users and roles',
        'Manage all jobs and applications',
        'Access audit logs and exports',
      ],
      hr_manager: [
        'Create and manage job campaigns',
        'Configure job parameters and criteria',
        'Upload CVs and manage applications',
        'View candidate results',
      ],
      recruiter: [
        'View applications and candidate results',
        'Export applications',
        'Cannot create or edit jobs',
        'Cannot manage users or subscription',
      ],
    } as Record<string, string[]>,
    addUserSave: 'Add User',
    addUserSaving: 'Adding...',
    cancelAdd: 'Cancel',
    lastLogin: 'Last login',
    never: 'Never',
    joined: 'Joined',
    trialBadge: 'Trial',
    editUser: 'Edit',
    saveUser: 'Save',
    cancelEdit: 'Cancel',
    editUserTitle: 'Edit User',
  },
  ar: {
    loading: 'جارٍ تحميل الملف الشخصي...',
    profileTitle: 'إعدادات الملف الشخصي',
    profileSub: 'تفاصيلك الشخصية والتنظيمية',
    editProfile: 'تعديل الملف الشخصي',
    tenantName: 'اسم الشركة',
    emailDomain: 'نطاق البريد الإلكتروني',
    emailReadOnly: 'عنوان البريد الإلكتروني',
    adminName: 'اسم المسؤول',
    cvIngestionMode: 'طريقة استقبال السير الذاتية',
    tenantStatus: 'حالة المستأجر',
    userRole: 'دور المستخدم',
    forwardingEmail: 'بريد التوجيه',
    platformEmail: 'بريد المنصة',
    forwarding: 'إعادة التوجيه',
    plan: 'الخطة',
    maxUsers: 'الحد الأقصى للمستخدمين',
    maxJobs: 'الحد الأقصى للحملات',
    activeUsers: 'المستخدمون النشطون',
    memberSince: 'عضو منذ',
    emailROLabel: 'عنوان البريد الإلكتروني (للقراءة فقط)',
    statusROLabel: 'حالة المستأجر (للقراءة فقط)',
    roleROLabel: 'دور المستخدم (للقراءة فقط)',
    cancel: 'إلغاء',
    save: 'حفظ التغييرات',
    saving: 'جارٍ الحفظ...',
    securityTitle: 'الأمان',
    securitySub: 'إدارة كلمة مرور حسابك',
    changePassword: 'تغيير كلمة المرور',
    currentPass: 'كلمة المرور الحالية',
    newPass: 'كلمة المرور الجديدة',
    confirmPass: 'تأكيد كلمة المرور الجديدة',
    cancelPass: 'إلغاء',
    updatePass: 'تحديث كلمة المرور',
    changingPass: 'جارٍ التغيير...',
    passHintMain: 'تم تغيير كلمة المرور مؤخراً.',
    passHintSub: 'نوصي بتغيير كلمة المرور كل 90 يوماً للحفاظ على أمان حسابك.',
    usersTitle: 'أعضاء الفريق',
    usersSub: 'إدارة المستخدمين داخل مؤسستك',
    addUser: 'إضافة مستخدم',
    userLimitReached: 'تم الوصول إلى حد الخطة',
    usersOf: 'من',
    active: 'نشط',
    disabled: 'معطّل',
    activate: 'تفعيل',
    deactivate: 'تعطيل',
    noUsers: 'لا يوجد أعضاء في الفريق.',
    addUserTitle: 'إضافة مستخدم جديد',
    fullName: 'الاسم الكامل',
    email: 'البريد الإلكتروني',
    password: 'كلمة المرور',
    role: 'الدور',
    roles: { admin: 'مدير الحساب', hr_manager: 'مدير الموارد البشرية', recruiter: 'مسؤول التوظيف', viewer: 'مشاهد' },
    rolePermissionsTitle: 'صلاحيات الأدوار',
    rolePermissions: {
      admin: [
        'إدارة إعدادات الحساب والاشتراك',
        'إدارة المستخدمين والأدوار',
        'إدارة جميع الوظائف والطلبات',
        'الوصول إلى سجلات التدقيق والتصدير',
      ],
      hr_manager: [
        'إنشاء وإدارة حملات الوظائف',
        'ضبط معايير ومتطلبات الوظائف',
        'رفع السير الذاتية وإدارة الطلبات',
        'عرض نتائج المرشحين',
      ],
      recruiter: [
        'عرض الطلبات ونتائج المرشحين',
        'تصدير الطلبات',
        'لا يمكن إنشاء أو تعديل الوظائف',
        'لا يمكن إدارة المستخدمين أو الاشتراك',
      ],
    } as Record<string, string[]>,
    addUserSave: 'إضافة مستخدم',
    addUserSaving: 'جارٍ الإضافة...',
    cancelAdd: 'إلغاء',
    lastLogin: 'آخر تسجيل دخول',
    never: 'لم يسجّل بعد',
    joined: 'انضم في',
    trialBadge: 'تجريبي',
    editUser: 'تعديل',
    saveUser: 'حفظ',
    cancelEdit: 'إلغاء',
    editUserTitle: 'تعديل المستخدم',
  },
};

export const Settings: React.FC<SettingsProps> = ({ auth, addToast }) => {
  const { lang, isAr } = useLanguage();
  const t = T[lang];

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [editForm, setEditForm] = useState({
    tenant_name: '',
    email_domain: '',
    admin_name: '',
  });

  const [passwordForm, setPasswordForm] = useState({
    current_password: '',
    new_password: '',
    confirm_password: '',
  });

  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  // Users section state
  const [tenantUsers, setTenantUsers] = useState<TenantUser[]>([]);
  const [usersActiveCount, setUsersActiveCount] = useState(0);
  const [usersMaxCount, setUsersMaxCount] = useState(0);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [usersAccessDenied, setUsersAccessDenied] = useState(false);
  const [showAddUser, setShowAddUser] = useState(false);
  const [addUserForm, setAddUserForm] = useState({ email: '', full_name: '', password: '', role: 'hr_manager' });
  const [savingUser, setSavingUser] = useState(false);
  const [togglingUserId, setTogglingUserId] = useState<string | null>(null);
  // Inline user edit state
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editUserForm, setEditUserForm] = useState({ full_name: '', role: '' });
  const [savingEditUser, setSavingEditUser] = useState(false);

  const role = (auth.user?.role || '').toLowerCase();
  const isSuperAdmin = role === 'super_admin';
  // Team management is only for tenant-level admins, not platform super_admin
  const isTenantAdmin = role === 'admin';

  const fetchProfile = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiService.get(WEBHOOK_CONFIG.GET_PROFILE_WEBHOOK_URL, {}, auth.token!);
      if (response?.success && response.profile) {
        const p: UserProfile = response.profile;
        setProfile(p);
        setEditForm({
          tenant_name: p.tenant_name || '',
          email_domain: p.email_domain || '',
          admin_name: p.admin_name || '',
        });
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to load profile.', 'error');
    } finally {
      setLoading(false);
    }
  }, [auth.token, addToast]);

  const fetchTenantUsers = useCallback(async () => {
    if (!isTenantAdmin) return;
    setLoadingUsers(true);
    try {
      const response = await apiService.get(WEBHOOK_CONFIG.TENANT_USERS_URL, {}, auth.token!);
      if (response?.success) {
        setUsersAccessDenied(false);
        setTenantUsers(response.users || []);
        setUsersActiveCount(response.active_count ?? 0);
        setUsersMaxCount(response.max_users ?? 0);
      }
    } catch (err: any) {
      const msg: string = err.message || '';
      if (msg.toLowerCase().includes('permission') || msg.toLowerCase().includes('forbidden') || msg.toLowerCase().includes('not authorized')) {
        setUsersAccessDenied(true);
      } else {
        addToast(msg || 'Failed to load users.', 'error');
      }
    } finally {
      setLoadingUsers(false);
    }
  }, [auth.token, addToast, isTenantAdmin]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  useEffect(() => {
    if (isTenantAdmin) fetchTenantUsers();
  }, [fetchTenantUsers, isTenantAdmin]);

  const handleCancelEdit = () => {
    if (profile) {
      setEditForm({
        tenant_name: profile.tenant_name || '',
        email_domain: profile.email_domain || '',
        admin_name: profile.admin_name || '',
      });
    }
    setIsEditing(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setEditForm(prev => ({ ...prev, [name]: value }));
  };

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingProfile(true);
    try {
      const payload = {
        tenant_name: editForm.tenant_name,
        email_domain: editForm.email_domain,
        admin_name: editForm.admin_name,
      };
      const response = await apiService.put(WEBHOOK_CONFIG.UPDATE_PROFILE_WEBHOOK_URL, payload, auth.token!);
      if (response?.success) {
        setProfile(response.profile);
        setIsEditing(false);
        addToast('Profile updated successfully!', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update profile.', 'error');
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      addToast('New passwords do not match.', 'error');
      return;
    }
    if (passwordForm.new_password.length < 8) {
      addToast('New password must be at least 8 characters.', 'error');
      return;
    }
    setSavingPassword(true);
    try {
      // Send both field names for compat; backend accepts either
      const response = await apiService.post(WEBHOOK_CONFIG.CHANGE_PASSWORD_WEBHOOK_URL, {
        old_password: passwordForm.current_password,
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
        confirm_password: passwordForm.confirm_password,
      }, auth.token!);
      if (response?.success) {
        addToast('Password changed successfully!', 'success');
        setIsChangingPassword(false);
        setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to change password.', 'error');
    } finally {
      setSavingPassword(false);
    }
  };

  const handleCancelPassword = () => {
    setIsChangingPassword(false);
    setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
  };

  const handleAddUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingUser(true);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.TENANT_USERS_URL, addUserForm, auth.token!);
      if (response?.success) {
        setTenantUsers(prev => [...prev, response.user]);
        setUsersActiveCount(prev => prev + 1);
        setShowAddUser(false);
        setAddUserForm({ email: '', full_name: '', password: '', role: 'hr_manager' });
        addToast('User added successfully.', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to add user.', 'error');
    } finally {
      setSavingUser(false);
    }
  };

  const handleToggleUserStatus = async (userId: string, currentStatus: string) => {
    setTogglingUserId(userId);
    const newStatus = currentStatus === 'active' ? 'disabled' : 'active';
    try {
      const response = await apiService.patch(
        `${WEBHOOK_CONFIG.TENANT_USERS_URL}/${userId}/status`,
        { status: newStatus },
        auth.token!
      );
      if (response?.success) {
        setTenantUsers(prev => prev.map(u => u.user_id === userId ? { ...u, status: newStatus as 'active' | 'disabled' } : u));
        setUsersActiveCount(prev => newStatus === 'active' ? prev + 1 : prev - 1);
        addToast(response.message || 'User status updated.', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update user status.', 'error');
    } finally {
      setTogglingUserId(null);
    }
  };

  const handleStartEditUser = (user: TenantUser) => {
    setEditingUserId(user.user_id);
    setEditUserForm({ full_name: user.full_name, role: user.role });
  };

  const handleSaveEditUser = async (userId: string) => {
    setSavingEditUser(true);
    try {
      const response = await apiService.patch(
        `${WEBHOOK_CONFIG.TENANT_USERS_URL}/${userId}`,
        editUserForm,
        auth.token!
      );
      if (response?.success) {
        setTenantUsers(prev => prev.map(u =>
          u.user_id === userId ? { ...u, full_name: editUserForm.full_name, role: editUserForm.role } : u
        ));
        setEditingUserId(null);
        addToast('User updated successfully.', 'success');
      }
    } catch (err: any) {
      addToast(err.message || 'Failed to update user.', 'error');
    } finally {
      setSavingEditUser(false);
    }
  };

  const PasswordToggle = ({ isVisible, onToggle, label }: { isVisible: boolean; onToggle: () => void; label: string }) => (
    <button
      type="button"
      onClick={onToggle}
      aria-label={isVisible ? `Hide ${label}` : `Show ${label}`}
      className={`absolute top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors focus:outline-none p-1 rounded-md hover:bg-slate-50 ${isAr ? 'left-3' : 'right-3'}`}
    >
      {isVisible ? (
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
        </svg>
      ) : (
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      )}
    </button>
  );

  const formatIngestionMode = (mode: string) => {
    if (mode === 'IMAP' || mode === 'platform_email') return t.platformEmail;
    if (mode === 'FORWARD' || mode === 'forwarding') return t.forwarding;
    return mode || 'N/A';
  };

  const formatDate = (iso: string | null | undefined) => {
    if (!iso) return 'N/A';
    return new Date(iso).toLocaleDateString(lang === 'ar' ? 'ar-SA' : 'en-GB', { year: 'numeric', month: 'short', day: 'numeric' });
  };

  const planLabel = (plan?: string) => {
    if (!plan) return 'N/A';
    return plan.charAt(0).toUpperCase() + plan.slice(1);
  };

  const atLimit = usersActiveCount >= usersMaxCount && usersMaxCount > 0;

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary"></div>
        <p className="text-textMuted animate-pulse font-medium uppercase tracking-widest text-xs">{t.loading}</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-12 animate-fade-in">

      {/* ── Profile Section ─────────────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-border bg-slate-50 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-textMain">{t.profileTitle}</h3>
            <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">{t.profileSub}</p>
          </div>
          <div className="flex items-center gap-3">
            {!isEditing && isTenantAdmin && (
              <button
                onClick={() => { setIsEditing(true); setIsChangingPassword(false); }}
                className="flex items-center gap-2 px-4 py-1.5 bg-white border border-border rounded-lg text-xs font-bold text-textMain hover:bg-slate-100 transition-colors shadow-sm"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
                <span>{t.editProfile}</span>
              </button>
            )}
            <div className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${profile?.role === 'admin' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-700'}`}>
              {profile?.role || auth.user?.role}
            </div>
          </div>
        </div>

        {!isEditing ? (
          <div className="p-8 space-y-8 animate-fade-in">
            {/* Core identity fields */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-6">
              <div className="space-y-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.tenantName}</p>
                <p className="text-sm font-bold text-textMain">{profile?.tenant_name || 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.emailDomain}</p>
                <p className="text-sm font-bold text-textMain">{profile?.email_domain || 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.emailReadOnly}</p>
                <p className="text-sm font-bold text-textMain">{profile?.email || 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.adminName}</p>
                <p className="text-sm font-bold text-textMain">{profile?.admin_name || 'N/A'}</p>
              </div>
              <div className="space-y-1">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.userRole}</p>
                <p className="text-sm font-bold text-textMain">{profile?.role || 'N/A'}</p>
              </div>
            </div>

            {/* Plan / limits strip */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-6 border-t border-border">
              <div className={`rounded-xl p-4 text-center ${(profile as any)?.subscription_status === 'trial' ? 'bg-amber-50 border border-amber-200' : 'bg-slate-50'}`}>
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.plan}</p>
                <p className={`text-sm font-black ${(profile as any)?.subscription_status === 'trial' ? 'text-amber-700' : 'text-primary'}`}>
                  {planLabel(profile?.plan)}
                </p>
                {(profile as any)?.subscription_status === 'trial' && (
                  <span className="mt-1 inline-block px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[9px] font-black uppercase tracking-widest">
                    {t.trialBadge}
                  </span>
                )}
              </div>
              <div className="bg-slate-50 rounded-xl p-4 text-center">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.tenantStatus}</p>
                <span className={`inline-block px-2 py-0.5 rounded text-xs font-black uppercase tracking-tight ${profile?.tenant_status === 'active' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                  {profile?.tenant_status || 'N/A'}
                </span>
              </div>
              <div className="bg-slate-50 rounded-xl p-4 text-center">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.activeUsers}</p>
                <p className="text-sm font-black text-textMain">
                  {profile?.active_users_count ?? 'N/A'}
                  {profile?.max_users ? <span className="text-textMuted font-normal"> / {profile.max_users}</span> : null}
                </p>
              </div>
              <div className="bg-slate-50 rounded-xl p-4 text-center">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-1">{t.memberSince}</p>
                <p className="text-xs font-bold text-textMain">{formatDate(profile?.tenant_created_at)}</p>
              </div>
            </div>
          </div>
        ) : (
          <form onSubmit={handleUpdateProfile} className="p-8 space-y-6 animate-fade-in bg-slate-50/30">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.tenantName}</label>
                  <input
                    required
                    name="tenant_name"
                    type="text"
                    className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium bg-white"
                    value={editForm.tenant_name}
                    onChange={handleInputChange}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.emailDomain}</label>
                  <input
                    name="email_domain"
                    type="text"
                    placeholder="company.com"
                    className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium bg-white"
                    value={editForm.email_domain}
                    onChange={handleInputChange}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.emailROLabel}</label>
                  <input
                    readOnly
                    type="text"
                    className="w-full px-4 py-2 border border-border rounded-lg bg-slate-100 text-textMuted text-sm outline-none cursor-not-allowed opacity-60"
                    value={profile?.email || ''}
                  />
                </div>
              </div>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.adminName}</label>
                  <input
                    required
                    name="admin_name"
                    type="text"
                    placeholder="e.g. John Smith"
                    className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium bg-white"
                    value={editForm.admin_name}
                    onChange={handleInputChange}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.roleROLabel}</label>
                  <input
                    readOnly
                    type="text"
                    className="w-full px-4 py-2 border border-border rounded-lg bg-slate-100 text-textMuted text-sm outline-none cursor-not-allowed opacity-60"
                    value={profile?.role || ''}
                  />
                </div>
              </div>
            </div>
            <div className="pt-6 border-t border-border flex justify-end gap-4">
              <button type="button" onClick={handleCancelEdit} disabled={savingProfile}
                className="px-6 py-2.5 rounded-xl font-bold text-textMuted hover:text-textMain transition-all text-sm disabled:opacity-50">
                {t.cancel}
              </button>
              <button type="submit" disabled={savingProfile}
                className="bg-primary hover:bg-primaryDark text-white px-8 py-2.5 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center gap-2 disabled:opacity-50">
                {savingProfile && <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
                <span>{savingProfile ? t.saving : t.save}</span>
              </button>
            </div>
          </form>
        )}
      </section>

      {/* ── Security Section ─────────────────────────────────────────────────── */}
      <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-border bg-slate-50 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-textMain">{t.securityTitle}</h3>
            <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">{t.securitySub}</p>
          </div>
          {!isChangingPassword && (
            <button
              onClick={() => { setIsChangingPassword(true); setIsEditing(false); }}
              className="flex items-center gap-2 px-4 py-1.5 bg-white border border-border rounded-lg text-xs font-bold text-textMain hover:bg-slate-100 transition-colors shadow-sm"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
              </svg>
              <span>{t.changePassword}</span>
            </button>
          )}
        </div>
        {isChangingPassword ? (
          <form onSubmit={handleChangePassword} className="p-8 space-y-6 animate-fade-in bg-slate-50/30">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {[
                { label: t.currentPass, field: 'current_password' as const, visible: showCurrentPassword, toggle: () => setShowCurrentPassword(p => !p) },
                { label: t.newPass, field: 'new_password' as const, visible: showNewPassword, toggle: () => setShowNewPassword(p => !p) },
                { label: t.confirmPass, field: 'confirm_password' as const, visible: showConfirmPassword, toggle: () => setShowConfirmPassword(p => !p) },
              ].map(({ label, field, visible, toggle }) => (
                <div key={field} className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{label}</label>
                  <div className="relative">
                    <input
                      required
                      type={visible ? 'text' : 'password'}
                      className={`w-full py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white ${isAr ? 'pr-4 pl-10' : 'pl-4 pr-10'}`}
                      value={passwordForm[field]}
                      onChange={e => setPasswordForm(prev => ({ ...prev, [field]: e.target.value }))}
                    />
                    <PasswordToggle isVisible={visible} onToggle={toggle} label={label} />
                  </div>
                </div>
              ))}
            </div>
            <div className="pt-6 border-t border-border flex justify-end gap-4">
              <button type="button" onClick={handleCancelPassword} disabled={savingPassword}
                className="px-6 py-2.5 rounded-xl font-bold text-textMuted hover:text-textMain transition-all text-sm disabled:opacity-50">
                {t.cancelPass}
              </button>
              <button type="submit" disabled={savingPassword}
                className="bg-slate-900 hover:bg-black text-white px-8 py-2.5 rounded-xl font-bold shadow-lg transition-all flex items-center gap-2 disabled:opacity-50">
                {savingPassword && <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
                <span>{savingPassword ? t.changingPass : t.updatePass}</span>
              </button>
            </div>
          </form>
        ) : (
          <div className="px-8 py-10 text-center bg-white">
            <p className="text-sm text-textMuted mb-2">{t.passHintMain}</p>
            <p className="text-xs text-textMuted opacity-60">{t.passHintSub}</p>
          </div>
        )}
      </section>

      {/* ── Team Members Section ─────────────────────────────────────────────── */}
      {isSuperAdmin && (
        <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-8 py-6 border-b border-border bg-slate-50">
            <h3 className="text-lg font-bold text-textMain">{t.usersTitle}</h3>
            <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">{t.usersSub}</p>
          </div>
          <div className="px-8 py-10 text-center">
            <svg className="w-8 h-8 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <p className="text-sm font-bold text-textMain mb-1">Team management is available for tenant administrators.</p>
            <p className="text-xs text-textMuted">Use the Admin Users panel to manage users across all tenants.</p>
          </div>
        </section>
      )}
      {isTenantAdmin && (
        <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
          <div className="px-8 py-6 border-b border-border bg-slate-50 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-bold text-textMain">{t.usersTitle}</h3>
              <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">{t.usersSub}</p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              {/* Active count badge */}
              <div className={`px-3 py-1 rounded-lg text-xs font-black ${atLimit ? 'bg-red-50 text-error border border-red-200' : 'bg-slate-100 text-textMuted'}`}>
                {usersActiveCount} {t.usersOf} {usersMaxCount} {t.active.toLowerCase()}
              </div>
              <button
                onClick={() => { setShowAddUser(true); setIsEditing(false); setIsChangingPassword(false); }}
                disabled={atLimit || showAddUser}
                title={atLimit ? t.userLimitReached : t.addUser}
                className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-bold transition-colors shadow-sm ${atLimit || showAddUser ? 'bg-slate-100 text-textMuted cursor-not-allowed' : 'bg-primary text-white hover:bg-primaryDark'}`}
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
                </svg>
                {atLimit ? t.userLimitReached : t.addUser}
              </button>
            </div>
          </div>

          {/* Add User Form */}
          {showAddUser && (
            <form onSubmit={handleAddUser} className="px-8 py-6 border-b border-border bg-blue-50/30 animate-fade-in">
              <h4 className="text-xs font-black text-textMuted uppercase tracking-widest mb-4">{t.addUserTitle}</h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.fullName}</label>
                  <input
                    required
                    type="text"
                    placeholder="Jane Smith"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={addUserForm.full_name}
                    onChange={e => setAddUserForm(prev => ({ ...prev, full_name: e.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.email}</label>
                  <input
                    required
                    type="email"
                    placeholder="jane@company.com"
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={addUserForm.email}
                    onChange={e => setAddUserForm(prev => ({ ...prev, email: e.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.password}</label>
                  <input
                    required
                    type="password"
                    placeholder="Min. 8 characters"
                    minLength={8}
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={addUserForm.password}
                    onChange={e => setAddUserForm(prev => ({ ...prev, password: e.target.value }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.role}</label>
                  <select
                    className="w-full px-3 py-2 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                    value={addUserForm.role}
                    onChange={e => setAddUserForm(prev => ({ ...prev, role: e.target.value }))}
                  >
                    {Object.entries(t.roles)
                      .filter(([val]) => val !== 'viewer')
                      .map(([val, label]) => (
                        <option key={val} value={val}>{label}</option>
                      ))}
                  </select>
                </div>
              </div>
              {/* Role Permissions panel */}
              {addUserForm.role && t.rolePermissions[addUserForm.role] && (
                <div className="mt-4 rounded-xl border border-border bg-slate-50 p-4">
                  <p className="text-[10px] font-black text-textMuted uppercase tracking-widest mb-3">{t.rolePermissionsTitle}</p>
                  <div className="flex items-center gap-2 mb-3">
                    <span className={`text-[10px] font-black uppercase tracking-widest px-2 py-1 rounded-md ${
                      addUserForm.role === 'admin' ? 'bg-indigo-100 text-indigo-700' :
                      addUserForm.role === 'hr_manager' ? 'bg-sky-100 text-sky-700' :
                      'bg-emerald-100 text-emerald-700'
                    }`}>
                      {(t.roles as any)[addUserForm.role]}
                    </span>
                  </div>
                  <ul className="space-y-1.5">
                    {t.rolePermissions[addUserForm.role].map((perm, i) => (
                      <li key={i} className={`flex items-start gap-2 text-xs ${perm.startsWith('Cannot') || perm.startsWith('لا ') ? 'text-slate-400' : 'text-textSecondary'}`}>
                        <span className={`mt-0.5 shrink-0 ${perm.startsWith('Cannot') || perm.startsWith('لا ') ? 'text-slate-300' : 'text-emerald-500'}`}>
                          {perm.startsWith('Cannot') || perm.startsWith('لا ') ? '✗' : '✓'}
                        </span>
                        {perm}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="flex justify-end gap-3 mt-4">
                <button type="button" onClick={() => { setShowAddUser(false); setAddUserForm({ email: '', full_name: '', password: '', role: 'hr_manager' }); }}
                  disabled={savingUser}
                  className="px-5 py-2 text-sm font-bold text-textMuted hover:text-textMain transition-colors disabled:opacity-50">
                  {t.cancelAdd}
                </button>
                <button type="submit" disabled={savingUser}
                  className="px-6 py-2 bg-primary text-white text-sm font-bold rounded-lg hover:bg-primaryDark transition-colors flex items-center gap-2 disabled:opacity-50">
                  {savingUser && <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
                  {savingUser ? t.addUserSaving : t.addUserSave}
                </button>
              </div>
            </form>
          )}

          {/* Users List */}
          <div className="divide-y divide-border">
            {loadingUsers ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
              </div>
            ) : usersAccessDenied ? (
              <div className="px-8 py-10 text-center">
                <svg className="w-8 h-8 mx-auto mb-3 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
                <p className="text-sm font-bold text-textMain mb-1">You do not have permission to access this section</p>
                <p className="text-xs text-textMuted">Contact your system administrator for access.</p>
              </div>
            ) : tenantUsers.length === 0 ? (
              <div className="px-8 py-10 text-center text-sm text-textMuted">{t.noUsers}</div>
            ) : (
              tenantUsers.map(user => (
                <div key={user.user_id} className="border-b border-border last:border-b-0">
                  {editingUserId === user.user_id ? (
                    <div className="px-8 py-4 bg-slate-50/60 animate-fade-in space-y-3">
                      <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.editUserTitle}</p>
                      <div className="flex flex-wrap gap-3">
                        <input
                          type="text"
                          className="flex-1 min-w-[160px] px-3 py-1.5 border border-border rounded-lg text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                          value={editUserForm.full_name}
                          onChange={e => setEditUserForm(p => ({ ...p, full_name: e.target.value }))}
                          placeholder={t.fullName}
                        />
                        <select
                          className="px-3 py-1.5 border border-border rounded-lg text-sm bg-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                          value={editUserForm.role}
                          onChange={e => setEditUserForm(p => ({ ...p, role: e.target.value }))}
                        >
                          {Object.entries(t.roles).map(([v, l]) => (
                            <option key={v} value={v}>{l as string}</option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleSaveEditUser(user.user_id)}
                          disabled={savingEditUser}
                          className="px-4 py-1.5 bg-primary text-white text-xs font-bold rounded-lg hover:bg-primaryDark transition-colors disabled:opacity-50"
                        >
                          {savingEditUser ? '…' : t.saveUser}
                        </button>
                        <button
                          onClick={() => setEditingUserId(null)}
                          className="px-4 py-1.5 border border-border text-xs font-bold rounded-lg text-textMuted hover:bg-slate-100 transition-colors"
                        >
                          {t.cancelEdit}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="px-8 py-4 flex items-center justify-between gap-4 hover:bg-slate-50/50 transition-colors">
                      <div className="flex items-center gap-4 min-w-0">
                        <div className={`w-9 h-9 rounded-full flex items-center justify-center text-xs font-black shrink-0 ${user.status === 'active' ? 'bg-primary/10 text-primary' : 'bg-slate-100 text-textMuted'}`}>
                          {user.full_name.charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-bold text-textMain truncate">{user.full_name}</p>
                          <p className="text-[11px] text-textMuted truncate">{user.email}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="hidden sm:block text-[10px] font-black uppercase tracking-widest text-textMuted px-2 py-1 bg-slate-100 rounded-md">
                          {(t.roles as any)[user.role] || user.role}
                        </span>
                        <span className={`text-[10px] font-black uppercase tracking-widest px-2 py-1 rounded-md ${user.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-slate-100 text-textMuted'}`}>
                          {user.status === 'active' ? t.active : t.disabled}
                        </span>
                        {user.user_id !== auth.user?.user_id && (
                          <button
                            onClick={() => handleStartEditUser(user)}
                            className="text-[10px] font-black uppercase tracking-widest px-2.5 py-1 rounded-lg border border-border text-textMuted hover:bg-slate-100 transition-colors"
                          >
                            {t.editUser}
                          </button>
                        )}
                        {user.user_id !== auth.user?.user_id && (
                          <button
                            onClick={() => handleToggleUserStatus(user.user_id, user.status)}
                            disabled={togglingUserId === user.user_id || (user.status === 'disabled' && atLimit)}
                            title={user.status === 'disabled' && atLimit ? t.userLimitReached : undefined}
                            className={`text-[10px] font-black uppercase tracking-widest px-2.5 py-1 rounded-lg border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${user.status === 'active' ? 'border-red-200 text-error hover:bg-red-50' : 'border-green-200 text-success hover:bg-green-50'}`}
                          >
                            {togglingUserId === user.user_id ? '…' : user.status === 'active' ? t.deactivate : t.activate}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </section>
      )}
    </div>
  );
};
