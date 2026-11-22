/* Synthetic component acceptance build; never a governed release artifact. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const root = path.resolve(__dirname, '../../..');
const req = createRequire(path.join(root, 'frontend/package.json'));
const out = process.env.INVENTORY_PURCHASE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out) || path.resolve(out) === root || path.resolve(out).startsWith(root + path.sep)) {
  throw Error('Absolute repo-external synthetic output required');
}
(async () => {
  fs.mkdirSync(out, {recursive:true});
  const {build} = await import(pathToFileURL(req.resolve('vite')).href);
  const {default:react} = await import(pathToFileURL(req.resolve('@vitejs/plugin-react')).href);
  const built = await build({configFile:false, root:__dirname, publicDir:false, plugins:[react()],
    resolve:{alias:[
      {find:/^react$/,replacement:req.resolve('react')},
      {find:'react/jsx-runtime',replacement:req.resolve('react/jsx-runtime')},
      {find:'react-dom/client',replacement:req.resolve('react-dom/client')},
      {find:'react-router-dom',replacement:req.resolve('react-router-dom')},
    ]},
    build:{outDir:path.join(out,'dist'),emptyOutDir:false},
    define:{'process.env.NODE_ENV':'"production"','process.env.REACT_APP_BACKEND_URL':'""'},
  });
  const files = new Set(['main.jsx','index.html','build.cjs','browser.cjs','fixtures.cjs','README.md']
    .map(file=>'scripts/testing/inventory_purchase/'+file));
  for (const bundle of Array.isArray(built) ? built : [built]) {
    for (const chunk of bundle.output) for (const file of Object.keys(chunk.modules || {})) {
      const clean = file.split('?')[0];
      if (clean.startsWith(root + path.sep) && !clean.includes('/node_modules/') && fs.existsSync(clean)) {
        files.add(path.relative(root,clean));
      }
    }
  }
  // Use the same local inventory fonts as production; remote font fallback
  // would conceal text wrapping/layout changes in the mobile acceptance run.
  const fontOut = path.join(out,'dist','inventory-fonts');
  fs.mkdirSync(fontOut,{recursive:true});
  for (const file of ['mezan-font-1.ttf','mezan-font-3.ttf','mezan-font-4.ttf','mezan-font-5.ttf','mezan-font-9.ttf','LICENSE-cairo.txt','LICENSE-tajawal.txt']) {
    const source = 'frontend/public/inventory-fonts/'+file;
    fs.copyFileSync(path.join(root,source),path.join(fontOut,file)); files.add(source);
  }
  fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify({synthetic_only:true,
    sha256:Object.fromEntries([...files].sort().map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file))).digest('hex')])),
  },null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
