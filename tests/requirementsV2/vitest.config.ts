// Frontend tests for the requirements-v2 editor. vitest/jsdom/testing-library are NOT project dependencies:
//   npm install --no-save vitest@3 jsdom@26 @testing-library/react@16 @testing-library/dom @testing-library/user-event@14
//   npx vitest run --config tests/requirementsV2/vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    include: ['tests/requirementsV2/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/requirementsV2/setup.ts'],
    globals: true,
  },
});
