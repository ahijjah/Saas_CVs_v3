import React, { useState, useEffect } from 'react';
import { APIService } from '../services/api';
import { TalentPoolResponse, TalentPoolCandidate } from '../types';
import { usePageTitle } from '../context/PageTitleContext';

interface TalentPoolProps {
  auth: any;
  addToast: (message: string, type: 'success' | 'error' | 'warning' | 'info') => void;
}

const TalentPool: React.FC<TalentPoolProps> = ({ auth, addToast }) => {
  const { setPageTitle } = usePageTitle();

  useEffect(() => {
    setPageTitle('Talent Pool');
    return () => { setPageTitle(null); };
  }, [setPageTitle]);

  const [candidates, setCandidates] = useState<TalentPoolCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [skip, setSkip] = useState(0);
  const [total, setTotal] = useState(0);
  const pageSize = 50;

  const api = new APIService(auth.token);

  const loadTalentPool = async () => {
    try {
      setLoading(true);
      const response: TalentPoolResponse = await api.getTalentPool(skip, pageSize);
      setCandidates(response.candidates);
      setTotal(response.total);
    } catch (error: any) {
      addToast(error.message || 'Failed to load talent pool', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTalentPool();
  }, [skip]);

  if (loading) {
    return <div className="p-6 text-center">Loading talent pool...</div>;
  }

  if (candidates.length === 0) {
    return (
      <div className="p-6 text-center">
        <p className="text-gray-500">No candidates in talent pool yet.</p>
        <p className="text-sm text-gray-400 mt-2">
          Add candidates to the talent pool from the Candidates Workspace for future reuse.
        </p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Talent Pool</h1>
        <p className="text-sm text-gray-500 mt-1">
          {total} candidate{total !== 1 ? 's' : ''} available for reuse
        </p>
      </div>

      <div className="overflow-x-auto shadow rounded-lg border border-gray-200">
        <table className="w-full bg-white">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Name</th>
              <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Email</th>
              <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Last Job</th>
              <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Status</th>
              <th className="px-6 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Assigned To</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((candidate) => (
              <tr key={candidate.application_id} className="border-b border-gray-200 hover:bg-gray-50">
                <td className="px-6 py-4 text-sm font-medium text-gray-900">{candidate.candidate_name}</td>
                <td className="px-6 py-4 text-sm text-gray-600">{candidate.candidate_email}</td>
                <td className="px-6 py-4 text-sm text-gray-600">{candidate.job_title}</td>
                <td className="px-6 py-4 text-sm">
                  <span className="px-2 py-1 text-xs font-semibold bg-blue-100 text-blue-800 rounded">
                    {candidate.workflow_status.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-6 py-4 text-sm text-gray-600">
                  {candidate.assigned_user_name || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {total > pageSize && (
        <div className="mt-4 flex items-center justify-between">
          <button
            onClick={() => setSkip(Math.max(0, skip - pageSize))}
            disabled={skip === 0}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <span className="text-sm text-gray-600">
            Showing {skip + 1}–{Math.min(skip + pageSize, total)} of {total}
          </span>
          <button
            onClick={() => setSkip(skip + pageSize)}
            disabled={skip + pageSize >= total}
            className="px-4 py-2 bg-gray-200 text-gray-700 rounded disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
};

export { TalentPool };
