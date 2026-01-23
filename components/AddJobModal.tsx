
import React, { useState } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';

interface AddJobModalProps {
  onClose: () => void;
  onSuccess: () => void;
  token: string;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

export const AddJobModal: React.FC<AddJobModalProps> = ({ onClose, onSuccess, token, addToast }) => {
  const [loading, setLoading] = useState(false);
  const [formData, setFormData] = useState({
    job_title: '',
    job_client: '',
    description: '',
    requirements: '',
    location: '',
    salary_range: ''
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await apiService.post(WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL, formData, token);
      addToast("Job created successfully!", "success");
      onSuccess();
    } catch (err: any) {
      addToast(err.message || "Failed to create job.", "error");
      // Simulation for demo
      setTimeout(() => {
        addToast("Demo Mode: Simulated successful creation.", "success");
        onSuccess();
      }, 1000);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-textMain/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden animate-scale-in">
        <div className="px-8 py-6 border-b border-border flex justify-between items-center">
          <h3 className="text-xl font-bold text-textMain">Create New Job Campaign</h3>
          <button onClick={onClose} className="text-textMuted hover:text-textMain">
             <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-8 space-y-6 max-h-[70vh] overflow-y-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="text-xs font-bold text-textMuted uppercase">Job Title</label>
              <input
                required
                type="text"
                placeholder="e.g. Senior Product Designer"
                className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                value={formData.job_title}
                onChange={(e) => setFormData({...formData, job_title: e.target.value})}
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-bold text-textMuted uppercase">Client Name</label>
              <input
                required
                type="text"
                placeholder="e.g. Acme Innovations"
                className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                value={formData.job_client}
                onChange={(e) => setFormData({...formData, job_client: e.target.value})}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="space-y-2">
              <label className="text-xs font-bold text-textMuted uppercase">Location</label>
              <input
                required
                type="text"
                placeholder="e.g. London / Remote"
                className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                value={formData.location}
                onChange={(e) => setFormData({...formData, location: e.target.value})}
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-bold text-textMuted uppercase">Salary Range</label>
              <input
                type="text"
                placeholder="e.g. £60,000 - £80,000"
                className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
                value={formData.salary_range}
                onChange={(e) => setFormData({...formData, salary_range: e.target.value})}
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-bold text-textMuted uppercase">Job Description</label>
            <textarea
              required
              rows={4}
              placeholder="Detailed description of the role..."
              className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all resize-none"
              value={formData.description}
              onChange={(e) => setFormData({...formData, description: e.target.value})}
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-bold text-textMuted uppercase">Technical Requirements (One per line)</label>
            <textarea
              required
              rows={4}
              placeholder="List the key skills required..."
              className="w-full px-4 py-2 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all resize-none"
              value={formData.requirements}
              onChange={(e) => setFormData({...formData, requirements: e.target.value})}
            />
          </div>
        </form>

        <div className="px-8 py-6 bg-slate-50 border-t border-border flex justify-end space-x-4">
          <button
            type="button"
            onClick={onClose}
            className="px-6 py-2 text-sm font-semibold text-textMuted hover:text-textMain transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="bg-primary hover:bg-primaryDark disabled:opacity-50 disabled:cursor-not-allowed text-white px-8 py-2 rounded-lg font-bold shadow-lg shadow-primary/20 transition-all flex items-center"
          >
            {loading && (
              <svg className="animate-spin -ml-1 mr-3 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
            )}
            Launch Campaign
          </button>
        </div>
      </div>
    </div>
  );
};
