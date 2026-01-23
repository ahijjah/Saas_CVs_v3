
/**
 * Global Configuration for Webhook URLs.
 * In a real-world scenario, these would be loaded from process.env 
 * or a configuration endpoint.
 */
export const WEBHOOK_CONFIG = {
  LOGIN_WEBHOOK_URL: 'https://n8n.example.com/webhook/login',
  REGISTER_WEBHOOK_URL: 'https://n8n.ai970.cloud/webhook/cv-saas/register',
  GET_JOBS_WEBHOOK_URL: 'https://n8n.example.com/webhook/jobs',
  GET_JOB_DETAILS_WEBHOOK_URL: 'https://n8n.example.com/webhook/job-details',
  CREATE_JOB_WEBHOOK_URL: 'https://n8n.example.com/webhook/create-job',
  GET_APPLICATIONS_WEBHOOK_URL: 'https://n8n.example.com/webhook/applications'
};

// Mock data generator for demo purposes if webhooks aren't reachable
// (But the app will attempt real calls first as requested)
