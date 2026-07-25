import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the local backend so the app can always use
// relative paths. In production nginx performs the same proxying, which keeps
// the client code identical in both environments.
//
// The backend publishes on 8011 rather than 8000 because 8000 is commonly
// already taken. Override with DEV_API_TARGET when yours runs elsewhere.
//
// Declared locally instead of pulling in @types/node, which would be the only
// reason this project needed it.
declare const process: { env: Record<string, string | undefined> };

const apiTarget = process.env['DEV_API_TARGET'] ?? 'http://localhost:8011';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // Server sent events are streamed, so no response buffering here.
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});
