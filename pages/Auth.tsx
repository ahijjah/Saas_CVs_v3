
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User } from '../types';
import { ToastType } from '../components/Toast';

interface AuthPageProps {
  onLoginSuccess: (token: string, user: User) => void;
  addToast: (msg: string, type: ToastType) => void;
}

export const AuthPage: React.FC<AuthPageProps> = ({ onLoginSuccess, addToast }) => {
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [loginForm, setLoginForm] = useState({ email: '', password: '' });
  
  const [registerForm, setRegisterForm] = useState({
    company_name: '',
    admin_name: '',
    admin_email: '',
    password: '',
    confirm_password: '',
    cv_ingestion_mode: 'platform_email' as 'platform_email' | 'forwarding',
    forward_email: ''
  });

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.LOGIN_WEBHOOK_URL, loginForm);
      
      if (response && response.token) {
        const userData: User = {
          ...response.user,
          email: response.user?.email || loginForm.email,
          cv_ingestion_mode: response.cv_ingestion_mode || response.user?.cv_ingestion_mode
        };
        onLoginSuccess(response.token, userData);
      } else {
        throw new Error("Missing authentication token.");
      }
    } catch (err: any) {
      // Catch specific backend error message from apiService
      const errorMsg = err.message || "Unable to sign in.";
      setError(errorMsg);
      addToast(errorMsg, "error");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (registerForm.password !== registerForm.confirm_password) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      // Ensure payload matches backend expectations: admin_name and intake_mode
      const payload = {
        ...registerForm,
        intake_mode: registerForm.cv_ingestion_mode // preferred field name
      };
      
      await apiService.post(WEBHOOK_CONFIG.REGISTER_WEBHOOK_URL, payload);
      addToast("Registration successful!", "success");
      setActiveTab('login');
    } catch (err: any) {
      setError(err.message || "Registration failed.");
      addToast(err.message || "Registration failed.", "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4 bg-slate-50">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold text-primary mb-2 tracking-tight">CV Analyzer</h1>
        <p className="text-textMuted max-w-xs mx-auto">Enterprise resume analysis and hiring intelligence.</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        <div className="flex border-b border-border bg-slate-50">
          <button
            onClick={() => { setActiveTab('login'); setError(null); }}
            className={`flex-1 py-4 text-xs font-bold uppercase tracking-widest transition-all ${
              activeTab === 'login' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted'
            }`}
          >
            Sign In
          </button>
          <button
            onClick={() => { setActiveTab('register'); setError(null); }}
            className={`flex-1 py-4 text-xs font-bold uppercase tracking-widest transition-all ${
              activeTab === 'register' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted'
            }`}
          >
            Register
          </button>
        </div>

        <div className="p-8">
          {error && (
            <div className="mb-6 p-4 bg-red-50 border-l-4 border-error text-error text-sm rounded flex items-start">
              <svg className="w-5 h-5 mr-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          {activeTab === 'login' ? (
            <form onSubmit={handleLogin} className="space-y-6">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Work Email</label>
                <input
                  required
                  type="email"
                  disabled={loading}
                  className="w-full px-4 py-3 border border-border rounded-xl outline-none focus:border-primary transition-all"
                  placeholder="name@company.com"
                  value={loginForm.email}
                  onChange={(e) => setLoginForm({...loginForm, email: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Password</label>
                <input
                  required
                  type="password"
                  disabled={loading}
                  className="w-full px-4 py-3 border border-border rounded-xl outline-none focus:border-primary transition-all"
                  placeholder="••••••••"
                  value={loginForm.password}
                  onChange={(e) => setLoginForm({...loginForm, password: e.target.value})}
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center"
              >
                {loading ? "Signing in..." : "Sign In"}
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Company Name</label>
                <input
                  required
                  type="text"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                  value={registerForm.company_name}
                  onChange={(e) => setRegisterForm({...registerForm, company_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Admin Full Name</label>
                <input
                  required
                  type="text"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                  placeholder="John Doe"
                  value={registerForm.admin_name}
                  onChange={(e) => setRegisterForm({...registerForm, admin_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Admin Email</label>
                <input
                  required
                  type="email"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                  placeholder="admin@company.com"
                  value={registerForm.admin_email}
                  onChange={(e) => setRegisterForm({...registerForm, admin_email: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Intake Mode</label>
                <select
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary bg-white"
                  value={registerForm.cv_ingestion_mode}
                  onChange={(e) => setRegisterForm({...registerForm, cv_ingestion_mode: e.target.value as any})}
                >
                  <option value="platform_email">Platform Email (Dedicated Inbox)</option>
                  <option value="forwarding">Forwarding (Manual Routing)</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Password</label>
                  <input
                    required
                    type="password"
                    disabled={loading}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    placeholder="••••••••"
                    value={registerForm.password}
                    onChange={(e) => setRegisterForm({...registerForm, password: e.target.value})}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Confirm</label>
                  <input
                    required
                    type="password"
                    disabled={loading}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    placeholder="••••••••"
                    value={registerForm.confirm_password}
                    onChange={(e) => setRegisterForm({...registerForm, confirm_password: e.target.value})}
                  />
                </div>
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold transition-all shadow-lg"
              >
                {loading ? "Creating..." : "Create Account"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
