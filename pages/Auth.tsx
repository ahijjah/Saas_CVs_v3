
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

  // Form States
  const [loginForm, setLoginForm] = useState({ email: '', password: '' });
  const [registerForm, setRegisterForm] = useState({
    company_name: '',
    admin_name: '',
    admin_email: '',
    password: '',
    confirm_password: '',
    intake_mode: 'generated' as 'forwarding' | 'generated',
    forward_email: ''
  });

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.LOGIN_WEBHOOK_URL, loginForm);
      
      // Strict validation of the response
      if (response && response.success === true && response.token) {
        onLoginSuccess(response.token, response.user);
      } else {
        // Handle case where body says success: false despite 2xx status
        const errorMsg = response?.message || "Invalid credentials. Please try again.";
        setError(errorMsg);
        addToast(errorMsg, "error");
      }
    } catch (err: any) {
      // Handle 401/403 or network errors
      const errorMsg = err.message || "Unable to connect to login service.";
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
      const response = await apiService.post(WEBHOOK_CONFIG.REGISTER_WEBHOOK_URL, registerForm);
      
      if (response && response.success !== false) {
        addToast("Registration successful! Please login.", "success");
        setActiveTab('login');
      } else {
        setError(response?.message || "Registration failed.");
      }
    } catch (err: any) {
      setError(err.message || "Registration failed. Please check your connection.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold text-primary mb-2 tracking-tight">CV Analyzer</h1>
        <p className="text-textMuted max-w-xs mx-auto">Enterprise-grade resume analysis and job management.</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden transition-all duration-300">
        {/* Tabs */}
        <div className="flex border-b border-border bg-slate-50">
          <button
            onClick={() => { setActiveTab('login'); setError(null); }}
            className={`flex-1 py-4 text-sm font-bold uppercase transition-all ${
              activeTab === 'login' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted hover:text-textMain'
            }`}
          >
            Sign In
          </button>
          <button
            onClick={() => { setActiveTab('register'); setError(null); }}
            className={`flex-1 py-4 text-sm font-bold uppercase transition-all ${
              activeTab === 'register' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted hover:text-textMain'
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
                <label className="text-xs font-bold text-textMuted uppercase">Work Email</label>
                <input
                  required
                  type="email"
                  disabled={loading}
                  className="w-full px-4 py-3 border border-border rounded-xl focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all disabled:bg-slate-50 disabled:cursor-not-allowed"
                  placeholder="name@company.com"
                  value={loginForm.email}
                  onChange={(e) => setLoginForm({...loginForm, email: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Password</label>
                <input
                  required
                  type="password"
                  disabled={loading}
                  className="w-full px-4 py-3 border border-border rounded-xl focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all disabled:bg-slate-50 disabled:cursor-not-allowed"
                  placeholder="••••••••"
                  value={loginForm.password}
                  onChange={(e) => setLoginForm({...loginForm, password: e.target.value})}
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold text-lg shadow-lg shadow-primary/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center group"
              >
                {loading ? (
                   <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                ) : (
                  <>
                    <span>Sign In</span>
                    <svg className="w-5 h-5 ml-2 transform group-hover:translate-x-1 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                    </svg>
                  </>
                )}
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Company Name</label>
                <input
                  required
                  type="text"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                  value={registerForm.company_name}
                  onChange={(e) => setRegisterForm({...registerForm, company_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Full Name</label>
                <input
                  required
                  type="text"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                  value={registerForm.admin_name}
                  onChange={(e) => setRegisterForm({...registerForm, admin_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Email</label>
                <input
                  required
                  type="email"
                  disabled={loading}
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                  value={registerForm.admin_email}
                  onChange={(e) => setRegisterForm({...registerForm, admin_email: e.target.value})}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-xs font-bold text-textMuted uppercase">Password</label>
                  <input
                    required
                    type="password"
                    disabled={loading}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                    value={registerForm.password}
                    onChange={(e) => setRegisterForm({...registerForm, password: e.target.value})}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-bold text-textMuted uppercase">Confirm</label>
                  <input
                    required
                    type="password"
                    disabled={loading}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                    value={registerForm.confirm_password}
                    onChange={(e) => setRegisterForm({...registerForm, confirm_password: e.target.value})}
                  />
                </div>
              </div>

              <div className="space-y-2 py-2">
                <label className="text-xs font-bold text-textMuted uppercase block mb-2">CV Intake Mode</label>
                <div className="flex space-x-4">
                  <label className="flex items-center space-x-2 cursor-pointer">
                    <input
                      type="radio"
                      disabled={loading}
                      className="text-primary focus:ring-primary"
                      checked={registerForm.intake_mode === 'generated'}
                      onChange={() => setRegisterForm({...registerForm, intake_mode: 'generated'})}
                    />
                    <span className="text-sm">Generated</span>
                  </label>
                  <label className="flex items-center space-x-2 cursor-pointer">
                    <input
                      type="radio"
                      disabled={loading}
                      className="text-primary focus:ring-primary"
                      checked={registerForm.intake_mode === 'forwarding'}
                      onChange={() => setRegisterForm({...registerForm, intake_mode: 'forwarding'})}
                    />
                    <span className="text-sm">Forwarding</span>
                  </label>
                </div>
              </div>

              {registerForm.intake_mode === 'forwarding' && (
                 <div className="space-y-2 animate-fade-in">
                  <label className="text-xs font-bold text-textMuted uppercase">Forwarding Email</label>
                  <input
                    required
                    type="email"
                    disabled={loading}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary disabled:bg-slate-50"
                    placeholder="jobs@company.com"
                    value={registerForm.forward_email}
                    onChange={(e) => setRegisterForm({...registerForm, forward_email: e.target.value})}
                  />
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold transition-all disabled:opacity-50 mt-4 flex items-center justify-center"
              >
                {loading ? (
                   <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                ) : "Create Account"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
