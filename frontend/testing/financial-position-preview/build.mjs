// Compile the real page into a self-contained, synthetic-only browser harness.
// This never invokes the governed production build or reads application env.
import {build} from 'vite';
import {copyFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const root = fileURLToPath(new URL('./', import.meta.url));
const output = resolve(root, '../../../.evidence/financial-preview-site');
await build({
  root,
  configFile: resolve(root, 'vite.config.mjs'),
  publicDir: false,
  build: {outDir: output, emptyOutDir: true},
});
await copyFile(resolve(root, 'server.mjs'), resolve(output, 'server.mjs'));
await copyFile(resolve(root, 'fixture.json'), resolve(output, 'fixture.json'));
await copyFile(resolve(root, 'compact.html'), resolve(output, 'compact.html'));
await writeFile(resolve(output, 'package.json'), JSON.stringify({
  name: 'synthetic-financial-position-preview', private: true, type: 'module',
  scripts: {dev: 'node server.mjs'},
}, null, 2) + '\n');
console.log('Synthetic preview compiled to ' + output);
