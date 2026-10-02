const path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {createRequire}=require('node:module'),{pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'../../..'),req=createRequire(path.join(root,'frontend/package.json'));
const out=process.env.MZ2_LATE_DIST;
if(!out||!path.isAbsolute(out))throw new Error('Absolute external fixture output required');
(async()=>{
  const {build}=await import(pathToFileURL(req.resolve('vite')).href);
  await build({configFile:false,root:__dirname,esbuild:{jsx:'automatic'},
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},{find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},{find:'react-dom/client',replacement:req.resolve('react-dom/client')},{find:'sonner',replacement:req.resolve('sonner')}]},
    css:{postcss:{plugins:[req('tailwindcss')({content:[path.join(__dirname,'*.jsx'),path.join(root,'frontend/src/pages/AmasiDeliveryApp.jsx'),path.join(root,'frontend/src/components/driver/*.jsx'),path.join(root,'frontend/src/pages/accounting/**/*.{js,jsx}')]}),req('autoprefixer')()]}},
    build:{outDir:out,emptyOutDir:false},define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files=['frontend/src/components/driver/LateDeliveryEvidence.jsx','frontend/src/pages/accounting/LateDeliveryEvidenceReview.jsx','backend/store_delivery_late_evidence.py','backend/store_delivery_payment_evidence_routes.py','backend/mobile_app_request_context.py','backend/operational_atomic.py','scripts/testing/mz2_late_delivery_evidence/server.py','scripts/testing/mz2_late_delivery_evidence/browser.cjs','scripts/testing/mz2_late_delivery_evidence/review.jsx'];
  fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify(Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])),null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
