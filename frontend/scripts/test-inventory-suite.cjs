const path=require('node:path');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const requested=process.argv.slice(2);
const files=requested.length?requested:['src/components/inventory/suite/InventorySuiteFrame.test.jsx','src/lib/webPageAccess.test.js'];
const result=spawnSync(process.execPath,[require.resolve('react-scripts/scripts/test.js'),
  '--watchAll=false','--runInBand','--testMatch','**/src/**/*.test.{js,jsx}',
  '--resolver',path.join(__dirname,'jest-package-exports.cjs'),'--runTestsByPath',...files],
  {cwd:root,stdio:'inherit',env:{...process.env,CI:'true'}});
if(result.error){process.stderr.write(result.error.message+'\n');process.exitCode=1;}
else process.exitCode=result.status??1;
