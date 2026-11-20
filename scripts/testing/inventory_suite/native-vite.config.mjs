import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import actualConfig from '../../../frontend/vite.config.js';
const repository=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../..');
export default environment=>{const actual=actualConfig(environment);return {...actual,
  root:path.join(repository,'frontend'),
  cacheDir:path.join(os.tmpdir(),'mezan-native-inventory-vite-core'),
  optimizeDeps:{noDiscovery:true,include:['react','react-dom/client','react/jsx-runtime','react-router-dom','axios','@phosphor-icons/react','@radix-ui/react-dialog','lucide-react','sonner']},
  server:{...actual.server,host:'127.0.0.1',port:4174,strictPort:true},
};};
