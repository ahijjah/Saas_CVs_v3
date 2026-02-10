
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState, UserProfile } from '../types';
import { ToastType } from '../components/Toast';

interface SettingsProps {
  auth: AuthState;
  addToast: (msg: string, type: ToastType) => void;
}

export const Settings: React.FC<SettingsProps> = ({ auth, addToast }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);

  // Form states
  const [adminName, setAdminName] = useState('');
  const [intakeMethod, setIntakeMethod] = useState<'IMAP' | 'FORWARD'>('IMAP');
  const [forwardingEmail, setForwardingEmail] = useState('');

  // Password form states
  const [passwordForm, setPasswordForm] = useState({
    current_password: '',
    new_password: '',
    confirm_password: ''
  });

  // Password visibility toggles
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
        setAdminName(p.admin_name || '');
        setIntakeMethod(p.intake_method || 'IMAP');
        setForwardingEmail(p.forwarding_email || '');
      }
    } catch (err: any) {
      addToast(err.message || "Failed to load profile.", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (intakeMethod === 'FORWARD' && (!forwardingEmail || !/^\S+@\S+\.\S+$/.test(forwardingEmail))) {
      addToast("Please provide a valid forwarding email.", "error");
      return;
    }

    setSavingProfile(true);
    try {
      const payload: any = {
        admin_name: adminName,
        intake_method: intakeMethod
      };
      if (intakeMethod === 'FORWARD') {
        payload.forwarding_email = forwardingEmail;
      }

      // Updated to use .put instead of .post as per backend requirements
      const response = await apiService.put(WEBHOOK_CONFIG.UPDATE_PROFILE_WEBHOOK_URL, payload, auth.token!);
      
      if (response && response.success) {
        setProfile(response.profile);
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
        setPasswordForm({
          current_password: '',
          new_password: '',
          confirm_password: ''
        });
      }
    } catch (err: any) {
      addToast(err.message || "Failed to change password.", "error");
    } finally {
      setSavingPassword(false);
    }
  };

  const PasswordToggle = ({ isVisible, onToggle, label }: { isVisible: boolean, onToggle: () => void, label: string }) => (
    <button
      type="button"
      onClick={onToggle}
      aria-label={isVisible ? `Hide ${label}` : `Show ${label}`}
      className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors focus:outline-none p-1 rounded-md hover:bg-slate-50"
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

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary"></div>
        <p className="text-textMuted animate-pulse font-medium uppercase tracking-widest text-xs">Fetching profile...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-12 animate-fade-in">
      {/* Profile Section */}
      <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-border bg-slate-50 flex justify-between items-center">
          <div>
            <h3 className="text-lg font-bold text-textMain">Profile Settings</h3>
            <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">Your personal and organizational details</p>
          </div>
          <div className={`px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest ${profile?.role === 'Admin' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-700'}`}>
            {profile?.role}
          </div>
        </div>

        <form onSubmit={handleUpdateProfile} className="p-8 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">Company / Tenant</label>
                <input
                  readOnly
                  type="text"
                  className="w-full px-4 py-2 border border-border rounded-lg bg-slate-50 text-textMuted text-sm outline-none cursor-not-allowed"
                  value={profile?.tenant_name || ''}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">Email Address</label>
                <input
                  readOnly
                  type="text"
                  className="w-full px-4 py-2 border border-border rounded-lg bg-slate-50 text-textMuted text-sm outline-none cursor-not-allowed"
                  value={profile?.email || ''}
                />
              </div>
            </div>

            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">Admin Name</label>
                <input
                  required
                  type="text"
                  placeholder="e.g. John Smith"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium"
                  value={adminName}
                  onChange={(e) => setAdminName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-black text-textMuted uppercase tracking-widest">Intake Method</label>
                <div className="flex space-x-4 pt-1">
                  {['IMAP', 'FORWARD'].map((method) => (
                    <label key={method} className="flex items-center space-x-2 cursor-pointer group">
                      <div className="relative flex items-center justify-center">
                        <input
                          type="radio"
                          className="peer appearance-none w-4 h-4 border border-border rounded-full checked:border-primary transition-all"
                          checked={intakeMethod === method}
                          onChange={() => setIntakeMethod(method as any)}
                        />
                        <div className="absolute w-2 h-2 rounded-full bg-primary scale-0 peer-checked:scale-100 transition-transform"></div>
                      </div>
                      <span className={`text-xs font-bold uppercase tracking-wider ${intakeMethod === method ? 'text-primary' : 'text-textMuted group-hover:text-textMain'}`}>
                        {method}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
              {intakeMethod === 'FORWARD' && (
                <div className="space-y-1.5 animate-fade-in">
                  <label className="text-xs font-black text-textMuted uppercase tracking-widest">Forwarding Email</label>
                  <input
                    required
                    type="email"
                    placeholder="cv@company.com"
                    className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm font-medium"
                    value={forwardingEmail}
                    onChange={(e) => setForwardingEmail(e.target.value)}
                  />
                  <p className="text-[10px] text-textMuted italic">CVs sent to this address will be automatically routed for analysis.</p>
                </div>
              )}
            </div>
          </div>

          <div className="pt-6 border-t border-border flex justify-end">
            <button
              type="submit"
              disabled={savingProfile}
              className="bg-primary hover:bg-primaryDark text-white px-8 py-2.5 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center space-x-2 disabled:opacity-50"
            >
              {savingProfile && <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
              <span>{savingProfile ? 'Saving...' : 'Update Profile'}</span>
            </button>
          </div>
        </form>
      </section>

      {/* Change Password Section */}
      <section className="bg-white rounded-2xl border border-border shadow-sm overflow-hidden">
        <div className="px-8 py-6 border-b border-border bg-slate-50">
          <h3 className="text-lg font-bold text-textMain">Security</h3>
          <p className="text-xs text-textMuted font-semibold uppercase tracking-wider">Manage your account password</p>
        </div>

        <form onSubmit={handleChangePassword} className="p-8 space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="space-y-1.5">
              <label className="text-xs font-black text-textMuted uppercase tracking-widest">Current Password</label>
              <div className="relative">
                <input
                  required
                  type={showCurrentPassword ? "text" : "password"}
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={passwordForm.current_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, current_password: e.target.value })}
                />
                <PasswordToggle 
                  isVisible={showCurrentPassword} 
                  onToggle={() => setShowCurrentPassword(!showCurrentPassword)} 
                  label="current password" 
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-black text-textMuted uppercase tracking-widest">New Password</label>
              <div className="relative">
                <input
                  required
                  type={showNewPassword ? "text" : "password"}
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={passwordForm.new_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                />
                <PasswordToggle 
                  isVisible={showNewPassword} 
                  onToggle={() => setShowNewPassword(!showNewPassword)} 
                  label="new password" 
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-black text-textMuted uppercase tracking-widest">Confirm New Password</label>
              <div className="relative">
                <input
                  required
                  type={showConfirmPassword ? "text" : "password"}
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={passwordForm.confirm_password}
                  onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                />
                <PasswordToggle 
                  isVisible={showConfirmPassword} 
                  onToggle={() => setShowConfirmPassword(!showConfirmPassword)} 
                  label="confirm new password" 
                />
              </div>
            </div>
          </div>

          <div className="pt-6 border-t border-border flex justify-end">
            <button
              type="submit"
              disabled={savingPassword}
              className="bg-slate-900 hover:bg-black text-white px-8 py-2.5 rounded-xl font-bold shadow-lg transition-all flex items-center space-x-2 disabled:opacity-50"
            >
              {savingPassword && <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>}
              <span>{savingPassword ? 'Changing...' : 'Change Password'}</span>
            </button>
          </div>
        </form>
      </section>
    </div>
  );
};
