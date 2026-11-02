/* Real UI -> HTTP -> Track A -> disposable Mongo. No mocked API transport. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.MZ2_AB_ORIGIN;
if (!origin || new URL(origin).hostname !== '127.0.0.1') throw new Error('Loopback origin required');
const out = process.env.MZ2_AB_OUTPUT;
fs.mkdirSync(out, {recursive:true});
const results = [], errors = [], blocked = [], writes = [], permissionErrors = [], permissionResponses = [];
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
  // The new recurring-source panel must respect the existing owner-only GET.
  // Keep the synthetic employee as an employee; verify this exact denial rather
  // than swapping its actor or relaxing zero-unexpected-errors below.
  page.on('console', message=>{
    if(message.type()!=='error')return;
    if(message.location().url === origin+'/api/recurring-obligations' && /status of 403/.test(message.text())) permissionErrors.push(message.text());
    else errors.push(message.text());
  });
  page.on('response', response=>{
    if(response.url()===origin+'/api/recurring-obligations') permissionResponses.push(response.json().then(body=>({status:response.status(),body})));
  });
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
      const text=await page.getByTestId('server-readiness').innerText();assert(text.includes('Smoke B: إثبات بيئة التشغيل المطلوبة غير مكتمل'));assert(text.includes('جاهزية الترحيل الفعلي: غير متاحة'));assert(!text.includes('BLOCKED_BY_ENVIRONMENT'));assert(text.includes('اعتماد الكميات الفعلية: غير مثبت'));
      for(const reason of ['التشغيل المالي V2 غير مفعّل','لم يُثبت تنفيذ الافتتاح','إثبات Smoke B لبيئة الإنتاج مطلوب','تفويض المالك الصريح'])assert(text.includes(reason));assert(!text.includes('تعذر إكمال الطلب'));
      const ready=(await api(base+'/sessions/'+session.id+'/readiness')).data;assert.equal(ready.ready_for_live_post,false);assert.equal(ready.p02_activation_allowed,false);assert.equal(ready.g47_activation_allowed,false);
    });
    await check('all 16 stages navigate in reviewed session; mobile RTL has no horizontal overflow',async()=>{
      for(let index=0;index<16;index++) {
        await stage(index);
        if ([1,8,11,14].includes(index)) await page.screenshot({path:path.join(out,`stage-${index+1}-desktop.png`),fullPage:true});
      }
      assert((await page.locator('main').getAttribute('dir'))==='rtl');
      await page.screenshot({path:path.join(out,'connected-reviewed-desktop.png'),fullPage:true});
      await page.setViewportSize({width:390,height:844});
      for(let index=0;index<16;index++) {await stage(index);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'stage '+(index+1));}
      await page.screenshot({path:path.join(out,'connected-reviewed-mobile.png'),fullPage:true});
    });
    await check('zero unexpected browser errors or external requests; zero financial effects',async()=>{
      await stage(12);
      await page.getByRole('region',{name:'الالتزامات التشغيلية القائمة',exact:true}).getByRole('alert').filter({hasText:'صلاحية قراءة هذا المصدر غير متاحة.'}).waitFor();
      const denials=await Promise.all(permissionResponses);
      assert(denials.length>0); assert(permissionErrors.length>0);
      for(const denial of denials) {assert.equal(denial.status,403);assert.equal(denial.body.detail.code,'owner_required');}
      assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
      assert(!writes.some(w=>/\/(post|transition|activate|approve|opening-draft)(\/|$)/.test(w.url)));
      assert.equal((await api('/__test/proof')).data.non_session_collections_unchanged,true);
    });
    // Supplemental real HTTP contracts use a second isolated database. The original
    // twelve UI assertions above remain intact, including their full fingerprint.
    const expanded = (await api('/expanded/__test/proof')).data;
    let row = expanded.session;
    let serial = 0;
    const call = async (url, method='GET', body, status=200) => {
      if(method !== 'GET') writes.push({url:'/expanded'+url, method, body});
      const response = await api('/expanded'+url, method, body);
      assert.equal(response.status,status,JSON.stringify(response.data)); return response.data;
    };
    const getSession = () => call(base+'/sessions/'+row.id);
    const persist = async (section, lines, extra={}) => {
      row = await call(base+'/sessions/'+row.id+'/sections/'+section,'PUT',{
        version:row.version,idempotency_key:'expanded-save-'+(++serial),status:'complete',
        reason:'Synthetic stage acceptance',evidence_file_id:row.sections[section].evidence_file_id,
        data:{...row.sections[section].data,lines,...extra}});
      const normalizeLines = values => values.map(value => {
        const money = String(value.original_amount);
        assert.match(money, /^\d+(\.\d{1,2})?$/);
        const [whole, fraction=''] = money.split('.');
        return {category:value.category, entity_id:value.entity_id ?? null,
          financial_account_id:value.financial_account_id ?? null,
          amount:(BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'))).toString(),
          currency:value.original_currency, meaning:value.meaning,
          evidence:value.evidence_file_id};
      }).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
      // A consistently lossy PUT+GET implementation must still fail against
      // the exact caller-submitted identities, amounts and financial meanings.
      assert.deepEqual(normalizeLines(row.sections[section].data.lines),normalizeLines(lines));
      for(const [key,value] of Object.entries(extra)) assert.deepEqual(row.sections[section].data[key],value);
      assert.deepEqual((await getSession()).sections[section],row.sections[section]);
    };
    const line = (section, category, entity, amount, meaning) => ({category,
      ...(category==='financial_account'?{financial_account_id:entity}:{entity_id:entity}),
      original_amount:amount,original_currency:'SAR',fx_rate_to_sar:'1',meaning,
      evidence_file_id:row.sections[section].evidence_file_id});
    const action = async (name,status=200) => call(base+'/sessions/'+row.id+'/'+name,'POST',{
      version:row.version,idempotency_key:'expanded-'+name+'-'+(++serial),note:'Synthetic stage acceptance'},status);
    await check('stage 4 HTTP employee salary, advance and custody persist independently',async()=>{
      assert((await call(base+'/identities/employee')).items.some(x=>x.id==='uat-employee'));
      await persist('payroll_obligations',[
        line('payroll_obligations','employee_salary_payable','uat-employee','30.00','owed_by_us'),
        line('payroll_obligations','employee_advance','uat-employee','10.00','available_to_us'),
        line('payroll_obligations','employee_custody','uat-employee','5.00','available_to_us')]);
    });
    await check('stage 5 HTTP supplier payable and advance remain separate',async()=>{
      assert((await call(base+'/identities/supplier')).items.some(x=>x.id==='uat-supplier'));
      await persist('suppliers',[line('suppliers','supplier_payable','uat-supplier','25.00','owed_by_us'),line('suppliers','supplier_advance','uat-supplier','10.00','available_to_us')]);
    });
    await check('stage 6 HTTP external person creation, invalid request and exact identity readback',async()=>{
      await call(base+'/external-persons','POST',{name:'',reference:'invalid'},422);
      const person=await call(base+'/external-persons','POST',{name:'Synthetic UAT person',reference:'uat-person-proof',person_type:'person'});
      assert((await call(base+'/identities/external_person')).items.some(x=>x.id===person.id));
      await persist('suppliers',[...row.sections.suppliers.data.lines,line('suppliers','customer_receivable',person.id,'12.00','available_to_us')]);
    });
    await check('stage 7 real Track F courier, rate and canonical bank binding setup with replay and CAS',async()=>{
      const ship='/api/accounting-module/shipping-v2';
      const common={confirmed:true,reason:'Synthetic signed courier terms'};
      const courier={...common,request_id:'uat-courier-0001',version:0,courier_key:'uat-courier',name:'Synthetic UAT courier',salla_carrier_keys:['uat-source']};
      const saved=await call(ship+'/couriers','POST',courier);assert.equal(saved.version,1);
      assert.equal((await call(ship+'/couriers','POST',courier)).state,'already_saved');
      await call(ship+'/rates','POST',{...common,request_id:'uat-rate-stale',version:0,party_type:'courier',party_id:'uat-courier',context:'delivery',effective_from:'2020-01-01T00:00:00Z',currency:'SAR',delivery_fee:'10.00',cod_fixed_fee:'1.00',cod_percent:'2',vat_percent:'15',vat_included:true,vat_treatment:'gross_expense_no_input_vat',contract_reference:'signed-uat'},409);
      await call(ship+'/rates','POST',{...common,request_id:'uat-rate-0001',version:1,party_type:'courier',party_id:'uat-courier',context:'delivery',effective_from:'2020-01-01T00:00:00Z',currency:'SAR',delivery_fee:'10.00',cod_fixed_fee:'1.00',cod_percent:'2',vat_percent:'15',vat_included:true,vat_treatment:'gross_expense_no_input_vat',contract_reference:'signed-uat'});
      await call(ship+'/bindings','POST',{...common,request_id:'uat-binding-0001',version:2,party_type:'courier',party_id:'uat-courier',financial_account_id:expanded.bank_id});
      const read=await call(ship+'/context');assert.equal(read.setup_version,3);assert(read.couriers.some(x=>x.courier_key==='uat-courier'));assert.equal(read.activation_performed,false);
    });
    await check('stage 7 full editor draft is rejected without changing native setup',async()=>{
      const ship='/api/accounting-module/shipping-v2';
      const before=await call(ship+'/context');assert.equal(before.setup_version,3);
      const beforeSetup=(await api('/expanded/__test/proof')).data.shipping_setup_snapshot;
      assert.equal(beforeSetup.version,3);assert.equal(beforeSetup.contracts.length,1);assert.equal(beforeSetup.bindings.length,1);
      const rejected=await call(ship+'/rates','POST',{
        confirmed:true,reason:'Synthetic unsupported editor contract',request_id:'uat-editor-unsupported',version:3,
        party_type:'courier',party_id:'uat-courier',context:'delivery',effective_from:'2020-01-01T00:00:00Z',currency:'SAR',
        delivery_fee:'10.00',cod_fixed_fee:'1.00',cod_percent:'2',vat_percent:'15',vat_included:true,
        vat_treatment:'gross_expense_no_input_vat',contract_reference:'signed-uat',
        payment_mode:'prepaid',cod_fee_tiers:[{min_amount:'0',max_amount:'',min_inclusive:true,max_inclusive:false,commission_percent:'0.02',fixed_fee:'1.00'}],
        shipping_vat_percent:'15',commission_vat_percent:'5',shipping_cost_vat_inclusive:true,commission_vat_inclusive:false,
      },422);
      const extras=rejected.detail.filter(error=>error.type==='extra_forbidden').map(error=>error.loc.at(-1)).sort();
      assert.deepEqual(extras,['payment_mode','cod_fee_tiers','shipping_vat_percent','commission_vat_percent','shipping_cost_vat_inclusive','commission_vat_inclusive'].sort());
      assert.deepEqual(await call(ship+'/context'),before);
      assert.deepEqual((await api('/expanded/__test/proof')).data.shipping_setup_snapshot,beforeSetup);
    });
    await check('stages 8 and 9 HTTP courier and driver receivable/payable balances persist without netting',async()=>{
      assert((await call(base+'/identities/courier')).items.some(x=>x.id==='uat-courier'));
      assert((await call(base+'/identities/store_driver')).items.some(x=>x.id==='uat-driver'));
      await persist('couriers_cod',[
        line('couriers_cod','courier_cod_receivable','uat-courier','150.00','available_to_us'),
        line('couriers_cod','courier_payable','uat-courier','20.00','owed_by_us'),
        line('couriers_cod','store_driver_cod_receivable','uat-driver','100.00','available_to_us'),
        line('couriers_cod','store_driver_fee_payable','uat-driver','15.00','owed_by_us')]);
    });
    await check('stage 12 HTTP confirmed advertising wallet and payable preserve exact separate identities',async()=>{
      assert((await call(base+'/identities/ad_account')).items.some(x=>x.id===expanded.ad_binding_id));
      await persist('providers',[line('providers','financial_account','uat-wallet','40.00','available_to_us'),line('providers','financial_account','uat-payable','15.00','owed_by_us')]);
    });
    await check('stage 13 HTTP prepaid uses paid invoice calendar days, explicit selection and replay',async()=>{
      const candidates=await call(base+'/prepaid-candidates?cutover=2026-10-01');
      assert.equal(candidates.items[0].calculation.remaining_prepaid_after_cutover,'3250.00');
      const payload={obligation_id:'uat-subscription',invoice_id:'uat-invoice',cutover_date:'2026-10-01',currency:'SAR',evidence:row.sections.equity.evidence_file_id};
      const selection=await call(base+'/prepaid-selections','POST',payload);
      assert.equal((await call(base+'/prepaid-selections','POST',payload)).id,selection.id);
      await persist('equity',[line('equity','prepaid_expense',selection.entity_id,'3250.00','available_to_us')],{prepaid_selection_ids:[selection.id]});
    });
    await check('stage 14 HTTP typed obligations and taxes remain separate; unknown deposit rejected',async()=>{
      const facts=[];
      for(const category of ['accrued_expense','other_payable','other_receivable','sales_vat_payable','input_vat']){
        facts.push(await call(base+'/typed-facts','POST',{category,display_name:'Synthetic '+category,reference:'uat-'+category,amount:'10.00',currency:'SAR',cutover_date:'2026-10-01',evidence:row.sections.equity.evidence_file_id}));
      }
      await call(base+'/typed-facts','POST',{category:'deposit',display_name:'Unsupported',reference:'uat-deposit',amount:'10.00',currency:'SAR',cutover_date:'2026-10-01',evidence:row.sections.equity.evidence_file_id},422);
      const listed=await call(base+'/typed-facts');assert.equal(listed.items.length,5);
      await persist('equity',[...row.sections.equity.data.lines,...facts.map(f=>line('equity',f.category,f.entity_id,f.amount,f.side==='debit'?'available_to_us':'owed_by_us'))],{typed_fact_ids:facts.map(f=>f.id)});
    });
    await check('stage 15 combined domain preview and review preserve independent financial meanings',async()=>{
      row=await action('preview');assert.equal(row.preview.balanced,true);
      for(const [entity,sub,side,value] of [['uat-supplier','payable','credit','25.00'],['uat-supplier','advance','debit','10.00'],['uat-employee','salary_payable','credit','30.00'],['uat-courier','cod_receivable','debit','150.00'],['uat-driver','delivery_fee_payable','credit','15.00']]){
        assert(row.preview.entries.some(x=>x.entity_id===entity&&x.sub_account===sub&&x.side===side&&x.sar_amount===value),entity+'/'+sub);
      }
      row=await action('review');assert.equal(row.status,'reviewed');assert.equal((await getSession()).status,'reviewed');
    });
    await check('stage 16 live approval remains locked and all non-setup domains unchanged',async()=>{
      const ready=await call(base+'/sessions/'+row.id+'/readiness');
      assert.equal(ready.stage_16_locked,true);assert.equal(ready.ready_for_live_post,false);assert.equal(ready.inventory_physical_approval_verified,false);
      assert.equal(ready.live_gates.smoke_b,'BLOCKED_BY_ENVIRONMENT');assert.equal(ready.live_gates.owner_authorization,'REQUIRED');
      assert.equal(ready.p02_activation_allowed,false);assert.equal(ready.g47_activation_allowed,false);
      assert(!writes.some(w=>/\/(post|transition|activate|approve|opening-draft)(\/|$)/.test(w.url)));
      assert.equal((await api('/expanded/__test/proof')).data.non_setup_collections_unchanged,true);
    });
  } finally {
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({results,errors,blocked,writes,expected_owner_only_read_denials:await Promise.all(permissionResponses),passed:results.length, business_uat:'BLOCKED', acceptance_limits:{stage_7_full_courier_draft:'C_NEW_SCOPE_REQUIRED', stage_10_physical_approval:'NOT_PERFORMED', stage_16_live_approval:'BLOCKED_BY_ENVIRONMENT', production_smoke_b:'BLOCKED_BY_ENVIRONMENT'}},null,2));
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
