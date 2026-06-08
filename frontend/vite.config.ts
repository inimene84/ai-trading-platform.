import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss()],
    define: {
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY),
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
      },
    },
  };
});
