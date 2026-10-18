import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Toast } from '@/components/Toast';

// Regression: callers pass 'warning' (CandidatesWorkspace), which had no style entry, so the
// toast rendered with `bg-undefined` and no background colour.
describe('Toast', () => {
  it('styles a warning toast with the warning background', () => {
    render(<Toast message="Check the answer" type="warning" onClose={vi.fn()} />);
    const text = screen.getByText('Check the answer');
    const container = text.parentElement as HTMLElement;
    expect(container.className).toContain('bg-warning');
    expect(container.className).not.toContain('undefined');
  });

  it.each([
    ['success', 'bg-success'],
    ['error', 'bg-error'],
    ['info', 'bg-primary'],
  ] as const)('keeps the %s background', (type, cls) => {
    render(<Toast message="msg" type={type} onClose={vi.fn()} />);
    expect((screen.getByText('msg').parentElement as HTMLElement).className).toContain(cls);
  });
});
