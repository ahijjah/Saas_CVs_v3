import React, { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User } from '../types';
import { ToastType } from '../components/Toast';

interface VerifyEmailProps {
  onVerifySuccess: (token: string, user: User) => void;
  addToast: (msg: string, type: ToastType) => void;
}

export const VerifyEmail: React.FC<VerifyEmailProps> = ({ onVerifySuccess, addToast }) => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token');

  const [status, setStatus] = useState<'verifying' | 'success' | 'error'>('verifying');
  const [errorMsg, setErrorMsg] = useState('');

  useEffect(() => {
    if (!token) {
      setStatus('error');
      setErrorMsg('No verification token found in the link.');
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const response = await apiService.get(
          `${WEBHOOK_CONFIG.VERIFY_EMAIL_URL}`,
          { token },
        );
        if (cancelled) return;
        if (response?.token) {
          const userData: User = {
            ...response.user,
            must_change_password: response.must_change_password ?? false,
          };
          setStatus('success');
          addToast('Email verified! Welcome to CV Analyzer.', 'success');
          onVerifySuccess(response.token, userData);
        } else {
          throw new Error('Verification failed — no token returned.');
        }
      } catch (err: any) {
        if (cancelled) return;
        setStatus('error');
        setErrorMsg(err.message || 'Invalid or expired verification link.');
      }
    })();

    return () => { cancelled = true; };
  }, [token]);

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl border border-border p-10 max-w-sm w-full text-center">
        {status === 'verifying' && (
          <>
            <div className="w-14 h-14 rounded-full bg-indigo-100 flex items-center justify-center mx-auto mb-5">
              <div className="w-7 h-7 border-3 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            </div>
            <h2 className="text-lg font-bold text-textMain mb-2">Verifying your email…</h2>
            <p className="text-sm text-textMuted">Please wait a moment.</p>
          </>
        )}

        {status === 'success' && (
          <>
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center mx-auto mb-5">
              <svg className="w-7 h-7 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-lg font-bold text-textMain mb-2">Email verified!</h2>
            <p className="text-sm text-textMuted">Redirecting you now…</p>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="w-14 h-14 rounded-full bg-red-100 flex items-center justify-center mx-auto mb-5">
              <svg className="w-7 h-7 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <h2 className="text-lg font-bold text-textMain mb-2">Verification failed</h2>
            <p className="text-sm text-textMuted mb-6">{errorMsg}</p>
            <button
              onClick={() => navigate('/login')}
              className="w-full py-2.5 bg-primary text-white text-sm font-bold rounded-xl hover:bg-primaryDark transition-colors"
            >
              Back to Login
            </button>
          </>
        )}
      </div>
    </div>
  );
};
