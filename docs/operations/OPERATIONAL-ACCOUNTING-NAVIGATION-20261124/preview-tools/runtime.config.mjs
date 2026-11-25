// Private source-serving Preview only. This is not a governed release config.
import baseConfig from '../source/frontend/vite.config.js';
import previewObserver from './preview-observer-plugin.mjs';
import { fileURLToPath } from 'node:url';

const sourceRoot = fileURLToPath(new URL('../source', import.meta.url));
export default async function privatePreviewConfig(environment) {
  const config = typeof baseConfig === 'function' ? await baseConfig(environment) : baseConfig;
  return {
    ...config,
    root: fileURLToPath(new URL('../source/frontend', import.meta.url)),
    plugins: [...(config.plugins || []), previewObserver({
      sourceRoot,
      expectedSourceSha: '9a851854132bd71be922c4aae24b4cc20a546e41',
      previewOrigin: 'https://salla-analytics.preview.emergentagent.com',
      arm: 'candidate',
    })],
    server: { ...config.server, hmr: false },
  };
}
