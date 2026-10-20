import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ApplicationDetails } from '../../pages/ApplicationDetails';
import { LanguageProvider } from '../../context/LanguageContext';

const DATA = {
  application_id: 'app-1', candidate_name: 'Sam Candidate', job_title: 'IT Support', workflow_status: 'awaiting_review',
  processing_status: 'ai_scored', recruiter_notes: 'Strong on Python', ai_score: 70, knockout_questions: [],
} as any;

function renderDetails(role: string) {
  return render(
    <MemoryRouter><LanguageProvider>
      <ApplicationDetails data={DATA} onBack={() => {}} token="t" userRole={role} advancedMoveEnabled={false}
        onWorkflowStatusChange={vi.fn()} onRecruiterNotesChange={vi.fn()} />
    </LanguageProvider></MemoryRouter>,
  );
}

describe('an application on screen', () => {
  it.each(['admin', 'hr_manager'])('lets %s save notes and move the application', (role) => {
    renderDetails(role);
    expect(screen.getByRole('button', { name: 'Save Notes' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('Strong on Python')).not.toHaveAttribute('readonly');
    expect(screen.getByText('Recruiter Workflow')).toBeInTheDocument();
  });

  it.each(['recruiter', 'viewer'])('shows %s the notes read-only, with no save and no workflow controls', (role) => {
    renderDetails(role);
    expect(screen.queryByRole('button', { name: 'Save Notes' })).toBeNull();
    expect(screen.getByDisplayValue('Strong on Python')).toHaveAttribute('readonly');
    expect(screen.queryByText('Recruiter Workflow')).toBeNull();
  });
});
