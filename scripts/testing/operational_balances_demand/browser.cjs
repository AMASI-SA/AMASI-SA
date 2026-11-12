/* Actual report/dialog components; loopback synthetic transport only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const out = process.env.OPERATIONAL_BALANCE_UAT_OUTPUT;
if (!out || !path.isAbsolute(out)) throw Error('Absolute synthetic output required');
const fixture = JSON.parse(fs.readFileSync(path.join(out,'fixture.json')));
const confirmedFixture = JSON.parse(fs.readFileSync(path.join(out,'fixture-confirmed.json')));
const pendingFixture = JSON.parse(fs.readFileSync(path.join(out,'fixture-confirmation-pending.json')));
const confirmation = confirmedFixture.currencies.SAR.deliveries.rows.find(row => row.order_id === 'pending-0')?.delivery_after_start_confirmation;
assert(confirmation?.state === 'applied' && confirmation.confirmation_id, 'The real projection must expose applied proof');
const files = path.join(out, 'dist');
const calls = [], errors = [], results = [];
let mode = 'success', manualSaved = false, releasePreview = null, previewStarted = null;
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const send = (status, body) => {if (response.destroyed) return; response.writeHead(status, {'content-type':'application/json'}); response.end(JSON.stringify(body));};
  if (url.pathname.startsWith('/api/')) {
    calls.push({method:request.method,path:url.pathname});
    if (url.pathname.endsWith('/delivery-after-start/preview') || url.pathname.endsWith('/delivery-after-start/confirm')) {
      let body = '';
      request.on('data', chunk => {body += chunk;});
      request.on('end', () => {
        try {
          const payload = JSON.parse(body);
          assert.equal(request.method, 'POST');
          assert.equal(payload.order_id, 'pending-0');
          assert.equal(payload.order_number, '9001');
          assert.equal(payload.party_type, 'store_driver');
          assert.equal(payload.party_id, 'synthetic-driver');
          assert.equal(payload.expected_session_scope, fixture.session_scope);
          assert.equal(payload.visible, true);
          if (url.pathname.endsWith('/preview')) {
            const result = {can_confirm:true, confirmation_state:'available', preview_hash:'a'.repeat(64),
              session_scope:fixture.session_scope, period_start:fixture.started_at, issues:[], recorded_confirmation:null};
            if (mode === 'manual-hidden') {
              releasePreview = () => send(200,result);
              previewStarted?.();
              return;
            }
            return send(200,result);
          }
          assert.equal(payload.expected_preview_hash, 'a'.repeat(64));
          assert.equal(payload.confirmed_after_start, true);
          assert.equal(typeof payload.request_id, 'string');
          assert(payload.request_id.length >= 8);
          assert.equal(manualSaved, false, 'Repeated confirmation must not be submitted');
          manualSaved = true;
          return send(200,{ok:true, pending_reconciliation:true, confirmed_at:confirmation.confirmed_at,
            session_scope:fixture.session_scope, order_id:payload.order_id, order_number:payload.order_number,
            recorded_confirmation:{id:confirmation.confirmation_id, confirmed_at:confirmation.confirmed_at,
              state:'pending_reconciliation'}});
        } catch (error) {errors.push(error.message); send(422,{detail:{message:'Synthetic confirmation contract rejected'}});}
      });
      return;
    }
    if (url.pathname.endsWith('/refresh') && request.method === 'POST') {
      if (mode === 'source-failed' || mode === 'manual-refresh-failed' && manualSaved) return send(409,{detail:{code:'operational_capacity_requires_archive',message:'تعذر تحديث مصدر الاختبار'}});
      if (mode === 'denied') return send(403,{detail:{code:'operational_forbidden'}});
      return send(200,{status:'refreshed'});
    }
    if (url.pathname.endsWith('/financial-position') && request.method === 'GET') {
      return send(200, manualSaved ? mode === 'manual-refresh-failed' ? pendingFixture : confirmedFixture : fixture);
    }
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
async function withDeadline(operation, label) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_,reject)=>{
      timer=setTimeout(()=>reject(Error(label+' did not reach the synthetic server')),5000);
    })]);
  } finally {clearTimeout(timer);}
}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.OPERATIONAL_UAT_CHROMIUM ? {executablePath:process.env.OPERATIONAL_UAT_CHROMIUM} : {})});
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
    await check('One explicit delivery confirmation preserves known money and refreshes once',async()=>{
      mode='manual-success'; manualSaved=false;
      await page.setViewportSize({width:1440,height:1080}); await page.reload();
      const drivers=page.getByTestId('financial-table-drivers'); await drivers.waitFor();
      assert((await drivers.innerText()).includes('75.00'));
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const dialog=page.getByRole('dialog');
      const row=dialog.locator('tbody tr').filter({hasText:'9001'});
      const before=calls.length;
      await row.getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await page.getByRole('button',{name:'6 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).waitFor();
      assert((await drivers.innerText()).includes('100.00'));
      await page.getByText('تم حفظ التأكيد وتطبيقه في التقرير.',{exact:true}).waitFor();
      const work=calls.slice(before);
      for(const suffix of ['/delivery-after-start/preview','/delivery-after-start/confirm','/refresh','/financial-position']) {
        assert.equal(work.filter(call=>call.path.endsWith(suffix)).length,1, suffix);
      }
      await page.getByRole('button',{name:'6 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      assert.equal(await page.getByRole('dialog').locator('tbody tr').count(),6);
      assert.equal(await page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'}).count(),0);
      await page.screenshot({path:path.join(out,'manual-confirmation-desktop.png'),fullPage:false});
      await page.keyboard.press('Escape');
      const finished=calls.length; await page.waitForTimeout(1200); assert.equal(calls.length,finished);
    });
    await check('Saved confirmation remains visible when the follow-up refresh fails',async()=>{
      mode='manual-refresh-failed'; manualSaved=false; await page.reload();
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const before=calls.length;
      await page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'})
        .getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await page.getByText(/تُعرض آخر أرصدة محفوظة بتاريخها/).first().waitFor();
      const trigger=page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true});
      await trigger.click();
      const row=page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'});
      assert((await row.innerText()).includes('تم حفظ التأكيد'));
      assert((await row.innerText()).includes('بانتظار تحديث الأرصدة'));
      const action=row.getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true});
      assert.equal(await action.count() ? await action.isEnabled() : false, false);
      assert((await page.getByTestId('financial-table-drivers').innerText()).includes('75.00'));
      assert.equal(calls.slice(before).filter(call=>call.path.endsWith('/delivery-after-start/confirm')).length,1);
      await page.screenshot({path:path.join(out,'manual-confirmation-pending.png'),fullPage:false});
      await page.keyboard.press('Escape');
    });
    await check('Hiding during delivery preflight prevents confirmation and automatic retry',async()=>{
      mode='manual-hidden'; manualSaved=false;
      const hidden=await context.newPage(); hidden.on('pageerror',error=>errors.push(error.message));
      await hidden.addInitScript(()=>{window.__testVisible=true;Object.defineProperty(document,'visibilityState',{get:()=>window.__testVisible?'visible':'hidden'});});
      await hidden.goto(origin);
      await hidden.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const ready=new Promise(resolve=>{previewStarted=resolve;});
      const before=calls.length;
      await hidden.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'})
        .getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await withDeadline(ready,'Delivery preflight');
      await hidden.evaluate(()=>{window.__testVisible=false;document.dispatchEvent(new Event('visibilitychange'));});
      releasePreview(); releasePreview=null; previewStarted=null;
      await hidden.waitForTimeout(250);
      await hidden.evaluate(()=>{window.__testVisible=true;document.dispatchEvent(new Event('visibilitychange'));});
      await hidden.waitForTimeout(250);
      const work=calls.slice(before);
      assert.equal(work.filter(call=>call.path.endsWith('/delivery-after-start/preview')).length,1);
      assert.equal(work.filter(call=>call.path.endsWith('/delivery-after-start/confirm')).length,0);
      assert.equal(work.filter(call=>call.path.endsWith('/refresh')).length,0);
      assert.equal(manualSaved,false);
      await hidden.close();
    });
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify({synthetic_only:true,results,calls,errors},null,2));
    await context.close();
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
