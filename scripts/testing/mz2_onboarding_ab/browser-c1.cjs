/* C1-only real browser -> default HTTP transport -> real Native APIs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.MZ2_AB_ORIGIN;
if (!origin || new URL(origin).hostname !== '127.0.0.1') throw new Error('Loopback origin required');
const out = process.env.MZ2_AB_OUTPUT;
fs.mkdirSync(out, {recursive:true});
const results = [], errors = [], blocked = [], http = [];
const shipping = '/api/accounting-module/shipping-v2';
async function get(url) {
  const response = await fetch(origin + url);
  const data = await response.json();
  http.push({method:'GET',url,status:response.status,body:data,observer:'node-readonly'});
  assert.equal(response.status,200,JSON.stringify(data));return data;
}
async function check(name, fn) {
  try { await fn(); results.push({name,status:'PASS'});console.log('PASS '+name); }
  catch(error) { results.push({name,status:'FAIL',error:error.message});throw error; }
}
(async()=>{
  const browser = await chromium.launch({headless:true,channel:process.env.MZ2_BROWSER_CHANNEL || 'msedge'});
  const page = await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
  page.on('pageerror', error=>errors.push(error.message));
  page.on('console', message=>{if(message.type()==='error')errors.push(message.text());});
  page.on('request', request=>http.push({method:request.method(),url:new URL(request.url()).pathname,body:request.postData(),observer:'browser-request'}));
  page.on('response', response=>http.push({method:response.request().method(),url:new URL(response.url()).pathname,status:response.status(),observer:'browser-response'}));
  await page.route('**/*', route=>{if(new URL(route.request().url()).origin===origin)return route.continue();blocked.push(route.request().url());return route.abort();});
  const field = name=>page.getByLabel(name,{exact:true});
  const button = name=>page.getByRole('button',{name,exact:true});
  const confirm = async()=>{await field('سبب إجراء العقد').fill('Synthetic accountant inspected retained original');await field('تأكيد إجراء العقد').check();};
  async function mutate(name, endpoint) {
    const pending=page.waitForResponse(r=>r.request().method()==='POST' && r.url().endsWith(endpoint));
    await button(name).click(); const response=await pending; const body=await response.json();
    assert.equal(response.status(),200,JSON.stringify(body));
    await button('تحديث العقود').waitFor({state:'visible'});
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='تحديث العقود'&&!b.disabled));
    return body;
  }
  const proof = await get('/__test/proof');
  assert.equal(proof.rich_shipping_mode,true);
  let draft, contract, contractEvidence;
  try {
    await check('Stage7 loads actual canonical registry and saves full rich terms through real HTTP',async()=>{
      await page.goto(origin+'?onboarding_session='+proof.session_id+'&onboarding_stage=courier_contracts');
      await field('شركة الشحن').selectOption(proof.courier_id);
      await field('تكلفة الشحن').fill('20.00'); await field('ضريبة الشحن %').fill('15');
      await field('شمول ضريبة الشحن').selectOption('false');await field('سداد الشركة').selectOption('postpaid');
      await field('ضريبة العمولة %').fill('15');await field('شمول ضريبة العمولة').selectOption('true');
      await field('بداية السريان — الرياض').fill('2020-01-01T00:00');
      await field('مرجع دليل الشركة — مطلوب').fill(proof.file_id);await field('نوع مصدر العقد').selectOption('contract');
      await button('إضافة شريحة').click();await field('من مبلغ 1').fill('0');
      await field('نسبة العمولة 1 (0.01 = 1%)').fill('0.01');await field('العمولة الثابتة 1').fill('2.00');
      await confirm();draft=(await mutate('حفظ مسودة الشركة','/rich-contracts/drafts')).draft;
      assert.equal(draft.terms.shipping_cost,'20.00');assert.equal(draft.terms.shipping_vat_percent,'15');assert.equal(draft.terms.commission_vat_percent,'15');
      assert.equal(draft.terms.shipping_cost_vat_inclusive,false);assert.equal(draft.terms.commission_vat_inclusive,true);
      assert.equal(draft.terms.cod_fee_tiers[0].commission_percent,'0.01');assert.equal(draft.context,'delivery');
      assert.equal(draft.terms.effective_from,'2019-12-31T21:00:00Z');
      assert(!Object.hasOwn(draft.terms,'settlement_bank_id'));assert(!Object.hasOwn(draft.terms,'opening_payable'));
    });
    await check('Accountant downloads exact retained bytes and explicitly reviews three evidence purposes',async()=>{
      await field('معرف الملف المحفوظ').fill(proof.file_id);await field('شركة دليل العقد').selectOption(proof.courier_id);
      const download=page.waitForEvent('download');await button('تنزيل الأصل المحفوظ للمراجعة').click();
      const artifact=await download; const file=path.join(out,'retained-original.bin');await artifact.saveAs(file);
      assert.equal(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),proof.source_sha256);
      for(const purpose of ['contract','shipping_tax','commission_tax']) {
        await field('غرض دليل العقد').selectOption(purpose);await confirm();
        const result=await mutate('راجعت الأصل وأعتمد هذا الدليل','/contract-evidence/review');
        assert.equal(result.evidence.purpose,purpose);assert.equal(result.evidence.file_id,proof.file_id);assert.equal(result.evidence.approved_by,'full');
        if(purpose==='contract')contractEvidence=result.evidence;
      }
    });
    await check('Saved terms render readable amounts and fractions; accountant explicitly approves',async()=>{
      const state=await get(shipping+'/rich-contracts');
      await field('مسودة العقد').selectOption(draft.id);
      for(const [purpose,label] of [['contract','دليل العقد'],['shipping_tax','دليل ضريبة الشحن'],['commission_tax','دليل ضريبة العمولة']]) {
        await field(label).selectOption(state.contract_evidence.find(row=>row.purpose===purpose).evidence_id);
      }
      const summary=await field('شروط المسودة المحفوظة').innerText();assert(summary.includes('20.00 ريال سعودي'));assert(summary.includes('1% (الكسر المحفوظ: 0.01)'));assert(summary.includes('آجل'));
      await confirm();contract=(await mutate('اعتماد شروط العقد المختار','/rich-contracts/approve')).contract;
      assert.equal(contract.kind,'rich');assert.equal(contract.draft_id,draft.id);assert.equal(contract.draft_hash,draft.hash);
      const ready=await get(shipping+'/context');assert.equal(ready.stages['7'].ready,true);assert.equal(ready.activation_performed,false);
    });
    await check('Browser reload restores actual approved terms on desktop and mobile',async()=>{
      await page.reload();
      await page.getByText('عقد Synthetic C1 courier · معتمد',{exact:true}).click();
      assert((await field('شروط العقد المعتمد').innerText()).includes('20.00 ريال سعودي'));
      const state=await get(shipping+'/rich-contracts');assert.deepEqual(state.contracts[0],contract);
      await page.screenshot({path:path.join(out,'c1-approved-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      await page.screenshot({path:path.join(out,'c1-approved-mobile.png'),fullPage:true});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Stage7 mobile horizontal overflow');
    });
    await check('Revocation persists and readiness fails closed without financial or control changes',async()=>{
      await confirm();await mutate('سحب اعتماد الدليل '+contractEvidence.evidence_id,'/contract-evidence/revoke');
      const state=await get(shipping+'/rich-contracts');assert.equal(state.contract_evidence.find(row=>row.evidence_id===contractEvidence.evidence_id).state,'revoked');
      const ready=await get(shipping+'/context');assert.equal(ready.stages['7'].ready,false);assert(ready.stages['7'].reasons.some(r=>r.code==='shipping_evidence_not_approved'));
      assert.equal(ready.activation_performed,false);
      const finalProof=await get('/__test/proof');assert.equal(finalProof.non_financial_collections_unchanged,true);
      const writes=http.filter(row=>row.observer==='browser-request'&&!['GET','HEAD'].includes(row.method));
      const allowed=['/rich-contracts/drafts','/contract-evidence/review','/rich-contracts/approve','/contract-evidence/revoke'];
      assert(writes.every(row=>allowed.some(endpoint=>row.url===shipping+endpoint)));
      assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
      await page.screenshot({path:path.join(out,'c1-revoked-mobile.png'),fullPage:true});
    });
  } finally {
    const finalProof = await get('/__test/proof');
    fs.writeFileSync(path.join(out,'browser-c1-results.json'),JSON.stringify({results,errors,blocked,http,finalProof,passed:results.filter(row=>row.status==='PASS').length,source_ui_acceptance_only:true,production_smoke_b:'NOT_PERFORMED',full_16_stage_business_uat:'NOT_PERFORMED'},null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
