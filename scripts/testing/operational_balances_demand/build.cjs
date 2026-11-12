/* Isolated component acceptance fixture, never a governed production artifact. */
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.OPERATIONAL_BALANCE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || out.startsWith(root + path.sep)) throw Error('Absolute repo-external fixture output required');
(async () => {
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  const {default: react} = await import(pathToFileURL(req.resolve('@vitejs/plugin-react')).href);
  await build({configFile:false, root:__dirname, plugins:[react()],
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},
      {find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},
      {find:'react-dom/client',replacement:req.resolve('react-dom/client')}]},
    build:{outDir:path.join(out,'dist'),emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files = ['backend/operational_financial_position_core.py', 'backend/operational_balance_completeness.py',
    'backend/operational_delivery_confirmation.py',
    'frontend/src/pages/operational/OperationalFinancialPosition.jsx', 'frontend/src/pages/operational/PendingOrdersDialog.jsx',
    'frontend/src/pages/operational/api.js', 'frontend/src/pages/operational/visibleOperationalRead.js',
    'frontend/src/pages/operational/financialPosition.css'];
  fs.writeFileSync(path.join(out,'source-manifest.json'), JSON.stringify(Object.fromEntries(files.map(file =>
    [file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])), null, 2));
})().catch(error=>{console.error(error);process.exitCode=1;});
