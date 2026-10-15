// Billing UI tests: plan selection, payment page, tenant access guard, provisioning form.
// apiService and fetch are mocked in every test; nothing touches the network.
//   npm install --no-save vitest@3 jsdom@26 @testing-library/react@16 @testing-library/dom @testing-library/user-event@14
//   npm run test:billing
import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    include: ['tests/billing/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/billing/setup.ts'],
    globals: true,
  },
});
