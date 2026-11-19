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
  refundOrder: jest.fn(), refunds: jest.fn(), refund: jest.fn(), requestRefund: jest.fn(), entry: jest.fn(), registerPending: jest.fn(), retirePending: jest.fn(), quoteAd: jest.fn(),
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
  host=document.createElement('div');document.body.append(host);root=createRoot(host);saved=jest.fn();
});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const render=async component=>act(async()=>root.render(<MemoryRouter>{component}</MemoryRouter>));
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
  await change('خرج المبلغ من',JSON.stringify(['bank','الإنماء']));
  await change('وصل المبلغ إلى',JSON.stringify(['supplier','التفصيل']));
  await change('المبلغ (ريال)','٨٥٫٥٠');
  await click('مراجعة الحركة');
  expect(host.textContent).toContain('راجع الحركة');
  expect(api.registerPending).not.toHaveBeenCalled(); expect(api.entry).not.toHaveBeenCalled();
  await click('تأكيد وتسجيل الحركة'); await click('تأكيد وتسجيل الحركة');
  expect(saved).toHaveBeenCalledTimes(1);
  expect(api.entry).toHaveBeenCalledTimes(1); expect(api.entry).toHaveBeenCalledWith(expect.objectContaining({source_id:'الإنماء',party_id:'التفصيل',reason:'payment',amount:'85.50',side:'for_us'}));
});
test('refund open and typing perform zero reads until explicit order lookup; amounts and history come from that lookup',async()=>{
  await render(<ApprovedRefund context={context()} identity="staff" onBack={()=>{}} onSaved={saved}/>);
  await change('رقم الطلب في سلة','٢٤٥');
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
