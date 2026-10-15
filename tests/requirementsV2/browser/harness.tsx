// TEST-ONLY page: mounts the real RequirementsV2Section (real services/requirementsV2Api + services/api) for a job.
//   ?job=<uuid>&token=<test token>&lang=en|ar&edit=1|0
import React from 'react';
import { createRoot } from 'react-dom/client';
import { RequirementsV2Section } from '../../../components/requirementsV2/RequirementsV2Section';

const q = new URLSearchParams(location.search);
const toasts: { msg: string; type: string }[] = [];
(window as any).__toasts = toasts;
const isAr = q.get('lang') === 'ar';
document.documentElement.lang = isAr ? 'ar' : 'en';

createRoot(document.getElementById('root')!).render(
  <RequirementsV2Section jobId={q.get('job') || ''} token={q.get('token') || ''} isAr={isAr} canEdit={q.get('edit') !== '0'}
                         currentUserId={q.get('uid')} addToast={(msg, type) => { toasts.push({ msg, type }); }} />,
);
