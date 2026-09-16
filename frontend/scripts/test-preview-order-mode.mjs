import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../src/demo/orderPreviewFixtures.js', import.meta.url), 'utf8');
const { getPreviewDemoMode } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
let checked = 0;
for (const [host, search, stored, expected] of [
  ['salla-analytics.preview.emergentagent.com', '', null, false],
  ['salla-analytics.preview.emergentagent.com', '?mock=1', null, true],
  ['salla-analytics.preview.emergentagent.com', '?mock=0', 'mock', false],
  ['salla-analytics.preview.emergentagent.com', '', 'mock', true],
  ['salla-analytics.preview.emergentagent.com', '', 'live', false],
  ['mezansalla.com', '?mock=1', 'mock', false],
]) {
  globalThis.window = { location: { hostname: host, search }, localStorage: { getItem: () => stored } };
  assert.equal(getPreviewDemoMode(), expected, `${host} ${search} ${stored}`);
  checked++;
}
globalThis.window = { location: { hostname: 'salla-analytics.preview.emergentagent.com', search: '' }, localStorage: { getItem: () => { throw new Error('storage unavailable'); } } };
assert.equal(getPreviewDemoMode(), false);
delete globalThis.window;
assert.equal(getPreviewDemoMode(), false);
console.log(`PASS: ${checked + 2} preview order mode cases`);
