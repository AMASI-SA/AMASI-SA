import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ApprovedCustomerCollections, { CustomerCollectionForm } from './ApprovedCustomerCollections';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => {
  globalThis.TextEncoder = require('util').TextEncoder; globalThis.TextDecoder = require('util').TextDecoder;
  return jest.requireActual('react-router');
});
jest.mock('./simpleBalancesApi', () => ({simpleBalancesApi:{customerAdjustments:jest.fn(),customerCollectionOrder:jest.fn(),customerCollections:jest.fn(),
  quoteCustomerCollection:jest.fn(),recordCustomerCollection:jest.fn(),registerPending:jest.fn(),pendingCommand:jest.fn(),retirePending:jest.fn(),uploadCustomerReceipt:jest.fn(),customerReceipt:jest.fn()},
simpleBalanceError:error => error?.localMessage || 'لم تتأكد نتيجة الحفظ'}));
global.IS_REACT_ACT_ENVIRONMENT = true;
const basis = 'a'.repeat(64), quoteHash = 'b'.repeat(64);
const context = () => ({actor:{id:'staff'},session_scope:'scope',permission_version:1,status:'active',operations:['customer.collection']});
const page = items => ({items,page:1,page_size:8,total:items.length,total_pages:1});
const order = () => ({order_id:'order-1',order_number:'245',provider:'salla',currency:'SAR',current_total:'500.00',paid_amount:'400.00',
  confirmed_collected_amount:'0.00',pending_collection_amount:'100.00',suggested_collection_amount:'100.00',basis_hash:basis,can_record:true,
  sources:[{kind:'bank',id:'bank1',name:'بنك المؤسسة'},{kind:'provider',id:'salla',name:'سلة'},{kind:'provider',id:'tabby',name:'تابي'}],history:page([])});
let host,root,saved,counter;
const render = async element => act(async () => root.render(<MemoryRouter>{element}</MemoryRouter>));
const field = text => [...host.querySelectorAll('label')].find(label => label.querySelector('span')?.textContent === text)?.querySelector('input,select');
async function change(label,value) {
  const input=field(label); if(!input)throw Error(`Missing field ${label}`);
  await act(async () => {Object.getOwnPropertyDescriptor(input.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(input,value);
    input.dispatchEvent(new Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}));});
}
const button = label => [...host.querySelectorAll('button')].find(item => item.textContent === label);
const click = async label => act(async () => {if(!button(label))throw Error(`Missing button ${label}`);button(label).click();});
const form = () => <CustomerCollectionForm context={context()} order={order()} onSaved={saved}/>;
async function fillBank() {
  await change('الحساب الذي استقبل المبلغ',JSON.stringify(['bank','bank1']));
  await change('مرجع التحصيل الفعلي','BANK-100');await change('سبب التحصيل','فرق الشحن');
}
beforeEach(() => {
  jest.resetAllMocks();localStorage.clear();counter=0;
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
  Object.defineProperty(navigator,'onLine',{configurable:true,value:true});
  Object.defineProperty(globalThis,'TextEncoder',{configurable:true,value:require('util').TextEncoder});
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:{randomUUID:()=>`00000000-0000-4000-8000-${String(++counter).padStart(12,'0')}`,
    subtle:{digest:async(algorithm,input)=>require('crypto').createHash('sha256').update(input).digest()}}});
  api.customerAdjustments.mockResolvedValue({...page([]),summary:{refund_pending_amount:'10.00',refund_pending_count:1,collection_pending_amount:'100.00',collection_pending_count:1,unknown_count:0}});
  api.customerCollectionOrder.mockResolvedValue(order());api.customerCollections.mockResolvedValue(page([]));
  api.quoteCustomerCollection.mockImplementation(async body => ({expected_basis_hash:basis,quote_hash:quoteHash,fee_amount:'0.00',net_amount:body.amount,fee_status:'none',remaining_amount:'0.00'}));
  api.registerPending.mockImplementation(async(id,value)=>({id,...value}));api.retirePending.mockImplementation(async id=>({id,deleted:true}));
  api.recordCustomerCollection.mockImplementation(async body=>({saved:true,item:{id:'collection-1',order_number:body.order_number,amount:body.amount,
    status:'confirmed',currency:'SAR',source:{kind:body.source_type,id:body.source_id,name:'الحساب'},fee_amount:'0.00',net_amount:body.amount,fee_status:'none'}}));
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);saved=jest.fn();
});
afterEach(() => {act(() => root.unmount());host.remove();});

test('the inbox reads once and typing does not query orders or fees; explicit order lookup proposes the remaining amount',async()=>{
  await render(<ApprovedCustomerCollections context={context()} identity="staff" onBack={()=>{}} onSaved={saved}/>);
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  await change('رقم الطلب في سلة','٢٤٥');
  expect(api.customerCollectionOrder).not.toHaveBeenCalled();expect(api.quoteCustomerCollection).not.toHaveBeenCalled();
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);await click('عرض الطلب');
  expect(api.customerCollectionOrder).toHaveBeenCalledWith('245',expect.objectContaining({params:{include_history:true},signal:expect.any(AbortSignal)}));
  expect(field('المبلغ المقبوض فعليًا').value).toBe('100.00');expect(api.customerCollections).not.toHaveBeenCalled();
});

test('bank collection requires an explicit quote and actual receipt confirmation, and changes invalidate the quote',async()=>{
  await render(form());await fillBank();
  expect(button('تأكيد وتسجيل التحصيل')).toBeUndefined();
  await click('مراجعة التحصيل والرسوم');
  expect(api.quoteCustomerCollection).toHaveBeenCalledWith(expect.objectContaining({amount:'100.00',source_type:'bank',source_id:'bank1',fee_mode:'new_transaction',payment_method:''}));
  await click('تأكيد وتسجيل التحصيل');expect(api.recordCustomerCollection).not.toHaveBeenCalled();
  await change('المبلغ المقبوض فعليًا','40');expect(button('تأكيد وتسجيل التحصيل')).toBeUndefined();
  await click('مراجعة التحصيل والرسوم');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());await click('تأكيد وتسجيل التحصيل');
  expect(api.recordCustomerCollection).toHaveBeenCalledTimes(1);
  expect(api.recordCustomerCollection).toHaveBeenCalledWith(expect.objectContaining({amount:'40.00',expected_basis_hash:basis,expected_quote_hash:quoteHash,confirmed:true,source_id:'bank1'}));
  expect(api.registerPending.mock.calls[0][1].action).toBe('customer.collection');expect(saved).toHaveBeenCalledTimes(1);
});

test('same-transaction collection uses a separate original reference and clears it when the destination changes',async()=>{
  await render(form());await fillBank();
  await change('الحساب الذي استقبل المبلغ',JSON.stringify(['provider','salla']));
  await change('نوع عملية التحصيل','same_transaction');await change('طريقة الدفع في سلة','mada');
  await click('مراجعة التحصيل والرسوم');expect(api.quoteCustomerCollection).not.toHaveBeenCalled();
  await change('مرجع عملية الدفع الأصلية','CAPTURE-1');await change('مرجع التحصيل الفعلي','ADDITION-1');
  await click('مراجعة التحصيل والرسوم');
  expect(api.quoteCustomerCollection).toHaveBeenCalledWith(expect.objectContaining({original_reference:'CAPTURE-1',reference:'ADDITION-1',fee_mode:'same_transaction',payment_method:'mada'}));
  await change('الحساب الذي استقبل المبلغ',JSON.stringify(['bank','bank1']));
  expect(field('مرجع عملية الدفع الأصلية')).toBeUndefined();expect(field('طريقة الدفع في سلة')).toBeUndefined();
  await click('مراجعة التحصيل والرسوم');const body=api.quoteCustomerCollection.mock.calls[1][0];
  expect(body.original_reference).toBeUndefined();expect(body.payment_method).toBe('');expect(body.fee_mode).toBe('new_transaction');
});

test('unknown platform fees preserve confirmed gross without inventing a fee or net amount',async()=>{
  await render(form());await fillBank();await change('الحساب الذي استقبل المبلغ',JSON.stringify(['provider','tabby']));
  await change('نوع عملية التحصيل','new_transaction');
  api.quoteCustomerCollection.mockResolvedValue({expected_basis_hash:basis,quote_hash:quoteHash,fee_amount:null,net_amount:null,fee_status:'unavailable',remaining_amount:'0.00'});
  await click('مراجعة التحصيل والرسوم');expect(host.textContent).toContain('يُثبت المبلغ المقبوض كاملًا');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());await click('تأكيد وتسجيل التحصيل');
  expect(api.recordCustomerCollection).toHaveBeenCalledWith(expect.objectContaining({amount:'100.00',source_type:'provider',source_id:'tabby',payment_method:'tabby'}));
  expect(api.recordCustomerCollection.mock.calls[0][0].fee_amount).toBeUndefined();
});

test('a small real capture retains a negative quoted net when its provider fee exceeds the gross',async()=>{
  await render(form());await fillBank();await change('الحساب الذي استقبل المبلغ',JSON.stringify(['provider','tabby']));
  await change('نوع عملية التحصيل','new_transaction');await change('المبلغ المقبوض فعليًا','0.80');
  api.quoteCustomerCollection.mockResolvedValue({expected_basis_hash:basis,quote_hash:quoteHash,fee_amount:'1.16',net_amount:'-0.36',fee_status:'estimated',remaining_amount:'99.20'});
  await click('مراجعة التحصيل والرسوم');
  expect(host.querySelector('.adjustment-quote').textContent).toContain('−0.36');
  await act(async()=>host.querySelector('input[type="checkbox"]').click());await click('تأكيد وتسجيل التحصيل');
  expect(api.recordCustomerCollection).toHaveBeenCalledWith(expect.objectContaining({amount:'0.80',source_id:'tabby',expected_quote_hash:quoteHash}));
});

test('an uncertain collection remount recovers the same command and never requests a replacement fee quote',async()=>{
  api.recordCustomerCollection.mockRejectedValueOnce(new Error('unknown result'));
  await render(form());await fillBank();await click('مراجعة التحصيل والرسوم');await act(async()=>host.querySelector('input[type="checkbox"]').click());await click('تأكيد وتسجيل التحصيل');
  const original=api.recordCustomerCollection.mock.calls[0][0], [id,registered]=api.registerPending.mock.calls[0];
  api.pendingCommand.mockResolvedValue({id,...registered});await render(<div/>);await render(form());
  expect(field('المبلغ المقبوض فعليًا').disabled).toBe(true);expect(api.recordCustomerCollection).toHaveBeenCalledTimes(1);
  await click('إعادة محاولة العملية نفسها');expect(api.recordCustomerCollection).toHaveBeenCalledTimes(2);
  expect(api.recordCustomerCollection.mock.calls[1][0]).toEqual(original);expect(api.quoteCustomerCollection).toHaveBeenCalledTimes(1);expect(localStorage.length).toBe(0);
});

test('read-only and absent collection grants never expose a financial write form',async()=>{
  await render(<CustomerCollectionForm context={context()} order={order()} readOnly onSaved={saved}/>);
  expect(host.querySelector('form')).toBeNull();
  await render(<ApprovedCustomerCollections context={{...context(),operations:[]}} identity="staff" onBack={()=>{}}/>);
  expect(api.customerAdjustments).not.toHaveBeenCalled();expect(api.customerCollectionOrder).not.toHaveBeenCalled();
  expect(api.recordCustomerCollection).not.toHaveBeenCalled();
});

test('a confirmed collection with ambiguous source matching remains visibly marked for review without writing another receipt',async()=>{
  api.customerCollections.mockResolvedValue(page([{id:'collection-review',order_number:'245',amount:'100.00',currency:'SAR',status:'confirmed',
    source:{kind:'provider',id:'salla',name:'سلة'},reference:'CAPTURE-100',executed_at:'2026-11-20T10:00:00Z',
    recorded_by:{name:'موظف الحسابات'},fee_status:'estimated',fee_amount:'1.00',net_amount:'99.00',match_status:'needs_review'}]));
  await render(<ApprovedCustomerCollections context={context()} identity="staff" onBack={()=>{}} onSaved={saved}/>);
  await click('تحصيلات مؤكدة');
  expect(host.querySelector('.collection-record .st-done').textContent).toBe('تحصيل مؤكد');
  expect(host.querySelector('.collection-record .issue-badge').textContent).toContain('مطابقته مع سجل المصدر تحتاج مراجعة');
  expect(api.recordCustomerCollection).not.toHaveBeenCalled();expect(api.quoteCustomerCollection).not.toHaveBeenCalled();
});
