import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import RequirementsPanel from '../../components/RequirementsPanel';
import { getRequirements } from '../../services/requirementsApi';
import { view } from './fixtures';

vi.mock('../../services/requirementsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/requirementsApi')>()),
  getRequirements: vi.fn(),
  saveRequirementEdits: vi.fn(),
}));

const getMock = vi.mocked(getRequirements);

function withContext(context: unknown) {
  return view({ document: { ...view().document, context: context as any } });
}

beforeEach(() => getMock.mockReset());

describe('job context is shown read-only and outside scoring', () => {
  it('shows location, work arrangement and reports to, labelled as not scored', async () => {
    getMock.mockResolvedValue(withContext({ location: 'Ramallah', work_arrangement: 'hybrid', reports_to: 'IT Operations Manager' }));
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    const section = await screen.findByTestId('job-context');
    expect(within(section).getByText('Not scored. Shown for reference only.')).toBeInTheDocument();
    expect(within(section).getByText('Ramallah')).toBeInTheDocument();
    expect(within(section).getByText('Hybrid')).toBeInTheDocument();
    expect(within(section).getByText('IT Operations Manager')).toBeInTheDocument();
  });

  it('offers no edit control for the context', async () => {
    getMock.mockResolvedValue(withContext({ location: 'Ramallah', work_arrangement: 'hybrid', reports_to: 'IT Operations Manager' }));
    render(<RequirementsPanel jobId="job-1" userRole="admin" extractionStatus="completed" />);
    const section = await screen.findByTestId('job-context');
    expect(within(section).queryAllByRole('textbox')).toHaveLength(0);
    expect(within(section).queryAllByRole('button')).toHaveLength(0);
  });

  it('shows nothing for a document without context', async () => {
    getMock.mockResolvedValue(view());
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    await screen.findByRole('heading', { name: 'Job analysis' });
    expect(screen.queryByTestId('job-context')).toBeNull();
  });

  it('shows nothing when the context holds no value', async () => {
    getMock.mockResolvedValue(withContext({ location: null, work_arrangement: null, reports_to: null }));
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    await screen.findByRole('heading', { name: 'Job analysis' });
    expect(screen.queryByTestId('job-context')).toBeNull();
  });

  it('renders Arabic values as written, in their own direction', async () => {
    getMock.mockResolvedValue(withContext({ location: 'رام الله', work_arrangement: 'hybrid', reports_to: 'مدير عمليات تقنية المعلومات' }));
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    const section = await screen.findByTestId('job-context');
    const place = within(section).getByText('رام الله');
    expect(place).toHaveAttribute('dir', 'auto');
    expect(within(section).getByText('مدير عمليات تقنية المعلومات')).toHaveAttribute('dir', 'auto');
  });

  it('does not change the scored categories or the readiness', async () => {
    getMock.mockResolvedValue(withContext({ location: 'Ramallah', work_arrangement: 'hybrid', reports_to: null }));
    render(<RequirementsPanel jobId="job-1" userRole="recruiter" extractionStatus="completed" />);
    await screen.findByTestId('job-context');
    expect(screen.queryAllByRole('textbox', { name: 'Requirement text' })).toHaveLength(0);
    expect(screen.getByText('Python')).toBeInTheDocument();
    expect(screen.getByText('SQL')).toBeInTheDocument();
    expect(screen.getByTestId('analysis-ready')).toBeInTheDocument();
    expect(screen.queryByText('Ramallah', { selector: 'textarea,input' })).toBeNull();
  });
});
