
const API_BASE = 'http://72.62.31.221:8000';

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
  UPLOADED_CVS_URL:          `${API_BASE}/applications/uploaded`,
  SCORE_PENDING_URL:         `${API_BASE}/applications/score-pending`,
  QUEUE_STATUS_URL:          `${API_BASE}/applications/queue-status`,
  RESET_STUCK_URL:           `${API_BASE}/applications/reset-stuck`,
  DELETE_APPLICATION_URL:    `${API_BASE}/applications`,
  ADMIN_CREATE_TENANT_URL:   `${API_BASE}/admin/tenants`,
  ADMIN_CREATE_USER_URL:     `${API_BASE}/admin/users`,
  // CV receiving ingestion settings (append /{job_id}/ingestion)
  JOB_INGESTION_BASE_URL:    `${API_BASE}/jobs`,
  // Tenant user management
  TENANT_USERS_URL:          `${API_BASE}/tenant/users`,
  // Platform Control (super_admin)
  PLATFORM_CONFIG_URL:          `${API_BASE}/admin/platform-config`,
  SUBSCRIPTION_PLANS_URL:       `${API_BASE}/admin/subscription-plans`,
  TENANT_SUBSCRIPTION_BASE_URL: `${API_BASE}/admin/tenants`,
  // Platform Control — Secrets & Credentials
  PLATFORM_SECRETS_URL:         `${API_BASE}/admin/platform-secrets`,
  PLATFORM_SECRETS_RESTART_URL: `${API_BASE}/admin/platform-secrets/restart-services`,
  // Platform Control — AI Prompts
  AI_PROMPTS_URL:               `${API_BASE}/admin/ai-prompts`,
  // Platform Control — Audit Logs
  AUDIT_LOGS_URL:               `${API_BASE}/admin/audit-logs`,
  // Job settings
  JOB_SETTINGS_BASE_URL:        `${API_BASE}/jobs`,
  // Duplicate submission logs (append /{job_id}/duplicate-logs)
  DUPLICATE_LOGS_BASE_URL:      `${API_BASE}/jobs`,
  // CV file download (append /{application_id}/cv)
  CV_DOWNLOAD_BASE_URL:         `${API_BASE}/applications`,
};

export const GLOBAL_FORWARDING_EMAIL = 'jobs@ai970.cloud';
