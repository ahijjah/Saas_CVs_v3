// What each role may do on screen. This mirrors backend/auth/roles.py, which is the authority: the server refuses every action
// listed here for a role that lacks it. Hiding a control is a convenience, never the protection.
//
//   admin        everything in the tenant, including users, subscription and the tenant's audit logs
//   hr_manager   jobs, campaigns, Requirements, applications and results, export, usage
//   recruiter    reads jobs, campaigns, applications, results and Requirements; exports
//   viewer       read-only; no export
//   super_admin  platform administration (kept where it already had access)

export type Role = 'super_admin' | 'admin' | 'hr_manager' | 'recruiter' | 'viewer';

const is = (role: string | null | undefined, allowed: readonly string[]): boolean => !!role && allowed.includes(role);

// Create and change jobs, campaigns and applications (upload, workflow, assignment, notes, tags, comments, interviews, approvals,
// communications, knockout answers, reuse, delete).
const MANAGE_ROLES = ['super_admin', 'admin', 'hr_manager'] as const;
// Requirements are edited by tenant admins and HR managers only.
export const REQUIREMENTS_EDIT_ROLES: readonly string[] = ['admin', 'hr_manager'];
export const REQUIREMENTS_READ_ROLES: readonly string[] = ['admin', 'hr_manager', 'recruiter', 'viewer'];
const EXPORT_ROLES = ['super_admin', 'admin', 'hr_manager', 'recruiter'] as const;

export const canManageJobs = (role: string | null | undefined): boolean => is(role, MANAGE_ROLES);
export const canManageCampaigns = canManageJobs;
export const canDeleteCampaign = (role: string | null | undefined): boolean => is(role, ['super_admin', 'admin']);
export const canMutateApplications = canManageJobs;
export const canEditRequirements = (role: string | null | undefined): boolean => is(role, REQUIREMENTS_EDIT_ROLES);
export const canReadRequirements = (role: string | null | undefined): boolean => is(role, REQUIREMENTS_READ_ROLES);
export const canExportApplications = (role: string | null | undefined): boolean => is(role, EXPORT_ROLES);
// Tenant administration: users and roles, subscription and plan.
export const canManageTenant = (role: string | null | undefined): boolean => role === 'admin';
// A tenant admin reads their own tenant's audit logs; a super admin reads across tenants.
export const canViewAuditLogs = (role: string | null | undefined): boolean => is(role, ['admin', 'super_admin']);
export const canViewUsage = (role: string | null | undefined): boolean => is(role, ['admin', 'hr_manager']);
