/* Real component + real auth/API modules; isolated synthetic acceptance build. */
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.PROVIDER_STATEMENT_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) throw Error('Absolute repo-external fixture output required');
(async () => {
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  const {default: react} = await import(pathToFileURL(req.resolve('@vitejs/plugin-react')).href);
  const tailwind = {...req('./tailwind.config.js'), content:[path.join(root, 'frontend/src/pages/operational/ProviderStatements.jsx'), path.join(root, 'frontend/src/pages/operational/PaginatedReportTable.jsx')]};
  fs.mkdirSync(out, {recursive:true});
  await build({configFile:false, envDir:false, root:__dirname, cacheDir:path.join(out, '.vite-cache'), plugins:[react()],
    resolve:{alias:[{find:/^react$/,replacement:req.resolve('react')},
      {find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')}, {find:'react-dom/client',replacement:req.resolve('react-dom/client')}]},
    css:{postcss:{plugins:[req('tailwindcss')(tailwind), req('autoprefixer')()]}},
    build:{outDir:path.join(out, 'dist'), emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'}});
  const files = ['frontend/src/pages/operational/ProviderStatements.jsx', 'frontend/src/pages/operational/providerStatementPresentation.js',
    'frontend/src/pages/operational/providerStatements.css', 'frontend/src/pages/operational/api.js',
    'frontend/src/pages/operational/visibleOperationalRead.js', 'frontend/src/pages/operational/PaginatedReportTable.jsx',
    'frontend/src/pages/operational/reportPagination.css', 'frontend/src/pages/operational/operational.css',
    'frontend/src/context/AuthContext.jsx', 'frontend/src/lib/api.js', 'frontend/src/index.css', 'frontend/tailwind.config.js',
    'scripts/testing/provider_statements/index.html', 'scripts/testing/provider_statements/main.jsx',
    'scripts/testing/provider_statements/build.cjs', 'scripts/testing/provider_statements/fixtures.cjs', 'scripts/testing/provider_statements/browser.cjs'];
  fs.writeFileSync(path.join(out,'source-manifest.json'), JSON.stringify(Object.fromEntries(files.map(file =>
    [file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])), null, 2) + '\n');
})().catch(error=>{console.error(error);process.exitCode=1;});
