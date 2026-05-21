import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { AuthState } from '../types';
import { ToastType } from '../components/Toast';

interface ForceChangePasswordProps {
  auth: AuthState;
  onPasswordChanged: () => void;
  addToast: (msg: string, type: ToastType) => void;
}

export const ForceChangePassword: React.FC<ForceChangePasswordProps> = ({
  auth, onPasswordChanged, addToast,
}) => {
  const [form, setForm] = useState({ new_password: '', confirm_password: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (form.new_password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (form.new_password !== form.confirm_password) {
      setError('Passwords do not match.');
      return;
    }

    setSaving(true);
    try {
      await apiService.post(
        WEBHOOK_CONFIG.CHANGE_PASSWORD_WEBHOOK_URL,
        {
          new_password: form.new_password,
          confirm_password: form.confirm_password,
        },
        auth.token!,
      );
      addToast('Password set successfully. Welcome!', 'success');
      onPasswordChanged();
    } catch (err: any) {
      setError(err.message || 'Failed to set password. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl border border-border p-8 max-w-sm w-full">
        <div className="mb-6 text-center">
          <div className="w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-textMain">Set your password</h1>
          <p className="text-sm text-textMuted mt-1">
            Please choose a new password to secure your account before continuing.
          </p>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border-l-4 border-red-400 text-red-700 text-sm rounded-lg">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1">
              New Password
            </label>
            <div className="relative">
              <input
                required
                type={showNew ? 'text' : 'password'}
                minLength={8}
                placeholder="Minimum 8 characters"
                value={form.new_password}
                onChange={e => setForm(f => ({ ...f, new_password: e.target.value }))}
                className="w-full px-4 py-3 pr-10 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
              <button
                type="button"
                onClick={() => setShowNew(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
                tabIndex={-1}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  {showNew
                    ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                    : <><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></>
                  }
                </svg>
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs font-black text-textMuted uppercase tracking-widest mb-1">
              Confirm Password
            </label>
            <div className="relative">
              <input
                required
                type={showConfirm ? 'text' : 'password'}
                minLength={8}
                placeholder="Repeat your new password"
                value={form.confirm_password}
                onChange={e => setForm(f => ({ ...f, confirm_password: e.target.value }))}
                className="w-full px-4 py-3 pr-10 border border-border rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
              <button
                type="button"
                onClick={() => setShowConfirm(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-textMain"
                tabIndex={-1}
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  {showConfirm
                    ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                    : <><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></>
                  }
                </svg>
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={saving}
            className="w-full py-3 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-50 flex items-center justify-center gap-2 mt-2"
          >
            {saving && <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
            {saving ? 'Setting password…' : 'Set Password & Continue'}
          </button>
        </form>
      </div>
    </div>
  );
};
