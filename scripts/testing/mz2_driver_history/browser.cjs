/* C2-only: actual DriverPanel, HTTP transport, native Mongo-backed readers. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.MZ2_HISTORY_ORIGIN;
if (!origin || new URL(origin).hostname !== '127.0.0.1') throw new Error('Explicit loopback origin required');
const out = process.env.MZ2_HISTORY_OUTPUT;
if (!out || !path.isAbsolute(out)) throw new Error('Absolute evidence output required');
fs.mkdirSync(out,{recursive:true});
const results=[], http=[], blocked=[], errors=[], consoleMessages=[];
const api='/api/accounting-module/shipping-v2/driver-payment-history';
async function get(url) {
  const response=await fetch(origin+url);const body=await response.json();
  assert.equal(response.status,200,JSON.stringify(body));return body;
}
async function check(name, action) {
  try {await action();results.push({name,status:'PASS'});console.log('PASS '+name);}
  catch(error){results.push({name,status:'FAIL',message:error.message});throw error;}
}
(async()=>{
  const before=await get('/__test/proof');
  assert.equal(before.synthetic_only,true);assert.equal(before.all_collections_unchanged,true);
  const browser=await chromium.launch({headless:true,channel:process.env.MZ2_BROWSER_CHANNEL || 'msedge'});
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await context.route('**/*',route=>{
    const request=route.request();
    if(new URL(request.url()).origin===origin && ['GET','HEAD'].includes(request.method()))return route.continue();
    blocked.push({url:request.url(),method:request.method()});return route.abort();
  });
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>consoleMessages.push({type:message.type(),text:message.text()}));
  page.on('request',request=>http.push({observer:'browser-request',method:request.method(),url:request.url()}));
  page.on('response',response=>http.push({observer:'browser-response',method:response.request().method(),url:response.url(),status:response.status()}));
  const history=()=>page.getByRole('region',{name:'سجل قرارات مراجعة الموصلين',exact:true});
  const rows=()=>page.getByRole('region',{name:'قرارات المراجعة الأصلية',exact:true}).locator('tbody tr');
  async function changed(action) {
    const [response]=await Promise.all([
      page.waitForResponse(response=>new URL(response.url()).pathname===api), action()]);
    const body=await response.json();
    assert.equal(response.status(),200,JSON.stringify(body));
    await rows().first().waitFor({state:'visible'});
    assert.equal(await rows().count(),body.items.length);
    return body;
  }
  async function filter(label,value){return changed(()=>page.getByLabel(label).selectOption(value));}
  let first, second;
  try {
    await check('Actual H2 context and first native page: explicit gaps, original decisions and read-only source',async()=>{
      first=await changed(()=>page.goto(origin));
      assert.equal(first.schema,'mz2.driver.review_history.v1');assert.equal(first.scope,'native_v2_decisions_only');
      assert.equal(first.items.length,50);assert.equal(first.has_more,true);
      assert.equal(first.coverage.unlinked_current_decisions,1);
      assert.deepEqual(first.coverage.missing_native_revisions,[{review_id:before.gap_review_id,revision:1}]);
      const text=await history().innerText();assert(text.includes('قرارات MZ2 الأصلية لكل مراجعة ونسخة'));
      assert(text.includes('التغطية غير مكتملة: 1'));
      await history().getByText('المراجعات التي تحتاج تحققًا',{exact:true}).click();
      assert((await history().innerText()).includes(before.gap_review_id));
      assert.equal(first.items.some(row=>row.financial_txn_group_id===before.pos_group),true);
      assert.equal(first.items.some(row=>row.id===before.rejected_event),true);
      await page.screenshot({path:path.join(out,'c2-native-desktop.png'),fullPage:false});
    });
    await check('Real UI cursor reaches all55 native decisions without duplicate or lost rows',async()=>{
      second=await changed(()=>page.getByRole('button',{name:'القرارات الأقدم',exact:true}).click());
      assert.equal(second.has_more,false);assert.equal(second.next_cursor,null);assert.equal(second.items.length,5);
      const ids=[...first.items,...second.items].map(row=>row.id);
      assert.equal(new Set(ids).size,before.expected_native_decisions);
      assert(http.some(row=>row.url.includes('cursor=')&&row.observer==='browser-request'));
      await changed(()=>page.getByRole('button',{name:'أحدث القرارات',exact:true}).click());
    });
    await check('Approved bank/POS identities, actor and later reversal are shown distinctly',async()=>{
      const approved=await filter('قرار السجل','approved');assert.equal(approved.items.length,3);
      assert.equal(approved.items.filter(row=>row.journal_reversed).length,1);
      const text=await history().innerText();
      assert(text.includes(before.pos_name));assert(text.includes(before.pos_identity));assert(text.includes('asset /'));
      assert(text.includes('other_receivable'));assert(text.includes(before.owner));assert(text.includes('bank / bank-f / main'));
      assert(text.includes('عُكس القيد لاحقًا'));assert(text.includes('وصول المال للبنك يحتاج تسوية منفصلة'));
      await page.screenshot({path:path.join(out,'c2-approved-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      await page.screenshot({path:path.join(out,'c2-approved-mobile.png'),fullPage:true});
      const dimensions=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,
        overflowing:[...document.querySelectorAll('*')].filter(el=>el.getBoundingClientRect().right>innerWidth+1||el.getBoundingClientRect().left< -1)
          .slice(0,25).map(el=>({tag:el.tagName,class:el.className,text:el.textContent.slice(0,100),width:el.getBoundingClientRect().width}))}));
      fs.writeFileSync(path.join(out,'mobile-dimensions.json'),JSON.stringify(dimensions,null,2));
      assert.equal(dimensions.document<=dimensions.viewport,true,'H2 mobile document horizontal overflow');
    });
    await check('Real filters keep approvedPOSrevision2 and original rejectedrevision1 separately',async()=>{
      await filter('طريقة دفع السجل','card_terminal');
      let data=await filter('موصل السجل','driver-f');
      assert.equal(data.items.length,2);assert(data.items.some(row=>row.review_id==='review-1'&&row.review_revision===2));
      data=await filter('قرار السجل','rejected');
      assert(data.items.every(row=>row.payment_method==='card_terminal'&&row.status==='rejected'));
      const original=data.items.find(row=>row.id===before.rejected_event);assert(original);assert.equal(original.review_revision,1);
      const text=await history().innerText();assert(text.includes('review-1 · نسخة 1'));
      assert(text.includes('رفض دون أثر مالي حسب Backend'));assert(text.includes('لا وجهة مالية عند الرفض'));
      await page.screenshot({path:path.join(out,'c2-rejected-mobile.png'),fullPage:false});
    });
    await check('Persisted revoked permission defeats stale synthetic claims and removes history',async()=>{
      await context.setExtraHTTPHeaders({'x-synthetic-actor':'revoked'});
      const pending=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/context'));
      await page.reload();const response=await pending;assert.equal(response.status(),403);
      await page.getByRole('alert').waitFor({state:'visible'});
      assert.equal(await rows().count(),0);assert.equal(await history().count(),0);
      assert((await page.locator('body').innerText()).includes('accounting_permission_required'));
      await page.screenshot({path:path.join(out,'c2-revoked-mobile.png'),fullPage:true});
    });
    await check('Browser emitted GET only, no external request, and full Mongo fingerprint is unchanged',async()=>{
      const after=await get('/__test/proof');
      assert.equal(after.current_hash,before.baseline_hash);assert.equal(after.all_collections_unchanged,true);
      assert.deepEqual(after.legacy_accesses,[]);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
      assert(http.filter(row=>row.observer==='browser-request').every(row=>['GET','HEAD'].includes(row.method)));
    });
  } finally {
    const after=await get('/__test/proof');
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,http,blocked,errors,consoleMessages,before,after,
      passed:results.filter(row=>row.status==='PASS').length,production_writes:0,source_ui_acceptance_only:true,
      smoke_b:'NOT_PERFORMED',full_16_stage_uat:'NOT_PERFORMED'},null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
