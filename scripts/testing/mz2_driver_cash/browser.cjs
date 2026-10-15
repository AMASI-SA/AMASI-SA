/* Actual C3 product components; only real loopback HTTP, no API replacements. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const origin=process.env.MZ2_CASH_ORIGIN,out=process.env.MZ2_CASH_OUTPUT;
if(!origin||new URL(origin).hostname!=='127.0.0.1'||!out||!path.isAbsolute(out))throw new Error('Explicit loopback origin and absolute output required');
fs.mkdirSync(out,{recursive:true});
const results=[],http=[],blocked=[],errors=[],consoleMessages=[],deliveryPayloads=[],injectedResponseLoss=[];
let loseNextDeliveredResponse=false;
const statusPath='/api/store-delivery/app/deliveries/status';
const cashPath='/api/accounting-module/shipping-v2/driver-cash/driver-f';
const allowedPosts=new Set([statusPath,'/api/store-delivery/evidence/delivery-proof',cashPath+'/reconciliations','/__test/cancel-second-assignment']);
async function get(url){const response=await fetch(origin+url),body=await response.json();assert.equal(response.status,200,JSON.stringify(body));return body;}
async function check(name,fn){try{await fn();results.push({name,status:'PASS'});console.log('PASS '+name);}catch(error){results.push({name,status:'FAIL',message:error.message});throw error;}}
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a94kAAAAASUVORK5CYII=','base64');
(async()=>{
 const before=await get('/__test/proof');assert(before.synthetic_only&&before.financial_unchanged);assert.equal(before.write_control.writes_paused,true);
 const browser=await chromium.launch({headless:true,channel:process.env.MZ2_BROWSER_CHANNEL||'msedge'});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',async route=>{
  const r=route.request(),url=new URL(r.url());
  if(url.origin===origin&&(['GET','HEAD'].includes(r.method())||(r.method()==='POST'&&allowedPosts.has(url.pathname)))){
   if(loseNextDeliveredResponse&&r.method()==='POST'&&url.pathname===statusPath){
    loseNextDeliveredResponse=false;
    const actual=await route.fetch();assert.equal(actual.status(),200,await actual.text());
    injectedResponseLoss.push({afterActualBackendCommit:true,actualStatus:actual.status(),path:url.pathname});
    return route.abort('failed');
   }
   return route.continue();
  }
  blocked.push({method:r.method(),url:r.url()});return route.abort();
 });
 const page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>consoleMessages.push({type:m.type(),text:m.text()}));
 page.on('request',r=>{http.push({event:'request',method:r.method(),url:r.url()});if(new URL(r.url()).pathname===statusPath)deliveryPayloads.push(r.postDataJSON());});
 page.on('response',r=>http.push({event:'response',method:r.request().method(),url:r.url(),status:r.status()}));
 async function submitDelivery(number,value,{loseResponse=false}={}){
  await page.getByRole('button',{name:'تسليم '+number,exact:true}).click();
  await page.getByRole('button',{name:'كاش',exact:true}).click();
  await page.getByLabel('النقد المستلم فعليًا',{exact:true}).fill(value);
  await page.getByRole('checkbox').check();
  await page.getByLabel('صورة إثبات تسليم الطلب',{exact:true}).setInputFiles({name:'synthetic-delivery.png',mimeType:'image/png',buffer:png});
  let response;
  if(loseResponse){
   const uploads=http.filter(r=>r.event==='request'&&r.url.endsWith('/api/store-delivery/evidence/delivery-proof')).length;
   loseNextDeliveredResponse=true;
   await page.getByRole('button',{name:'تأكيد تم التوصيل',exact:true}).click();
   await page.getByRole('button',{name:'إعادة إرسال التأكيد نفسه',exact:true}).waitFor();
   const payload=deliveryPayloads.at(-1),saved=await get('/__test/proof');
   assert.equal(saved.collections.filter(r=>r.order_number===number).length,1);
   await page.screenshot({path:path.join(out,'c3-retained-confirmation-mobile.png'),fullPage:false});
   [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===statusPath),page.getByRole('button',{name:'إعادة إرسال التأكيد نفسه',exact:true}).click()]);
   assert.deepEqual(deliveryPayloads.at(-1),payload);
   assert.equal(http.filter(r=>r.event==='request'&&r.url.endsWith('/api/store-delivery/evidence/delivery-proof')).length,uploads+1);
   assert.equal((await get('/__test/proof')).salla_calls,saved.salla_calls);
  }else{
   [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===statusPath),page.getByRole('button',{name:'تأكيد تم التوصيل',exact:true}).click()]);
  }
  assert.equal(response.status(),200,await response.text());
  await page.getByText('تم حفظ تسليم '+number,{exact:true}).waitFor();
  return response.json();
 }
 async function explicitMatch(sourceType,sourceId,number,value){
  await page.getByLabel('توريد قائم للمطابقة',{exact:true}).selectOption(JSON.stringify([sourceType,sourceId]));
  await page.getByLabel('مبلغ مطابقة الطلب '+number,{exact:true}).fill(value);
  await page.getByLabel('سبب ربط النقد',{exact:true}).fill('Synthetic accountant reviewed exact existing handover');
  const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===cashPath+'/reconciliations'),page.getByRole('button',{name:'حفظ ربط المطابقة',exact:true}).click()]);
  const body=await response.json();assert.equal(response.status(),200,JSON.stringify(body));assert.equal(body.financial_effect,'none');
  await page.getByText('حُفظ ربط المطابقة دون إنشاء قيد مالي.',{exact:true}).waitFor();
  await page.waitForFunction(()=>!document.querySelector('[aria-label="سبب ربط النقد"]')?.value);
  return body;
 }
 try{
  await check('Actual driver component reports unknown history and never infers missing actual cash',async()=>{
   await page.goto(origin);await page.getByText('التغطية غير مكتملة',{exact:false}).waitFor();
   const data=await get('/api/store-delivery/app/accounts/physical-cash');
   assert.equal(data.totals.confirmed_cash,'200.00');assert.equal(data.totals.expected_cod,'200.00');
   assert.deepEqual(data.coverage.missing_confirmation_collection_ids,['collection-historical-missing']);
   assert.equal(data.coverage.historical_cash_inferred,false);assert.equal(data.coverage.opening_physical_cash,null);
   const text=await page.locator('body').innerText();assert(text.includes('لا يمثل الرصيد المحاسبي'));assert(text.includes('لا يمكن افتراض قيمتها صفرًا'));
  });
  await check('Real delivered modal requires explicit actual amount and confirmation before any POST',async()=>{
   await page.getByRole('button',{name:'تسليم browser-1',exact:true}).click();await page.getByRole('button',{name:'كاش',exact:true}).click();
   const postCount=http.filter(r=>r.event==='request'&&r.method==='POST').length;
   await page.getByRole('button',{name:'تأكيد تم التوصيل',exact:true}).click();
   await page.getByRole('alert').filter({hasText:'أدخل النقد'}).waitFor();
   assert.equal(http.filter(r=>r.event==='request'&&r.method==='POST').length,postCount);
   await page.getByLabel('النقد المستلم فعليًا',{exact:true}).fill('450.00');
   assert((await page.locator('body').innerText()).includes('نقص في النقد المستلم'));
   await page.setViewportSize({width:390,height:844});
   await page.screenshot({path:path.join(out,'c3-cash-confirmation-mobile.png'),fullPage:false});
   await page.getByRole('button',{name:'إغلاق',exact:true}).click();
  });
  await check('Actual proof upload and delivered POST capture shortage once; exact HTTP replay creates no duplicate',async()=>{
   await submitDelivery('browser-1','450.00');
   const payload=deliveryPayloads.at(-1);assert.equal(payload.physical_cash_amount,'450.00');assert.equal(payload.physical_cash_confirmed,true);
   let proof=await get('/__test/proof');
   let rows=proof.collections.filter(r=>r.order_number==='browser-1');assert.equal(rows.length,1);
   assert.equal(rows[0].physical_cash_evidence.physical_cash_amount,'450.00');assert.equal(rows[0].physical_cash_evidence.cod_amount,'500.00');
   assert.equal(rows[0].physical_cash_evidence.variance,'-50.00');assert.equal(proof.financial_unchanged,true);
   const seal=rows[0].physical_cash_evidence.seal,calls=proof.salla_calls;
   const repeated=await page.request.post(origin+statusPath,{data:payload});assert.equal(repeated.status(),200,await repeated.text());
   proof=await get('/__test/proof');rows=proof.collections.filter(r=>r.order_number==='browser-1');
   assert.equal(rows.length,1);assert.equal(rows[0].physical_cash_evidence.seal,seal);assert.equal(proof.salla_calls,calls);
   const data=await get('/api/store-delivery/app/accounts/physical-cash');assert.equal(data.totals.confirmed_cash,'650.00');assert.equal(data.totals.variance,'-50.00');
  });
  await check('Lost actual committed response retries the identical proof and payload; multiple deliveries aggregate once',async()=>{
   await submitDelivery('browser-2','300.00',{loseResponse:true});
   assert.equal(injectedResponseLoss.length,1);
   const data=await get('/api/store-delivery/app/accounts/physical-cash');
   assert.equal(data.items.length,3);assert.equal(data.totals.confirmed_cash,'950.00');assert.equal(data.totals.expected_cod,'1000.00');
   assert.equal(data.totals.variance,'-50.00');assert.equal(data.totals.matched_handover,'0.00');assert.equal(data.totals.confirmed_cash_remaining,'950.00');
   await page.getByRole('button',{name:'تحديث سجل النقد',exact:true}).click();
   await page.getByText('الطلب: browser-2',{exact:true}).waitFor();
   await page.screenshot({path:path.join(out,'c3-driver-multiple-mobile.png'),fullPage:true});
  });
  await check('Actual H2 matching requires explicit native and operational sources; creates metadata only',async()=>{
   await page.setViewportSize({width:1440,height:1100});await page.getByRole('button',{name:'مطابقة المحاسب',exact:true}).click();
   await page.getByLabel('توريد قائم للمطابقة',{exact:true}).waitFor();assert.equal(await page.getByLabel('توريد قائم للمطابقة').inputValue(),'');
   assert.equal(await page.getByRole('button',{name:'حفظ ربط المطابقة',exact:true}).isEnabled(),false);
   await explicitMatch('native_cash_settlement',before.native_source_id,'native-seed','200.00');
   await explicitMatch('operational_cod_remittance',before.operational_source_id,'browser-1','450.00');
   const data=await get(cashPath);assert.equal(data.reconciliations.length,2);assert.equal(data.totals.matched_handover,'650.00');assert.equal(data.totals.confirmed_cash_remaining,'300.00');
   const sources=data.reconciliations.map(r=>r.source);assert(sources.some(s=>s.txn_group_id===before.native_group&&s.financial_proof==='verified_native_cash_settlement'));
   assert(sources.some(s=>s.txn_group_id===null&&s.financial_proof==='operational_only_no_native_settlement_proof'));
   assert(data.reconciliations.every(r=>r.recorded_by==='c3-browser-accountant'&&r.financial_effect==='none'));
   await page.getByText('لا توجد توريدات غير مرتبطة معروضة.',{exact:true}).waitFor();
   await page.screenshot({path:path.join(out,'c3-h2-matched-desktop.png'),fullPage:true});
   await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'c3-h2-matched-mobile.png'),fullPage:true});
   const dimensions=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
   fs.writeFileSync(path.join(out,'mobile-dimensions.json'),JSON.stringify(dimensions));assert(dimensions.document<=dimensions.viewport,'Mobile page horizontal overflow');
  });
  await check('Synthetic later status change preserves actual observation and removes matching eligibility',async()=>{
   const prior=(await get(cashPath)).items.find(r=>r.order_number==='browser-2');
   const changed=await page.request.post(origin+'/__test/cancel-second-assignment');assert.equal(changed.status(),200);
   await page.getByRole('button',{name:'إقرارات الموصل',exact:true}).click();
   await page.getByText('الحالة الحالية: cancelled',{exact:false}).waitFor();
   const data=await get('/api/store-delivery/app/accounts/physical-cash'),item=data.items.find(r=>r.order_number==='browser-2');
   assert.equal(item.seal,prior.seal);assert.equal(item.physical_cash_amount,'300.00');assert.equal(item.eligible_for_reconciliation,false);
   assert.equal(data.totals.confirmed_cash,'950.00');assert.equal(data.totals.confirmed_cash_remaining,'300.00');assert.equal(data.coverage.complete,false);
   await page.screenshot({path:path.join(out,'c3-preserved-source-change-mobile.png'),fullPage:true});
  });
  await check('Financial records and all write-control fields stay unchanged; no Legacy or external browser traffic',async()=>{
   const after=await get('/__test/proof');assert.equal(after.financial_current_hash,before.financial_baseline_hash);
   assert.equal(after.financial_unchanged,true);assert.equal(after.write_control.writes_paused,true);
   const withoutRevision=o=>{const c={...o};delete c.revision;return c;};assert.deepEqual(withoutRevision(after.write_control),withoutRevision(before.write_control));
   assert.deepEqual(after.legacy_accesses,[]);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
   assert.equal(after.links.length,2);assert.equal(after.production_writes,0);assert.equal(after.synthetic_faults.length,1);
  });
 }finally{
  const after=await get('/__test/proof');fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,http,blocked,errors,consoleMessages,injectedResponseLoss,before,after,passed:results.filter(r=>r.status==='PASS').length,
   source_ui_acceptance_only:true,production_writes:0,smoke_b:'NOT_PERFORMED',full_16_stage_uat:'NOT_PERFORMED'},null,2));
  await browser.close();
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
