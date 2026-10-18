import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProductivityLegend } from '@/components/charts/RecruiterProductivityChart';

// Regression: the legend passed a `payload` prop that Recharts 3 does not accept, so the
// "Top performer" entry was never shown. The legend now renders both entries itself.
describe('ProductivityLegend', () => {
  it('shows both the assigned and the top-performer entries with their swatches', () => {
    const { container } = render(<ProductivityLegend />);
    expect(screen.getByText('Applications assigned')).toBeInTheDocument();
    expect(screen.getByText('Top performer')).toBeInTheDocument();

    const swatches = container.querySelectorAll('span[aria-hidden="true"]');
    expect(swatches).toHaveLength(2);
    expect((swatches[0] as HTMLElement).style.background).toBe('rgb(59, 130, 246)');
    expect((swatches[1] as HTMLElement).style.background).toBe('rgb(16, 185, 129)');
  });
});
