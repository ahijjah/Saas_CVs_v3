
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { ToastType } from '../components/Toast';

interface ForgotPasswordProps {
  addToast: (msg: string, type: ToastType) => void;
}

export const ForgotPassword: React.FC<ForgotPasswordProps> = ({ addToast }) => {
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
        <h1 className="text-3xl font-bold text-primary mb-2 tracking-tight">CV Analyzer</h1>
        <p className="text-textMuted max-w-xs mx-auto">Enterprise resume analysis and hiring intelligence.</p>
      </div>

      <div className="bg-white w-full max-w-md rounded-2xl shadow-xl border border-border overflow-hidden">
        <div className="p-8">
          <h2 className="text-xl font-bold text-textMain mb-2">Forgot Password</h2>
          <p className="text-sm text-textMuted mb-8">Enter your work email and we'll send you a link to reset your password.</p>

          {submitted ? (
            <div className="text-center py-6 space-y-4 animate-scale-in">
              <div className="w-16 h-16 bg-blue-100 text-primary rounded-full flex items-center justify-center mx-auto">
                <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
              </div>
              <p className="font-bold text-textMain px-4">If the email exists, a reset link has been sent.</p>
              <div className="pt-6">
                <a href="/" className="text-primary font-bold text-sm hover:underline">Back to Sign In</a>
              </div>
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
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Work Email</label>
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
                  <div className="flex items-center space-x-2">
                    <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin"></div>
                    <span>Sending...</span>
                  </div>
                ) : "Send reset link"}
              </button>

              <div className="text-center pt-2">
                <a href="/" className="text-xs font-bold text-textMuted hover:text-primary transition-colors">
                  Back to Sign In
                </a>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
};
