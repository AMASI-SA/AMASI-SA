import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

const fixture = fileURLToPath(new URL('./fixture.json', import.meta.url));
export default defineConfig({
  envDir:false,
  cacheDir:'../../../.evidence/financial-position-vite-cache',
  plugins:[react(),{
    name:'isolated-financial-position-fixture',
    configureServer(server){
      server.middlewares.use((req,res,next)=>{
        if(req.url?.split('?')[0] !== '/api/operational-balances/financial-position') return next();
        res.setHeader('Content-Type','application/json; charset=utf-8');
        res.setHeader('Cache-Control','no-store');
        res.end(readFileSync(fixture));
      });
    },
  }],
  define:{'process.env.REACT_APP_BACKEND_URL':JSON.stringify(''),'process.env.NODE_ENV':JSON.stringify('development')},
  server:{host:'0.0.0.0',allowedHosts:['terminal.local'],port:4173,strictPort:true,fs:{allow:[fileURLToPath(new URL('../../',import.meta.url))]}},
});
