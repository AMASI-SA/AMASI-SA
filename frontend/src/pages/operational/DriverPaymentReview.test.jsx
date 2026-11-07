import React, {act, StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import Review from '../StoreDeliveryPaymentReview';
import api from '../../lib/api';
import {toast} from 'sonner';

jest.mock('../../lib/api', () => ({__esModule: true, default: {get: jest.fn(), post: jest.fn()}}));
jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn()}}));
let host, root, item, context, getOptions;
const pending = '/store-delivery/payment-review/pending';
const scope = 'a'.repeat(64);
const button = text => [...host.querySelectorAll('button')].find(b => b.textContent === text);
const click = async text => {await act(async () => button(text).click());};
const field = text => [...host.querySelectorAll('label')].find(l => l.textContent.startsWith(text)).querySelector('input,select');
const change = async (element, value) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(element.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
  });
};
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};};
const render = async (strict=false) => {await act(async () => root.render(strict ? <StrictMode><Review/></StrictMode> : <Review/>));};
const preparePos = async () => {await click('تحميل خيارات الاعتماد'); await change(field('ذمة الشبكة المختارة'), 'pos'); await change(field('مبلغ إيصال الشبكة المطابق'), '200.00');};
beforeEach(() => {
  // Each scenario owns its mock calls, implementations and queued responses.
  jest.resetAllMocks();
  global.IS_REACT_ACT_ENVIRONMENT=true;
  item={assignment_id:'assignment',order_number:'123',driver_name:'Synthetic driver',payment_method:'card_terminal',amount:'200.00',receipt_reference:'receipt',receipt_url:'/synthetic-receipt'};
  context={assignment_id:'assignment',payment_method:'card_terminal',amount:'200.00',financial_source:'operational',expected_review_revision:1,session_scope:scope,can_approve:true,can_reject:true,pos_receivables:[{id:'pos',display_name:'Synthetic POS'}]};
  getOptions=()=>Promise.resolve({data:context});
  api.get.mockImplementation(url => {
    if(url===pending) return Promise.resolve({data:{items:[item]}});
    if(url.endsWith('/bank-accounts')) return Promise.resolve({data:{source:'mz2_financial_accounts',items:[{id:'bank',name:'Synthetic bank'},{id:'other',name:'Other bank'}]}});
    if(url.endsWith('/approval-options')) return getOptions();
    return Promise.resolve({data:{items:[]}});
  });
  api.post.mockResolvedValue({data:{state:'operational_recorded'}});
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
});
afterEach(()=>{act(()=>root.unmount());host.remove();jest.restoreAllMocks();});

test('opening and option load read only; approval needs explicit matching POS amount',async()=>{
  await render();expect(button('اعتماد مدفوع').disabled).toBe(true);expect(api.post).not.toHaveBeenCalled();
  await click('تحميل خيارات الاعتماد');await change(field('ذمة الشبكة المختارة'),'pos');
  await change(field('مبلغ إيصال الشبكة المطابق'),'199.99');expect(button('اعتماد مدفوع').disabled).toBe(true);
  await change(field('مبلغ إيصال الشبكة المطابق'),'200.00');await click('اعتماد مدفوع');
  expect(api.post).toHaveBeenCalledWith('/store-delivery/payment-review/assignment',{
    decision:'approved',note:'تمت المطابقة',expected_financial_source:'operational',expected_review_revision:1,
    expected_session_scope:scope,destination_financial_id:'pos',pos_reviewed_amount:'200.00'});
});

test('bank approval pins submitted bank and exact statement movement',async()=>{
  item.payment_method='bank_transfer';context={...context,payment_method:'bank_transfer',bank_account_id:'bank'};
  await render();await click('تحميل خيارات الاعتماد');
  await change(field('البنك المستلم المؤكد'),'other');await change(field('معرف حركة البنك'),'statement-row');
  expect(button('اعتماد مدفوع').disabled).toBe(true);
  await change(field('البنك المستلم المؤكد'),'bank');await click('اعتماد مدفوع');
  expect(api.post.mock.calls[0][1]).toMatchObject({destination_financial_id:'bank',settlement_reference:'statement-row',expected_session_scope:scope});
  expect(api.post.mock.calls[0][1]).not.toHaveProperty('pos_reviewed_amount');
});

test.each([{amount:'199.99'},{amount:null},{assignment_id:'foreign'},{payment_method:'cash'},{session_scope:''},{expected_review_revision:'1'}])('bad options cannot authorize: %j',async patch=>{
  context={...context,...patch};await render();await click('تحميل خيارات الاعتماد');
  expect(button('اعتماد مدفوع').disabled).toBe(true);await click('اعتماد مدفوع');expect(api.post).not.toHaveBeenCalled();
});

test('ambiguous response retries exactly the original request, not edited fields',async()=>{
  api.post.mockRejectedValueOnce(new Error('network'));await render();await preparePos();await click('اعتماد مدفوع');
  const first=api.post.mock.calls[0][1];expect(button('اعتماد مدفوع').disabled).toBe(true);
  await change(field('مبلغ إيصال الشبكة المطابق'),'999.00');await click('إعادة إرسال نفس الطلب');
  expect(api.post.mock.calls[1][1]).toEqual(first);expect(api.post).toHaveBeenCalledTimes(2);
});

test('double click cannot create two approvals',async()=>{
  const wait=deferred();api.post.mockReturnValueOnce(wait.promise);await render();await preparePos();
  await act(async()=>{button('اعتماد مدفوع').click();button('اعتماد مدفوع').click();});
  expect(api.post).toHaveBeenCalledTimes(1);await act(async()=>wait.resolve({data:{state:'operational_recorded'}}));
});

test('stale source rejection removes authorization and requires re-read',async()=>{
  api.post.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'stale'}}}});
  await render();await preparePos();await click('اعتماد مدفوع');expect(button('اعتماد مدفوع').disabled).toBe(true);
  expect(button('إعادة إرسال نفس الطلب')).toBeUndefined();expect(toast.error).toHaveBeenCalledWith('stale');
});

test('late options after list reload cannot authorize the new list',async()=>{
  const wait=deferred();getOptions=()=>wait.promise;await render();await click('تحميل خيارات الاعتماد');
  await click('تحديث القائمة للتحقق');await act(async()=>wait.resolve({data:context}));
  expect(button('اعتماد مدفوع').disabled).toBe(true);expect(host.textContent).not.toContain('مسار الاعتماد:');
});

test('options failure and pending-list failure clear usable state',async()=>{
  getOptions=()=>Promise.reject(new Error('network'));await render();await click('تحميل خيارات الاعتماد');
  expect(host.textContent).toContain('تعذر تحميل خيارات الاعتماد');expect(button('اعتماد مدفوع').disabled).toBe(true);
  api.get.mockRejectedValueOnce(new Error('network'));await click('تحديث القائمة للتحقق');
  expect(button('اعتماد مدفوع')).toBeUndefined();expect(api.post).not.toHaveBeenCalled();
});

test('strict-mode effect replay still loads the current list and explicit options',async()=>{
  await render(true);expect(button('تحميل خيارات الاعتماد')).toBeDefined();await preparePos();
  expect(button('اعتماد مدفوع').disabled).toBe(false);expect(api.post).not.toHaveBeenCalled();
});

test('rejection requires a reason and preserves the pinned review context without a destination',async()=>{
  context={...context,can_approve:false,can_reject:true,blocker:'receipt_not_eligible'};
  const prompt=jest.spyOn(window,'prompt').mockReturnValueOnce('   ').mockReturnValueOnce('  المبلغ غير موجود بالشبكة  ');
  await render();await click('تحميل خيارات الاعتماد');await click('رفض');
  expect(api.post).not.toHaveBeenCalled();expect(toast.error).toHaveBeenCalledWith('اكتب سبب الرفض حتى يظهر للموصل.');
  await click('رفض');
  expect(api.post).toHaveBeenCalledWith('/store-delivery/payment-review/assignment',{
    decision:'rejected',note:'المبلغ غير موجود بالشبكة',expected_financial_source:'operational',
    expected_review_revision:1,expected_session_scope:scope});
  expect(prompt).toHaveBeenCalledTimes(2);
});
