import path from 'path';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Kept separate from vite.config.ts so the production build configuration is unchanged.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, '.') } },
  test: {
    environment: 'jsdom',
    include: ['tests/frontend/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/frontend/setup.ts'],
    clearMocks: true,
  },
});
