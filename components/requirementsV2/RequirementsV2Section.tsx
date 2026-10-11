import React, { useMemo } from 'react';
import { createRequirementsApi } from '../../services/requirementsV2Api';
import { RequirementsV2Panel } from './RequirementsV2Panel';

// Adapter used by the job page: builds the API client once per token. Only rendered for requirements-v2 jobs.
export const RequirementsV2Section: React.FC<{
  jobId: string; token: string; isAr: boolean; canEdit: boolean; currentUserId?: string | null;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}> = ({ jobId, token, isAr, canEdit, currentUserId, addToast }) => {
  const api = useMemo(() => createRequirementsApi(token), [token]);
  return <RequirementsV2Panel jobId={jobId} api={api} isAr={isAr} canEdit={canEdit} currentUserId={currentUserId} addToast={addToast} />;
};
