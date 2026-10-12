import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import CustomerReturns from './CustomerReturns';
import {operationalApi as api,requestId} from './api';
jest.mock('./api',()=>({operationalApi:{context:jest.fn(),customerReturns:jest.fn(),entities:jest.fn(),returnOrder:jest.fn(),saveCustomerReturn:jest.fn(),returnShippingQuote:jest.fn()},requestId:jest.fn(),messageFor:e=>e.response?.data?.detail?.message||'تعذر الحفظ'}));
let host,root;
const scope='a'.repeat(64);
const click=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text).click());
const field=name=>[...host.querySelectorAll('.op-field')].find(l=>l.querySelector('span')?.textContent===name).querySelector('input,select');
const input=async(name,value)=>act(async()=>{const e=field(name);Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));});
const load=async()=>{await act(async()=>root.render(<CustomerReturns/>));await input('رقم الطلب','1001');await click('عرض الطلب');await click('الطلب كاملًا — الكميات المتاحة');};
beforeEach(()=>{
 global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
 requestId.mockImplementation(()=>`request-${Math.random()}`);
 api.context.mockResolvedValue({session_scope:scope,status:'active',permissions:{move:true}});
 api.customerReturns.mockResolvedValue({items:[]});api.saveCustomerReturn.mockResolvedValue({id:'case1'});
 api.entities.mockImplementation(kind=>Promise.resolve({items:[{id:kind==='provider'?'tamara':kind+'1',name:kind,currency:'SAR'}]}));
 api.returnOrder.mockResolvedValue({id:'o1',order_number:'1001',total:'300.00',refunded:'0.00',provider:'tamara',items:[{id:'a',name:'منتج أ',quantity:2,remaining:2}]});
 api.returnShippingQuote.mockResolvedValue({amount:'28.75',quote_hash:'quote'});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('pending return selects all quantities and saves without a financial debit instruction',async()=>{
 await load();expect(host.textContent).toContain('لن يُخصم مبلغ');await click('حفظ المرتجع');
 expect(api.saveCustomerReturn).toHaveBeenCalledWith(expect.objectContaining({status:'pending',items:[{id:'a',quantity:2}],expected_session_scope:scope}),null);
});
test('actual platform refund uses selected source and stable durable retry after remount',async()=>{
 await load();await input('حالة رد المبلغ','refunded');await input('الرد من','provider');await input('البنك أو منصة الدفع','tamara');await input('المبلغ المردود فعليًا','100');await input('مرجع عملية رد المبلغ','platform-ref-1');await input('وقت رد المبلغ','2026-10-07T10:00');
 api.saveCustomerReturn.mockRejectedValueOnce(new Error('connection lost'));
 await click('حفظ المرتجع');const sent=api.saveCustomerReturn.mock.calls[0][0];
 expect(sent).toEqual(expect.objectContaining({status:'refunded',amount:'100',refund_source_type:'provider',refund_source_id:'tamara'}));
 await act(async()=>root.render(<div/>));await act(async()=>root.render(<CustomerReturns/>));
 expect(host.textContent).toContain('بانتظار تأكيد النتيجة');await click('إعادة محاولة الحفظ');
 expect(api.saveCustomerReturn.mock.calls[1][0]).toEqual(sent);expect(localStorage.length).toBe(0);
});
test('opening pending case confirms the same case instead of creating another',async()=>{
 api.customerReturns.mockResolvedValue({items:[{id:'case1',order_number:'1001',status:'pending',items:[{id:'a',quantity:1}],shipping:{kind:'none',amount:'0.00',status:'pending'}}]});
 await act(async()=>root.render(<CustomerReturns/>));await click('عرض / تأكيد');await input('حالة رد المبلغ','refunded');await input('البنك أو منصة الدفع','bank1');await input('المبلغ المردود فعليًا','50');await input('مرجع عملية رد المبلغ','bank-ref');await input('وقت رد المبلغ','2026-10-07T10:00');await click('حفظ التأكيد');
 expect(api.saveCustomerReturn).toHaveBeenCalledWith(expect.objectContaining({status:'refunded',items:[{id:'a',quantity:1}]}),'case1');
});
test('shipping quote is server-derived and missing refund fields block submission',async()=>{
 await load();await input('شحنة الاسترجاع','courier');await input('منفذ الاسترجاع','courier1');expect(host.textContent).toContain('28.75');
 await input('رقم البوليصة أو مرجع الاسترجاع','awb1');await input('حالة رد المبلغ','refunded');await click('حفظ المرتجع');expect(api.saveCustomerReturn).not.toHaveBeenCalled();expect(host.textContent).toContain('أكمل مبلغ الرد');
 await input('حالة رد المبلغ','pending');await click('حفظ المرتجع');expect(api.saveCustomerReturn).toHaveBeenCalledWith(expect.objectContaining({shipping_quote_hash:'quote',shipment_completed:false}),null);
});
test('read-only context disables editing and cannot post',async()=>{
 api.context.mockResolvedValue({session_scope:scope,status:'active',permissions:{move:false}});await act(async()=>root.render(<CustomerReturns/>));expect(field('رقم الطلب').disabled).toBe(true);expect(api.saveCustomerReturn).not.toHaveBeenCalled();
});
