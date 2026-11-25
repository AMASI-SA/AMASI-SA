// Import only from a private Vite Preview config. Never add to product source.
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export default function previewObserver({ sourceRoot, expectedSourceSha, previewOrigin, arm }) {
  if (process.env.MEZAN_PREVIEW_PERF_ENABLED !== '1') throw new Error('Explicit private observer enablement required');
  if (!['baseline', 'candidate'].includes(arm) || !/^[0-9a-f]{40}$/.test(expectedSourceSha || '')) throw new Error('Invalid observer source identity');
  const origin = new URL(previewOrigin);
  const hostname = origin.hostname;
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(hostname);
  if (!(loopback || /^[-a-z0-9]+\.preview\.emergentagent\.com$/.test(hostname))
    || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') throw new Error('Private Preview origin required');
  if (!loopback && origin.protocol !== 'https:') throw new Error('HTTPS Preview required');
  const head = execFileSync('git', ['-C', sourceRoot, 'rev-parse', 'HEAD'], { encoding: 'utf8', timeout: 15000 }).trim();
  if (head !== expectedSourceSha) throw new Error('Preview source does not match expected Git HEAD');
  if (execFileSync('git', ['-C', sourceRoot, 'status', '--porcelain', '--untracked-files=no'], { encoding: 'utf8', timeout: 15000 }).trim()) throw new Error('Tracked Preview source is not clean');
  const assets = new Map([
    ['/__pr2095_observer/observer.mjs', { body: readFileSync(fileURLToPath(new URL('./observer.mjs', import.meta.url))), mime: 'text/javascript; charset=utf-8' }],
    ['/__pr2095_observer/observer.css', { body: readFileSync(fileURLToPath(new URL('./observer.css', import.meta.url))), mime: 'text/css; charset=utf-8' }],
  ]);
  const overlaySha = createHash('sha256').update(assets.get('/__pr2095_observer/observer.mjs').body).digest('hex');
  return {
    name: 'private-pr2095-passive-observer', apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const asset = assets.get((req.url || '').split('?')[0]);
        if (!asset) return next();
        let requestHost;
        try { requestHost = new URL('http://' + req.headers.host).hostname; } catch { requestHost = ''; }
        if (![hostname, 'localhost', '127.0.0.1', '[::1]'].includes(requestHost)) { res.statusCode = 403; return res.end('Preview host required'); }
        if (!['GET', 'HEAD'].includes(req.method)) { res.statusCode = 405; return res.end(); }
        res.setHeader('Content-Type', asset.mime); res.setHeader('Cache-Control', 'no-store');
        res.setHeader('X-Content-Type-Options', 'nosniff');
        res.end(req.method === 'HEAD' ? undefined : asset.body);
      });
    },
    transformIndexHtml() {
      return [
        { tag: 'link', attrs: { rel: 'stylesheet', href: '/__pr2095_observer/observer.css' }, injectTo: 'head' },
        { tag: 'script', attrs: { type: 'module', src: '/__pr2095_observer/observer.mjs',
          'data-mezan-preview-observer': '1', 'data-source-sha': head, 'data-overlay-sha': overlaySha,
          'data-arm': arm, 'data-allowed-host': hostname }, injectTo: 'body' },
      ];
    },
  };
}
