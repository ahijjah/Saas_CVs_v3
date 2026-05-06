
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
    // Login
    loginTitle: 'Login to your account',
    loginSubtitle: 'Access your CV Analyzer dashboard',
    workEmail: 'Work Email',
    password: 'Password',
    forgotPw: 'Forgot password?',
    signingIn: 'Signing in…',
    signInBtn: 'Login',
    noAccount: 'Create a new company account',

    // Registration
    registerTitle: 'Create your company account',
    registerSubtitle: 'Start analyzing and managing CVs with AI',
    companyName: 'Company Name',
    emailDomain: 'Email Domain',
    emailDomainPlaceholder: 'company.com',
    adminFullName: 'Admin Full Name',
    adminEmail: 'Admin Email',
    creating: 'Creating account…',
    createAccount: 'Create account',
    haveAccount: 'Already have an account?',
    loginLink: 'Login',

    // Explainer card
    explainerTitle: 'Everything you need to hire smarter',
    feature1Title: 'Create your company workspace',
    feature1Desc: 'Set up your organisation in seconds with a dedicated workspace.',
    feature2Title: 'Post jobs and manage pipelines',
    feature2Desc: 'Create job postings and let AI extract the hiring criteria for you.',
    feature3Title: 'Receive and score CVs automatically',
    feature3Desc: 'CVs are scored, ranked and explained by AI — so you focus on the best.',

    langBtn: 'عربي',
  },
  ar: {
    loginTitle: 'تسجيل الدخول إلى حسابك',
    loginSubtitle: 'الوصول إلى لوحة تحكم محلل السير الذاتية',
    workEmail: 'البريد الإلكتروني',
    password: 'كلمة المرور',
    forgotPw: 'نسيت كلمة المرور؟',
    signingIn: 'جارٍ تسجيل الدخول…',
    signInBtn: 'تسجيل الدخول',
    noAccount: 'إنشاء حساب شركة جديد',

    registerTitle: 'إنشاء حساب شركتك',
    registerSubtitle: 'ابدأ تحليل وإدارة السير الذاتية بالذكاء الاصطناعي',
    companyName: 'اسم الشركة',
    emailDomain: 'نطاق البريد الإلكتروني',
    emailDomainPlaceholder: 'company.com',
    adminFullName: 'الاسم الكامل للمسؤول',
    adminEmail: 'البريد الإلكتروني للمسؤول',
    creating: 'جارٍ إنشاء الحساب…',
    createAccount: 'إنشاء الحساب',
    haveAccount: 'هل لديك حساب بالفعل؟',
    loginLink: 'تسجيل الدخول',

    explainerTitle: 'كل ما تحتاجه للتوظيف الذكي',
    feature1Title: 'أنشئ مساحة عمل شركتك',
    feature1Desc: 'أعدّ مؤسستك في ثوانٍ مع مساحة عمل مخصصة.',
    feature2Title: 'انشر وظائف وأدر خطوط التوظيف',
    feature2Desc: 'أنشئ إعلانات الوظائف ودع الذكاء الاصطناعي يستخرج معايير التوظيف.',
    feature3Title: 'استقبل وقيّم السير الذاتية تلقائياً',
    feature3Desc: 'يتم تسجيل السير الذاتية وترتيبها وشرحها بالذكاء الاصطناعي — ركّز على الأفضل.',

    langBtn: 'English',
  },
};

const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 shrink-0">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

const EyeOpenIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5">
    <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.644C3.399 8.049 7.21 5 12 5c4.79 0 8.601 3.049 9.964 6.678.14.373.14.76 0 1.134C20.601 15.951 16.79 19 12 19c-4.79 0-8.601-3.049-9.964-6.678z" />
    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

const EyeClosedIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-5 h-5">
    <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
  </svg>
);

export const AuthPage: React.FC<AuthPageProps> = ({ onLoginSuccess, addToast }) => {
  const { lang, setLang, isAr } = useLanguage();
  const t = T[lang];

  const [view, setView] = useState<'login' | 'register'>('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showLoginPw, setShowLoginPw] = useState(false);
  const [showRegPw, setShowRegPw] = useState(false);

  const [loginForm, setLoginForm] = useState({ email: '', password: '' });
  const [registerForm, setRegisterForm] = useState({
    company_name: '', email_domain: '', admin_name: '', admin_email: '', password: '',
  });

  const switchView = (v: 'login' | 'register') => { setView(v); setError(null); };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null);
    try {
      const response = await apiService.post(WEBHOOK_CONFIG.LOGIN_WEBHOOK_URL, loginForm);
      if (response?.token) {
        const userData: User = {
          ...response.user,
          email: response.user?.email || loginForm.email,
          cv_ingestion_mode: response.cv_ingestion_mode || response.user?.cv_ingestion_mode,
        };
        onLoginSuccess(response.token, userData);
      } else {
        throw new Error('Missing authentication token.');
      }
    } catch (err: any) {
      const msg = err.message || 'Unable to sign in.';
      setError(msg); addToast(msg, 'error');
    } finally { setLoading(false); }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null);
    setLoading(true);
    try {
      await apiService.post(WEBHOOK_CONFIG.REGISTER_WEBHOOK_URL, {
        company_name: registerForm.company_name,
        tenant_name:  registerForm.company_name,
        email_domain: registerForm.email_domain,
        admin_name:   registerForm.admin_name,
        admin_email:  registerForm.admin_email,
        email:        registerForm.admin_email,
        password:     registerForm.password,
      });
      addToast('Account created! Please sign in.', 'success');
      switchView('login');
    } catch (err: any) {
      const msg = err.message || 'Registration failed.';
      setError(msg); addToast(msg, 'error');
    } finally { setLoading(false); }
  };

  const PasswordToggle = ({ visible, onToggle }: { visible: boolean; onToggle: () => void }) => (
    <button type="button" onClick={onToggle}
      className={`absolute top-1/2 -translate-y-1/2 text-textMuted hover:text-primary transition-colors focus:outline-none p-1 rounded-md hover:bg-slate-50 ${isAr ? 'left-3' : 'right-3'}`}>
      {visible ? <EyeClosedIcon /> : <EyeOpenIcon />}
    </button>
  );

  const ErrorBanner = () => error ? (
    <div className={`mb-5 p-3.5 bg-red-50 ${isAr ? 'border-r-4' : 'border-l-4'} border-error text-error text-sm rounded-lg flex items-start gap-2.5`}>
      <svg className="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{error}</span>
    </div>
  ) : null;

  const Logo = () => (
    <div className={`flex items-center gap-2.5 mb-1 ${isAr ? 'flex-row-reverse' : ''}`}>
      <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center">
        <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /><polyline points="10 9 9 9 8 9" />
        </svg>
      </div>
      <span className="text-xl font-bold text-slate-800 tracking-tight">CV Analyzer</span>
    </div>
  );

  const LangToggle = () => (
    <button onClick={() => setLang(isAr ? 'en' : 'ar')}
      className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-lg border border-slate-200 hover:border-primary text-textMuted hover:text-primary transition-all">
      <GlobeIcon />{t.langBtn}
    </button>
  );

  // ── Login View ──────────────────────────────────────────────────────────────
  if (view === 'login') {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen p-4 bg-slate-50">
        <div className="w-full max-w-sm">
          {/* Header */}
          <div className={`flex items-start justify-between mb-8 ${isAr ? 'flex-row-reverse' : ''}`}>
            <div>
              <Logo />
            </div>
            <LangToggle />
          </div>

          {/* Card */}
          <div className="bg-white rounded-2xl shadow-xl border border-border p-8">
            <div className="mb-6">
              <h1 className="text-2xl font-bold text-slate-800 mb-1">{t.loginTitle}</h1>
              <p className="text-sm text-textMuted">{t.loginSubtitle}</p>
            </div>

            <ErrorBanner />

            <form onSubmit={handleLogin} className="space-y-5">
              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.workEmail}</label>
                <input
                  required type="email" disabled={loading}
                  placeholder="name@company.com"
                  className="w-full px-4 py-3 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm"
                  value={loginForm.email}
                  onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })}
                />
              </div>

              <div className="space-y-1.5">
                <div className={`flex justify-between items-center ${isAr ? 'flex-row-reverse' : ''}`}>
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.password}</label>
                  <a href="/forgot-password" className="text-[10px] font-bold text-primary hover:underline uppercase tracking-widest">{t.forgotPw}</a>
                </div>
                <div className="relative">
                  <input
                    required type={showLoginPw ? 'text' : 'password'} disabled={loading}
                    placeholder="••••••••"
                    className={`w-full py-3 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm ${isAr ? 'pr-4 pl-12' : 'pl-4 pr-12'}`}
                    value={loginForm.password}
                    onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })}
                  />
                  <PasswordToggle visible={showLoginPw} onToggle={() => setShowLoginPw(!showLoginPw)} />
                </div>
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center text-sm mt-1">
                {loading ? t.signingIn : t.signInBtn}
              </button>
            </form>
          </div>

          {/* Switch to register */}
          <p className={`mt-5 text-center text-sm text-textMuted ${isAr ? 'flex-row-reverse' : ''}`}>
            <button onClick={() => switchView('register')}
              className="text-primary font-semibold hover:underline">
              {t.noAccount}
            </button>
          </p>
        </div>
      </div>
    );
  }

  // ── Register View ───────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4 bg-slate-50">
      <div className="w-full max-w-4xl">
        {/* Header */}
        <div className={`flex items-start justify-between mb-8 ${isAr ? 'flex-row-reverse' : ''}`}>
          <div>
            <Logo />
          </div>
          <LangToggle />
        </div>

        <div className={`grid grid-cols-1 lg:grid-cols-5 gap-6 items-start ${isAr ? 'lg:flex lg:flex-row-reverse' : ''}`}>

          {/* ── Explainer card (left / 2 cols) ── */}
          <div className="lg:col-span-2 bg-primary rounded-2xl p-7 text-white">
            <h2 className="text-lg font-bold mb-1">{t.explainerTitle}</h2>
            <p className="text-primary-100 text-sm mb-6 opacity-80">{t.registerSubtitle}</p>

            <div className="space-y-5">
              {([
                { title: t.feature1Title, desc: t.feature1Desc },
                { title: t.feature2Title, desc: t.feature2Desc },
                { title: t.feature3Title, desc: t.feature3Desc },
              ] as const).map(({ title, desc }) => (
                <div key={title} className={`flex gap-3 ${isAr ? 'flex-row-reverse text-right' : ''}`}>
                  <div className="mt-0.5 w-6 h-6 rounded-full bg-white/20 flex items-center justify-center shrink-0">
                    <CheckIcon />
                  </div>
                  <div>
                    <p className="font-semibold text-sm">{title}</p>
                    <p className="text-xs opacity-75 mt-0.5 leading-relaxed">{desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ── Registration form (right / 3 cols) ── */}
          <div className="lg:col-span-3 bg-white rounded-2xl shadow-xl border border-border p-8">
            <div className="mb-6">
              <h1 className="text-2xl font-bold text-slate-800 mb-1">{t.registerTitle}</h1>
              <p className="text-sm text-textMuted">{t.registerSubtitle}</p>
            </div>

            <ErrorBanner />

            <form onSubmit={handleRegister} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.companyName}</label>
                  <input
                    required type="text" disabled={loading}
                    placeholder="Acme Corp"
                    className="w-full px-4 py-2.5 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm"
                    value={registerForm.company_name}
                    onChange={(e) => setRegisterForm({ ...registerForm, company_name: e.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.emailDomain}</label>
                  <input
                    required type="text" disabled={loading}
                    placeholder={t.emailDomainPlaceholder}
                    className="w-full px-4 py-2.5 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm"
                    value={registerForm.email_domain}
                    onChange={(e) => setRegisterForm({ ...registerForm, email_domain: e.target.value })}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.adminFullName}</label>
                <input
                  required type="text" disabled={loading}
                  placeholder="John Doe"
                  className="w-full px-4 py-2.5 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm"
                  value={registerForm.admin_name}
                  onChange={(e) => setRegisterForm({ ...registerForm, admin_name: e.target.value })}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.adminEmail}</label>
                <input
                  required type="email" disabled={loading}
                  placeholder="admin@company.com"
                  className="w-full px-4 py-2.5 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm"
                  value={registerForm.admin_email}
                  onChange={(e) => setRegisterForm({ ...registerForm, admin_email: e.target.value })}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.password}</label>
                <div className="relative">
                  <input
                    required type={showRegPw ? 'text' : 'password'} disabled={loading}
                    placeholder="••••••••"
                    className={`w-full py-2.5 border border-border rounded-xl outline-none focus:border-primary transition-all text-sm ${isAr ? 'pr-4 pl-12' : 'pl-4 pr-12'}`}
                    value={registerForm.password}
                    onChange={(e) => setRegisterForm({ ...registerForm, password: e.target.value })}
                  />
                  <PasswordToggle visible={showRegPw} onToggle={() => setShowRegPw(!showRegPw)} />
                </div>
              </div>

              <button type="submit" disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center text-sm mt-2">
                {loading ? t.creating : t.createAccount}
              </button>
            </form>
          </div>
        </div>

        {/* Switch to login */}
        <p className="mt-5 text-center text-sm text-textMuted">
          {t.haveAccount}{' '}
          <button onClick={() => switchView('login')} className="text-primary font-semibold hover:underline">
            {t.loginLink}
          </button>
        </p>
      </div>
    </div>
  );
};
