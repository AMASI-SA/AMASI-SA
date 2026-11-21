import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import actualConfig from '../../../frontend/vite.config.js';
const repository=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
export default environment=>{const actual=actualConfig(environment);return {...actual,
  root:path.join(repository,'frontend'),
  cacheDir:path.join(os.tmpdir(),'mezan-native-inventory-vite-core'),
  optimizeDeps:{noDiscovery:true,include:['react','react-dom/client','react/jsx-runtime','react-router-dom','axios','@phosphor-icons/react','@radix-ui/react-dialog','lucide-react','sonner']},
  // Fixture-only server: allow only its loopback API and local HMR. The real
  // application's production CSP and deployment configuration are unchanged.
  server:{...actual.server,host:'127.0.0.1',port:4174,strictPort:true,headers:{...actual.server.headers,
    'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data: blob: http://127.0.0.1:4175; connect-src 'self' http://127.0.0.1:4175 ws://127.0.0.1:4174; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  }},
};};
