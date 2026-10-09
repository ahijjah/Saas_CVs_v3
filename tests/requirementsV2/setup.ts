import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
// jsdom has no layout engine
Element.prototype.scrollIntoView = vi.fn();
