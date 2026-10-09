import React from 'react';

// Decorative icons (aria-hidden): meaning is always also given in text.
const base = { width: 16, height: 16, fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const, viewBox: '0 0 24 24', 'aria-hidden': true, focusable: false };

export const BriefcaseIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><rect x="2" y="7" width="20" height="14" rx="2" /><path d="M16 7V5a2 2 0 00-2-2h-4a2 2 0 00-2 2v2" /><path d="M2 13h20" /></svg>
);
export const WarnIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" /><path d="M12 9v4M12 17h.01" /></svg>
);
export const InfoIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></svg>
);
export const CheckIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="10" /><path d="M8 12l3 3 5-6" /></svg>
);
export const ChevronIcon = ({ open, ...p }: React.SVGProps<SVGSVGElement> & { open?: boolean }) => (
  <svg {...base} {...p} style={{ transform: open ? 'rotate(180deg)' : undefined, transition: 'transform .15s' }}><path d="M6 9l6 6 6-6" /></svg>
);
export const QuoteIcon = (p: React.SVGProps<SVGSVGElement>) => (
  <svg {...base} {...p}><path d="M4 6h16M4 12h10M4 18h16" /></svg>
);
