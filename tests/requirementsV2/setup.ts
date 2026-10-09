import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
// jsdom has no layout engine
Element.prototype.scrollIntoView = vi.fn();
// jsdom lacks CSS.escape (user-event needs it for radio-group arrow keys)
const g = globalThis as any;
g.CSS = g.CSS || {};
g.CSS.escape = g.CSS.escape || ((v: string) => String(v).replace(/[^a-zA-Z0-9_-]/g, c => `\\${c}`));
