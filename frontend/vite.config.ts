import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss()],
    build: {
      rolldownOptions: {
        output: {
          // Split long-lived vendor code out of the entry chunk so a change to
          // app code doesn't invalidate the whole download for returning users.
          advancedChunks: {
            groups: [
              { name: 'vendor-react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
              { name: 'vendor-flow', test: /node_modules[\\/]@xyflow[\\/]/ },
              { name: 'vendor-motion', test: /node_modules[\\/]motion/ },
              { name: 'vendor-icons', test: /node_modules[\\/]lucide-react[\\/]/ },
              { name: 'vendor-genai', test: /node_modules[\\/]@google[\\/]genai[\\/]/ },
            ],
          },
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      proxy: {
        // Proxy all /api/backend requests to the FastAPI backend
        '/api/backend': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api\/backend/, ''),
        },
        // Direct /api/news and /api/market-data (same FastAPI backend)
        '/api/news': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
        '/api/market-data': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
        '/api/historical': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
        '/api/telemetry': {
          target: env.BACKEND_URL || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  };
});
