
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { ToastType } from '../components/Toast';

interface ResetPasswordProps {
  addToast: (msg: string, type: ToastType) => void;
  onSuccess: () => void;
}

export const ResetPassword: React.FC<ResetPasswordProps> = ({ addToast, onSuccess }) => {
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const [showPass, setShowPass] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  
  const [form, setForm] = useState({
    new_password: '',
    confirm_password: ''
  });

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const tokenParam = params.get('token');
    if (!tokenParam) {
      setError("This reset link is invalid or missing a security token.");
    } else {
      setToken(tokenParam);
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;

    if (form.new_password.length < 9) {
      addToast("Password must be at least 9 characters.", "error");
      return;
    }
    if (form.new_password !== form.confirm_password) {
      addToast("Passwords do not match.", "error");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await apiService.post(WEBHOOK_CONFIG.RESET_PASSWORD_WEBHOOK_URL, {
        token,
        new_password: form.new_password,
        confirm_password: form.confirm_password
      });

      if (response && response.success) {
        setSuccess(true);
        addToast("Password reset successfully!", "success");
        // Clear auth data via parent callback
        onSuccess();
        // Automatic redirect after 3 seconds
        setTimeout(() => {
          window.location.href = '/';
        }, 3000);
      } else {
        throw response;
      }
    } catch (err: any) {
      let msg = "Something went wrong. Please try again.";
      if (err.error === 'invalid_or_expired_token') {
        msg = "This reset link is invalid or expired. Please request a new one.";
      } else if (err.error === 'validation_failed') {
        msg = err.details || "Validation failed. Please check your password strength.";
      } else if (err.message) {
        msg = err.message;
      }
      setError(msg);
      addToast(msg, "error");
    } finally {
      setLoading(false);
    }
  };

  const PasswordToggle = ({ isVisible, onToggle }: { isVisible: boolean, onToggle: () => void }) => (
    <button
      type="button"
      onClick={onToggle}
      className="absolute right-3 top-1/2 -translate-y-1/2 text-textMuted hover:text-primary p-1"
    >
      {isVisible ? (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" /></svg>
      ) : (
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
      )}
    </button>
  );

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 bg-slate-50 animate-fade-in">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold text-primary mb-2 tracking-tight">CV Analyzer</h1>
        <p className="text-textMuted max-w-xs mx-auto">Security & Identity</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        <div className="p-8">
          <h2 className="text-xl font-bold text-textMain mb-2">Reset Password</h2>
          <p className="text-sm text-textMuted mb-8">Please enter a new secure password for your account.</p>

          {success ? (
            <div className="text-center py-8 space-y-4 animate-scale-in">
              <div className="w-16 h-16 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" d="M5 13l4 4L19 7" /></svg>
              </div>
              <p className="font-bold text-textMain">Password Updated!</p>
              <p className="text-sm text-textMuted">You will be redirected to the login page in a few seconds.</p>
              <a href="/" className="inline-block text-primary font-bold text-sm hover:underline pt-4">Click here if not redirected</a>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-6">
              {error && (
                <div className="p-4 bg-red-50 border-l-4 border-error text-error text-xs rounded flex items-start animate-shake">
                  <svg className="w-4 h-4 mr-2 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                  <span>{error}</span>
                </div>
              )}

              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">New Password</label>
                <div className="relative">
                  <input
                    required
                    disabled={loading || !token}
                    type={showPass ? "text" : "password"}
                    placeholder="Min 9 characters"
                    className="w-full pl-4 pr-12 py-3 border border-border rounded-xl outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm"
                    value={form.new_password}
                    onChange={(e) => setForm({ ...form, new_password: e.target.value })}
                  />
                  <PasswordToggle isVisible={showPass} onToggle={() => setShowPass(!showPass)} />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Confirm Password</label>
                <div className="relative">
                  <input
                    required
                    disabled={loading || !token}
                    type={showConfirm ? "text" : "password"}
                    placeholder="Repeat password"
                    className="w-full pl-4 pr-12 py-3 border border-border rounded-xl outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm"
                    value={form.confirm_password}
                    onChange={(e) => setForm({ ...form, confirm_password: e.target.value })}
                  />
                  <PasswordToggle isVisible={showConfirm} onToggle={() => setShowConfirm(!showConfirm)} />
                </div>
              </div>

              <button
                type="submit"
                disabled={loading || !token}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3.5 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center disabled:opacity-50 disabled:bg-slate-300"
              >
                {loading ? (
                  <div className="flex items-center space-x-2">
                    <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>
                    <span>Updating...</span>
                  </div>
                ) : "Reset Password"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
