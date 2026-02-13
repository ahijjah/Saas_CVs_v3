
/**
 * Global Configuration for Webhook URLs.
 */
export const WEBHOOK_CONFIG = {
  LOGIN_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/login',
  REGISTER_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/register',
  GET_JOBS_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/jobs',
  GET_JOB_DETAILS_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/job-details',
  CREATE_JOB_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/jobs/create',
  GET_APPLICATIONS_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/applications2',
  APPLICATION_DETAILS_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/application-details',
  GET_PROFILE_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/me',
  UPDATE_PROFILE_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/me',
  CHANGE_PASSWORD_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/me/change-password',
  RESET_PASSWORD_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/reset-password',
  FORGOT_PASSWORD_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/forgot-password',
  // Super Admin Endpoints
  ADMIN_DASHBOARD_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/admin/dashboard',
  ADMIN_TENANTS_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/admin/tenants',
  ADMIN_USERS_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/admin/users',
  ADMIN_USER_STATUS_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/admin/users/status',
  ADMIN_TENANT_STATUS_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/admin/tenants/status'
};

export const GLOBAL_FORWARDING_EMAIL = 'jobs@ai970.cloud';
