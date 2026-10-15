const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.MZ2_HISTORY_DIST;
if (!out || !path.isAbsolute(out)) throw new Error('Absolute external fixture output required');
(async () => {
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  await build({configFile:false, root:__dirname,
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},{find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},{find:'react-dom/client',replacement:req.resolve('react-dom/client')}]},
    css:{postcss:{plugins:[req('tailwindcss')({content:[path.join(__dirname,'*.jsx'),path.join(root,'frontend/src/pages/accounting/**/*.{js,jsx}')]}),req('autoprefixer')()]}},
    build:{outDir:out,emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files = ['frontend/src/pages/accounting/h2/DriverPanel.jsx','frontend/src/pages/accounting/h2/DriverReviewHistory.jsx','frontend/src/pages/accounting/h2/driverAdapter.js','frontend/src/pages/accounting/AccountingUI.jsx','frontend/src/pages/accounting/accountingUI.css','frontend/src/pages/accounting/h2/h2.css','backend/accounting_driver_review_history.py','backend/accounting_shipping_native_routes.py'];
  const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex');
  fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify(Object.fromEntries(files.map(file=>[file,sha256(file)])),null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
