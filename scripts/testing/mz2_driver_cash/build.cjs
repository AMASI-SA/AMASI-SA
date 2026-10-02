const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'../../..'),req=createRequire(path.join(root,'frontend/package.json'));
const out=process.env.MZ2_CASH_DIST;
if(!out||!path.isAbsolute(out))throw new Error('Absolute external fixture output required');
(async()=>{
  const {build}=await import(pathToFileURL(req.resolve('vite')).href);
  await build({configFile:false,root:__dirname,
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},{find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},{find:'react-dom/client',replacement:req.resolve('react-dom/client')},{find:'sonner',replacement:req.resolve('sonner')}]},
    css:{postcss:{plugins:[req('tailwindcss')({content:[path.join(__dirname,'*.jsx'),path.join(root,'frontend/src/pages/AmasiDeliveryApp.jsx'),path.join(root,'frontend/src/components/driver/*.jsx'),path.join(root,'frontend/src/pages/accounting/**/*.{js,jsx}')]}),req('autoprefixer')()]}},
    build:{outDir:out,emptyOutDir:false},define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files=['frontend/src/pages/AmasiDeliveryApp.jsx','frontend/src/components/driver/DriverPhysicalCash.jsx','frontend/src/pages/accounting/h2/DriverCashReconciliation.jsx','frontend/src/pages/accounting/AccountingUI.jsx','frontend/src/pages/accounting/accountingUI.css','frontend/src/pages/accounting/h2/h2.css','backend/store_delivery_cash_evidence.py','backend/store_delivery_delivery_commit.py','backend/store_delivery_driver_app_routes.py','backend/accounting_shipping_native_routes.py','backend/operational_atomic.py'];
  fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify(Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])),null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
