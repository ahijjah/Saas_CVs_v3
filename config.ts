
const API_BASE = 'https://api.ai970.cloud';

export const WEBHOOK_CONFIG = {
  LOGIN_WEBHOOK_URL:             `${API_BASE}/auth/login`,
  REGISTER_WEBHOOK_URL:          `${API_BASE}/auth/register`,
  GET_JOBS_WEBHOOK_URL:          `${API_BASE}/jobs`,
  GET_JOB_DETAILS_WEBHOOK_URL:   `${API_BASE}/jobs/details`,
  CREATE_JOB_WEBHOOK_URL:        `${API_BASE}/jobs`,
  GET_APPLICATIONS_WEBHOOK_URL:  `${API_BASE}/applications`,
  APPLICATION_DETAILS_WEBHOOK_URL: `${API_BASE}/applications/details`,
  GET_PROFILE_WEBHOOK_URL:       `${API_BASE}/auth/me`,
  UPDATE_PROFILE_WEBHOOK_URL:    `${API_BASE}/auth/me`,
  CHANGE_PASSWORD_WEBHOOK_URL:   `${API_BASE}/auth/change-password`,
  RESET_PASSWORD_WEBHOOK_URL:    `${API_BASE}/auth/reset-password`,
  FORGOT_PASSWORD_WEBHOOK_URL:   `${API_BASE}/auth/forgot-password`,
  // Admin endpoints
  ADMIN_DASHBOARD_URL:       `${API_BASE}/admin/dashboard`,
  ADMIN_TENANTS_URL:         `${API_BASE}/admin/tenants`,
  ADMIN_USERS_URL:           `${API_BASE}/admin/users`,
  ADMIN_USER_STATUS_URL:     `${API_BASE}/admin/users/status`,
  ADMIN_TENANT_STATUS_URL:   `${API_BASE}/admin/tenants/status`,
  // New endpoints (no n8n equivalent)
  CV_UPLOAD_URL:             `${API_BASE}/applications/upload`,
  ADMIN_CREATE_TENANT_URL:   `${API_BASE}/admin/tenants`,
  ADMIN_CREATE_USER_URL:     `${API_BASE}/admin/users`,
};

export const GLOBAL_FORWARDING_EMAIL = 'jobs@ai970.cloud';
