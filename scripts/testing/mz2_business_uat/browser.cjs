/* One actual wizard session. Expected facts are declared before execution. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const source = require('./source-register.json');
const origin = process.env.MZ2_C5_ORIGIN, out = process.env.MZ2_C5_EVIDENCE;
if (!origin || new URL(origin).hostname !== '127.0.0.1' || !path.isAbsolute(out || '')) throw new Error('Explicit loopback origin/external evidence required');
fs.mkdirSync(out,{recursive:true});
const base='/api/accounting-module/onboarding', ship='/api/accounting-module/shipping-v2';
const results=[], errors=[], external=[], requests=[], responses=[];
const sourceBytes=fs.readFileSync(path.join(__dirname,'source-register.json'));
const sourceHash=crypto.createHash('sha256').update(sourceBytes).digest('hex');
fs.writeFileSync(path.join(out,'source-register.json'),sourceBytes);
let auth, page, session, externalId, richContract, facts=[], prepaid;
async function api(url,method='GET',body,token=auth?.token) {
  const response=await fetch(origin+url,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const data=await response.json(); const recorded=url==='/__test/bootstrap'?{owner:data.owner,paused_owner:data.paused_owner,ad_binding_id:data.ad_binding_id,authentication:'synthetic session secrets omitted'}:data;
  responses.push({url,method,status:response.status,channel:'authenticated-http',data:recorded});
  return {status:response.status,data};
}
async function readSession() { const result=await api(base+'/sessions/'+session.id);assert.equal(result.status,200);return result.data; }
const field=name=>page.getByLabel(name,{exact:true});
const button=name=>page.getByRole('button',{name,exact:true});
const stage=i=>page.locator('nav button').nth(i-1).click();
async function expectedResponse(method,suffix,action,status=200) {
  const pending=page.waitForResponse(r=>r.request().method()===method&&new URL(r.url()).pathname.endsWith(suffix));
  await action(); const response=await pending; const data=await response.json();
  assert.equal(response.status(),status,JSON.stringify(data));return data;
}
async function save(status='complete') {
  if(await field('حالة القسم المالي').count()) await field('حالة القسم المالي').selectOption(status);
  session=await expectedResponse('PUT','/sections/'+({banks:'banks_cash',providers:'providers',employees:'payroll_obligations',suppliers:'suppliers',external_persons:'suppliers',courier_balances:'couriers_cod',drivers:'couriers_cod',inventory:'inventory',payment_fees:'providers',advertising:'providers',prepaid:'equity',obligations:'equity'})[new URL(page.url()).searchParams.get('onboarding_stage')],()=>button('حفظ البيانات المالية').click());
  return session;
}
async function upload(label='رفع دليل القسم المالي') {
  const evidence=await expectedResponse('POST','/opening-balances/evidence',()=>field(label).setInputFiles({name:'synthetic-c5-source-register.json',mimeType:'application/json',buffer:sourceBytes}));
  assert.equal(evidence.sha256,sourceHash); assert.equal(evidence.size,sourceBytes.length);return evidence;
}
async function reloadAt(number) {
  const id=session.id;await page.reload();await page.locator('nav button').nth(number-1).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('onboarding_session'),id);
  await stage(number); const stored=await readSession();assert.equal(stored.id,id);session=stored;
}
async function record(number,name,work) {
  const began=new Date().toISOString();
  try { await work();const row={stage:number,name,status:'PASS',began,finished:new Date().toISOString(),session_id:session?.id,session_version:session?.version};results.push(row);console.log('PASS stage '+number+' '+name); }
  catch(error){results.push({stage:number,name,status:'FAIL',began,error:error.stack});throw error;}
  finally {fs.writeFileSync(path.join(out,'stage-results.json'),JSON.stringify(results,null,2));}
}
async function addEntity(id,amounts,index=1) {
  await button('اختيار جهة موجودة').click();await field('الجهة '+index).selectOption(id);
  for(const [label,value] of Object.entries(amounts))await field(label+' '+index).fill(value);
  await field('الدليل المطلوب '+index).fill('C5 declared source register '+sourceHash);
}
function lines(section) {return session.sections[section].data.lines;}
function assertLine(section,category,id,amount,meaning) {
  const rows=lines(section).filter(row=>row.category===category&&(row.entity_id||row.financial_account_id)===id);
  assert.equal(rows.length,1);assert.equal(Number(rows[0].original_amount),Number(amount));assert.equal(rows[0].meaning,meaning);assert.equal(rows[0].original_currency,'SAR');assert(rows[0].evidence_file_id);
}
async function confirmRich(){await field('سبب إجراء العقد').fill('C5 synthetic original inspected against declared register');await field('تأكيد إجراء العقد').check();}
async function mutateRich(label,suffix){await confirmRich();const result=await expectedResponse('POST',suffix,()=>button(label).click());await button('تحديث العقود').waitFor();await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='تحديث العقود'&&!b.disabled));return result;}

(async()=>{
  auth=(await api('/__test/bootstrap')).data;
  const browser=await chromium.launch({headless:true,channel:process.env.MZ2_BROWSER_CHANNEL||'msedge'});
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  await context.addInitScript(token=>localStorage.setItem('access_token',token),auth.token);
  page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{
    if(['GET','HEAD'].includes(request.method()))return;
    let body;try{body=request.postDataJSON();}catch{body={multipart:true};}
    requests.push({url:new URL(request.url()).pathname,method:request.method(),body});
  });
  page.on('response',response=>responses.push({url:new URL(response.url()).pathname,method:response.request().method(),status:response.status(),channel:'browser'}));
  await page.route('**/*',route=>{if(new URL(route.request().url()).origin===origin)return route.continue();external.push(route.request().url());return route.abort();});
  try {
    await record(1,'Explicit cutover and retained original in a newly created session',async()=>{
      await page.goto(origin);await field('لحظة القطع للجلسة الجديدة').fill('2026-10-01T00:00');
      session=await expectedResponse('POST','/sessions',()=>button('إنشاء جلسة').click());
      const evidence=await upload();
      session=await expectedResponse('PUT','/cutover',()=>button('حفظ البيانات المالية').click());
      assert.equal(session.cutover.cutover_at,source.cutover);assert.equal(session.cutover.cutover_evidence_file_id,evidence.source_file_id);
      await reloadAt(1);assert.equal(await field('لحظة القطع — Asia/Riyadh').inputValue(),'2026-10-01T00:00');
    });
    await record(2,'Bank/cash/overdraft exact identities; explicit zero and separate liability',async()=>{
      await stage(2);let index=0;for(const [id,amount] of Object.entries(source.banks_cash))await addEntity(id,{'الرصيد الافتتاحي':amount},++index);
      await upload();await save();await reloadAt(2);
      for(const [id,amount] of Object.entries(source.banks_cash))assertLine('banks_cash','financial_account',id,amount,id==='c5-cash'?'zero':id==='c5-overdraft'?'owed_by_us':'available_to_us');
    });
    await record(3,'Provider receivable and explicit canonical bank binding',async()=>{
      await stage(3);await addEntity('tabby',{'الرصيد المستحق لنا':source.providers.tabby_receivable});await field('بنك التسوية 1').selectOption('c5-bank');
      await upload();await save();await reloadAt(3);assertLine('providers','provider_receivable','tabby','17.00','available_to_us');assert.equal(session.sections.providers.data.provider_bindings[0].bank_account_id,'c5-bank');
    });
    await record(4,'Employee salary, advance and custody entered and restored independently',async()=>{
      await stage(4);await addEntity('c5-employee',{'راتب مستحق':'30.00','سلفة الموظف':'10.00','عهدة الموظف':'5.00'});await upload();await save();await reloadAt(4);
      for(const [category,amount,meaning] of [['employee_salary_payable','30.00','owed_by_us'],['employee_advance','10.00','available_to_us'],['employee_custody','5.00','available_to_us']])assertLine('payroll_obligations',category,'c5-employee',amount,meaning);
    });
    await record(5,'Supplier payable and advance remain separate',async()=>{
      await stage(5);await addEntity('c5-supplier',{'مستحق للمورد':'25.00','دفعة مقدمة للمورد':'10.00'});await upload();await save();await reloadAt(5);assertLine('suppliers','supplier_payable','c5-supplier','25.00','owed_by_us');assertLine('suppliers','supplier_advance','c5-supplier','10.00','available_to_us');
    });
    await record(6,'Create/select actual external contact and preserve supplier siblings',async()=>{
      await stage(6);await button('إضافة طرف جديد').click();await field('اسم الطرف').fill(source.external_person.name);await field('هاتف الطرف').fill(source.external_person.phone);await field('ملاحظات الطرف').fill('Synthetic source-register counterparty');
      const person=await expectedResponse('POST','/external-persons',()=>button('حفظ الطرف واختياره').click());externalId=person.id;
      await field('مستحق لنا على الطرف 1').fill('12.00');await field('الدليل المطلوب 1').fill('C5 source register');await save();await reloadAt(6);
      assertLine('suppliers','customer_receivable',externalId,'12.00','available_to_us');assertLine('suppliers','supplier_payable','c5-supplier','25.00','owed_by_us');assertLine('suppliers','supplier_advance','c5-supplier','10.00','available_to_us');assert.equal(await field('الجهة 1').inputValue(),externalId);
    });
    await record(7,'Rich courier terms, actual uploaded source review and immutable approval',async()=>{
      await stage(7);const original=await upload('رفع أصل العقد');await field('شركة الشحن').selectOption('c5-courier');
      await field('تكلفة الشحن').fill('20.00');await field('ضريبة الشحن %').fill('15');await field('شمول ضريبة الشحن').selectOption('false');await field('سداد الشركة').selectOption('postpaid');await field('ضريبة العمولة %').fill('15');await field('شمول ضريبة العمولة').selectOption('true');await field('بداية السريان — الرياض').fill('2020-01-01T00:00');await field('مرجع دليل الشركة — مطلوب').fill(original.source_file_id);await field('نوع مصدر العقد').selectOption('contract');
      await button('إضافة شريحة').click();await field('من مبلغ 1').fill('0');await field('نسبة العمولة 1 (0.01 = 1%)').fill('0.01');await field('العمولة الثابتة 1').fill('2.00');
      const draft=(await mutateRich('حفظ مسودة الشركة','/rich-contracts/drafts')).draft;
      await field('شركة دليل العقد').selectOption('c5-courier');const download=page.waitForEvent('download');await button('تنزيل الأصل المحفوظ للمراجعة').click();const originalPath=path.join(out,'reviewed-courier-original.json');await (await download).saveAs(originalPath);assert.equal(crypto.createHash('sha256').update(fs.readFileSync(originalPath)).digest('hex'),sourceHash);
      for(const purpose of ['contract','shipping_tax','commission_tax']){await field('غرض دليل العقد').selectOption(purpose);const row=(await mutateRich('راجعت الأصل وأعتمد هذا الدليل','/contract-evidence/review')).evidence;assert.equal(row.source_sha256,sourceHash);assert.equal(row.approved_by,auth.owner);}
      const state=(await api(ship+'/rich-contracts')).data;await field('مسودة العقد').selectOption(draft.id);
      for(const [purpose,label] of [['contract','دليل العقد'],['shipping_tax','دليل ضريبة الشحن'],['commission_tax','دليل ضريبة العمولة']])await field(label).selectOption(state.contract_evidence.find(row=>row.purpose===purpose).evidence_id);
      richContract=(await mutateRich('اعتماد شروط العقد المختار','/rich-contracts/approve')).contract;assert.equal(richContract.kind,'rich');assert.equal(richContract.contract_version.terms?.courier_id||richContract.party_id,'c5-courier');
      await reloadAt(7);assert.equal((await api(ship+'/rich-contracts')).data.contracts[0].id,richContract.id);
    });
    await record(8,'Courier balances entered through Stage7 and financially saved from Stage8',async()=>{
      await stage(7);await field('شركة الشحن').selectOption('c5-courier');await field('COD افتتاحي لنا').fill('150.00');await field('مستحق افتتاحي للشركة').fill('20.00');await field('بنك التسوية').selectOption('c5-bank');
      await stage(8);await upload();await save();await reloadAt(8);assertLine('couriers_cod','courier_cod_receivable','c5-courier','150.00','available_to_us');assertLine('couriers_cod','courier_payable','c5-courier','20.00','owed_by_us');
    });
    await record(9,'Driver opening responsibility and fees stay separate from courier siblings',async()=>{
      await stage(9);await addEntity('c5-driver',{'COD في عهدة الموصل':'100.00','أجرة مستحقة للموصل':'15.00'});await save();await reloadAt(9);assertLine('couriers_cod','store_driver_cod_receivable','c5-driver','100.00','available_to_us');assertLine('couriers_cod','store_driver_fee_payable','c5-driver','15.00','owed_by_us');assertLine('couriers_cod','courier_cod_receivable','c5-courier','150.00','available_to_us');
    });
    await record(10,'Actual V2 inventory catalogue, variant/component draft and independent account valuation',async()=>{
      await stage(10);await page.getByText('الكتالوج: 1 منتج · 1 مكوّن · 0 خانة',{exact:true}).waitFor();
      for(const [index,variant,quantity,cost,total] of [[1,'black-54','2','50','100.00'],[2,'blue-54','3','60','180.00']]){
        await button('إضافة منتج أو مكوّن').click();await field('بحث المنتج '+index).fill('C5-ABAYA');await page.getByRole('button').filter({hasText:'SKU: C5-ABAYA'}).click();await field('خيار المنتج '+index).selectOption(variant);await field('الكمية '+index).fill(quantity);await field('تكلفة الوحدة '+index).fill(cost);assert.equal(await field('الإجمالي '+index).inputValue(),total);
      }
      await button('إضافة منتج أو مكوّن').click();await field('نوع البند 3').selectOption('STOCK_COMPONENT');await field('التصنيف 3').selectOption('fabric-cat');await field('المكوّن 3').selectOption('fabric');await field('الكمية 3').fill('1.5');await field('تكلفة الوحدة 3').fill('10');assert.equal(await field('الإجمالي 3').inputValue(),'15.00');
      let index=0;for(const [id,amount] of Object.entries(source.inventory.account_totals)){await button('إضافة قيمة حساب مخزون').click();await field('مرجع حساب المخزون '+(++index)).fill(id);await field('قيمة حساب المخزون '+index).fill(amount);}
      await button('حفظ مسودة المخزون الآن').click();await page.getByText('مسودة المخزون محفوظة على الخادم؛ يمكن استعادتها بعد التحديث.',{exact:true}).waitFor();await upload();await save();await reloadAt(10);
      assert.equal(session.inventory_draft.rows.length,3);assert.deepEqual(session.sections.inventory.data.inventory_valuation.account_totals,source.inventory.account_totals);assert.equal(session.sections.inventory.data.inventory_valuation.total_sar,'295.00');assert.equal(await field('خيار المنتج 2').inputValue(),'blue-54');
    });
    await record(11,'Create actual effective fee policy through UI and preserve provider facts',async()=>{
      await stage(11);await field('مزود سياسة الرسوم').selectOption('tabby');await field('النسبة المئوية').fill('2.5');await field('المبلغ الثابت').fill('1.00');await field('سارية من').fill('2026-01-01');await field('معالجة الضريبة').selectOption('exclusive');
      const policy=await expectedResponse('POST','/fee-policies',()=>button('إنشاء العقد وحفظ اختياره').click());assert.equal(policy.percentage,'2.5');
      await page.getByText('حُفظ العقد واختياره في الجلسة. أكمل دليل القسم ومراجعته.',{exact:true}).waitFor();await save();await reloadAt(11);assert(session.sections.providers.data.fee_policy_ids.includes(policy.id));assertLine('providers','provider_receivable','tabby','17.00','available_to_us');
      const indicator=await page.locator('nav button').nth(10).innerText();assert(indicator.includes('مكتمل'),'Saved fee section must render complete in actual Stage11 navigation: '+indicator);assert(!indicator.includes('دليل ناقص'),'Saved source evidence must not render missing');
    });
    await record(12,'Confirmed advertising identity with explicit wallet and payable selection',async()=>{
      await stage(12);await addEntity(auth.ad_binding_id,{'محفظة مدفوعة مقدمًا':'40.00','مستحق للمنصة':'15.00'});await field('حساب المحفظة المالي 1').selectOption('c5-wallet');await field('حساب الذمة المالي 1').selectOption('c5-ad-payable');await save();await reloadAt(12);
      assertLine('providers','financial_account','c5-wallet','40.00','available_to_us');assertLine('providers','financial_account','c5-ad-payable','15.00','owed_by_us');assertLine('providers','provider_receivable','tabby','17.00','available_to_us');assert.equal(session.sections.providers.data.fee_policy_ids.length,1);
    });
    await record(13,'Select actual paid native invoice with independently calculated325 remaining days',async()=>{
      await stage(13);await upload();await save('incomplete');
      prepaid=await expectedResponse('POST','/prepaid-selections',()=>button('اختيار الالتزام وحفظ الرصيد').click());assert.equal(prepaid.calculation.remaining_prepaid_after_cutover,'3250.00');
      await page.getByText('حُفظ العقد واختياره في الجلسة. أكمل دليل القسم ومراجعته.',{exact:true}).waitFor();await save();await reloadAt(13);assertLine('equity','prepaid_expense',prepaid.entity_id,'3250.00','available_to_us');assert(session.sections.equity.data.prepaid_selection_ids.includes(prepaid.id));
    });
    await record(14,'Actual supported typed obligations/taxes without netting or deposit fallback',async()=>{
      await stage(14);for(const fact of source.obligations){
        await field('عقد التصنيف').selectOption(fact.category);await field('اسم الجهة أو الالتزام').fill('C5 '+fact.reference);await field('مرجع العقد').fill(fact.reference);await field('المبلغ الموثق').fill(fact.amount);
        const made=await expectedResponse('POST','/typed-facts',()=>button('إنشاء العقد وحفظ اختياره').click());assert.equal(Number(made.amount),Number(fact.amount));assert.equal(made.category,fact.category);facts.push(made);await page.getByText('حُفظ العقد واختياره في الجلسة. أكمل دليل القسم ومراجعته.',{exact:true}).waitFor();
      }
      await save();await reloadAt(14);for(const fact of facts)assertLine('equity',fact.category,fact.entity_id,fact.amount,fact.side==='debit'?'available_to_us':'owed_by_us');assertLine('equity','prepaid_expense',prepaid.entity_id,'3250.00','available_to_us');
      const denied=await api(base+'/typed-facts','POST',{category:'deposit',display_name:'Unsupported deposit',reference:'c5-no-deposit',amount:'1.00',currency:'SAR',cutover_date:'2026-10-01',evidence:session.sections.equity.evidence_file_id});assert.equal(denied.status,422);
    });
    await record(15,'One complete source session: exact independent legs, original hashes and locked review',async()=>{
      await stage(15);const feeRow=page.locator('tbody tr').filter({hasText:'عمولات طرق الدفع والضرائب'});assert.equal(await feeRow.count(),1);assert.equal((await feeRow.locator('td').nth(1).innerText()).trim(),'قسم المزوّدين: مكتمل');assert.equal((await feeRow.locator('td').nth(2).innerText()).trim(),session.sections.providers.evidence_file_id);await field('ملاحظة المعاينة والمراجعة').fill('C5 independent source register all16-stage business setup acceptance');
      session=await expectedResponse('POST','/preview',()=>button('معاينة الجلسة على الخادم').click());
      assert.equal(session.preview.debit_total,'4929.00');assert.equal(session.preview.credit_total,'4929.00');assert.equal(session.preview.balanced,true);assert.equal(session.preview.inventory_reconciliation.physical_inventory_verified,false);
      const expected=[['bank','c5-bank','main','debit','1000.00'],['liability','c5-overdraft','bank_overdraft','credit','40.00'],['payment_gateway','tabby','receivable','debit','17.00'],['employee','c5-employee','salary_payable','credit','30.00'],['employee','c5-employee','advance','debit','10.00'],['employee','c5-employee','custody','debit','5.00'],['supplier','c5-supplier','payable','credit','25.00'],['supplier','c5-supplier','advance','debit','10.00'],['external_person',externalId,'receivable','debit','12.00'],['courier','c5-courier','cod_receivable','debit','150.00'],['courier','c5-courier','payable','credit','20.00'],['store_driver','c5-driver','cod_receivable','debit','100.00'],['store_driver','c5-driver','delivery_fee_payable','credit','15.00'],['asset','inventory-products','inventory','debit','280.00'],['asset','inventory-components','inventory','debit','15.00'],['ad_account','c5-wallet','balance','debit','40.00'],['ad_account','c5-ad-payable','debt','credit','15.00'],['asset',prepaid.entity_id,'prepaid_expense','debit','3250.00'],...facts.map(f=>{const original=source.obligations.find(x=>x.category===f.category);return [original.entity_type,f.entity_id,original.sub_account,original.side,original.amount];}),['equity','opening_balance_equity','main','credit','4729.00']];
      const actual=session.preview.entries.map(e=>[e.entity_type,e.entity_id,e.sub_account,e.side,e.sar_amount]);const sort=rows=>rows.map(row=>JSON.stringify(row)).sort();assert.deepEqual(sort(actual),sort(expected));assert.equal(session.preview.zero_accounts.length,1);const zero=session.preview.zero_accounts[0];assert.deepEqual([zero.entity_type,zero.entity_id,zero.sub_account],['bank','c5-cash','main']);assert.equal(zero.account_snapshot.id,'c5-cash');const zeroLine=session.preview.lines.find(row=>row.financial_account_id==='c5-cash');assert.equal(zeroLine.meaning,'zero');assert.equal(zeroLine.sar_amount,'0.00');assert.equal(zero.evidence_file_id,zeroLine.evidence_file_id);
      fs.writeFileSync(path.join(out,'independent-expected-legs.json'),JSON.stringify(expected,null,2));
      session=await expectedResponse('POST','/review',()=>button('مراجعة الجلسة وقفلها').click());assert.equal(session.status,'reviewed');await reloadAt(15);assert.equal(session.status,'reviewed');await page.screenshot({path:path.join(out,'stage15-reviewed-desktop.png'),fullPage:true});
      const conflict=await api(base+'/sessions/'+session.id+'/sections/banks_cash','PUT',{version:session.version,idempotency_key:'c5-reviewed-edit-denied',status:'incomplete',data:{lines:[]}});assert.equal(conflict.status,409);assert.equal(conflict.data.detail.code,'onboarding_session_locked');
    });
    await record(16,'Correct final lock; separate initially-paused owner rejects actual handoff423',async()=>{
      await stage(16);await page.getByText('الاعتماد النهائي مقفل',{exact:true}).waitFor();await button('فحص جاهزية المصدر').click();await page.getByTestId('server-readiness').waitFor();
      const ready=(await api(base+'/sessions/'+session.id+'/readiness')).data;assert.equal(ready.source_ready,true);assert.equal(ready.stage_16_locked,true);assert.equal(ready.ready_for_live_post,false);assert.equal(ready.inventory_physical_approval_verified,false);assert.equal(ready.financial_writes_paused,false);
      const absent='c5-never-created-'+crypto.randomUUID();assert.equal((await api(base+'/sessions/'+absent,'GET',undefined,auth.paused_token)).status,404);
      const rejected=await api(base+'/sessions/'+absent+'/opening-draft','POST',{version:1,idempotency_key:'c5-paused-'+crypto.randomUUID(),note:'Explicit separate initially-paused synthetic owner'},auth.paused_token);assert.equal(rejected.status,423);assert.equal(rejected.data.detail.code,'mz2_writes_paused');
      await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'stage16-held-mobile.png'),fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      const proof=(await api('/__test/proof')).data;assert.equal(proof.non_setup_unchanged,true);assert.equal(proof.no_external_network_attempts,true);assert.deepEqual(proof.unexpected_collections,[]);assert(Object.values(proof.financial_counts).every(value=>value===0));assert.equal(proof.controls.find(c=>c._id===auth.owner).writes_paused,false);assert.equal(proof.controls.find(c=>c._id===auth.paused_owner).writes_paused,true);assert.equal(proof.controls.find(c=>c._id===auth.paused_owner).revision,0);assert.equal(proof.source.source_register_sha256,sourceHash);assert.equal(proof.current_head,proof.source.head);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
      assert(!requests.some(r=>/\/(post|transition|activate|write-control)(\/|$)/.test(r.url)));
    });
  } finally {
    let proof;try{proof=(await api('/__test/proof')).data;}catch(error){proof={error:String(error)};}
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,passed:results.filter(r=>r.status==='PASS').length,errors,external,requests,responses,proof,scope:'16-stage synthetic setup business acceptance; Stage16 held as specified',physical_stock_approval:'NOT_EXECUTED',opening_post:'NOT_EXECUTED',activation:'NOT_EXECUTED',production_writes:0,production_verified:false},null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
