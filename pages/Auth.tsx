
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User } from '../types';
// Fix: Import ToastType from its actual definition in components/Toast
import { ToastType } from '../components/Toast';

interface AuthPageProps {
  onLoginSuccess: (token: string, user: User) => void;
  addToast: (msg: string, type: ToastType) => void;
}

export const AuthPage: React.FC<AuthPageProps> = ({ onLoginSuccess, addToast }) => {
  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);

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
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.LOGIN_WEBHOOK_URL, loginForm);
      if (response.success) {
        onLoginSuccess(response.token, response.user);
      } else {
        throw new Error("Invalid credentials");
      }
    } catch (err: any) {
      addToast(err.message || "Login failed", "error");
      // Simulation for demo purposes since we don't have a real endpoint
      setTimeout(() => {
        addToast("Demo Mode: Logging in with mock user", "success");
        onLoginSuccess("mock-jwt-token-xyz", {
          email: loginForm.email || "admin@example.com",
          role: "Admin",
          tenant_name: "Demo Corp"
        });
      }, 1000);
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (registerForm.password !== registerForm.confirm_password) {
      addToast("Passwords do not match", "error");
      return;
    }
    setLoading(true);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.REGISTER_WEBHOOK_URL, registerForm);
      addToast("Registration successful! Please login.", "success");
      setActiveTab('login');
    } catch (err: any) {
      addToast(err.message || "Registration failed", "error");
      // Simulation
      setTimeout(() => {
        addToast("Demo Mode: Registration simulated.", "success");
        setActiveTab('login');
      }, 1000);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold text-primary mb-2">CV Analyzer</h1>
        <p className="text-textMuted">Intelligent Resume Screening for Modern HR Teams</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        {/* Tabs */}
        <div className="flex border-b border-border bg-slate-50">
          <button
            onClick={() => setActiveTab('login')}
            className={`flex-1 py-4 text-sm font-bold uppercase transition-all ${
              activeTab === 'login' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted hover:text-textMain'
            }`}
          >
            Sign In
          </button>
          <button
            onClick={() => setActiveTab('register')}
            className={`flex-1 py-4 text-sm font-bold uppercase transition-all ${
              activeTab === 'register' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted hover:text-textMain'
            }`}
          >
            Register
          </button>
        </div>

        <div className="p-8">
          {activeTab === 'login' ? (
            <form onSubmit={handleLogin} className="space-y-6">
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Work Email</label>
                <input
                  required
                  type="email"
                  className="w-full px-4 py-3 border border-border rounded-xl focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
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
                  className="w-full px-4 py-3 border border-border rounded-xl focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                  placeholder="••••••••"
                  value={loginForm.password}
                  onChange={(e) => setLoginForm({...loginForm, password: e.target.value})}
                />
              </div>
              <button
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold text-lg shadow-lg shadow-primary/20 transition-all disabled:opacity-50 flex items-center justify-center"
              >
                {loading && (
                   <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                )}
                Sign In
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Company Name</label>
                <input
                  required
                  type="text"
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                  value={registerForm.company_name}
                  onChange={(e) => setRegisterForm({...registerForm, company_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Full Name</label>
                <input
                  required
                  type="text"
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                  value={registerForm.admin_name}
                  onChange={(e) => setRegisterForm({...registerForm, admin_name: e.target.value})}
                />
              </div>
              <div className="space-y-2">
                <label className="text-xs font-bold text-textMuted uppercase">Email</label>
                <input
                  required
                  type="email"
                  className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
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
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    value={registerForm.password}
                    onChange={(e) => setRegisterForm({...registerForm, password: e.target.value})}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-xs font-bold text-textMuted uppercase">Confirm</label>
                  <input
                    required
                    type="password"
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
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
                      className="text-primary focus:ring-primary"
                      checked={registerForm.intake_mode === 'generated'}
                      onChange={() => setRegisterForm({...registerForm, intake_mode: 'generated'})}
                    />
                    <span className="text-sm">Generated</span>
                  </label>
                  <label className="flex items-center space-x-2 cursor-pointer">
                    <input
                      type="radio"
                      className="text-primary focus:ring-primary"
                      checked={registerForm.intake_mode === 'forwarding'}
                      onChange={() => setRegisterForm({...registerForm, intake_mode: 'forwarding'})}
                    />
                    <span className="text-sm">Forwarding</span>
                  </label>
                </div>
              </div>

              {registerForm.intake_mode === 'forwarding' && (
                 <div className="space-y-2">
                  <label className="text-xs font-bold text-textMuted uppercase">Forwarding Email</label>
                  <input
                    required
                    type="email"
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    placeholder="jobs@company.com"
                    value={registerForm.forward_email}
                    onChange={(e) => setRegisterForm({...registerForm, forward_email: e.target.value})}
                  />
                </div>
              )}

              <button
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold transition-all disabled:opacity-50 mt-4"
              >
                Create Account
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
