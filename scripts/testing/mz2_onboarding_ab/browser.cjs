/* Real UI -> HTTP -> Track A -> disposable Mongo. No mocked API transport. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.MZ2_AB_ORIGIN;
if (!origin || new URL(origin).hostname !== '127.0.0.1') throw new Error('Loopback origin required');
const out = process.env.MZ2_AB_OUTPUT;
fs.mkdirSync(out, {recursive:true});
const results = [], errors = [], blocked = [], writes = [];
const base = '/api/accounting-module/onboarding';
async function api(url, method='GET', body) {
  const response = await fetch(origin + url, {method,headers:{'Content-Type':'application/json'},...(body ? {body:JSON.stringify(body)} : {})});
  return {status:response.status, data:await response.json()};
}
async function check(name, fn) { await fn(); results.push({name,status:'PASS'}); console.log('PASS '+name); }
(async()=>{
  const browser = await chromium.launch({headless:true, ...(process.env.MZ2_BROWSER_CHANNEL ? {channel:process.env.MZ2_BROWSER_CHANNEL} : {})});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror', error=>errors.push(error.message));
  page.on('console', message=>{if(message.type()==='error')errors.push(message.text());});
  page.on('request', request=>{if(!['GET','HEAD'].includes(request.method()))writes.push({url:new URL(request.url()).pathname,method:request.method(),body:request.postDataJSON()});});
  await page.route('**/*', route=>{if(new URL(route.request().url()).origin===origin)return route.continue();blocked.push(route.request().url());return route.abort();});
  const button = name=>page.getByRole('button',{name,exact:true});
  const field = name=>page.getByLabel(name,{exact:true});
  const stage = index=>page.locator('nav button').nth(index).click();
  const save = async()=>{const pending=page.waitForResponse(r=>r.request().method()==='PUT' && r.url().includes('/sessions/'));await button('حفظ البيانات المالية').click();const response=await pending;assert.equal(response.status(),200,await response.text());return response.json();};
  const proof = (await api('/__test/proof')).data;
  let created, session, lastSave;
  try {
    await check('UI create persists a new backend session; list/get use same ID',async()=>{
      await page.goto(origin);
      await field('لحظة القطع للجلسة الجديدة').fill('2026-10-01T00:00');
      const pending=page.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith('/sessions'));
      await button('إنشاء جلسة').click(); const response=await pending;assert.equal(response.status(),200);created=await response.json();
      assert.equal((await api(base+'/sessions/'+created.id)).data.id,created.id);
      assert((await api(base+'/sessions')).data.items.some(s=>s.id===created.id));
      assert.equal((await api('/__test/proof')).data.persisted_sessions,proof.persisted_sessions+1);
      assert.equal(await page.locator('nav button').count(),16);
    });
    await check('UI cutover save persists and reload restores it',async()=>{
      await field('لحظة القطع — Asia/Riyadh').fill('2026-10-01T00:01');created=await save();
      assert.equal((await api(base+'/sessions/'+created.id)).data.cutover.cutover_at,created.cutover.cutover_at);
      await page.reload();await field('الجلسات المحفوظة').selectOption(created.id);await button('استعادة المحفوظ وتجاهل التعديلات المحلية').click();
      await field('لحظة القطع — Asia/Riyadh').waitFor();assert.equal(await field('لحظة القطع — Asia/Riyadh').inputValue(),'2026-10-01T00:01');
    });
    await check('section save / reload restores explicit zero',async()=>{
      await field('الجلسات المحفوظة').selectOption(proof.session_id);await button('استعادة المحفوظ وتجاهل التعديلات المحلية').click();await stage(1);
      await field('الرصيد الافتتاحي 1').fill('0.00');await field('حالة القسم المالي').selectOption('complete');session=await save();
      lastSave=writes.at(-1);assert.equal(session.sections.banks_cash.data.lines[0].meaning,'zero');
      await page.reload();await field('الجلسات المحفوظة').selectOption(proof.session_id);await button('استعادة المحفوظ وتجاهل التعديلات المحلية').click();await stage(1);
      assert.equal(await field('الرصيد الافتتاحي 1').inputValue(),'0.00');
    });
    await check('identical retry is idempotent; stale CAS returns 409',async()=>{
      const retry=await api(lastSave.url,'PUT',lastSave.body);assert.equal(retry.status,200);assert.equal(retry.data.version,session.version);assert.equal(retry.data.existing,true);
      const stale=await api(lastSave.url,'PUT',{...lastSave.body,idempotency_key:'browser-stale-version-0001'});assert.equal(stale.status,409);assert.equal(stale.data.detail.code,'onboarding_version_conflict');
    });
    await check('N/A requires reason and evidence and round-trips without inferred zero',async()=>{
      const url=base+'/sessions/'+created.id+'/sections/suppliers';
      for(const bad of [{reason:'',evidence_file_id:null},{reason:'No suppliers',evidence_file_id:null}]){
        const result=await api(url,'PUT',{version:created.version,idempotency_key:'browser-na-invalid-'+results.length,...bad,status:'not_applicable',data:{lines:[]}});assert.equal(result.status,422);
      }
      await stage(4);await field('حالة القسم المالي').selectOption('not_applicable');await field('سبب القسم المالي').fill('Synthetic evidence confirms no suppliers');session=await save();
      const restored=(await api(base+'/sessions/'+session.id)).data.sections.suppliers;
      assert.equal(restored.status,'not_applicable');assert(restored.evidence_file_id);assert.equal(restored.data.lines.length,0);
    });
    await check('UI provider/bank binding persists exact IDs',async()=>{
      await stage(2);await field('الرصيد المستحق لنا 1').fill('17.00');await field('بنك التسوية 1').selectOption(proof.bank_id);await field('حالة القسم المالي').selectOption('complete');session=await save();
      assert.equal(session.sections.providers.data.provider_bindings[0].bank_account_id,proof.bank_id);
      await stage(10);
      const selected=page.waitForResponse(r=>r.request().method()==='PUT' && r.url().endsWith('/sections/providers'));
      await button('اختيار العقد المحفوظ').click();
      const selectedResponse=await selected;assert.equal(selectedResponse.status(),200,await selectedResponse.text());
      session=await selectedResponse.json();assert.equal(session.sections.providers.data.fee_policy_ids.length,1);
      await stage(2);await field('حالة القسم المالي').selectOption('complete');session=await save();
    });
    await check('UI per-account inventory valuation round-trip excludes physical quantities',async()=>{
      await stage(9);assert.equal(await field('قيمة حساب المخزون 1').inputValue(),'70.00');assert.equal(await field('قيمة حساب المخزون 2').inputValue(),'30.00');
      session=await save();assert.deepEqual(session.sections.inventory.data.inventory_valuation.account_totals,{'inventory-a':'70.00','inventory-b':'30.00'});
      assert(!/product_id|location_id|opening_quantity/.test(JSON.stringify(session.sections.inventory.data)));
      await page.screenshot({path:path.join(out,'connected-inventory-desktop.png'),fullPage:true});
    });
    await check('server preview contains bank mapping and per-account inventory reconciliation',async()=>{
      await field('ملاحظة المعاينة والمراجعة').fill('Synthetic isolated A+B review');
      const pending=page.waitForResponse(r=>r.url().endsWith('/preview'));await button('معاينة الجلسة على الخادم').click();const response=await pending;assert.equal(response.status(),200,await response.text());session=await response.json();
      assert(session.preview.mappings.some(m=>m.kind==='provider_bank_binding'&&m.bank.id===proof.bank_id));
      assert.equal(session.preview.inventory_reconciliation.verified,true);assert.equal(session.preview.inventory_reconciliation.physical_inventory_verified,false);
      assert.equal(session.preview.zero_accounts.length,1);
    });
    await check('review locks server session and UI edits',async()=>{
      const pending=page.waitForResponse(r=>r.url().endsWith('/review'));await button('مراجعة الجلسة وقفلها').click();const response=await pending;assert.equal(response.status(),200,await response.text());session=await response.json();assert.equal(session.status,'reviewed');
      assert(await button('حفظ البيانات المالية').isDisabled());
      const locked=await api(base+'/sessions/'+session.id+'/sections/banks_cash','PUT',{...lastSave.body,version:session.version,idempotency_key:'browser-reviewed-lock-0001'});assert.equal(locked.status,409);assert.equal(locked.data.detail.code,'onboarding_session_locked');
    });
    await check('readiness exposes Smoke B hold and live-post false, not physical approval',async()=>{
      await button('فحص جاهزية المصدر').click();await page.getByTestId('server-readiness').waitFor();
      const text=await page.getByTestId('server-readiness').innerText();assert(text.includes('Smoke B: BLOCKED_BY_ENVIRONMENT'));assert(text.includes('ready_for_live_post=false'));assert(text.includes('اعتماد الكميات الفعلية: غير مثبت'));
      const ready=(await api(base+'/sessions/'+session.id+'/readiness')).data;assert.equal(ready.ready_for_live_post,false);assert.equal(ready.p02_activation_allowed,false);assert.equal(ready.g47_activation_allowed,false);
    });
    await check('all 16 stages navigate in reviewed session; mobile RTL has no horizontal overflow',async()=>{
      for(let index=0;index<16;index++)await stage(index);
      assert((await page.locator('main').getAttribute('dir'))==='rtl');
      await page.screenshot({path:path.join(out,'connected-reviewed-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      for(let index=0;index<16;index++) {await stage(index);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'stage '+(index+1));}
      await page.screenshot({path:path.join(out,'connected-reviewed-mobile.png'),fullPage:true});
    });
    await check('zero unexpected browser errors or external requests; zero financial effects',async()=>{
      assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
      assert(!writes.some(w=>/\/(post|transition|activate|approve|opening-draft)(\/|$)/.test(w.url)));
      assert.equal((await api('/__test/proof')).data.non_session_collections_unchanged,true);
    });
  } finally {
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,errors,blocked,writes,passed:results.length},null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
