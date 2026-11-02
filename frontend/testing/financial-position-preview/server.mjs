// Isolated preview only: one synthetic GET response plus compiled static files.
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {extname, resolve, sep} from 'node:path';

const root = fileURLToPath(new URL('./', import.meta.url));
const argument = process.argv.indexOf('--port');
const port = argument < 0 ? 4173 : Number(process.argv[argument + 1]);
const types = {'.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8',
  '.css':'text/css; charset=utf-8', '.json':'application/json; charset=utf-8', '.woff2':'font/woff2'};
createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405).end();
    return;
  }
  let pathname;
  try { pathname = decodeURIComponent(new URL(request.url, 'http://preview.local').pathname); }
  catch { response.writeHead(400).end(); return; }
  const relative = pathname === '/api/operational-balances/financial-position' ? 'fixture.json'
    : pathname === '/' ? 'index.html' : pathname.slice(1);
  const target = resolve(root, relative);
  if (!target.startsWith(root.endsWith(sep) ? root : root + sep)
      || !['index.html', 'fixture.json', 'compact.html'].includes(relative) && !relative.startsWith('assets/')) {
    response.writeHead(404).end();
    return;
  }
  try {
    const body = await readFile(target);
    response.writeHead(200, {'Content-Type':types[extname(target)] || 'application/octet-stream', 'Cache-Control':'no-store'});
    response.end(request.method === 'HEAD' ? undefined : body);
  } catch { response.writeHead(404).end(); }
}).listen(port, '0.0.0.0', () => console.log('Synthetic-only preview listening on ' + port));
