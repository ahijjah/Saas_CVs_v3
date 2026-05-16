
import React, { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';

const API_BASE = 'http://72.62.31.221:8000';

interface PublicJobApplyProps {
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

interface PublicJobInfo {
  job_title: string;
  job_code: string;
  job_client?: string;
  location?: string;
  job_type?: string;
  work_mode?: string;
  experience_level?: string;
  description?: string;
  application_deadline?: string;
}

export const PublicJobApply: React.FC<PublicJobApplyProps> = ({ addToast }) => {
  const { jobCode } = useParams<{ jobCode: string }>();
  const [job, setJob] = useState<PublicJobInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [form, setForm] = useState({
    candidate_name: '',
    email: '',
    phone: '',
    cover_letter: '',
  });
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    if (!jobCode) return;
    fetch(`${API_BASE}/jobs/public/${jobCode}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(data => setJob(data))
      .catch(() => setJob(null))
      .finally(() => setLoading(false));
  }, [jobCode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) { addToast('Please attach your CV.', 'error'); return; }
    if (!form.candidate_name.trim() || !form.email.trim()) {
      addToast('Name and email are required.', 'error'); return;
    }
    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('job_code', jobCode!);
      fd.append('candidate_name', form.candidate_name.trim());
      fd.append('email', form.email.trim());
      if (form.phone.trim()) fd.append('phone', form.phone.trim());
      if (form.cover_letter.trim()) fd.append('cover_letter', form.cover_letter.trim());
      fd.append('file', file);
      const resp = await fetch(`${API_BASE}/applications/public`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Submission failed.');
      }
      setSubmitted(true);
      addToast('Application submitted successfully!', 'success');
    } catch (err: any) {
      addToast(err.message || 'Failed to submit application.', 'error');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-10 h-10 border-4 border-primary/20 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-8 text-center">
        <div className="bg-white rounded-3xl shadow-sm border border-border p-10 max-w-md w-full">
          <div className="w-12 h-12 bg-red-100 rounded-xl flex items-center justify-center mx-auto mb-4">
            <svg className="w-6 h-6 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <h2 className="text-lg font-black text-textMain mb-2">Position Not Found</h2>
          <p className="text-sm text-textMuted">This job posting may have closed or the link is incorrect.</p>
        </div>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center p-8 text-center">
        <div className="bg-white rounded-3xl shadow-sm border border-border p-10 max-w-md w-full">
          <div className="w-14 h-14 bg-green-100 rounded-xl flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-xl font-black text-textMain mb-2">Application Received!</h2>
          <p className="text-sm text-textMuted">
            Thank you for applying to <span className="font-bold text-textMain">{job.job_title}</span>. We'll review your application and be in touch.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 py-12 px-4">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="bg-white rounded-3xl shadow-sm border border-border p-8 mb-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 bg-primary rounded-xl flex items-center justify-center text-white font-black text-lg">C</div>
            <span className="text-xs font-black text-textMuted uppercase tracking-widest">CV Analyzer</span>
          </div>
          <h1 className="text-2xl font-black text-textMain mb-1">{job.job_title}</h1>
          {(job.job_client || job.location) && (
            <p className="text-sm text-textMuted mb-3">
              {[job.job_client, job.location].filter(Boolean).join(' · ')}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {job.job_type && (
              <span className="px-3 py-1 bg-slate-100 text-textMuted text-[11px] font-bold rounded-lg">{job.job_type}</span>
            )}
            {job.work_mode && (
              <span className="px-3 py-1 bg-blue-50 text-primary text-[11px] font-bold rounded-lg">{job.work_mode}</span>
            )}
            {job.experience_level && (
              <span className="px-3 py-1 bg-violet-50 text-violet-700 text-[11px] font-bold rounded-lg">{job.experience_level}</span>
            )}
            {job.application_deadline && (
              <span className="px-3 py-1 bg-amber-50 text-amber-700 text-[11px] font-bold rounded-lg">
                Deadline: {new Date(job.application_deadline).toLocaleDateString()}
              </span>
            )}
          </div>
          {job.description && (
            <details className="mt-4">
              <summary className="text-xs font-black text-primary cursor-pointer hover:text-primaryDark uppercase tracking-widest">View Job Description</summary>
              <p className="mt-3 text-sm text-textMain leading-relaxed opacity-80 whitespace-pre-wrap">{job.description}</p>
            </details>
          )}
        </div>

        {/* Application Form */}
        <div className="bg-white rounded-3xl shadow-sm border border-border p-8">
          <h2 className="text-sm font-black text-textMain uppercase tracking-widest mb-6 flex items-center">
            <span className="w-2 h-4 bg-primary rounded-full mr-3" />
            Your Application
          </h2>
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="space-y-1">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Full Name *</label>
                <input
                  type="text"
                  required
                  value={form.candidate_name}
                  onChange={e => setForm(p => ({ ...p, candidate_name: e.target.value }))}
                  className="w-full px-4 py-2.5 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  placeholder="Your full name"
                />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Email Address *</label>
                <input
                  type="email"
                  required
                  value={form.email}
                  onChange={e => setForm(p => ({ ...p, email: e.target.value }))}
                  className="w-full px-4 py-2.5 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                  placeholder="you@example.com"
                />
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Phone (optional)</label>
              <input
                type="tel"
                value={form.phone}
                onChange={e => setForm(p => ({ ...p, phone: e.target.value }))}
                className="w-full px-4 py-2.5 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
                placeholder="+1 555 000 0000"
              />
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">CV / Resume *</label>
              <label className="flex items-center gap-3 px-4 py-3 border-2 border-dashed border-border rounded-xl cursor-pointer hover:border-primary/50 hover:bg-slate-50 transition-colors">
                <svg className="w-5 h-5 text-textMuted shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                </svg>
                <span className="text-sm text-textMuted flex-1 truncate">
                  {file ? file.name : 'Attach CV (PDF, DOC, DOCX)'}
                </span>
                <input
                  type="file"
                  accept=".pdf,.doc,.docx"
                  className="hidden"
                  onChange={e => setFile(e.target.files?.[0] || null)}
                />
              </label>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-black text-textMuted uppercase tracking-widest">Cover Letter (optional)</label>
              <textarea
                rows={4}
                value={form.cover_letter}
                onChange={e => setForm(p => ({ ...p, cover_letter: e.target.value }))}
                className="w-full px-4 py-2.5 border border-border rounded-xl text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none resize-none"
                placeholder="Tell us why you're a great fit..."
              />
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3 bg-primary text-white font-black rounded-xl hover:bg-primaryDark transition-colors disabled:opacity-60 flex items-center justify-center gap-2"
            >
              {submitting && (
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              )}
              {submitting ? 'Submitting...' : 'Submit Application'}
            </button>
          </form>
        </div>

        <p className="text-center text-[11px] text-textMuted mt-6">
          Job reference: <span className="font-mono font-bold">{jobCode}</span>
        </p>
      </div>
    </div>
  );
};
