const path = require('node:path');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const deps = process.env.MZ2_FIXTURE_NODE_MODULES;
const out = process.env.MZ2_FIXTURE_DIST;
if (!deps || !out || !path.isAbsolute(out)) throw Error('Explicit dependencies and absolute fixture output required');
const req = createRequire(path.join(deps, '..', 'package.json'));
const root = path.resolve(__dirname, '../../..');
(async () => {
    const {build} = await import(pathToFileURL(req.resolve('vite')).href);
    await build({configFile: false, root: __dirname,
        esbuild: {jsx: 'automatic'},
        resolve: {alias: ['react/jsx-runtime','react-dom/client','react','sonner','axios','@phosphor-icons/react'].map(name => ({find: name, replacement: req.resolve(name)}))},
        css: {postcss: {plugins: [req('tailwindcss')({content: [path.join(__dirname,'*.jsx'),path.join(root,'frontend/src/pages/EmployeesV2Management.jsx')]}),req('autoprefixer')()]}},
        build: {outDir: out, emptyOutDir: false},
        define: {'process.env.NODE_ENV': '"production"', 'process.env.REACT_APP_BACKEND_URL': '""'},
    });
})().catch(error => {console.error(error); process.exitCode = 1;});
