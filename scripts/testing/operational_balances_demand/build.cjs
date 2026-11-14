/* Isolated component acceptance fixture, never a governed production artifact. */
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.OPERATIONAL_BALANCE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) throw Error('Absolute repo-external fixture output required');
(async () => {
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  const {default: react} = await import(pathToFileURL(req.resolve('@vitejs/plugin-react')).href);
  await build({configFile:false, root:__dirname, plugins:[react()],
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},
      {find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},
      {find:'react-dom/client',replacement:req.resolve('react-dom/client')}]},
    build:{outDir:path.join(out,'dist'),emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files = ['backend/operational_financial_position.py', 'backend/operational_financial_position_core.py',
    'backend/operational_financial_position_views.py', 'backend/operational_financial_position_state.py',
    'backend/operational_balance_routes.py', 'backend/operational_balance_completeness.py',
    'backend/operational_delivery_confirmation.py',
    'frontend/src/pages/operational/OperationalFinancialPosition.jsx', 'frontend/src/pages/operational/PendingOrdersDialog.jsx',
    'frontend/src/pages/operational/api.js', 'frontend/src/pages/operational/visibleOperationalRead.js',
    'frontend/src/pages/operational/financialPositionViews.js', 'frontend/src/pages/operational/financialPositionPresentation.js',
    'frontend/src/lib/safeSpreadsheet.js',
    'frontend/src/pages/operational/financialPosition.css',
    'scripts/testing/operational_balances_demand/fixture.py', 'scripts/testing/operational_balances_demand/build.cjs',
    'scripts/testing/operational_balances_demand/browser.cjs', 'scripts/testing/operational_balances_demand/main.jsx',
    'scripts/testing/operational_balances_demand/index.html'];
  fs.writeFileSync(path.join(out,'source-manifest.json'), JSON.stringify(Object.fromEntries(files.map(file =>
    [file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])), null, 2));
})().catch(error=>{console.error(error);process.exitCode=1;});
