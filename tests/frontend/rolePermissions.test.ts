import { describe, expect, it } from 'vitest';
import {
  canDeleteCampaign, canEditRequirements, canExportApplications, canManageCampaigns, canManageJobs, canManageTenant,
  canMutateApplications, canReadRequirements, canViewAuditLogs, canViewUsage,
} from '../../utils/permissions';

// role -> [manage jobs/campaigns/applications, edit requirements, read requirements, export, tenant admin, audit logs, usage, delete campaign]
const MATRIX: Record<string, boolean[]> = {
  admin:       [true,  true,  true,  true,  true,  true,  true,  true],
  hr_manager:  [true,  true,  true,  true,  false, false, true,  false],
  recruiter:   [false, false, true,  true,  false, false, false, false],
  viewer:      [false, false, true,  false, false, false, false, false],
  super_admin: [true,  false, false, true,  false, true,  false, true],
};
const CHECKS = [canManageJobs, canEditRequirements, canReadRequirements, canExportApplications, canManageTenant, canViewAuditLogs, canViewUsage, canDeleteCampaign];

describe('role permissions on the client mirror the approved matrix', () => {
  for (const [role, expected] of Object.entries(MATRIX)) {
    it(`${role}`, () => {
      expect(CHECKS.map(check => check(role))).toEqual(expected);
    });
  }

  it('refuses a missing, empty or unknown role everywhere', () => {
    for (const role of [null, undefined, '', 'owner', 'Admin', 'ADMIN']) {
      expect(CHECKS.map(check => check(role as string | null | undefined))).toEqual(CHECKS.map(() => false));
    }
  });

  it('keeps campaigns and applications on the same rule as jobs', () => {
    for (const role of Object.keys(MATRIX)) {
      expect(canManageCampaigns(role)).toBe(canManageJobs(role));
      expect(canMutateApplications(role)).toBe(canManageJobs(role));
    }
  });
});
