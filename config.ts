
const API_BASE = 'http://72.62.31.221:8000';

export const WEBHOOK_CONFIG = {
  LOGIN_WEBHOOK_URL:             `${API_BASE}/auth/login`,
  REGISTER_WEBHOOK_URL:          `${API_BASE}/auth/register`,
  GET_JOBS_WEBHOOK_URL:          `${API_BASE}/jobs`,
  GET_JOB_DETAILS_WEBHOOK_URL:   `${API_BASE}/jobs/details`,
  CREATE_JOB_WEBHOOK_URL:        `${API_BASE}/jobs`,
  GET_APPLICATIONS_WEBHOOK_URL:  `${API_BASE}/applications`,
  CANDIDATES_SEARCH_URL:         `${API_BASE}/applications`,  // tenant-wide mode (no job_id)
  APPLICATION_DETAILS_WEBHOOK_URL: `${API_BASE}/applications/details`,
  GET_PROFILE_WEBHOOK_URL:       `${API_BASE}/auth/me`,
  UPDATE_PROFILE_WEBHOOK_URL:    `${API_BASE}/auth/me`,
  CHANGE_PASSWORD_WEBHOOK_URL:   `${API_BASE}/auth/change-password`,
  VERIFY_EMAIL_URL:              `${API_BASE}/auth/verify-email`,
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
  TENANT_USAGE_URL:          `${API_BASE}/tenant/usage`,
  // Dashboard
  DASHBOARD_SUMMARY_URL:     `${API_BASE}/dashboard/summary`,
  // Platform Control (super_admin)
  PLATFORM_CONFIG_URL:          `${API_BASE}/admin/platform-config`,
  SUBSCRIPTION_PLANS_URL:       `${API_BASE}/admin/subscription-plans`,
  SUBSCRIPTION_PUBLIC_URL:      `${API_BASE}/subscription/plans`,
  SUBSCRIPTION_SUBSCRIBE_URL:   `${API_BASE}/subscription/subscribe`,
  TENANT_SUBSCRIPTION_BASE_URL: `${API_BASE}/admin/tenants`,
  // Platform Control — Secrets & Credentials
  PLATFORM_SECRETS_URL:         `${API_BASE}/admin/platform-secrets`,
  PLATFORM_SECRETS_RESTART_URL: `${API_BASE}/admin/platform-secrets/restart-services`,
  // Platform Control — AI Prompts
  AI_PROMPTS_URL:               `${API_BASE}/admin/ai-prompts`,
  // Platform Control — Audit Logs
  AUDIT_LOGS_URL:               `${API_BASE}/admin/audit-logs`,
  // Platform Control — AI Usage & Cost
  AI_USAGE_URL:                 `${API_BASE}/admin/ai-usage`,
  AI_PRICING_URL:               `${API_BASE}/admin/ai-usage/pricing`,
  // Platform Control — AI Models
  AI_MODELS_URL:                `${API_BASE}/admin/ai-models`,
  AI_MODELS_REGISTRY_URL:       `${API_BASE}/admin/ai-models/registry`,
  AI_STAGE_DEFAULTS_URL:        `${API_BASE}/admin/ai-models/stage-defaults`,
  AI_PROVIDER_SECRETS_URL:      `${API_BASE}/admin/ai-models/provider-secrets-status`,
  // Job settings
  JOB_SETTINGS_BASE_URL:        `${API_BASE}/jobs`,
  UPDATE_JOB_URL:            `${API_BASE}/jobs`,
  UPDATE_CRITERIA_CONTENT_URL: `${API_BASE}/jobs`,  // append /{job_id}/criteria/content
  // Duplicate submission logs (append /{job_id}/duplicate-logs)
  DUPLICATE_LOGS_BASE_URL:      `${API_BASE}/jobs`,
  // Duplicate CV file download (append /{job_id}/duplicate-logs/{log_id}/cv)
  DUPLICATE_CV_BASE_URL:        `${API_BASE}/jobs`,
  // CV file download (append /{application_id}/cv)
  CV_DOWNLOAD_BASE_URL:         `${API_BASE}/applications`,
  // Workflow status transition (append /{application_id}/workflow-status)
  APPLICATION_WORKFLOW_STATUS_URL: `${API_BASE}/applications`,
  // Recruiter notes (append /{application_id}/recruiter-notes)
  APPLICATION_RECRUITER_NOTES_URL: `${API_BASE}/applications`,
  // Public (no-auth) endpoints
  PUBLIC_JOB_BASE_URL:          `${API_BASE}/jobs/public`,   // append /{job_code}
  PUBLIC_APPLY_URL:             `${API_BASE}/applications/public`,
  // Client organisations (agency/individual_recruiter tenants)
  CLIENT_ORGANIZATIONS_URL:     `${API_BASE}/client-organizations`,
  MY_CLIENT_ASSIGNMENTS_URL:    `${API_BASE}/client-organizations/my/assignments`,
  // Campaigns (optional job grouping layer) — append /{campaign_id} for detail/update/delete
  CAMPAIGNS_URL:                `${API_BASE}/campaigns`,
  // Candidate saved views — append /{saved_view_id} for patch/delete
  CANDIDATE_SAVED_VIEWS_URL:    `${API_BASE}/candidate-saved-views`,
  // Recruiter assignment — append /{application_id}/assignment
  APPLICATION_ASSIGNMENT_URL:   `${API_BASE}/applications`,
  // Bulk assignment
  BULK_ASSIGNMENT_URL:          `${API_BASE}/applications/bulk-assignment`,
  // Assignable users (team members for assignment dropdown)
  ASSIGNABLE_USERS_URL:         `${API_BASE}/applications/assignable-users`,
  // Candidate comments — append /{application_id}/comments[/{comment_id}]
  APPLICATION_COMMENTS_URL:     `${API_BASE}/applications`,
  // Admin fair-usage controls (append /{tenant_id}/fair-usage)
  ADMIN_FAIR_USAGE_BASE_URL:    `${API_BASE}/admin/tenants`,
  // Tenant self-service: subscription
  ACTIVATE_TRIAL_URL:           `${API_BASE}/tenant/subscription/activate-trial`,
  SELECT_PLAN_URL:              `${API_BASE}/tenant/subscription/select-plan`,
  SIMULATE_PAYMENT_URL:         `${API_BASE}/tenant/subscription/simulate-payment`,
};

export const GLOBAL_FORWARDING_EMAIL = 'jobs@ai970.cloud';
