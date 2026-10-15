import { afterEach, expect, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllEnvs(); });
// jsdom has no layout engine
Element.prototype.scrollIntoView = vi.fn();
// jsdom lacks CSS.escape (user-event needs it for radio-group arrow keys)
const g = globalThis as any;
g.CSS = g.CSS || {};
g.CSS.escape = g.CSS.escape || ((v: string) => String(v).replace(/[^a-zA-Z0-9_-]/g, c => `\\${c}`));

// Minimal DOM matchers so the billing tests need no extra dependency (no jest-dom in this repo).
expect.extend({
  toBeInTheDocument(received: unknown) {
    const pass = received instanceof Element && received.ownerDocument.contains(received);
    return { pass, message: () => `expected element ${pass ? 'not ' : ''}to be in the document` };
  },
  toBeDisabled(received: Element) {
    const pass = (received as HTMLButtonElement).disabled === true;
    return { pass, message: () => `expected element ${pass ? 'not ' : ''}to be disabled` };
  },
  toBeEnabled(received: Element) {
    const pass = (received as HTMLButtonElement).disabled !== true;
    return { pass, message: () => `expected element ${pass ? 'not ' : ''}to be enabled` };
  },
  toHaveTextContent(received: Element, expected: string | RegExp) {
    const text = received.textContent ?? '';
    const pass = typeof expected === 'string' ? text.includes(expected) : expected.test(text);
    return { pass, message: () => `expected text ${pass ? 'not ' : ''}to match ${String(expected)}` };
  },
  toHaveAttribute(received: Element, name: string, expected?: unknown) {
    if (!received.hasAttribute(name)) return { pass: false, message: () => `expected attribute ${name}` };
    if (expected === undefined) return { pass: true, message: () => `expected no attribute ${name}` };
    const pass = this.equals(received.getAttribute(name), expected);
    return { pass, message: () => `expected attribute ${name} ${pass ? 'not ' : ''}to equal ${String(expected)}` };
  },
});
