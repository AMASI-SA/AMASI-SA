import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  root: 'testing/bundles', envDir: false, plugins: [react()],
  define: { 'process.env.REACT_APP_BACKEND_URL': JSON.stringify(''), 'process.env.NODE_ENV': JSON.stringify('development') },
  server: { host: '127.0.0.1', port: 4317, strictPort: true,
    fs: { allow: ['.'] }, proxy: { '/api': 'http://127.0.0.1:8017' } },
});
