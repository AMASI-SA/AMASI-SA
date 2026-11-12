/* Actual report/dialog components; loopback synthetic transport only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const out = process.env.OPERATIONAL_BALANCE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out)) throw Error('Absolute synthetic output required');
const fixture = JSON.parse(fs.readFileSync(path.join(out,'fixture.json')));
const files = path.join(out, 'dist');
const calls = [], errors = [], results = [];
let mode = 'success';
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const send = (status, body) => {response.writeHead(status, {'content-type':'application/json'}); response.end(JSON.stringify(body));};
  if (url.pathname.startsWith('/api/')) {
    calls.push({method:request.method,path:url.pathname});
    if (url.pathname.endsWith('/refresh') && request.method === 'POST') {
      if (mode === 'source-failed') return send(409,{detail:{code:'operational_capacity_requires_archive',message:'تعذر تحديث مصدر الاختبار'}});
      if (mode === 'denied') return send(403,{detail:{code:'operational_forbidden'}});
      return send(200,{status:'refreshed'});
    }
    if (url.pathname.endsWith('/financial-position') && request.method === 'GET') return send(200,fixture);
    return send(404,{detail:'Unexpected fixture API'});
  }
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const file = path.resolve(files, relative);
  if (!file.startsWith(files + path.sep) || !fs.existsSync(file)) {response.writeHead(404);return response.end();}
  response.writeHead(200,{'content-type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
  fs.createReadStream(file).pipe(response);
});
async function check(name, operation) {
  await operation(); results.push({name,status:'PASS'}); console.log('PASS '+name);
}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({headless:true,executablePath:process.env.OPERATIONAL_UAT_CHROMIUM || '/usr/bin/chromium',args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1080}});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage(); page.on('pageerror', error=>errors.push(error.message));
    await check('Known balances, seven distinct orders, reasons and dialog keyboard close', async()=>{
      await page.goto(origin); const drivers=page.getByTestId('financial-table-drivers');
      await drivers.waitFor(); assert((await drivers.innerText()).includes('75.00'));
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const dialog=page.getByRole('dialog'); await dialog.waitFor();
      assert.equal(await dialog.locator('tbody tr').count(),7);
      for(let index=0;index<7;index++) assert((await dialog.innerText()).includes(String(9001+index)));
      assert((await dialog.innerText()).includes('تاريخ التسليم المؤكد غير متوفر'));
      assert((await drivers.innerText()).includes('75.00'));
      await page.screenshot({path:path.join(out,'pending-orders-desktop.png'),fullPage:false});
      await page.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
      assert.equal(await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).evaluate(el=>el===document.activeElement),true);
    });
    await check('Mobile dialog fits the viewport and all orders remain reachable',async()=>{
      await page.setViewportSize({width:390,height:844});
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const dialog=page.getByRole('dialog'); const box=await dialog.boundingBox();
      assert(box.x>=0 && box.x+box.width<=391 && box.y>=0 && box.height<=844);
      await page.screenshot({path:path.join(out,'pending-orders-mobile.png'),fullPage:false});
      await dialog.getByRole('button',{name:'إغلاق نافذة الطلبات'}).click();
    });
    await check('No repeat traffic while the completed page stays open',async()=>{
      const count=calls.length; await page.waitForTimeout(1200); assert.equal(calls.length,count);
    });
    await check('Source refresh failure retains saved money and a visible warning',async()=>{
      mode='source-failed'; await page.reload(); await page.getByTestId('financial-table-drivers').waitFor();
      assert((await page.locator('body').innerText()).includes('تُعرض آخر أرصدة محفوظة بتاريخها'));
      assert((await page.getByTestId('financial-table-drivers').innerText()).includes('75.00'));
    });
    await check('A hidden page does no request; one visible activation reads once',async()=>{
      mode='success'; const hidden=await context.newPage(); hidden.on('pageerror',error=>errors.push(error.message));
      await hidden.addInitScript(()=>{window.__testVisible=false;Object.defineProperty(document,'visibilityState',{get:()=>window.__testVisible?'visible':'hidden'});});
      const count=calls.length; await hidden.goto(origin); await hidden.waitForTimeout(300); assert.equal(calls.length,count);
      await hidden.evaluate(()=>{window.__testVisible=true;document.dispatchEvent(new Event('visibilitychange'));});
      await hidden.getByTestId('financial-table-drivers').waitFor(); assert.equal(calls.length,count+2); await hidden.close();
    });
    await check('Denied refresh never reads a financial snapshot',async()=>{
      mode='denied'; const count=calls.filter(row=>row.path.endsWith('/financial-position')).length;
      await page.reload(); await page.getByRole('alert').waitFor();
      assert.equal(calls.filter(row=>row.path.endsWith('/financial-position')).length,count);
      assert.equal(await page.getByTestId('financial-table-drivers').count(),0);
    });
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify({synthetic_only:true,results,calls,errors},null,2));
    await context.close();
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
