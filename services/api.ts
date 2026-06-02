
import { WEBHOOK_CONFIG } from '../config';

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

async function handleResponse(response: Response) {
  // Always try to parse JSON to get rich error messages from the backend
  let data;
  try {
    data = await response.json();
  } catch (e) {
    data = null;
  }

  // Handle Unauthorized — only 401 means the session/token is invalid
  if (response.status === 401) {
    const isLoginEndpoint = response.url.includes('login');
    if (!isLoginEndpoint) {
      console.warn('Session expired. Clearing storage.');
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      localStorage.removeItem('cv_analyzer_auth');
      window.location.reload();
      return new Promise(() => {});
    }
  }
  // 403 Forbidden = valid session but insufficient permissions; let it fall through to error handling

  if (!response.ok) {
    // Throw an error with the backend's specific message if available
    const detail = Array.isArray(data?.detail) ? data.detail.map((d: any) => d.msg).join(', ') : data?.detail;
    const errorMsg = detail || data?.message || data?.error || `API Error: ${response.status} ${response.statusText}`;
    throw new Error(errorMsg);
  }

  return data;
}

export const apiService = {
  async post(url: string, data: any, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    return handleResponse(response);
  },

  async put(url: string, data: any, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const response = await fetch(url, {
      method: 'PUT',
      headers,
      body: JSON.stringify(data),
    });
    return handleResponse(response);
  },

  async get(url: string, params: Record<string, string> = {}, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const queryString = new URLSearchParams(params).toString();
    const fullUrl = queryString ? `${url}?${queryString}` : url;

    const response = await fetch(fullUrl, {
      method: 'GET',
      headers,
    });
    return handleResponse(response);
  },

  async patch(url: string, data: any, token?: string) {
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) headers['Authorization'] = `Bearer ${activeToken}`;
    const response = await fetch(url, { method: 'PATCH', headers, body: JSON.stringify(data) });
    return handleResponse(response);
  },

  async postForm(url: string, data: FormData, token?: string) {
    const headers: HeadersInit = {};
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }
    const response = await fetch(url, { method: 'POST', headers, body: data });
    return handleResponse(response);
  },

  async delete(url: string, token?: string) {
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) headers['Authorization'] = `Bearer ${activeToken}`;
    const response = await fetch(url, { method: 'DELETE', headers });
    return handleResponse(response);
  },

  async requestPasswordReset(email: string) {
    return this.post(WEBHOOK_CONFIG.FORGOT_PASSWORD_WEBHOOK_URL, { email });
  }
};

export class APIService {
  token: string | null;

  constructor(token: string | null = null) {
    this.token = token;
  }

  async getFunnelMetrics(filters: {
    job_id?: string;
    campaign_id?: string;
    recruiter_id?: string;
    date_from?: string;
    date_to?: string;
  }) {
    const params: Record<string, string> = {};
    if (filters.date_from) params.date_from = filters.date_from;
    if (filters.date_to) params.date_to = filters.date_to;
    if (filters.job_id) params.job_id = filters.job_id;
    if (filters.campaign_id) params.campaign_id = filters.campaign_id;
    if (filters.recruiter_id) params.recruiter_id = filters.recruiter_id;

    return apiService.get(WEBHOOK_CONFIG.ANALYTICS_FUNNEL_URL, params, this.token);
  }

  async getRecruiterProductivity(filters: {
    recruiter_id?: string;
    date_from?: string;
    date_to?: string;
  }) {
    const params: Record<string, string> = {};
    if (filters.date_from) params.date_from = filters.date_from;
    if (filters.date_to) params.date_to = filters.date_to;
    if (filters.recruiter_id) params.recruiter_id = filters.recruiter_id;

    return apiService.get(WEBHOOK_CONFIG.ANALYTICS_RECRUITER_PRODUCTIVITY_URL, params, this.token);
  }

  async getAgingMetrics(filters: {
    workflow_status?: string;
    days_threshold?: number;
  }) {
    const params: Record<string, string> = {};
    if (filters.workflow_status) params.workflow_status = filters.workflow_status;
    if (filters.days_threshold !== undefined) params.days_threshold = String(filters.days_threshold);

    return apiService.get(WEBHOOK_CONFIG.ANALYTICS_AGING_URL, params, this.token);
  }

  async getInsights() {
    return apiService.get(WEBHOOK_CONFIG.ANALYTICS_INSIGHTS_URL, {}, this.token);
  }

  // ── Candidate Tags ────────────────────────────────────────────────────────

  async getApplicationTags(applicationId: string) {
    const url = `${WEBHOOK_CONFIG.TAGS_URL.replace('/tags', '')}/applications/${applicationId}/tags`;
    return apiService.get(url, {}, this.token);
  }

  async addTagsToApplication(applicationId: string, tagIds: string[]) {
    const url = `${WEBHOOK_CONFIG.TAGS_URL.replace('/tags', '')}/applications/${applicationId}/tags`;
    return apiService.post(url, { tag_ids: tagIds }, this.token);
  }

  async removeTagFromApplication(applicationId: string, tagId: string) {
    const url = `${WEBHOOK_CONFIG.TAGS_URL.replace('/tags', '')}/applications/${applicationId}/tags/${tagId}`;
    return apiService.delete(url, this.token);
  }

  async listTags(search?: string) {
    const params: Record<string, string> = {};
    if (search) params.search = search;
    return apiService.get(WEBHOOK_CONFIG.TAGS_URL, params, this.token);
  }

  async createTag(tagName: string, color?: string) {
    return apiService.post(WEBHOOK_CONFIG.TAGS_URL, { tag_name: tagName, color }, this.token);
  }

  async deleteTag(tagId: string) {
    return apiService.delete(`${WEBHOOK_CONFIG.TAGS_URL}/${tagId}`, this.token);
  }

  // ── Talent Pool ───────────────────────────────────────────────────────────

  async getTalentPool(skip: number = 0, limit: number = 50) {
    const params: Record<string, string> = {
      skip: String(skip),
      limit: String(limit),
    };
    return apiService.get(WEBHOOK_CONFIG.TALENT_POOL_URL, params, this.token);
  }

  async toggleTalentPool(applicationId: string, isTalentPool: boolean) {
    const url = `${WEBHOOK_CONFIG.TALENT_POOL_URL.replace('/applications/talent-pool', '')}/applications/${applicationId}/talent-pool`;
    return apiService.post(url, { is_talent_pool: isTalentPool }, this.token);
  }

  async reuseCandidate(applicationId: string, targetJobId: string) {
    const url = `${WEBHOOK_CONFIG.TALENT_POOL_URL.replace('/applications/talent-pool', '')}/applications/${applicationId}/reuse-to-job`;
    return apiService.post(url, { target_job_id: targetJobId }, this.token);
  }

  // ── Communication Templates ───────────────────────────────────────────────

  async listTemplates(params: { category?: string; active_only?: boolean } = {}) {
    const p: Record<string, string> = {};
    if (params.category) p.category = params.category;
    if (params.active_only === false) p.active_only = 'false';
    return apiService.get(WEBHOOK_CONFIG.COMMUNICATION_TEMPLATES_URL, p, this.token);
  }

  async createTemplate(data: { name: string; category: string; subject: string; body: string; language?: string; is_active?: boolean }) {
    return apiService.post(WEBHOOK_CONFIG.COMMUNICATION_TEMPLATES_URL, data, this.token);
  }

  async updateTemplate(templateId: string, data: Partial<{ name: string; category: string; subject: string; body: string; language: string; is_active: boolean }>) {
    return apiService.patch(`${WEBHOOK_CONFIG.COMMUNICATION_TEMPLATES_URL}/${templateId}`, data, this.token);
  }

  async deleteTemplate(templateId: string) {
    return apiService.delete(`${WEBHOOK_CONFIG.COMMUNICATION_TEMPLATES_URL}/${templateId}`, this.token);
  }

  // ── Candidate Communications ──────────────────────────────────────────────

  async listCommunications(applicationId: string) {
    return apiService.get(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications`, {}, this.token);
  }

  async logCommunication(applicationId: string, data: { subject?: string; body?: string; status?: string; template_id?: string }) {
    return apiService.post(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications/log`, data, this.token);
  }

  async sendCommunication(applicationId: string, data: { subject: string; body: string; to_email?: string; template_id?: string }) {
    return apiService.post(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications/send`, data, this.token);
  }

  async updateCommunication(applicationId: string, communicationId: string, data: { subject?: string; body?: string; template_id?: string }) {
    return apiService.patch(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications/${communicationId}`, data, this.token);
  }

  async sendExistingCommunication(applicationId: string, communicationId: string) {
    return apiService.post(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications/${communicationId}/send`, {}, this.token);
  }

  async deleteCommunication(applicationId: string, communicationId: string) {
    return apiService.delete(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/communications/${communicationId}`, this.token);
  }

  async updatePreferredContact(applicationId: string, data: {
    preferred_contact_email?: string | null;
    preferred_contact_source?: string;
    preferred_contact_locked?: boolean;
  }) {
    return apiService.patch(`${WEBHOOK_CONFIG.COMMUNICATION_BASE_URL}/${applicationId}/preferred-contact`, data, this.token);
  }
};
