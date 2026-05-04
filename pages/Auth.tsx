
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User } from '../types';
import { ToastType } from '../components/Toast';
import { useLanguage } from '../context/LanguageContext';

interface AuthPageProps {
  onLoginSuccess: (token: string, user: User) => void;
  addToast: (msg: string, type: ToastType) => void;
}

const T = {
  en: {
    subtitle: 'Enterprise resume analysis and hiring intelligence.',
    signInTab: 'Sign In', registerTab: 'Register',
    workEmail: 'Work Email', password: 'Password',
    forgotPw: 'Forgot password?', signingIn: 'Signing in...', signInBtn: 'Sign In',
    companyName: 'Company Name', adminFullName: 'Admin Full Name', adminEmail: 'Admin Email',
    intakeMode: 'Intake Mode',
    platformEmailOpt: 'Platform Email (Dedicated Inbox)',
    forwardingOpt: 'Forwarding (Manual Routing)',
    forwardingEmail: 'Forwarding Email', confirmLabel: 'Confirm',
    creating: 'Creating...', createAccount: 'Create Account',
    langBtn: 'عربي',
  },
  ar: {
    subtitle: 'تحليل السير الذاتية المؤسسي وذكاء التوظيف.',
    signInTab: 'تسجيل الدخول', registerTab: 'إنشاء حساب',
    workEmail: 'البريد الإلكتروني', password: 'كلمة المرور',
    forgotPw: 'نسيت كلمة المرور؟', signingIn: 'جارٍ تسجيل الدخول...', signInBtn: 'تسجيل الدخول',
    companyName: 'اسم الشركة', adminFullName: 'الاسم الكامل للمسؤول', adminEmail: 'البريد الإلكتروني للمسؤول',
    intakeMode: 'طريقة استقبال السير الذاتية',
    platformEmailOpt: 'بريد المنصة (صندوق بريد مخصص)',
    forwardingOpt: 'إعادة التوجيه (توجيه يدوي)',
    forwardingEmail: 'البريد الإلكتروني للتوجيه', confirmLabel: 'تأكيد',
    creating: 'جارٍ الإنشاء...', createAccount: 'إنشاء حساب',
    langBtn: 'English',
  },
};

const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

export const AuthPage: React.FC<AuthPageProps> = ({ onLoginSuccess, addToast }) => {
  const { lang, setLang, isAr } = useLanguage();
  const t = T[lang];

  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showLoginPassword, setShowLoginPassword] = useState(false);
  const [showRegisterPassword, setShowRegisterPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [loginForm, setLoginForm] = useState({ email: '', password: '' });
  const [registerForm, setRegisterForm] = useState({
    company_name: '', admin_name: '', admin_email: '', password: '', confirm_password: '',
    cv_ingestion_mode: 'platform_email' as 'platform_email' | 'forwarding', forward_email: ''
  });

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.LOGIN_WEBHOOK_URL, loginForm);
      if (response && response.token) {
        const userData: User = { ...response.user, email: response.user?.email || loginForm.email, cv_ingestion_mode: response.cv_ingestion_mode || response.user?.cv_ingestion_mode };
        onLoginSuccess(response.token, userData);
      } else { throw new Error("Missing authentication token."); }
    } catch (err: any) {
      const msg = err.message || "Unable to sign in.";
      setError(msg); addToast(msg, "error");
    } finally { setLoading(false); }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null);
    if (registerForm.password !== registerForm.confirm_password) { setError("Passwords do not match"); return; }
    setLoading(true);
    try {
      const payload: any = { company_name: registerForm.company_name, admin_email: registerForm.admin_email, admin_name: registerForm.admin_name, password: registerForm.password, confirm_password: registerForm.confirm_password, intake_mode: registerForm.cv_ingestion_mode };
      if (registerForm.cv_ingestion_mode === 'forwarding') payload.forward_email = registerForm.forward_email;
      await apiService.post(WEBHOOK_CONFIG.REGISTER_WEBHOOK_URL, payload);
      addToast("Registration successful!", "success");
      setActiveTab('login');
    } catch (err: any) {
      setError(err.message || "Registration failed."); addToast(err.message || "Registration failed.", "error");
    } finally { setLoading(false); }
  };

  const PasswordToggle = ({ isVisible, onToggle, label }: { isVisible: boolean; onToggle: () => void; label: string }) => (
    <button type="button" onClick={onToggle} aria-label={isVisible ? `Hide ${label}` : `Show ${label}`}
      className={`absolute top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors focus:outline-none p-1 rounded-md hover:bg-slate-50 ${isAr ? 'left-3' : 'right-3'}`}>
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

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4 bg-slate-50">
      <div className="mb-8 text-center">
        <div className="flex items-center justify-center gap-2 mb-3">
          <h1 className="text-3xl font-bold text-primary tracking-tight">CV Analyzer</h1>
          <button onClick={() => setLang(isAr ? 'en' : 'ar')}
            className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-lg border border-slate-200 hover:border-primary text-textMuted hover:text-primary transition-all">
            <GlobeIcon />{t.langBtn}
          </button>
        </div>
        <p className="text-textMuted max-w-xs mx-auto">{t.subtitle}</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        <div className="flex border-b border-border bg-slate-50">
          <button onClick={() => { setActiveTab('login'); setError(null); }}
            className={`flex-1 py-4 text-xs font-bold uppercase tracking-widest transition-all ${activeTab === 'login' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted'}`}>
            {t.signInTab}
          </button>
          <button onClick={() => { setActiveTab('register'); setError(null); }}
            className={`flex-1 py-4 text-xs font-bold uppercase tracking-widest transition-all ${activeTab === 'register' ? 'bg-white text-primary border-b-2 border-primary' : 'text-textMuted'}`}>
            {t.registerTab}
          </button>
        </div>

        <div className="p-8">
          {error && (
            <div className={`mb-6 p-4 bg-red-50 ${isAr ? 'border-r-4' : 'border-l-4'} border-error text-error text-sm rounded flex items-start gap-3`}>
              <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          {activeTab === 'login' ? (
            <form onSubmit={handleLogin} className="space-y-6">
              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.workEmail}</label>
                <input required type="email" disabled={loading} placeholder="name@company.com"
                  className="w-full px-4 py-3 border border-border rounded-xl outline-none focus:border-primary transition-all"
                  value={loginForm.email} onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })} />
              </div>
              <div className="space-y-2">
                <div className="flex justify-between items-center">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.password}</label>
                  <a href="/forgot-password" className="text-[10px] font-bold text-primary hover:underline uppercase tracking-widest">{t.forgotPw}</a>
                </div>
                <div className="relative">
                  <input required type={showLoginPassword ? 'text' : 'password'} disabled={loading} placeholder="••••••••"
                    className={`w-full py-3 border border-border rounded-xl outline-none focus:border-primary transition-all ${isAr ? 'pr-4 pl-12' : 'pl-4 pr-12'}`}
                    value={loginForm.password} onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })} />
                  <PasswordToggle isVisible={showLoginPassword} onToggle={() => setShowLoginPassword(!showLoginPassword)} label="password" />
                </div>
              </div>
              <button type="submit" disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center">
                {loading ? t.signingIn : t.signInBtn}
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              {[
                { label: t.companyName, field: 'company_name', type: 'text', placeholder: '' },
                { label: t.adminFullName, field: 'admin_name', type: 'text', placeholder: 'John Doe' },
                { label: t.adminEmail, field: 'admin_email', type: 'email', placeholder: 'admin@company.com' },
              ].map(({ label, field, type, placeholder }) => (
                <div key={field} className="space-y-2">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{label}</label>
                  <input required type={type} disabled={loading} placeholder={placeholder}
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    value={(registerForm as any)[field]}
                    onChange={(e) => setRegisterForm({ ...registerForm, [field]: e.target.value })} />
                </div>
              ))}

              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.intakeMode}</label>
                <select disabled={loading} className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary bg-white"
                  value={registerForm.cv_ingestion_mode} onChange={(e) => setRegisterForm({ ...registerForm, cv_ingestion_mode: e.target.value as any })}>
                  <option value="platform_email">{t.platformEmailOpt}</option>
                  <option value="forwarding">{t.forwardingOpt}</option>
                </select>
              </div>

              {registerForm.cv_ingestion_mode === 'forwarding' && (
                <div className="space-y-2 animate-fade-in">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.forwardingEmail}</label>
                  <input required type="email" disabled={loading} placeholder="cv@yourcompany.com"
                    className="w-full px-4 py-2 border border-border rounded-lg outline-none focus:border-primary"
                    value={registerForm.forward_email} onChange={(e) => setRegisterForm({ ...registerForm, forward_email: e.target.value })} />
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                {([
                  { label: t.password, field: 'password', visible: showRegisterPassword, toggle: () => setShowRegisterPassword(!showRegisterPassword) },
                  { label: t.confirmLabel, field: 'confirm_password', visible: showConfirmPassword, toggle: () => setShowConfirmPassword(!showConfirmPassword) },
                ] as const).map(({ label, field, visible, toggle }) => (
                  <div key={field} className="space-y-2">
                    <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{label}</label>
                    <div className="relative">
                      <input required type={visible ? 'text' : 'password'} disabled={loading} placeholder="••••••••"
                        className={`w-full py-2 border border-border rounded-lg outline-none focus:border-primary ${isAr ? 'pr-4 pl-10' : 'pl-4 pr-10'}`}
                        value={(registerForm as any)[field]} onChange={(e) => setRegisterForm({ ...registerForm, [field]: e.target.value })} />
                      <PasswordToggle isVisible={visible} onToggle={toggle} label={label} />
                    </div>
                  </div>
                ))}
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold transition-all shadow-lg">
                {loading ? t.creating : t.createAccount}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
