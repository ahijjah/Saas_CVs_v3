
import React, { useState, useEffect } from 'react';
import { apiService } from '../services/api';
import { WEBHOOK_CONFIG } from '../config';
import { User, ClientOrganization } from '../types';
import { useLanguage } from '../context/LanguageContext';

interface AddJobModalProps {
  onClose: () => void;
  onSuccess: (jobId: string) => void;
  token: string;
  user: User | null;
  addToast: (msg: string, type: 'success' | 'error') => void;
}

interface FormData {
  job_title: string;
  job_description: string;
  client: string;
  job_location: string;
  job_type: string;
  job_duration: string;
  client_organization_id: string;
}

const T = {
  en: {
    title: 'Create New Job',
    subtitle: 'Campaign Details & Candidate Criteria',
    jobTitle: 'Job Title',
    clientOrg: 'Client Organization',
    clientOrgPlaceholder: 'Select client…',
    clientOrgRequired: 'Client organization is required for agency tenants.',
    noClientsWarning: 'You must create a client organization before creating a job.',
    department: 'Department / Client',
    jobLocation: 'Location',
    jobType: 'Job Type',
    jobTypePlaceholder: 'Select type…',
    jobTypes: ['Full-time', 'Part-time', 'Contract', 'Freelance', 'Internship', 'Temporary'],
    duration: 'Duration',
    jobDesc: 'Job Description',
    jobDescPlaceholder: 'Describe the role, responsibilities, required skills and experience…',
    cancel: 'Cancel',
    creating: 'Creating…',
    submit: 'Create Job',
    errorTitle: 'Job title is required.',
    errorDesc: 'Job description is required.',
  },
  ar: {
    title: 'إنشاء وظيفة جديدة',
    subtitle: 'تفاصيل الحملة ومعايير المرشحين',
    jobTitle: 'المسمى الوظيفي',
    clientOrg: 'منظمة العميل',
    clientOrgPlaceholder: 'اختر عميلاً…',
    clientOrgRequired: 'منظمة العميل مطلوبة لمستأجري الوكالات.',
    noClientsWarning: 'يجب إنشاء منظمة عميل قبل إنشاء وظيفة.',
    department: 'القسم / العميل',
    jobLocation: 'الموقع',
    jobType: 'نوع الوظيفة',
    jobTypePlaceholder: 'اختر النوع…',
    jobTypes: ['دوام كامل', 'دوام جزئي', 'عقد', 'مستقل', 'تدريب', 'مؤقت'],
    duration: 'المدة',
    jobDesc: 'وصف الوظيفة',
    jobDescPlaceholder: 'صف الدور والمسؤوليات والمهارات والخبرات المطلوبة…',
    cancel: 'إلغاء',
    creating: 'جارٍ الإنشاء…',
    submit: 'إنشاء الوظيفة',
    errorTitle: 'المسمى الوظيفي مطلوب.',
    errorDesc: 'وصف الوظيفة مطلوب.',
  },
};

export const AddJobModal: React.FC<AddJobModalProps> = ({ onClose, onSuccess, token, user, addToast }) => {
  const { lang } = useLanguage();
  const t = T[lang];

  // Derive agency status directly from the user prop — no async needed.
  // tenant_type is populated from the JWT / profile fetch when the user logs in.
  const tenantType = user?.tenant_type ?? '';
  const isAgencyTenant = tenantType === 'agency' || tenantType === 'individual_recruiter';

  const [loading, setLoading] = useState(false);
  const [clientOrgs, setClientOrgs] = useState<ClientOrganization[]>([]);
  const [clientOrgsLoading, setClientOrgsLoading] = useState(false);

  const [formData, setFormData] = useState<FormData>({
    job_title: '',
    job_description: '',
    client: '',
    job_location: '',
    job_type: '',
    job_duration: '',
    client_organization_id: '',
  });

  // Only fetch client orgs for agency/recruiter tenants
  useEffect(() => {
    if (!token || !isAgencyTenant) return;
    setClientOrgsLoading(true);
    apiService.get(WEBHOOK_CONFIG.CLIENT_ORGANIZATIONS_URL, {}, token)
      .then((data: any) => {
        // API returns { client_organizations: [...], total: N }
        const orgs: ClientOrganization[] = data?.client_organizations ?? [];
        setClientOrgs(orgs.filter(o => o.status === 'active'));
      })
      .catch(() => { /* 403 for org tenants is expected and silently ignored */ })
      .finally(() => setClientOrgsLoading(false));
  }, [token, isAgencyTenant]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleClientOrgChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const orgId = e.target.value;
    const org = clientOrgs.find(o => o.client_organization_id === orgId);
    setFormData(prev => ({
      ...prev,
      client_organization_id: orgId,
      // Keep department in sync with org name for backend compatibility
      client: org ? org.organization_name : '',
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const title = formData.job_title.trim();
    const description = formData.job_description.trim();

    if (!title) { addToast(t.errorTitle, 'error'); return; }
    if (!description) { addToast(t.errorDesc, 'error'); return; }
    if (isAgencyTenant && !formData.client_organization_id) {
      addToast(t.clientOrgRequired, 'error'); return;
    }

    setLoading(true);
    try {
      const payload: Record<string, string | null> = { title, description };
      if (formData.client_organization_id) payload.client_organization_id = formData.client_organization_id;
      if (formData.client.trim()) payload.department = formData.client.trim();
      if (formData.job_location.trim()) payload.location = formData.job_location.trim();
      if (formData.job_type) payload.job_type = formData.job_type;
      if (formData.job_duration.trim()) payload.duration = formData.job_duration.trim();

      const responseData = await apiService.post(WEBHOOK_CONFIG.CREATE_JOB_WEBHOOK_URL, payload, token);
      addToast('Job created successfully!', 'success');
      onSuccess(responseData.job_id || '');
    } catch (err: any) {
      const errorMsg = err.name === 'TypeError' && err.message === 'Failed to fetch'
        ? 'Network error. The creation service is unreachable.'
        : (err.message || 'Failed to create job.');
      addToast(errorMsg, 'error');
    } finally {
      setLoading(false);
    }
  };

  // Validation:
  // - job_title and job_description are always required
  // - client_organization_id is required only for agency/recruiter tenants
  // - agency tenants must also have at least one active client org loaded (otherwise button blocked with warning shown)
  const hasTitle = formData.job_title.trim().length > 0;
  const hasDesc = formData.job_description.trim().length > 0;
  const clientOrgSatisfied = !isAgencyTenant || !!formData.client_organization_id;
  const hasClientsAvailable = !isAgencyTenant || clientOrgs.length > 0;
  const isFormValid = hasTitle && hasDesc && clientOrgSatisfied && hasClientsAvailable;

  return (
    <div className="fixed inset-0 bg-textMain/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl my-8 overflow-hidden animate-scale-in">
        <form onSubmit={handleSubmit} className="flex flex-col h-full">
          {/* Header */}
          <div className="px-8 py-6 border-b border-border flex justify-between items-center bg-white sticky top-0 z-10">
            <div>
              <h3 className="text-xl font-bold text-textMain">{t.title}</h3>
              <p className="text-xs text-textMuted uppercase tracking-wider font-semibold mt-0.5">{t.subtitle}</p>
            </div>
            <button type="button" onClick={onClose} className="text-textMuted hover:text-textMain transition-colors p-2 hover:bg-slate-100 rounded-full">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-8 space-y-5 max-h-[70vh] overflow-y-auto bg-white">

            {/* Agency tenant: no active clients warning */}
            {isAgencyTenant && !clientOrgsLoading && clientOrgs.length === 0 && (
              <div className="flex items-center gap-3 px-4 py-3 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800">
                <svg className="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
                <span>{t.noClientsWarning}</span>
              </div>
            )}

            {/* Row 1: Job Title + Client Org (agency) or Department (org tenant) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                  {t.jobTitle} <span className="text-error">*</span>
                </label>
                <input
                  required
                  name="job_title"
                  type="text"
                  placeholder="Senior Backend Developer"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_title}
                  onChange={handleChange}
                />
              </div>

              {isAgencyTenant ? (
                /* Agency/recruiter: required client org dropdown */
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                    {t.clientOrg} <span className="text-error">*</span>
                  </label>
                  {clientOrgsLoading ? (
                    <div className="w-full px-4 py-2.5 border border-border rounded-lg text-sm text-textMuted bg-slate-50">
                      Loading clients…
                    </div>
                  ) : (
                    <select
                      value={formData.client_organization_id}
                      onChange={handleClientOrgChange}
                      className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white"
                      disabled={clientOrgs.length === 0}
                    >
                      <option value="">{t.clientOrgPlaceholder}</option>
                      {clientOrgs.map(org => (
                        <option key={org.client_organization_id} value={org.client_organization_id}>
                          {org.organization_name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              ) : (
                /* Organization tenant: optional free-text department */
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                    {t.department}
                  </label>
                  <input
                    name="client"
                    type="text"
                    placeholder="Engineering / Finance"
                    className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                    value={formData.client}
                    onChange={handleChange}
                  />
                </div>
              )}
            </div>

            {/* Row 2: Location + Job Type + Duration */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.jobLocation}</label>
                <input
                  name="job_location"
                  type="text"
                  placeholder="Hybrid / Riyadh, SA"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_location}
                  onChange={handleChange}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.jobType}</label>
                <select
                  name="job_type"
                  value={formData.job_type}
                  onChange={handleChange}
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm bg-white"
                >
                  <option value="">{t.jobTypePlaceholder}</option>
                  {t.jobTypes.map(jt => (
                    <option key={jt} value={jt}>{jt}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-textMuted uppercase tracking-widest">{t.duration}</label>
                <input
                  name="job_duration"
                  type="text"
                  placeholder="Permanent"
                  className="w-full px-4 py-2.5 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm"
                  value={formData.job_duration}
                  onChange={handleChange}
                />
              </div>
            </div>

            {/* Row 3: Description */}
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-textMuted uppercase tracking-widest">
                {t.jobDesc} <span className="text-error">*</span>
              </label>
              <textarea
                required
                name="job_description"
                rows={7}
                placeholder={t.jobDescPlaceholder}
                className="w-full px-4 py-3 border border-border rounded-lg focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all text-sm leading-relaxed"
                value={formData.job_description}
                onChange={handleChange}
              />
              <p className="text-xs text-textMuted">
                {formData.job_description.trim().length > 0
                  ? `${formData.job_description.trim().length} chars — AI will extract criteria automatically`
                  : 'The more detail you provide, the better the AI scoring will be.'}
              </p>
            </div>
          </div>

          {/* Footer */}
          <div className="px-8 py-5 bg-slate-50 border-t border-border flex justify-end items-center gap-4 sticky bottom-0 z-10">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-6 py-2 text-sm font-semibold text-textMuted hover:text-textMain transition-colors disabled:opacity-50"
            >
              {t.cancel}
            </button>
            <button
              type="submit"
              disabled={loading || !isFormValid}
              className="bg-primary hover:bg-primaryDark disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-10 py-2.5 rounded-lg font-bold shadow-lg shadow-primary/20 transition-all flex items-center justify-center min-w-[140px]"
            >
              {loading ? (
                <>
                  <svg className="animate-spin h-5 w-5 text-white mr-2" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {t.creating}
                </>
              ) : t.submit}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
