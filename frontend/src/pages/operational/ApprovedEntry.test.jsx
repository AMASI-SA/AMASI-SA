import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ApprovedEntry from './ApprovedEntry';
import ApprovedRefund from './ApprovedRefund';
import SupplierCreditPurchase from './SupplierCreditPurchase';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => {
  globalThis.TextEncoder = require('util').TextEncoder; globalThis.TextDecoder = require('util').TextDecoder;
  return jest.requireActual('react-router');
});

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: {
  options: jest.fn(), selection: jest.fn(), accountGroup: jest.fn(), customerAdjustments: jest.fn(), refundOrder: jest.fn(), refunds: jest.fn(), refund: jest.fn(), requestRefund: jest.fn(), entry: jest.fn(), pendingCommand: jest.fn(), discardPending: jest.fn(), registerPending: jest.fn(), retirePending: jest.fn(), quoteAd: jest.fn(),
}, simpleBalanceError: err => err?.localMessage || 'تعذر الحفظ' }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const resource = (kind,id, use = true) => ({kind,id,name:id,currency:'SAR',access:{use}});
const context = () => ({actor:{id:'staff',name:'ليلى'},status:'active',session_scope:'scope',permission_version:1,
  operations:['supplier.payment','accounts.transfer','customer.refund_request'],resources:[resource('supplier','التفصيل'),resource('bank','الإنماء'),resource('bank','مخفي',false)]});
const order = () => ({id:'o1',order_number:'245',currency:'SAR',can_request:true,paid_amount:'350.00', refunded_amount:'50.00', reserved_amount:'20.00',available_amount:'280.00',
  methods:[{id:'original',label:'تابي'}],banks:[],history:{items:[],page:1,page_size:8,total:0,total_pages:1}});
let host,root,saved;
beforeEach(() => {
  jest.clearAllMocks(); localStorage.clear();
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
  Object.defineProperty(navigator,'onLine',{configurable:true,value:true});
  Object.defineProperty(globalThis,'TextEncoder',{configurable:true,value:require('util').TextEncoder});
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{randomUUID:()=>'00000000-0000-4010-8000-000000000123',subtle:{digest:async(algorithm,input)=>require('crypto').createHash('sha256').update(input).digest()}}});
  api.registerPending.mockImplementation(async(id,record)=>({id,...record})); api.retirePending.mockImplementation(async id=>({deleted:true,id}));
  api.entry.mockImplementation(async command=>({saved:true,movement_id:'M1',party_type:command.party_type,party_id:command.party_id}));
  api.refundOrder.mockImplementation(async()=>order());
  api.customerAdjustments.mockResolvedValue({items:[],page:1,page_size:8,total:0,total_pages:1,summary:{refund_pending_amount:'100.00',refund_pending_count:1,collection_pending_amount:'20.00',collection_pending_count:1,unknown_count:0}});
  host=document.createElement('div');document.body.append(host);root=createRoot(host);saved=jest.fn();
});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const render=async component=>act(async()=>root.render(<MemoryRouter>{component}</MemoryRouter>));
test('a shared refund-read grant can inspect the same pending inbox without any write controls', async () => {
  const scoped = {...context(), operations:['customer.refund_read']};
  api.refundOrder.mockResolvedValue({...order(),can_request:false,can_record_execution:false});
  await render(<ApprovedRefund context={scoped} identity="refund-read" readOnly onBack={()=>{}} onSaved={saved}/>);
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  expect(host.textContent).not.toContain('عرض طلبات الإرجاع غير متاح');
  await change('رقم الطلب في سلة','245');
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(api.refundOrder).toHaveBeenCalledWith('245',expect.objectContaining({signal:expect.any(AbortSignal)}));
  expect(host.textContent).toContain('سجل إرجاعات الطلب');
  expect(api.requestRefund).not.toHaveBeenCalled();
  expect(api.registerPending).not.toHaveBeenCalled();
  expect(host.querySelector('input[name="amount"]')).toBeNull();
});
const button=txt=>[...host.querySelectorAll('button')].find(b=>b.textContent.includes(txt));
async function click(text){await act(async()=>button(text).click());}
async function change(label,value){
  const el=[...host.querySelectorAll('label')].find(l=>l.querySelector('span')?.textContent===label).querySelector('input,select');
  const select=el.tagName==='SELECT';
  await act(async()=>{Object.getOwnPropertyDescriptor(select?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(el,value); el.dispatchEvent(new Event(select?'change':'input',{bubbles:true}));});
}
test('a supplier payment reviews endpoint identities before registering a single financial intent',async()=>{
  await render(<ApprovedEntry context={context()} mode="out" onBack={()=>{}} onSaved={saved}/>);
  expect(host.textContent).not.toContain('مخفي');
  await change('نوع السداد','supplier');
  await change('وصل المبلغ إلى',JSON.stringify(['supplier','التفصيل']));
  await change('نوع مصدر الدفع','bank');
  await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('المبلغ (ريال)','٨٥٫٥٠');
  await click('مراجعة الحركة');
  expect(host.textContent).toContain('راجع الحركة');
  expect(api.registerPending).not.toHaveBeenCalled(); expect(api.entry).not.toHaveBeenCalled();
  await click('تأكيد وتسجيل الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(saved).toHaveBeenCalledTimes(1);
  expect(api.entry).toHaveBeenCalledTimes(1); expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({source_id:'الإنماء',party_id:'التفصيل',reason:'payment',amount:'85.50',side:'for_us'}));
});
const payoutField = label => [...host.querySelectorAll('label')].find(item => item.querySelector('span')?.textContent === label)?.querySelector('select');
const chooseDemand = async (label,value) => {
  const input=host.querySelector(`select[aria-label="${label}"]`);
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(input,value);input.dispatchEvent(new Event('change',{bubbles:true}));});
};
const demandContext=()=>({...context(),demandOnly:true,resources:[],capabilities:{create_entries:true}});
const optionPage=items=>({items,total:items.length,page:1,page_size:24,pages:1,session_scope:'scope',permission_version:1});

test('demand entry loads only the selected payee kind and then its selected source group before saving unchanged intent',async()=>{
  const ctx=demandContext(),supplier={...resource('supplier','المورد'),operations:['supplier.payment']};
  const bank={...resource('bank','بنك الدفع'),can_fund_payment:true,balance:{visible:true,amount:'100.00',side:'for_us'},access:{use:true,balance:true}};
  api.options.mockImplementation(async params=>optionPage(params.role==='recipient'?[supplier]:[bank]));
  await render(<ApprovedEntry context={ctx} mode="choose" onBack={()=>{}} onSaved={saved}/>);
  expect(api.options).not.toHaveBeenCalled();
  await click('دفعت مبلغ');
  expect(api.options).not.toHaveBeenCalled();
  await change('نوع السداد','supplier');
  expect(api.options).toHaveBeenCalledTimes(1);
  expect(api.options.mock.calls[0][0]).toEqual({operation:'out',role:'recipient',kind:'supplier',page:1,page_size:24});
  await chooseDemand('وصل المبلغ إلى',JSON.stringify(['supplier','المورد']));
  expect(api.options).toHaveBeenCalledTimes(1);
  await change('نوع مصدر الدفع','bank');
  expect(api.options.mock.calls[1][0]).toEqual({operation:'out',role:'source',kind:'bank',page:1,page_size:24,selected_kind:'supplier',selected_id:'المورد'});
  expect(api.accountGroup).not.toHaveBeenCalled();
  await chooseDemand('خرج المبلغ من',JSON.stringify(['bank','بنك الدفع']));
  await change('المبلغ (ريال)','25');
  await click('مراجعة الحركة');
  expect(host.textContent).toContain('بنك الدفع');
  expect(api.entry).not.toHaveBeenCalled();
  await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(api.entry.mock.calls[0][0]).toMatchObject({party_type:'supplier',party_id:'المورد',source_type:'bank',source_id:'بنك الدفع',amount:'25.00',reason:'payment',side:'for_us'});
  expect(saved).toHaveBeenCalledTimes(1);
});

test('changing payee type aborts the old source options and excludes their late response',async()=>{
  const ctx=demandContext();ctx.operations.push('employee.payment');
  const supplier={...resource('supplier','المورد'),operations:['supplier.payment']};
  let finish;
  api.options.mockImplementation((params)=>params.role==='source'?new Promise(resolve=>{finish=resolve;}):Promise.resolve(optionPage(params.kind==='supplier'?[supplier]:[])));
  await render(<ApprovedEntry context={ctx} mode="out" onBack={()=>{}} onSaved={saved}/>);
  await change('نوع السداد','supplier');
  await chooseDemand('وصل المبلغ إلى',JSON.stringify(['supplier','المورد']));
  await change('نوع مصدر الدفع','bank');
  const signal=api.options.mock.calls.find(([params])=>params.role==='source')[1].signal;
  await change('نوع السداد','employee');
  expect(signal.aborted).toBe(true);
  await act(async()=>{finish(optionPage([resource('bank','مصدر قديم')]));});
  expect(host.textContent).not.toContain('مصدر قديم');
  expect(host.querySelector('select[aria-label="خرج المبلغ من"]')).toBeNull();
  expect(api.entry).not.toHaveBeenCalled();
});
test('opening a supplier ledger fixes the payee and separates funded custody from unavailable sources',async()=>{
  const ctx=context(), supplier=ctx.resources[0];
  const funds=(kind,id,amount)=>({...resource(kind,id),access:{use:true,balance:true},balance:{visible:true,amount,side:'for_us'}});
  ctx.operations.push('custody.spend');
  ctx.resources.push(resource('supplier','مورد آخر'),resource('employee','موظف رواتب'),funds('bank','بنك فارغ','0.00'),
    funds('cash','الصندوق','25.00'),funds('employee_custody','عهدة فارغة','0.00'),resource('employee_custody','عهدة غير مثبتة'),
    {...resource('employee_custody','عهدة مستلمة'),can_fund_payment:true});
  await render(<ApprovedEntry context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
  expect(host.textContent).toContain(supplier.name);
  expect(payoutField('وصل المبلغ إلى')).toBeUndefined();
  expect(host.textContent).not.toContain('مورد آخر');
  expect(payoutField('خرج المبلغ من')).toBeUndefined();
  await change('نوع مصدر الدفع','bank');
  expect(payoutField('خرج المبلغ من').querySelector('option:disabled').textContent).toContain('بنك فارغ');
  expect(host.textContent).not.toContain('عهدة مستلمة');
  await change('نوع مصدر الدفع','employee_custody');
  expect(Array.from(payoutField('خرج المبلغ من').options).slice(1).map(o=>o.textContent)).toEqual(['عهدة مستلمة']);
  await change('نوع مصدر الدفع','cash');
  await change('خرج المبلغ من',JSON.stringify(['cash','الصندوق']));
  await change('المبلغ (ريال)','26');
  await click('مراجعة الحركة');
  expect(host.querySelector('[role="alert"]').textContent).toContain('رصيد مصدر الدفع لا يكفي');
  expect(api.entry).not.toHaveBeenCalled();
});
test('changing payee type clears the previous payee and funding identity',async()=>{
  const ctx=context();
  ctx.operations.push('courier.pay','employee.payment');
  ctx.resources.push(resource('courier','شركة الشحن'),resource('employee','موظف'),resource('external_person','جهة بلا صلاحية'));
  await render(<ApprovedEntry context={ctx} mode="out" onBack={()=>{}} onSaved={saved}/>);
  expect(Array.from(payoutField('نوع السداد').options).map(o=>o.value)).toEqual(['','supplier','courier','employee']);
  await change('نوع السداد','supplier');
  expect(host.textContent).not.toContain('شركة الشحن');
  await change('وصل المبلغ إلى',JSON.stringify(['supplier','التفصيل']));
  await change('نوع مصدر الدفع','bank'); await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('نوع السداد','courier');
  expect(payoutField('وصل المبلغ إلى').value).toBe('');
  expect(host.textContent).not.toContain('التفصيل');
  expect(payoutField('خرج المبلغ من')).toBeUndefined();
  await change('وصل المبلغ إلى',JSON.stringify(['courier','شركة الشحن']));
  expect(payoutField('نوع مصدر الدفع').value).toBe('');
  await change('نوع مصدر الدفع','bank'); await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('المبلغ (ريال)','10'); await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({party_type:'courier',party_id:'شركة الشحن'}));
});
test('refund inbox loads once while typing never searches orders; explicit lookup supplies the available amount and history',async()=>{
  await render(<ApprovedRefund context={context()} identity="staff" onBack={()=>{}} onSaved={saved}/>);
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('استرجاع ينتظر التأكيد');
  await change('رقم الطلب في سلة','٢٤٥');
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  expect(api.refundOrder).not.toHaveBeenCalled(); expect(api.refunds).not.toHaveBeenCalled();
  await click('عرض الطلب');
  expect(api.refundOrder).toHaveBeenCalledTimes(1);
  expect(api.refundOrder).toHaveBeenCalledWith('245',expect.objectContaining({params:{include_history:true},signal:expect.any(AbortSignal)}));
  expect(api.refunds).not.toHaveBeenCalled();expect(host.textContent).toContain('تابي');expect(host.textContent).toContain('280');
  await click('كامل المتاح'); await change('كيف بترجع المبلغ؟','original'); await change('سبب الإرجاع','فرق سعر');
  api.requestRefund.mockImplementation(async body=>({saved:true,item:{id:'r1',...body,status:'pending',currency:'SAR',source:{name:'تابي'}}}));
  await click('حفظ طلب الإرجاع');
  expect(api.requestRefund).toHaveBeenCalledWith(expect.objectContaining({order_number:'245',amount:'280.00',method:'original'}));
  expect(saved).not.toHaveBeenCalled();expect(host.textContent).toContain('حُفظ طلب الإرجاع');
});

test('confirmed refunds use a server status filter before pagination and stay separate from detected differences',async()=>{
  api.refunds.mockResolvedValue({items:[],page:1,page_size:8,total:0,total_pages:1});
  await render(<ApprovedRefund context={context()} identity="staff" onBack={()=>{}} onSaved={saved}/>);
  await click('ردود مؤكدة');
  expect(api.refunds).toHaveBeenCalledWith({page:1,page_size:8,status:'refunded'},expect.objectContaining({signal:expect.any(AbortSignal)}));
  expect(api.refundOrder).not.toHaveBeenCalled();expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  await click('طلبات رد معلّقة');expect(api.refunds).toHaveBeenLastCalledWith({page:1,page_size:8,status:'pending'},expect.any(Object));
});

test('an internal transfer uses a different receiving account and preserves the shipping wallet bank reference',async()=>{
  const ctx=context(); ctx.resources.push({...resource('cash','سلة'),prepaid_shipping_cash:true});
  await render(<ApprovedEntry context={ctx} resource={ctx.resources[3]} mode="wallet" onBack={()=>{}} onSaved={saved}/>);
  await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('المبلغ (ريال)','73');
  await click('مراجعة الحركة'); expect(host.textContent).toContain('أدخل مرجع');
  await change('مرجع العملية البنكية','W-209'); await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({party_id:'سلة',source_id:'الإنماء',reason:'transfer',reference:'W-209'}));
});

test('the USD advertising payment uses the selected account quote only and the existing bank charge contract',async()=>{
  const ctx=context(); ctx.operations.push('ads.fund'); ctx.resources.push({...resource('ad_account','سناب'),currency:'USD',ad_bank_funding:{mode:'prepaid',terms:{fx:true}}});
  api.quoteAd.mockResolvedValue({gross_sar:'375.00',fee_sar:'3.75',amount_usd:'99.00'});
  await render(<ApprovedEntry context={ctx} resource={ctx.resources[3]} mode="ads" onBack={()=>{}} onSaved={saved}/>);
  await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('طريقة إدخال الدفعة','SAR');
  await change('إجمالي ما خُصم من البنك (ريال)','375');
  await change('مرجع العملية البنكية','B1200');
  await click('احسب المقابل'); expect(api.quoteAd).toHaveBeenCalledTimes(1);
  await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({party_id:'سناب',source_id:'الإنماء',amount:'99.00',ad_bank_funding:{bank_amount:'375.00',bank_fee_amount:'3.75',kind:'wallet_funding'}}));
});

test('independent embroidery invoice supports mixed lines, Arabic decimals, preview and safe confirmation', async () => {
  const ctx = context(), vendor = {...ctx.resources[0],access:{balance:true,use:true},balance:{side:'for_party',amount:'4761.00'}};
  api.entry.mockImplementation(async c=>({saved:true,party_type:c.party_type,party_id:c.party_id,amount:c.amount,
    movement_id:'obligation',invoice_id:'credit-1',invoice_number:'MZ-PI-000001'}));
  await render(<SupplierCreditPurchase context={ctx} resource={vendor} onSaved={saved}/>);
  const set = async (el,value) => act(async()=>{
    const isSelect = el.tagName==='SELECT';
    Object.getOwnPropertyDescriptor(isSelect?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(el,value);
    el.dispatchEvent(new Event(isSelect?'change':'input',{bubbles:true}));
  });
  await set(host.querySelector('input[placeholder]'),'تطريز إضافي');
  const [qty,price] = host.querySelectorAll('fieldset input[inputmode=decimal]');
  await set(qty,'١٠'); await set(price,'٨');
  expect(host.textContent).toContain('4,841.00');
  await click('إضافة بند');
  const second = host.querySelectorAll('fieldset')[1];
  await set(second.querySelector('select'),'product');
  await set(second.querySelector('input[placeholder]'),'خيط');
  const [qty2,price2] = second.querySelectorAll('input[inputmode=decimal]');
  await set(qty2,'١٫٢٥'); await set(price2,'٢٫٥٠');
  await click('مراجعة الفاتورة');
  expect(api.registerPending).not.toHaveBeenCalled();
  expect(host.textContent).toContain('4,844.13');
  await click('حفظ الفاتورة على حساب المورد'); await click('حفظ الفاتورة على حساب المورد');
  expect(saved).toHaveBeenCalledTimes(1);
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(api.entry.mock.calls[0][0]).toMatchObject({party_id:vendor.id,party_type:'supplier',side:'for_party',
    reason:'credit_purchase',amount:'83.13',invoice:{tax:'0.00',lines:[
      {kind:'service',name:'تطريز إضافي',quantity:'10',unit_price:'8.00'},
      {kind:'product',name:'خيط',quantity:'1.25',unit_price:'2.50'}]}});
  expect(api.entry.mock.calls[0][0]).not.toHaveProperty('source_id');
});

test('incoming receipts can still be deposited to an empty bank',async()=>{
  const ctx=context();
  ctx.operations.push('supplier.collection');
  ctx.resources[1].can_fund_payment=false;
  await render(<ApprovedEntry context={ctx} mode="in" resource={ctx.resources[0]} onBack={()=>{}} onSaved={saved}/>);
  const bank=payoutField('دخل المبلغ في').querySelector('option[value*="الإنماء"]');
  expect(bank.disabled).toBe(false); expect(payoutField('نوع مصدر الدفع')).toBeUndefined();
  await change('دخل المبلغ في',bank.value); await change('المبلغ (ريال)','30');
  await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({party_type:'supplier',side:'for_party',reason:'collection',source_id:'الإنماء'}));
});

test('a lost acknowledgement can be recovered after the chosen bank has been exhausted', async()=>{
  const ctx=context(), supplier=ctx.resources[0];
  api.entry.mockRejectedValueOnce(new Error('response lost'));
  await render(<ApprovedEntry key="before" context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
  await change('نوع مصدر الدفع','bank'); await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('المبلغ (ريال)','30'); await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  const [id,registered]=api.registerPending.mock.calls[0];
  const original=api.entry.mock.calls[0][0];
  ctx.resources[1].can_fund_payment=false;
  api.pendingCommand.mockResolvedValue({id,...registered});
  await render(<ApprovedEntry key="after" context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
  await click('مراجعة عملية سابقة لهذا الحساب');
  expect(api.entry).toHaveBeenCalledTimes(1);
  await click('إعادة محاولة العملية نفسها');
  expect(api.entry.mock.calls[1][0]).toEqual(original);
  expect(saved).toHaveBeenCalledTimes(1);
});

async function reopenSupplierPayment(bankName, resolution={status:'pending'}) {
  const ctx=context(), supplier=ctx.resources[0];
  ctx.resources.push(resource('bank','الأهلي'));
  let sequence=0;
  globalThis.crypto.randomUUID=()=>`00000000-0000-4010-8000-${String(++sequence).padStart(12,'0')}`;
  const review=async(key,bank)=>{
    await render(<ApprovedEntry key={key} context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
    await change('نوع مصدر الدفع','bank');
    await change('خرج المبلغ من',JSON.stringify(['bank',bank]));
    await change('المبلغ (ريال)','1'); await click('مراجعة الحركة');
  };
  api.entry.mockRejectedValueOnce(new Error('unknown outcome'));
  await review('original','الإنماء'); await click('تأكيد وتسجيل الحركة');
  const [id,record]=api.registerPending.mock.calls[0];
  // The server validates and expands defaults before storing the command.
  api.pendingCommand.mockResolvedValue({id,...record,command:{owner_id:null,employee_id:null,expense_id:null,reference:'',...record.command},resolution});
  await review('new',bankName);
  return {id,record};
}
test('the expanded server defaults do not create a false mismatch or trigger another payment',async()=>{
  await reopenSupplierPayment('الإنماء');
  expect(host.textContent).not.toContain('يختلف عن الطلب السابق');
  expect(button('إعادة محاولة العملية نفسها').disabled).toBe(false);
  expect(api.entry).toHaveBeenCalledTimes(1);
});
test('switching from Inma to Ahli requires a confirmed atomic discard, then explicit submission using a new ID',async()=>{
  const {id}=await reopenSupplierPayment('الأهلي');
  expect(host.textContent).toContain('طلبك الحالي من الأهلي');
  expect(host.textContent).toContain('الطلب السابق من الإنماء');
  expect(button('إعادة محاولة العملية نفسها').disabled).toBe(true);
  localStorage.setItem('independent-marker','keep');
  api.discardPending.mockResolvedValue({id,status:'discarded'});
  await click('إلغاء الطلب السابق إذا لم يُنفّذ');
  expect(api.discardPending).toHaveBeenCalledWith(id,'scope');
  expect(host.textContent).toContain('أُلغي الطلب السابق قبل تنفيذه');
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem('independent-marker')).toBe('keep');
  await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledTimes(2);
  const replacement=api.entry.mock.calls[1][0];
  expect(replacement.source_id).toBe('الأهلي'); expect(replacement.request_id).not.toBe(id);
});
test('a receipt returned during discard or GET never executes a replacement payment',async()=>{
  const result={saved:true,movement_id:'receipt-M1',party_type:'supplier',party_id:'التفصيل'};
  const {id}=await reopenSupplierPayment('الأهلي');
  api.discardPending.mockResolvedValue({id,status:'saved',result});
  await click('إلغاء الطلب السابق إذا لم يُنفّذ');
  expect(saved).toHaveBeenCalledWith(result,expect.objectContaining({recovered:true}));
  expect(api.entry).toHaveBeenCalledTimes(1);
});
test('a saved recovery navigates directly to the confirmed receipt even when the replacement bank differs',async()=>{
  const result={saved:true,movement_id:'receipt-M1',party_type:'supplier',party_id:'التفصيل'};
  await reopenSupplierPayment('الأهلي',{status:'saved',result});
  expect(saved).toHaveBeenCalledWith(result,expect.objectContaining({recovered:true}));
  expect(api.discardPending).not.toHaveBeenCalled(); expect(api.entry).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0);
});
test('a proven rejection displays the actual reason while an unconfirmed discard keeps the exact pending identity',async()=>{
  const {id,record}=await reopenSupplierPayment('الأهلي');
  api.discardPending.mockRejectedValueOnce(new Error('discard response lost'));
  await click('إلغاء الطلب السابق إذا لم يُنفّذ');
  expect(localStorage.getItem(localStorage.key(0))).toBe(id);
  expect(api.entry).toHaveBeenCalledTimes(1);
  api.pendingCommand.mockResolvedValue({id,...record,resolution:{status:'rejected',detail:{not_applied:true,message:'لا يمكن سداد المورد بأكثر من مستحقه'}}});
  await click('استعادة العملية السابقة');
  expect(host.textContent).toContain('لا يمكن سداد المورد بأكثر من مستحقه');
  expect(api.entry).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(0);
});

test('registration returning a previous receipt immediately opens it without executing a payment',async()=>{
  const ctx=context(), supplier=ctx.resources[0];
  const result={saved:true,movement_id:'receipt-M1',party_type:'supplier',party_id:supplier.id};
  api.registerPending.mockImplementationOnce(async(id,record)=>({id,...record,resolution:{status:'saved',result}}));
  await render(<ApprovedEntry context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
  await change('نوع مصدر الدفع','bank'); await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('المبلغ (ريال)','1'); await click('مراجعة الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(api.entry).not.toHaveBeenCalled();
  expect(saved).toHaveBeenCalledWith(result,expect.objectContaining({recovered:true}));
});
test('a lost discard result can be recovered by GET without executing a financial write',async()=>{
  await reopenSupplierPayment('الأهلي',{status:'discarded'});
  expect(host.textContent).toContain('أُلغي الطلب السابق قبل تنفيذه');
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(localStorage.length).toBe(0);
});

test('directory-only payout resolves one source group, rejects changed ACL and offers only funded authorized custody on retry',async()=>{
  const ctx=context(), supplier=ctx.resources[0];
  ctx.directoryOnly=true; ctx.operations.push('custody.spend');
  const held=resource('employee_custody','عهدة مستلمة'), empty=resource('employee_custody','عهدة فارغة');
  ctx.resources.push(held,empty);
  const group={session_scope:ctx.session_scope,permission_version:ctx.permission_version,items:[
    {...held,can_fund_payment:true},{...empty,can_fund_payment:false},
    {...resource('employee_custody','عهدة خارج الصلاحية'),can_fund_payment:true}]};
  api.accountGroup.mockResolvedValueOnce({...group,permission_version:12}).mockResolvedValueOnce(group);
  await render(<ApprovedEntry context={ctx} mode="out" resource={supplier} onBack={()=>{}} onSaved={saved}/>);
  expect(api.accountGroup).not.toHaveBeenCalled();
  await change('نوع مصدر الدفع','employee_custody');
  expect(payoutField('خرج المبلغ من')).toBeUndefined();
  expect(host.querySelector('[role="alert"]').textContent).toContain('تعذر التحقق من صلاحيات');
  await click('إعادة المحاولة');
  expect(Array.from(payoutField('خرج المبلغ من').options).slice(1).map(o=>o.textContent)).toEqual(['عهدة مستلمة']);
  expect(api.accountGroup.mock.calls.map(([kind])=>kind)).toEqual(['employee_custody','employee_custody']);
});


test('carrier entry keeps receive and payment in one workflow with the exact cash amount and no implicit offset',async()=>{
  const ctx=context(),carrier=resource('courier','أرامكس');
  ctx.operations.push('courier.receive','courier.pay');ctx.resources.push(carrier);
  await render(<ApprovedEntry context={ctx} resource={carrier} mode="choose" onBack={()=>{}} onSaved={saved}/>);
  expect(button('حوّلت بين حسابين')).toBeUndefined();
  await click('استلام تحصيل من أرامكس');
  expect(host.textContent).toContain('لا تخصم أجور الشحن تلقائيًا');
  await change('دخل المبلغ في',JSON.stringify(['bank','الإنماء']));
  await change('صافي المبلغ الوارد إلى البنك (ريال)','7700');
  await act(async()=>host.querySelector('form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(host.textContent).toContain('طابق وصول التحويل واكتب مرجعه');
  expect(api.registerPending).not.toHaveBeenCalled();
  await change('مرجع العملية البنكية','TEST-COURIER-ARRIVAL');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());
  await click('مراجعة الحركة');
  expect(api.entry).not.toHaveBeenCalled();
  await click('تأكيد وتسجيل الحركة');
  expect(api.entry).toHaveBeenCalledTimes(1);
  expect(api.entry.mock.calls[0][0]).toMatchObject({party_type:'courier',party_id:'أرامكس',amount:'7700.00',reason:'collection',side:'for_party',source_type:'bank',source_id:'الإنماء',bank_arrival_confirmed:true,reference:'TEST-COURIER-ARRIVAL'});
  expect(api.entry.mock.calls[0][0]).not.toHaveProperty('offset_amount');
});

test('courier cash receipt does not claim bank arrival and changing bank clears its confirmation',async()=>{
  const ctx=context(),carrier=resource('courier','أرامكس');
  ctx.operations.push('courier.receive');ctx.resources.push(carrier,resource('bank','الأهلي'),resource('cash','الصندوق'));
  await render(<ApprovedEntry context={ctx} resource={carrier} mode="in" onBack={()=>{}} onSaved={saved}/>);
  await change('دخل المبلغ في',JSON.stringify(['bank','الإنماء']));
  await change('صافي المبلغ الوارد إلى البنك (ريال)','200');
  await change('مرجع العملية البنكية','TEST-OLD-BANK');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());
  await change('دخل المبلغ في',JSON.stringify(['bank','الأهلي']));
  expect(host.querySelector('input[type="checkbox"]').checked).toBe(false);
  await change('دخل المبلغ في',JSON.stringify(['cash','الصندوق']));
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  await click('مراجعة الحركة');await click('تأكيد وتسجيل الحركة');
  expect(api.entry.mock.calls[0][0]).toMatchObject({party_type:'courier',source_type:'cash',source_id:'الصندوق',amount:'200.00'});
  expect(api.entry.mock.calls[0][0]).not.toHaveProperty('bank_arrival_confirmed');
});
