import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  envDir:false, plugins:[react()],
  cacheDir:'.operational-preview-cache',
  optimizeDeps:{entries:['operational-preview.html']},
  build:{rollupOptions:{input:'operational-preview.html'}},
  define:{'process.env.REACT_APP_BACKEND_URL':JSON.stringify('http://127.0.0.1:8135'),'process.env.NODE_ENV':JSON.stringify('development')},
  server:{host:'127.0.0.1',port:5178,strictPort:true},
});
