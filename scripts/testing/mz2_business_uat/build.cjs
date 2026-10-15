const path = require('node:path');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
if (!path.isAbsolute(process.env.MZ2_C5_DIST || '')) throw new Error('External absolute build directory required');
(async () => {
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  await build({configFile:false, root:__dirname,
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},{find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},{find:'react-dom/client',replacement:req.resolve('react-dom/client')}]},
    css:{postcss:{plugins:[req('tailwindcss')({content:[path.join(__dirname,'*.jsx'),path.join(root,'frontend/src/pages/accounting/onboarding/*.{js,jsx}')]}),req('autoprefixer')()]}},
    build:{outDir:process.env.MZ2_C5_DIST,emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
})().catch(error=>{console.error(error);process.exitCode=1;});
