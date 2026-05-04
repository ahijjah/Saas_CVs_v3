
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { ToastType } from '../components/Toast';
import { useLanguage } from '../context/LanguageContext';

interface ForgotPasswordProps {
  addToast: (msg: string, type: ToastType) => void;
}

const T = {
  en: {
    subtitle: 'Enterprise resume analysis and hiring intelligence.',
    title: 'Forgot Password',
    desc: "Enter your work email and we'll send you a link to reset your password.",
    workEmail: 'Work Email',
    send: 'Send reset link',
    sending: 'Sending...',
    sentMsg: 'If the email exists, a reset link has been sent.',
    backToSignIn: 'Back to Sign In',
    langBtn: 'عربي',
  },
  ar: {
    subtitle: 'تحليل السير الذاتية المؤسسي وذكاء التوظيف.',
    title: 'نسيت كلمة المرور',
    desc: 'أدخل بريدك الإلكتروني وسنرسل لك رابطاً لإعادة تعيين كلمة المرور.',
    workEmail: 'البريد الإلكتروني',
    send: 'إرسال رابط إعادة التعيين',
    sending: 'جارٍ الإرسال...',
    sentMsg: 'إذا كان البريد الإلكتروني موجوداً، فقد تم إرسال رابط إعادة التعيين.',
    backToSignIn: 'العودة لتسجيل الدخول',
    langBtn: 'English',
  },
};

const GlobeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <circle cx="12" cy="12" r="10" /><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
  </svg>
);

export const ForgotPassword: React.FC<ForgotPasswordProps> = ({ addToast }) => {
  const { lang, setLang, isAr } = useLanguage();
  const t = T[lang];

  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;

    setLoading(true);
    setError(null);

    try {
      const response = await apiService.requestPasswordReset(email);
      if (response && response.success) {
        setSubmitted(true);
        addToast("Request sent!", "success");
      } else {
        throw response;
      }
    } catch (err: any) {
      let msg = "Something went wrong. Please try again.";
      if (err.error === 'validation_failed') {
        msg = err.details || "Please check the email format.";
      } else if (err.message) {
        msg = err.message;
      }
      setError(msg);
      addToast(msg, "error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 bg-slate-50 animate-fade-in">
      <div className="mb-8 text-center">
        <div className="flex items-center justify-center gap-2 mb-2">
          <h1 className="text-3xl font-bold text-primary tracking-tight">CV Analyzer</h1>
          <button onClick={() => setLang(isAr ? 'en' : 'ar')}
            className="flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1 rounded-lg border border-slate-200 hover:border-primary text-textMuted hover:text-primary transition-all">
            <GlobeIcon />{t.langBtn}
          </button>
        </div>
        <p className="text-textMuted max-w-xs mx-auto">{t.subtitle}</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        <div className="p-8">
          <h2 className="text-xl font-bold text-textMain mb-2">{t.title}</h2>
          <p className="text-sm text-textMuted mb-8">{t.desc}</p>

          {submitted ? (
            <div className="text-center py-6 space-y-4 animate-scale-in">
              <div className="w-16 h-16 bg-blue-100 text-primary rounded-full flex items-center justify-center mx-auto">
                <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <p className="font-bold text-textMain px-4">{t.sentMsg}</p>
              <div className="pt-6">
                <a href="/" className="text-primary font-bold text-sm hover:underline">{t.backToSignIn}</a>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-6">
              {error && (
                <div className={`p-4 bg-red-50 ${isAr ? 'border-r-4' : 'border-l-4'} border-error text-error text-xs rounded flex items-start animate-shake`}>
                  <svg className={`w-4 h-4 shrink-0 mt-0.5 ${isAr ? 'ml-2' : 'mr-2'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                  <span>{error}</span>
                </div>
              )}

              <div className="space-y-2">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">{t.workEmail}</label>
                <input
                  required
                  type="email"
                  disabled={loading}
                  placeholder="name@company.com"
                  className="w-full px-4 py-3 border border-border rounded-xl outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 transition-all text-sm"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-primary hover:bg-primaryDark text-white py-3.5 rounded-xl font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center disabled:opacity-50"
              >
                {loading ? (
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>
                    <span>{t.sending}</span>
                  </div>
                ) : t.send}
              </button>

              <div className="text-center pt-2">
                <a href="/" className="text-xs font-bold text-textMuted hover:text-primary transition-colors">
                  {t.backToSignIn}
                </a>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
