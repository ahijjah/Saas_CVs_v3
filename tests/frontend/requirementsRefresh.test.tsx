import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import RequirementsPanel from '../../components/RequirementsPanel';
import { getRequirements, RequirementsApiError } from '../../services/requirementsApi';
import { view } from './fixtures';

vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(),
  saveRequirementEdits: vi.fn(),
}));

const getMock = vi.mocked(getRequirements);
const notYet = () => new RequirementsApiError(404, 'not_found', 'Requirements not found.', null);

beforeEach(() => getMock.mockReset());

describe('the requirements replace the waiting state without a browser refresh', () => {
  it('shows the requirements once extraction completes after the job was created', async () => {
    getMock.mockRejectedValueOnce(notYet()).mockResolvedValue(view());
    const { rerender } = render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="pending" />);
    expect(await screen.findByText(/being generated/)).toBeInTheDocument();

    rerender(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    expect(await screen.findByRole('heading', { name: 'Job analysis' })).toBeInTheDocument();
    expect(screen.queryByText(/being generated/)).toBeNull();
  });

  it('shows the regenerated requirements after a retry: failed, then pending, then completed', async () => {
    getMock.mockRejectedValueOnce(notYet()).mockRejectedValueOnce(notYet()).mockResolvedValue(view());
    const { rerender } = render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="failed" />);
    expect(await screen.findByRole('button', { name: 'Reload requirements' })).toBeInTheDocument();

    rerender(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="pending" />);
    expect(await screen.findByText(/being generated/)).toBeInTheDocument();

    rerender(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    expect(await screen.findByRole('heading', { name: 'Job analysis' })).toBeInTheDocument();
  });

  it('does not fetch again while the status is unchanged (no duplicate requests)', async () => {
    getMock.mockResolvedValue(view());
    const { rerender } = render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    await screen.findByRole('heading', { name: 'Job analysis' });
    const calls = getMock.mock.calls.length;
    rerender(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    await act(async () => { await Promise.resolve(); });
    expect(getMock.mock.calls.length).toBe(calls);
  });

  it('offers a manual reload when the requirements cannot be loaded and extraction is not running', async () => {
    getMock.mockRejectedValueOnce(new Error('Connection lost.')).mockResolvedValue(view());
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    const reload = await screen.findByRole('button', { name: 'Reload requirements' });
    await act(async () => { reload.click(); });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('heading', { name: 'Job analysis' })).toBeInTheDocument();
  });
});
