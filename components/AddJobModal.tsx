
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';

interface AddJobModalProps {
  onClose: () => void;
  onSuccess: () => void;
  token: string;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

type JobType = 'Full-time' | 'Part-time' | 'Contract' | 'Consultancy';
type JobStatus = 'Draft' | 'Open' | 'Closed';

interface FormData {
  job_code: string;
  status: JobStatus;
  client: string;
  job_type: JobType;
  job_duration: string;
  job_location: string;
  job_title: string;
  job_description: string;
  other_information: string;
  end_date: string;
}

export const AddJobModal: React.FC<AddJobModalProps> = ({ onClose, onSuccess, token, addToast }) => {
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState<FormData>({
    job_code: '',
    status: 'Open',
    client: '',
    job_type: 'Full-time',
    job_duration: '',
    job_location: '',
    job_title: '',
    job_description: '',
    other_information: '',
    end_date: ''
  });

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    console.log("CREATE JOB SUBMIT CLICKED");

    try {
      console.log("SUBMIT START");

      const token = localStorage.getItem("token");
      if (!token) {
        alert("Not authenticated. Please login again.");
        return;
      }

      console.log("token exists:", !!token);

      // UI Validation for required fields
      if (!formData.job_code || !formData.job_type || !formData.job_title || !formData.job_description) {
        addToast("Please fill in all required fields.", "error");
        return;
      }

      setLoading(true);

      // Submit Create Job using JWT only (tenant_id is NOT sent)
      const res = await fetch(WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "authorization": `Bearer ${token}`
        },
        body: JSON.stringify({
          job_code: formData.job_code,
          status: formData.status,
          client: formData.client || null,
          job_type: formData.job_type,
          job_duration: formData.job_duration || null,
          job_location: formData.job_location || null,
          job_title: formData.job_title,
          job_description: formData.job_description,
          other_information: formData.other_information || null,
          end_date: formData.end_date || null
        })
      });

      const text = await res.text();
      console.log("RESPONSE STATUS:", res.status);
      console.log("RESPONSE BODY:", text);

      if (!res.ok) {
        throw new Error(`Server responded with ${res.status}: ${text}`);
      }

      addToast("Job campaign created successfully!", "success");
      onSuccess();
    } catch (err: any) {
      console.error("SUBMIT ERROR", err);
      alert(err.message || "Submit failed");
      addToast(err.message || "Failed to create job.", "error");
    } finally {
      setLoading(false);
    }
  };

  const isFormValid = formData.job_code && formData.job_type && formData.job_title && formData.job_description;

  return (
    <div className="fixed inset-0 bg-textMain/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-8 overflow-hidden animate-scale-in">
        <form onSubmit={handleSubmit} className="flex flex-col h-full">
          <div className="px-8 py-6 border-b border-border flex justify-between items-center bg-white sticky top-0 z-10">
            <div>
              <h3 className="text-xl font-bold text-textMain">Create New Job</h3>
              <p className="text-xs text-textMuted uppercase tracking-wider font-semibold mt-0.5">Campaign Details & Candidate Criteria</p>
            </div>
            <button type="button" onClick={onClose} className="text-textMuted hover:text-textMain transition-colors p-2 hover:bg-slate-100 rounded-full">
               <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-8 space-y-6 max-h-[70vh] overflow-y-auto bg-white">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Job Code <span className="text-error font-black">*</span></label>
                <input
                  required
                  name="job_code"
                  type="text"
                  placeholder="ENG-001"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_code}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Status</label>
                <select
                  name="status"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white"
                  value={formData.status}
                  onChange={handleChange}
                >
                  <option value="Open">Open</option>
                  <option value="Draft">Draft</option>
                  <option value="Closed">Closed</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Client</label>
                <input
                  name="client"
                  type="text"
                  placeholder="Company Name"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.client}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Job Type <span className="text-error font-black">*</span></label>
                <select
                  required
                  name="job_type"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white"
                  value={formData.job_type}
                  onChange={handleChange}
                >
                  <option value="Full-time">Full-time</option>
                  <option value="Part-time">Part-time</option>
                  <option value="Contract">Contract</option>
                  <option value="Consultancy">Consultancy</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Job Duration</label>
                <input
                  name="job_duration"
                  type="text"
                  placeholder="e.g. 12 months"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_duration}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Job Title <span className="text-error font-black">*</span></label>
                <input
                  required
                  name="job_title"
                  type="text"
                  placeholder="Senior Backend Developer"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_title}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Job Location</label>
                <input
                  name="job_location"
                  type="text"
                  placeholder="Hybrid / London, UK"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_location}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-textMuted uppercase">Job Description <span className="text-error font-black">*</span></label>
              <textarea
                required
                name="job_description"
                rows={4}
                placeholder="Outline role responsibilities..."
                className="w-full px-4 py-3 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm leading-relaxed"
                value={formData.job_description}
                onChange={handleChange}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">Other Information</label>
                <textarea
                  name="other_information"
                  rows={3}
                  placeholder="Benefits, etc."
                  className="w-full px-4 py-3 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm leading-relaxed"
                  value={formData.other_information}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase">End Date</label>
                <input
                  name="end_date"
                  type="date"
                  className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white"
                  value={formData.end_date}
                  onChange={handleChange}
                />
              </div>
            </div>
          </div>

          <div className="px-8 py-5 bg-slate-50 border-t border-border flex justify-end items-center space-x-4 sticky bottom-0 z-10">
            <button
              type="button"
              onClick={onClose}
              className="px-6 py-2 text-sm font-semibold text-textMuted hover:text-textMain transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !isFormValid}
              className="bg-primary hover:bg-primaryDark disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-10 py-2.5 rounded-lg font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center min-w-[140px]"
            >
              {loading ? (
                <svg className="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : (
                "Submit Job"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
