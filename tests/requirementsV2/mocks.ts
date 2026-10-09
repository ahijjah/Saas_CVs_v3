import { vi, type Mock } from 'vitest';
import type { RequirementsApi, RequirementsView } from '../../services/requirementsV2Api';

export type MockApi = Record<keyof RequirementsApi, Mock>;
export const mockApi = (initial: RequirementsView): MockApi => ({
  get: vi.fn().mockResolvedValue(initial),
  save: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1, changed: true }),
  acknowledge: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  confirmStructure: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
  confirmNoScore: vi.fn().mockResolvedValue({ ...initial, revision: initial.revision + 1 }),
});

