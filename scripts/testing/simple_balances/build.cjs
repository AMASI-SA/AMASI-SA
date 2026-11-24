/* Isolated component acceptance fixture; never a production/release artifact. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.SIMPLE_BALANCES_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) {
  throw Error('Absolute repo-external synthetic output required');
}
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const { build } = await import(pathToFileURL(req.resolve('vite')).href);
  const { default: react } = await import(pathToFileURL(req.resolve('@vitejs/plugin-react')).href);
  await build({ configFile: false, root: __dirname, plugins: [react()],
    resolve: { alias: [
      { find: /^react$/, replacement: req.resolve('react') },
      { find: 'react/jsx-runtime', replacement: req.resolve('react/jsx-runtime') },
      { find: 'react-dom/client', replacement: req.resolve('react-dom/client') },
      { find: 'react-router-dom', replacement: req.resolve('react-router-dom') },
    ] },
    build: { outDir: path.join(out, 'dist'), emptyOutDir: false },
    define: { 'process.env.NODE_ENV': '"production"', 'process.env.REACT_APP_BACKEND_URL': '""' },
  });
  const files = [
    ...['ApprovedFinance.jsx','ApprovedEntry.jsx','ApprovedLedger.jsx','PaymentSourceFields.jsx','PendingEntryActions.jsx',
      'workspaceReadHooks.js','WorkspaceResourcePicker.jsx','WorkspaceSnapshot.jsx']
      .map(file => `frontend/src/pages/operational/${file}`),
    ...['BalanceFreshnessNotice.jsx', 'BalanceAutomationSettings.jsx', 'BalanceRecoveryProof.jsx',
      'balanceFreshness.css', 'balanceFreshnessApi.js',
      'useBalanceFreshnessExpiry.js', 'useVisibleBalanceFreshness.js', 'visibleBalanceFreshness.js']
      .map(file => `frontend/src/pages/operational/${file}`),
    'frontend/src/pages/operational/SimpleBalances.jsx', 'frontend/src/pages/operational/SimpleBalanceAccess.jsx',
    'frontend/src/pages/operational/SimpleOrderRefunds.jsx', 'frontend/src/pages/operational/simpleBalancesApi.js',
    'frontend/src/pages/operational/SimpleBalanceStatement.jsx', 'frontend/src/pages/operational/SimpleBalanceReviews.jsx',
    'frontend/src/pages/operational/simpleBalanceReviews.css',
    'frontend/src/pages/operational/simpleBalancesModel.js', 'frontend/src/pages/operational/simpleBalancesHooks.js',
    'frontend/src/pages/operational/simpleBalancesPending.js',
    'frontend/src/pages/operational/simpleBalances.css', 'frontend/src/pages/operational/visibleOperationalRead.js',
    'frontend/src/context/AuthContext.jsx', 'frontend/src/lib/api.js', 'frontend/src/lib/demandRead.js',
    ...['main.jsx', 'index.html', 'build.cjs', 'browser.cjs', 'fixtures.cjs', 'features.cjs'].map(file => `scripts/testing/simple_balances/${file}`),
  ];
  fs.writeFileSync(path.join(out, 'source-manifest.json'), JSON.stringify({ synthetic_only: true,
    sha256: Object.fromEntries(files.map(file => [file, crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex')])),
  }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });

