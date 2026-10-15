/* Connected synthetic acceptance: actual components and actual loopback routes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const origin=process.env.MZ2_LATE_ORIGIN,out=process.env.MZ2_LATE_OUTPUT;
if(!origin||new URL(origin).origin!==origin||new URL(origin).hostname!=='127.0.0.1'||!out||!path.isAbsolute(out))throw Error('Explicit loopback origin and absolute evidence output required');
fs.mkdirSync(out,{recursive:true});
const upload='/api/store-delivery/evidence/late-delivery',queue='/api/accounting-module/shipping-v2/late-delivery-evidence';
const results=[],http=[],blocked=[],errors=[],injected=[],payloads=[];
let loseUpload=true,before,after,browser;
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a94kAAAAASUVORK5CYII=','base64');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
async function proof(){const response=await fetch(origin+'/__test/proof');assert.equal(response.status,200);return response.json();}
async function check(name,fn){await fn();results.push({name,status:'PASS'});console.log('PASS '+name);}
(async()=>{
 try{
  before=await proof();assert(before.synthetic_only&&before.financial_controls_originals_c3_unchanged);assert.equal(before.write_control.writes_paused,true);assert.equal(before.attachment_count,0);assert.equal(before.review_count,0);
  browser=await chromium.launch({headless:true,channel:process.env.MZ2_BROWSER_CHANNEL||'msedge'});
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',async route=>{
   const r=route.request(),u=new URL(r.url()),isReview=u.pathname.startsWith(queue+'/')&&u.pathname.endsWith('/review');
   if(u.origin!==origin||!(['GET','HEAD'].includes(r.method())||(r.method()==='POST'&&(u.pathname===upload||isReview)))){blocked.push({method:r.method(),url:r.url()});return route.abort();}
   if(r.method()==='POST'&&u.pathname===upload){
    const requestId=/name="request_id"\r\n\r\n([^\r\n]+)/.exec(r.postData()||'')?.[1];
    payloads.push({requestId});
    if(loseUpload){loseUpload=false;const actual=await route.fetch();assert.equal(actual.status(),200,await actual.text());injected.push({afterActualBackendCommit:true,body:await actual.json()});return route.abort('failed');}
   }
   return route.continue();
  });
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>http.push({event:'request',method:r.method(),url:r.url()}));
  page.on('response',r=>http.push({event:'response',status:r.status(),url:r.url()}));
  await page.goto(origin);
  async function send(number,retry){
   await page.getByRole('button',{name:'طلب '+number,exact:true}).click();
   await page.getByLabel('سبب الإضافة',{exact:true}).fill('Synthetic later evidence for delivery '+number);
   await page.getByLabel('ملف الدليل اللاحق',{exact:true}).setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:png});
   const submit=page.getByRole('button',{name:'إرفاق للمراجعة',exact:true});
   if(retry){await submit.click();await page.getByRole('alert').waitFor();assert.equal((await proof()).attachment_count,1);}
   const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===upload&&r.request().method()==='POST'),submit.click()]);
   assert.equal(response.status(),200,await response.text());const body=await response.json();
   assert.equal(body.state,'pending');assert.equal(body.attachment.financial_effect,'none');assert.equal(body.attachment.proof_sha256,hash(png));
   if(retry){assert.equal(body.replayed,true);assert(payloads[0].requestId);assert.equal(payloads[0].requestId,payloads[1].requestId);assert.equal(body.attachment.id,injected[0].body.attachment.id);}
   await page.getByText('تم إرفاق الدليل للمراجعة فقط. لم تتغير حالة التسوية أو أي أرصدة.',{exact:true}).waitFor();return body;
  }
  let first,second;
  await check('Lost upload response retries the same request after real commit without duplication',async()=>{first=await send('1',true);second=await send('2',false);assert.equal((await proof()).attachment_count,2);});
  await check('View-only accountant cannot request original or decide',async()=>{
   await page.getByRole('button',{name:'عرض فقط',exact:true}).click();await page.getByRole('heading',{name:'طلب #1 — بانتظار المراجعة'}).waitFor();
   assert.equal(await page.getByRole('button',{name:'عرض الملف الأصلي',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'قبول المرفق',exact:true}).count(),0);
   assert.equal(http.filter(row=>row.event==='request'&&row.url.endsWith('/original')).length,0);
   const forbidden=await fetch(origin+queue+'/'+first.attachment.id+'/original',{headers:{'x-synthetic-actor':'viewer'}});assert.equal(forbidden.status,403);
   await page.screenshot({path:path.join(out,'view-only.png'),fullPage:true});
  });
  await page.getByRole('button',{name:'المراجع',exact:true}).click();
  async function decide(number,item,decision){
   const row=page.locator('article').filter({has:page.getByRole('heading',{name:'طلب #'+number+' — بانتظار المراجعة'})});
   const button=row.getByRole('button',{name:decision==='approved'?'قبول المرفق':'رفض المرفق',exact:true});
   await row.waitFor();assert(await button.isDisabled());
   const [original]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===queue+'/'+item.attachment.id+'/original'),row.getByRole('button',{name:'عرض الملف الأصلي',exact:true}).click()]);
   assert.equal(original.status(),200);assert.equal(hash(await original.body()),hash(png));
   await row.getByRole('img').waitFor();await page.waitForFunction(()=>Array.from(document.images).some(i=>i.naturalWidth>0));
   await row.getByLabel('ملاحظة المراجعة',{exact:true}).fill('ab');assert(await button.isDisabled());
   await row.getByLabel('ملاحظة المراجعة',{exact:true}).fill('Synthetic exact original inspected: '+decision);
   const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===queue+'/'+item.attachment.id+'/review'),button.click()]);
   assert.equal(response.status(),200,await response.text());const body=await response.json();assert.equal(body.state,decision);assert.equal(body.attachment.id,item.attachment.id);assert.equal(body.attachment.financial_effect,'none');
  }
  await check('Authorized original inspection gates approval and rejection, with a meaningful note',async()=>{await decide('1',first,'approved');await decide('2',second,'rejected');});
  await page.screenshot({path:path.join(out,'review-decisions-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'review-decisions-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await check('Driver sees recorded decision without a settlement claim',async()=>{
   await page.getByRole('button',{name:'الموصل',exact:true}).click();await page.getByRole('button',{name:'طلب 1',exact:true}).click();await page.getByText('تم قبول المرفق',{exact:true}).waitFor();
   await page.getByRole('button',{name:'طلب 2',exact:true}).click();await page.getByText('تم رفض المرفق',{exact:true}).waitFor();await page.screenshot({path:path.join(out,'driver-reviewed-mobile.png'),fullPage:true});
  });
  await check('Financial state, paused controls, original delivery proof and C3 remain identical',async()=>{
   after=await proof();assert(after.financial_controls_originals_c3_unchanged);assert.equal(after.baseline_hash,before.baseline_hash);assert.equal(after.current_hash,before.current_hash);assert.equal(after.attachment_count,2);assert.equal(after.review_count,2);assert.deepEqual(after.legacy_accesses,[]);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);
  });
 }catch(error){results.push({status:'FAIL',message:error.stack});process.exitCode=1;}
 finally{fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,before,after,http,blocked,errors,injected,payloads},null,2));if(browser)await browser.close();}
})();
