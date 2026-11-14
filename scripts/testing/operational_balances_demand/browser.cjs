/* Actual report/dialog components; loopback synthetic transport only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const out = process.env.OPERATIONAL_BALANCE_UAT_OUTPUT;
const root = path.resolve(__dirname, '../../..');
if (!out || !path.isAbsolute(out) || path.resolve(out).startsWith(root + path.sep) || path.resolve(out) === root) {
  throw Error('Absolute repo-external synthetic output required');
}
const read = name => JSON.parse(fs.readFileSync(path.join(out, name)));
const fixtureManifest = read('fixture-manifest.json');
assert.equal(fixtureManifest.synthetic_only, true);
for (const [file, expected] of Object.entries(fixtureManifest.sha256)) {
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(out, file))).digest('hex'), expected, file);
}
const fixtures = read('fixture-views.json');
const confirmedFixtures = read('fixture-confirmed-views.json');
const pendingFixtures = read('fixture-confirmation-pending-views.json');
const fixture = fixtures.all;
const pageViews = fixtureManifest.views.filter(view => view !== 'all' && view !== 'forecast');
assert.deepEqual(pageViews, ['summary', 'providers', 'couriers', 'drivers', 'employees', 'accounts', 'suppliers', 'advertising', 'other', 'expected']);
const confirmation = confirmedFixtures.all.currencies.SAR.deliveries.rows.find(row => row.order_id === 'pending-0')?.delivery_after_start_confirmation;
assert(confirmation?.state === 'applied' && confirmation.confirmation_id, 'The real projection must expose applied proof');
const files = path.join(out, 'dist');
const calls = [], errors = [], results = [];
let mode = 'success', scenario = '', manualSaved = false;
let releasePreview = null, previewStarted = null, releaseForecast = null, forecastStarted = null;
const currentFixtures = () => manualSaved ? mode === 'manual-refresh-failed' ? pendingFixtures : confirmedFixtures : fixtures;
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1');
  const send = (status, body) => {
    if (response.destroyed) return;
    response.writeHead(status, {'content-type':'application/json'});
    response.end(JSON.stringify(body));
  };
  if (url.pathname.startsWith('/api/')) {
    calls.push({method:request.method, path:url.pathname, params:Object.fromEntries(url.searchParams), scenario, mode});
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
              releasePreview = () => send(200, result);
              previewStarted?.();
              return;
            }
            return send(200, result);
          }
          assert.equal(payload.expected_preview_hash, 'a'.repeat(64));
          assert.equal(payload.confirmed_after_start, true);
          assert.equal(typeof payload.request_id, 'string');
          assert(payload.request_id.length >= 8);
          assert.equal(manualSaved, false, 'Repeated confirmation must not be submitted');
          manualSaved = true;
          return send(200, {ok:true, pending_reconciliation:true, confirmed_at:confirmation.confirmed_at,
            session_scope:fixture.session_scope, order_id:payload.order_id, order_number:payload.order_number,
            recorded_confirmation:{id:confirmation.confirmation_id, confirmed_at:confirmation.confirmed_at,
              state:'pending_reconciliation'}});
        } catch (error) {errors.push(error.message); send(422, {detail:{message:'Synthetic confirmation contract rejected'}});}
      });
      return;
    }
    if (url.pathname.endsWith('/refresh') && request.method === 'POST') {
      if (mode === 'source-failed' || mode === 'manual-refresh-failed' && manualSaved) {
        return send(409, {detail:{code:'operational_capacity_requires_archive', message:'تعذر تحديث مصدر الاختبار'}});
      }
      if (mode === 'denied') return send(403, {detail:{code:'operational_forbidden'}});
      return send(200, {status:'refreshed'});
    }
    if (url.pathname.endsWith('/financial-position') && request.method === 'GET') {
      if (mode === 'denied') return send(403, {detail:{code:'operational_forbidden'}});
      const view = url.searchParams.get('view');
      const report = currentFixtures()[view];
      if (!report) {
        errors.push('The page requested a missing or unsupported view: ' + view);
        return send(422, {detail:{code:'operational_financial_view_invalid'}});
      }
      if (view === 'forecast' || view === 'all') {
        const bindings = {expected_revision:String(report.revision), expected_as_of:report.as_of, expected_started_at:report.started_at};
        if (Object.entries(bindings).some(([key]) => !url.searchParams.has(key))) {
          errors.push('Bound report request omitted snapshot identity: ' + view);
          return send(422, {detail:{code:'operational_financial_snapshot_binding_required'}});
        }
        if (Object.entries(bindings).some(([key, value]) => url.searchParams.get(key) !== value)) {
          errors.push('Bound report request changed snapshot identity: ' + view);
          return send(409, {detail:{code:'operational_financial_snapshot_changed'}});
        }
      }
      if (view === 'forecast' && mode === 'hold-forecast') {
        releaseForecast = () => send(200, report);
        forecastStarted?.();
        return;
      }
      // Deliberately return another actual generated snapshot to exercise the
      // client's identity check, even when a faulty server accepts the binding.
      if (view === 'forecast' && mode === 'forecast-mismatch') return send(200, confirmedFixtures.forecast);
      return send(200, report);
    }
    errors.push('Unexpected fixture API: ' + request.method + ' ' + url.pathname);
    return send(404, {detail:'Unexpected fixture API'});
  }
  const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const file = path.resolve(files, relative);
  if (!file.startsWith(files + path.sep) || !fs.existsSync(file)) {response.writeHead(404);return response.end();}
  response.writeHead(200, {'content-type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
  fs.createReadStream(file).pipe(response);
});
async function check(name, operation) {
  scenario = name;
  const firstRequest = calls.length;
  await operation();
  results.push({name, status:'PASS', first_request:firstRequest, request_count:calls.length-firstRequest});
  console.log('PASS ' + name);
}
async function withDeadline(operation, label) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_,reject) => {
      timer=setTimeout(() => reject(Error(label+' did not reach the synthetic server')), 5000);
    })]);
  } finally {clearTimeout(timer);}
}
const reads = from => calls.slice(from).filter(row => row.path.endsWith('/financial-position'));
const assertNoPost = from => assert.equal(calls.slice(from).filter(row => row.method !== 'GET').length, 0);
const navLink = (page, view) => page.locator('nav[aria-label="صفحات المركز المالي"] a[href*="view=' + view + '"]');
async function waitForView(page, view, currency = 'SAR') {
  await page.getByTestId('financial-position-content').waitFor();
  await page.waitForFunction(({view, currency}) => {
    const active = document.querySelector('nav a[aria-current="page"]');
    const selector = document.querySelector('select[aria-label="عملة التقرير"]');
    return active && new URL(active.href).searchParams.get('view') === view
      && selector?.value === currency && document.querySelector('.ofp-page-content')?.getAttribute('aria-busy') === 'false';
  }, {view, currency});
}
async function assertScopedTables(page, view, currency = 'SAR') {
  const expected = fixtures[view].currencies[currency].tables.map(table => 'financial-table-' + table.id).sort();
  const actual = await page.locator('[data-testid^="financial-table-"]').evaluateAll(nodes => nodes.map(node => node.dataset.testid).sort());
  assert.deepEqual(actual, expected, 'The visible tables must exactly match the generated ' + view + ' response');
}
async function assertQuiet(page, milliseconds = 300) {
  const count = calls.length;
  await page.waitForTimeout(milliseconds);
  assert.equal(calls.length, count, 'Closed or completed pages must not fetch again');
}
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({headless:true, chromiumSandbox:true,
    ...(process.env.OPERATIONAL_UAT_CHROMIUM ? {executablePath:process.env.OPERATIONAL_UAT_CHROMIUM} : {})});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1080}, acceptDownloads:true});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
    await check('Saved summary appears before its bound forecast without refreshing sources', async () => {
      mode='hold-forecast'; manualSaved=false;
      const ready = new Promise(resolve => {forecastStarted=resolve;});
      const before = calls.length;
      await page.goto(origin); await waitForView(page, 'summary');
      await withDeadline(ready, 'Summary forecast');
      for (const title of ['إجمالي الديون والمستحقات', 'حساب الربح المتوقع', 'صافي الأرصدة الحالية', 'صافي الربح المتوقع']) {
        assert.equal(await page.getByRole('heading', {name:title, exact:true}).count(), 1);
      }
      assert.match(await page.locator('.ofp-current').innerText(), /1,?200\.00/);
      assert.equal(await page.getByTestId('financial-forecast-pending').count(), 1);
      assert.equal(await page.getByTestId('financial-forecast-position').locator('.ofp-money').count(), 0);
      assert.equal(await page.getByText(/آخر احتساب محفوظ:/).count(), 1);
      await assertScopedTables(page, 'summary');
      const saved = await page.getByTestId('financial-debt-summary').innerText();
      await page.screenshot({path:path.join(out, 'financial-summary-desktop.png'), fullPage:false});
      releaseForecast(); releaseForecast=null; forecastStarted=null;
      await page.getByTestId('financial-forecast-pending').waitFor({state:'hidden'});
      assert.equal(await page.getByTestId('financial-debt-summary').innerText(), saved);
      assert.equal(fixtures.forecast.currencies.SAR.metrics.forecast_monetary_position, null);
      assert.equal(await page.getByTestId('financial-forecast-position').getByLabel('المبلغ غير متاح').count(), 1);
      assert.deepEqual(reads(before).map(row => row.params.view), ['summary', 'forecast']);
      assertNoPost(before);
    });
    await check('Every sidebar page and currency is reachable with scoped GET-only navigation', async () => {
      mode='success'; const before=calls.length, observedTables=new Set();
      assert.equal(await page.locator('nav[aria-label="صفحات المركز المالي"] a').count(), pageViews.length);
      for (const view of pageViews.slice(1)) {
        const first=calls.length;
        await navLink(page, view).click(); await waitForView(page, view);
        await assertScopedTables(page, view);
        fixtures[view].currencies.SAR.tables.forEach(table => observedTables.add(table.id));
        assert.deepEqual(reads(first).map(row => row.params.view), [view]);
        assert.equal(await page.getByTestId('financial-debt-summary').count(), 0);
        await page.getByLabel('عملة التقرير', {exact:true}).selectOption('USD');
        await waitForView(page, view, 'USD'); await assertScopedTables(page, view, 'USD');
        await page.getByLabel('عملة التقرير', {exact:true}).selectOption('SAR');
        await waitForView(page, view); assert.equal(calls.length, first+1, 'Currency selection must not restart any request');
      }
      assert.deepEqual([...observedTables].sort(), fixture.currencies.SAR.tables.map(table => table.id).sort());
      assertNoPost(before);
      assert.deepEqual(reads(before).map(row => row.params.view), pageViews.slice(1));
      await page.setViewportSize({width:390,height:844});
      await navLink(page, 'drivers').click(); await waitForView(page, 'drivers');
      await page.screenshot({path:path.join(out,'financial-sidebar-mobile.png'),fullPage:false});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.setViewportSize({width:1440,height:1080});
    });
    await check('Leaving the summary consumes its pending forecast and prevents late replacement', async () => {
      mode='hold-forecast'; const ready=new Promise(resolve => {forecastStarted=resolve;});
      const before=calls.length;
      await page.goto(origin); await waitForView(page,'summary'); await withDeadline(ready,'Deferred summary forecast');
      await navLink(page,'drivers').click(); await waitForView(page,'drivers');
      releaseForecast(); releaseForecast=null; forecastStarted=null;
      await assertQuiet(page);
      await assertScopedTables(page,'drivers');
      assert.equal(await page.getByTestId('financial-debt-summary').count(),0);
      assert.deepEqual(reads(before).map(row => row.params.view),['summary','forecast','drivers']);
      assertNoPost(before);
    });
    await check('A different forecast snapshot never replaces saved values or becomes zero', async () => {
      mode='forecast-mismatch'; const before=calls.length;
      await page.goto(origin); await waitForView(page,'summary');
      await page.getByText('لم تطابق التوقعات لقطة الملخص؛ أعد قراءة الصفحة لعرض لقطة واحدة متسقة.',{exact:true}).first().waitFor();
      assert.match(await page.locator('.ofp-current').innerText(),/1,?200\.00/);
      assert.equal(await page.getByTestId('financial-forecast-position').locator('.ofp-money').count(),0);
      await page.getByRole('button',{name:'إعادة قراءة الملخص والتوقعات',exact:true}).waitFor();
      await assertQuiet(page);
      assert.deepEqual(reads(before).map(row => row.params.view),['summary','forecast']); assertNoPost(before);
    });
    await check('Known balances, seven distinct orders, reasons and dialog keyboard close', async () => {
      mode='success'; manualSaved=false;
      await page.goto(origin+'/?view=drivers'); await waitForView(page,'drivers');
      const drivers=page.getByTestId('financial-table-drivers');
      assert((await drivers.innerText()).includes('75.00'));
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const dialog=page.getByRole('dialog'); await dialog.waitFor();
      assert.equal(await dialog.locator('tbody tr').count(),7);
      for(let index=0;index<7;index++) assert((await dialog.innerText()).includes(String(9001+index)));
      assert((await dialog.innerText()).includes('تاريخ التسليم المؤكد غير متوفر'));
      assert((await drivers.innerText()).includes('75.00'));
      await page.screenshot({path:path.join(out,'pending-orders-desktop.png'),fullPage:false});
      await page.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
      assert.equal(await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).evaluate(el => el===document.activeElement),true);
    });
    await check('Mobile dialog fits the viewport and all orders remain reachable', async () => {
      await page.setViewportSize({width:390,height:844});
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const dialog=page.getByRole('dialog'); const box=await dialog.boundingBox();
      assert(box.x>=0 && box.x+box.width<=391 && box.y>=0 && box.height<=844);
      await page.screenshot({path:path.join(out,'pending-orders-mobile.png'),fullPage:false});
      await dialog.getByRole('button',{name:'إغلاق نافذة الطلبات'}).click();
    });
    await check('No repeat traffic while the completed page stays open', async () => {
      await assertQuiet(page,1200);
    });
    await check('An explicit source refresh failure retains saved money and a visible warning', async () => {
      mode='source-failed'; await page.reload(); await waitForView(page,'drivers');
      const before=calls.length;
      await page.getByRole('button',{name:'تحديث المصادر والأرصدة',exact:true}).click();
      await page.getByText(/تُعرض آخر أرصدة محفوظة بتاريخها/).first().waitFor();
      assert((await page.getByTestId('financial-table-drivers').innerText()).includes('75.00'));
      assert.deepEqual(calls.slice(before).map(row => [row.method,row.path.split('/').pop(),row.params.view]),
        [['POST','refresh',undefined],['GET','financial-position','drivers']]);
    });
    await check('A hidden page does no request and one visible activation reads its section once', async () => {
      mode='success'; const hidden=await context.newPage(); hidden.on('pageerror',error => errors.push(error.message));
      await hidden.addInitScript(() => {window.__testVisible=false;Object.defineProperty(document,'visibilityState',{get:() => window.__testVisible?'visible':'hidden'});});
      const count=calls.length; await hidden.goto(origin+'/?view=drivers'); await assertQuiet(hidden);
      assert.equal(calls.length,count);
      await hidden.evaluate(() => {window.__testVisible=true;document.dispatchEvent(new Event('visibilitychange'));});
      await waitForView(hidden,'drivers');
      assert.deepEqual(calls.slice(count).map(row => [row.method,row.params.view]),[['GET','drivers']]);
      await hidden.evaluate(() => {window.__testVisible=false;document.dispatchEvent(new Event('visibilitychange'));window.__testVisible=true;document.dispatchEvent(new Event('visibilitychange'));});
      await assertQuiet(hidden); await hidden.close();
    });
    await check('Denied section GET exposes no financial content and performs no refresh', async () => {
      mode='denied'; const before=calls.length;
      await page.reload(); await page.getByRole('alert').waitFor();
      assert.equal(await page.getByTestId('financial-position-content').count(),0);
      assert.deepEqual(reads(before).map(row => row.params.view),['drivers']); assertNoPost(before);
    });
    await check('Page export uses saved data and full export is explicit with all snapshot bindings', async () => {
      mode='success'; manualSaved=false;
      await page.goto(origin+'/?view=drivers'); await waitForView(page,'drivers');
      let before=calls.length;
      const partialReady=page.waitForEvent('download');
      await page.getByRole('button',{name:'تصدير هذه الصفحة',exact:true}).click();
      const partial=await partialReady;
      assert.match(partial.suggestedFilename(),/-drivers-SAR-/);
      await partial.saveAs(path.join(out,'financial-page-report.xlsx'));
      assert.equal(calls.length,before);
      before=calls.length;
      const completeReady=page.waitForEvent('download');
      await page.getByRole('button',{name:'تصدير جميع الأقسام · SAR',exact:true}).click();
      const complete=await completeReady;
      assert.match(complete.suggestedFilename(),/-all-SAR-/);
      await complete.saveAs(path.join(out,'financial-full-report.xlsx'));
      for(const name of ['financial-page-report.xlsx','financial-full-report.xlsx']) {
        const bytes=fs.readFileSync(path.join(out,name));
        assert(bytes.length>1000 && bytes.subarray(0,2).toString()==='PK','A real spreadsheet must be downloaded');
      }
      assert.deepEqual(reads(before).map(row => row.params.view),['all']); assertNoPost(before);
      await assertQuiet(page);
    });
    await check('One explicit delivery confirmation preserves known money and refreshes once', async () => {
      mode='manual-success'; manualSaved=false;
      await page.setViewportSize({width:1440,height:1080}); await page.reload(); await waitForView(page,'drivers');
      const drivers=page.getByTestId('financial-table-drivers'); assert((await drivers.innerText()).includes('75.00'));
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const row=page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'});
      const before=calls.length;
      await row.getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await page.getByRole('button',{name:'6 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).waitFor();
      assert((await drivers.innerText()).includes('100.00'));
      await page.getByText('تم حفظ التأكيد وتطبيقه في التقرير.',{exact:true}).waitFor();
      const work=calls.slice(before);
      for(const suffix of ['/delivery-after-start/preview','/delivery-after-start/confirm','/refresh','/financial-position']) {
        assert.equal(work.filter(call => call.path.endsWith(suffix)).length,1,suffix);
      }
      assert.equal(work.length,4);
      assert.equal(work[3].method,'GET'); assert.equal(work[3].params.view,'drivers');
      const appliedDialog=page.getByRole('dialog');
      await appliedDialog.waitFor();
      assert.equal(await appliedDialog.locator('tbody tr').count(),6);
      assert.equal(await appliedDialog.locator('tbody tr').filter({hasText:'9001'}).count(),0);
      await page.keyboard.press('Escape'); await appliedDialog.waitFor({state:'hidden'});
      await page.getByRole('button',{name:'6 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      assert.equal(await page.getByRole('dialog').locator('tbody tr').count(),6);
      assert.equal(await page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'}).count(),0);
      await page.screenshot({path:path.join(out,'manual-confirmation-desktop.png'),fullPage:false});
      await page.keyboard.press('Escape'); await assertQuiet(page,1200);
    });
    await check('Saved confirmation remains visible when the follow-up refresh fails', async () => {
      mode='manual-refresh-failed'; manualSaved=false; await page.reload(); await waitForView(page,'drivers');
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const before=calls.length;
      await page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'})
        .getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await page.getByText(/تُعرض آخر أرصدة محفوظة بتاريخها/).first().waitFor();
      const savedDialog=page.getByRole('dialog');
      await savedDialog.waitFor();
      const savedRow=savedDialog.locator('tbody tr').filter({hasText:'9001'});
      assert((await savedRow.innerText()).includes('تم حفظ التأكيد'));
      assert((await savedRow.innerText()).includes('بانتظار تحديث الأرصدة'));
      await page.keyboard.press('Escape'); await savedDialog.waitFor({state:'hidden'});
      await page.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const row=page.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'});
      assert((await row.innerText()).includes('تم حفظ التأكيد'));
      assert((await row.innerText()).includes('بانتظار تحديث الأرصدة'));
      const action=row.getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true});
      assert.equal(await action.count() ? await action.isEnabled() : false,false);
      assert((await page.getByTestId('financial-table-drivers').innerText()).includes('75.00'));
      for(const suffix of ['/delivery-after-start/preview','/delivery-after-start/confirm','/refresh','/financial-position']) {
        assert.equal(calls.slice(before).filter(call => call.path.endsWith(suffix)).length,1,suffix);
      }
      assert.equal(reads(before)[0].params.view,'drivers');
      await page.screenshot({path:path.join(out,'manual-confirmation-pending.png'),fullPage:false});
      await page.keyboard.press('Escape'); await assertQuiet(page);
    });
    await check('Hiding during delivery preflight prevents confirmation and automatic retry', async () => {
      mode='manual-hidden'; manualSaved=false;
      const hidden=await context.newPage(); hidden.on('pageerror',error => errors.push(error.message));
      await hidden.addInitScript(() => {window.__testVisible=true;Object.defineProperty(document,'visibilityState',{get:() => window.__testVisible?'visible':'hidden'});});
      await hidden.goto(origin+'/?view=drivers'); await waitForView(hidden,'drivers');
      await hidden.getByRole('button',{name:'7 طلبات غير مؤكدة — مندوب تجريبي',exact:true}).click();
      const ready=new Promise(resolve => {previewStarted=resolve;});
      const before=calls.length;
      await hidden.getByRole('dialog').locator('tbody tr').filter({hasText:'9001'})
        .getByRole('button',{name:'تأكيد التوصيل بعد بداية التشغيل',exact:true}).click();
      await withDeadline(ready,'Delivery preflight');
      await hidden.evaluate(() => {window.__testVisible=false;document.dispatchEvent(new Event('visibilitychange'));});
      releasePreview(); releasePreview=null; previewStarted=null;
      await hidden.waitForTimeout(250);
      await hidden.evaluate(() => {window.__testVisible=true;document.dispatchEvent(new Event('visibilitychange'));});
      await assertQuiet(hidden);
      const work=calls.slice(before);
      assert.deepEqual(work.map(call => call.path.split('/').pop()),['preview']);
      assert.equal(manualSaved,false); await hidden.close();
    });
    assert.equal(results.length,14);
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(out,'browser-evidence.json'),JSON.stringify({synthetic_only:true,results,calls,errors,
      fixture_sha256:fixtureManifest.sha256,views:pageViews},null,2));
    await context.close();
  } finally {await browser.close();await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error);server.close();process.exitCode=1;});
