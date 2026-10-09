// Builds the test-only harness page:  npx vite build --config tests/requirementsV2/browser/vite.config.ts --outDir <dir>
import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  root: __dirname,
  base: './',
  plugins: [react()],
  build: { emptyOutDir: true, rollupOptions: { input: path.resolve(__dirname, 'harness.html') } },
});
