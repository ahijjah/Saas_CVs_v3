
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, UserProfile } from '../types';
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
    tenantName: 'Tenant Name',
    emailReadOnly: 'Email Address',
    adminName: 'Admin Name',
    cvIngestionMode: 'CV Ingestion Mode',
    tenantStatus: 'Tenant Status',
    active: 'Active',
    userRole: 'User Role',
    forwardingEmail: 'Forwarding Email',
    platformEmail: 'Platform Email',
    forwarding: 'Forwarding',
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
  },
  ar: {
    loading: 'جارٍ تحميل الملف الشخصي...',
    profileTitle: 'إعدادات الملف الشخصي',
    profileSub: 'تفاصيلك الشخصية والتنظيمية',
    editProfile: 'تعديل الملف الشخصي',
    tenantName: 'اسم المستأجر',
    emailReadOnly: 'عنوان البريد الإلكتروني',
    adminName: 'اسم المسؤول',
    cvIngestionMode: 'طريقة استقبال السير الذاتية',
    tenantStatus: 'حالة المستأجر',
    active: 'نشط',
    userRole: 'دور المستخدم',
    forwardingEmail: 'بريد التوجيه',
    platformEmail: 'بريد المنصة',
    forwarding: 'إعادة التوجيه',
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
    admin_name: '',
    cv_ingestion_mode: 'platform_email' as 'platform_email' | 'FORWARD',
    forwarding_email: ''
  });

  const [passwordForm, setPasswordForm] = useState({
    current_password: '',
    new_password: '',
    confirm_password: ''
  });

  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  useEffect(() => {
    fetchProfile();
  }, []);

  const fetchProfile = async () => {
    setLoading(true);
    try {
      const response = await apiService.get(WEBHOOK_CONFIG.GET_PROFILE_WEBHOOK_URL, {}, auth.token!);
      if (response && response.success && response.profile) {
        const p = response.profile;
        setProfile(p);
        const mappedIngestionMode = (p.intake_method === 'IMAP' ? 'platform_email' : p.intake_method) as 'platform_email' | 'FORWARD';
        setEditForm({
          tenant_name: p.tenant_name || '',
          admin_name: p.admin_name || '',
          cv_ingestion_mode: mappedIngestionMode || 'platform_email',
          forwarding_email: p.forwarding_email || ''
        });
      }
    } catch (err: any) {
      addToast(err.message || "Failed to load profile.", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleCancelEdit = () => {
    if (profile) {
      const mappedIngestionMode = (profile.intake_method === 'IMAP' ? 'platform_email' : profile.intake_method) as 'platform_email' | 'FORWARD';
      setEditForm({
        tenant_name: profile.tenant_name || '',
        admin_name: profile.admin_name || '',
        cv_ingestion_mode: mappedIngestionMode || 'platform_email',
        forwarding_email: profile.forwarding_email || ''
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
    if (editForm.cv_ingestion_mode === 'FORWARD' && (!editForm.forwarding_email || !/^\S+@\S+\.\S+$/.test(editForm.forwarding_email))) {
      addToast("Please provide a valid forwarding email.", "error");
      return;
    }

    setSavingProfile(true);
    try {
      const payload: any = {
        tenant_name: editForm.tenant_name,
        admin_name: editForm.admin_name,
        cv_ingestion_mode: editForm.cv_ingestion_mode
      };

      if (editForm.cv_ingestion_mode === 'FORWARD') {
        payload.forwarding_email = editForm.forwarding_email;
      }

      const response = await apiService.put(WEBHOOK_CONFIG.UPDATE_PROFILE_WEBHOOK_URL, payload, auth.token!);

      if (response && response.success) {
        setProfile(response.profile);
        setIsEditing(false);
        addToast("Profile updated successfully!", "success");
      }
    } catch (err: any) {
      addToast(err.message || "Failed to update profile.", "error");
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      addToast("New passwords do not match.", "error");
      return;
    }
    if (passwordForm.new_password.length < 8) {
      addToast("New password must be at least 8 characters.", "error");
      return;
    }

    setSavingPassword(true);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.CHANGE_PASSWORD_WEBHOOK_URL, passwordForm, auth.token!);
      if (response && response.success) {
        addToast("Password changed successfully!", "success");
        setIsChangingPassword(false);
        setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
      }
    } catch (err: any) {
      addToast(err.message || "Failed to change password.", "error");
    } finally {
      setSavingPassword(false);
    }
  };

  const handleCancelPassword = () => {
    setIsChangingPassword(false);
    setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
  };

  const PasswordToggle = ({ isVisible, onToggle, label }: { isVisible: boolean, onToggle: () => void, label: string }) => (
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
    if (mode === 'FORWARD') return t.forwarding;
    return mode || 'N/A';
  };

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
      {/* Profile Section */}
      <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-border bg-slate-50 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-textMain">{t.profileTitle}</h3>
            <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">{t.profileSub}</p>
          </div>
          <div className="flex items-center gap-3">
            {!isEditing && (
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
            <div className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${profile?.role === 'Admin' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-700'}`}>
              {profile?.role}
            </div>
          </div>
        </div>

        {!isEditing ? (
          <div className="p-8 grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-8 animate-fade-in">
            <div className="space-y-1">
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.tenantName}</p>
              <p className="text-sm font-bold text-textMain">{profile?.tenant_name || 'N/A'}</p>
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
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.cvIngestionMode}</p>
              <p className="text-sm font-bold text-textMain">{formatIngestionMode(profile?.intake_method || '')}</p>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.tenantStatus}</p>
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold bg-green-100 text-green-800 uppercase tracking-tighter">
                {t.active}
              </span>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.userRole}</p>
              <p className="text-sm font-bold text-textMain">{profile?.role || 'N/A'}</p>
            </div>
            {profile?.intake_method === 'FORWARD' && (
              <div className="space-y-1 md:col-span-2">
                <p className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.forwardingEmail}</p>
                <p className="text-sm font-bold text-textMain">{profile?.forwarding_email || 'N/A'}</p>
              </div>
            )}
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
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.emailROLabel}</label>
                  <input
                    readOnly
                    type="text"
                    className="w-full px-4 py-2 border border-border rounded-lg bg-slate-100 text-textMuted text-sm outline-none cursor-not-allowed opacity-60"
                    value={profile?.email || ''}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.statusROLabel}</label>
                  <input
                    readOnly
                    type="text"
                    className="w-full px-4 py-2 border border-border rounded-lg bg-slate-100 text-green-800 text-sm outline-none cursor-not-allowed font-bold"
                    value="ACTIVE"
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
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.cvIngestionMode}</label>
                  <div className="flex gap-4 pt-1">
                    {[
                      { value: 'platform_email', label: t.platformEmail },
                      { value: 'FORWARD', label: t.forwarding }
                    ].map((mode) => (
                      <label key={mode.value} className="flex items-center gap-2 cursor-pointer group">
                        <div className="relative flex items-center justify-center">
                          <input
                            type="radio"
                            className="peer appearance-none w-4 h-4 border border-border rounded-full checked:border-primary transition-all"
                            checked={editForm.cv_ingestion_mode === mode.value}
                            onChange={() => setEditForm(prev => ({ ...prev, cv_ingestion_mode: mode.value as any }))}
                          />
                          <div className="absolute w-2 h-2 rounded-full bg-primary scale-0 peer-checked:scale-100 transition-transform"></div>
                        </div>
                        <span className={`text-xs font-bold uppercase tracking-wider ${editForm.cv_ingestion_mode === mode.value ? 'text-primary' : 'text-textMuted group-hover:text-textMain'}`}>
                          {mode.label}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
                {editForm.cv_ingestion_mode === 'FORWARD' && (
                  <div className="space-y-1.5 animate-fade-in">
                    <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.forwardingEmail}</label>
                    <input
                      required
                      name="forwarding_email"
                      type="email"
                      placeholder="cv@company.com"
                      className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium bg-white"
                      value={editForm.forwarding_email}
                      onChange={handleInputChange}
                    />
                  </div>
                )}
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
              <button
                type="button"
                onClick={handleCancelEdit}
                disabled={savingProfile}
                className="px-6 py-2.5 rounded-xl font-bold text-textMuted hover:text-textMain transition-all text-sm disabled:opacity-50"
              >
                {t.cancel}
              </button>
              <button
                type="submit"
                disabled={savingProfile}
                className="bg-primary hover:bg-primaryDark text-white px-8 py-2.5 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center gap-2 disabled:opacity-50"
              >
                {savingProfile && <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
                <span>{savingProfile ? t.saving : t.save}</span>
              </button>
            </div>
          </form>
        )}
      </section>

      {/* Security Section */}
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
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.currentPass}</label>
                <div className="relative">
                  <input
                    required
                    type={showCurrentPassword ? "text" : "password"}
                    className={`w-full py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white ${isAr ? 'pr-4 pl-10' : 'pl-4 pr-10'}`}
                    value={passwordForm.current_password}
                    onChange={(e) => setPasswordForm({ ...passwordForm, current_password: e.target.value })}
                  />
                  <PasswordToggle isVisible={showCurrentPassword} onToggle={() => setShowCurrentPassword(!showCurrentPassword)} label="current password" />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.newPass}</label>
                <div className="relative">
                  <input
                    required
                    type={showNewPassword ? "text" : "password"}
                    className={`w-full py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white ${isAr ? 'pr-4 pl-10' : 'pl-4 pr-10'}`}
                    value={passwordForm.new_password}
                    onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                  />
                  <PasswordToggle isVisible={showNewPassword} onToggle={() => setShowNewPassword(!showNewPassword)} label="new password" />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">{t.confirmPass}</label>
                <div className="relative">
                  <input
                    required
                    type={showConfirmPassword ? "text" : "password"}
                    className={`w-full py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white ${isAr ? 'pr-4 pl-10' : 'pl-4 pr-10'}`}
                    value={passwordForm.confirm_password}
                    onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                  />
                  <PasswordToggle isVisible={showConfirmPassword} onToggle={() => setShowConfirmPassword(!showConfirmPassword)} label="confirm password" />
                </div>
              </div>
            </div>

            <div className="pt-6 border-t border-border flex justify-end gap-4">
              <button
                type="button"
                onClick={handleCancelPassword}
                disabled={savingPassword}
                className="px-6 py-2.5 rounded-xl font-bold text-textMuted hover:text-textMain transition-all text-sm disabled:opacity-50"
              >
                {t.cancelPass}
              </button>
              <button
                type="submit"
                disabled={savingPassword}
                className="bg-slate-900 hover:bg-black text-white px-8 py-2.5 rounded-xl font-bold shadow-lg transition-all flex items-center gap-2 disabled:opacity-50"
              >
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
    </div>
  );
};
