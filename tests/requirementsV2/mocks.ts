import { vi, type Mock } from 'vitest';
import type { RecoveryVersion, RequirementsApi, RequirementsView } from '../../services/requirementsV2Api';

export type MockApi = Record<keyof RequirementsApi, Mock>;
export const mockApi = (initial: RequirementsView): MockApi => ({
  get: vi.fn().mockResolvedValue(initial),
  save: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1, changed: true }),
  acknowledge: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  confirmStructure: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  confirmNoScore: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  retryExtraction: vi.fn().mockResolvedValue({ job_id: initial.job_id, extraction: { status: 'pending' } }),
  requestRecovery: vi.fn().mockResolvedValue({ version: recoveryVersion({ status: 'requested' }), idempotent: false }),
  mergePreview: vi.fn().mockResolvedValue({ version_id: VERSION_ID, revision: initial.revision, conflicts: [], draft_only: [], candidate_only: [], weights: [] }),
  applyRecovery: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  keepRecovery: vi.fn().mockResolvedValue({ kept: true, version_id: VERSION_ID }),
  discardRecovery: vi.fn().mockResolvedValue({ discarded: true, version_id: VERSION_ID, idempotent: false }),
});

export const VERSION_ID = '00000000-0000-0000-0000-0000000000c1';
export const recoveryVersion = (over: Partial<RecoveryVersion> = {}): RecoveryVersion => ({
  version_id: VERSION_ID, version_no: 2, kind: 'candidate', status: 'completed', requested_at: '2026-10-13T09:00:00+00:00',
  completed_at: '2026-10-13T09:01:00+00:00', request_key: '44444444-4444-4444-4444-444444444444', input_sha256: 'ab'.repeat(32),
  job_revision_at_request: 3, ...over,
});

