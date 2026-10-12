import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import CustomerExchanges from './CustomerExchanges';
import {operationalApi as api,requestId} from './api';
jest.mock('./api',()=>({operationalApi:{context:jest.fn(),customerExchanges:jest.fn(),movements:jest.fn(),entities:jest.fn(),exchangeOrder:jest.fn(),saveExchange:jest.fn(),returnShippingQuote:jest.fn()},requestId:jest.fn(),messageFor:e=>e.response?.data?.detail?.message||'خطأ في API'}));
let host,root;const scope='a'.repeat(64);
const row={id:'case1',order_number:'1001',purchase_status:'pending',items:[{id:'a',name:'منتج أ',quantity:2,remaining_to_buy:2}],shipping:{id:'carrier',name:'شركة الشحن',amount:'28.75',status:'pending',reference:''},purchases:[],contributions:[],summary:{expected_products:'100.00',confirmed_products:'0.00',shipping:'28.75',customer_contribution:'0.00',net_cost:'128.75'}};
const field=name=>[...host.querySelectorAll('.op-field')].find(l=>l.querySelector('span')?.textContent===name).querySelector('input,select');
const input=async(name,value)=>act(async()=>{const e=field(name);Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));});
const click=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text).click());
const load=async()=>{await act(async()=>root.render(<CustomerExchanges/>));await input('رقم الطلب القديم','1001');await click('عرض الطلب والاستبدالات');await click('استبدال الطلب كاملًا — المتاح');await input('شركة شحن البدل','carrier');};
beforeEach(()=>{
 global.IS_REACT_ACT_ENVIRONMENT=true;localStorage.clear();jest.resetAllMocks();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
 requestId.mockImplementation(()=>`request-${Math.random()}`);api.context.mockResolvedValue({session_scope:scope,status:'active',permissions:{move:true}});
 api.customerExchanges.mockResolvedValue({items:[]});api.movements.mockResolvedValue({items:[]});api.saveExchange.mockResolvedValue(row);
 api.entities.mockImplementation(k=>Promise.resolve({items:[{id:k==='courier'?'carrier':k,name:k,currency:'SAR'}]}));
 api.exchangeOrder.mockResolvedValue({id:'order1',order_number:'1001',items:[{id:'a',name:'منتج أ',quantity:2,remaining:2,unit_estimate:'50.00'}]});
 api.returnShippingQuote.mockResolvedValue({amount:'28.75',quote_hash:'q'.repeat(64)});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('old order and all products save expected shipping without contribution',async()=>{
 await load();expect(host.textContent).toContain('لا يُنشأ طلب بيع جديد');await click('حفظ الاستبدال');
 expect(api.saveExchange).toHaveBeenCalledWith(expect.objectContaining({order_number:'1001',items:[{id:'a',quantity:2}],contribution:null,shipping_id:'carrier'}),null);
 expect(host.querySelector('main').getAttribute('dir')).toBe('rtl');
});
test('paid contribution selects bank and records explicit payment alongside creation',async()=>{
 await load();await input('هل دفع العميل بالفعل؟','yes');await input('مبلغ مساهمة العميل','30');await input('البنك المستلم','bank');await input('مرجع دفع العميل','customer-ref');await click('حفظ الاستبدال');
 expect(api.saveExchange.mock.calls[0][0].contribution).toEqual(expect.objectContaining({bank_id:'bank',amount:'30',reference:'customer-ref',existing_movement_id:null}));
});
test('partial actual invoice preserves net tax gross and case identity',async()=>{
 api.customerExchanges.mockResolvedValue({items:[row]});await act(async()=>root.render(<CustomerExchanges/>));await click('شراء بدل');
 await input('المورد','supplier');await input('رقم فاتورة المورد','bill1');await input('الكمية المشتراة — منتج أ','1');await input('صافي البند — منتج أ','80');await input('ضريبة البند — منتج أ','12');await input('إجمالي البند شامل الضريبة — منتج أ','92');await click('حفظ العملية');
 expect(api.saveExchange).toHaveBeenCalledWith(expect.objectContaining({action:'purchase',net:'80.00',tax:'12.00',gross:'92.00',lines:[{item_id:'a',quantity:1,net:'80',tax:'12',gross:'92'}]}),'case1');
});
test('unknown response persists exact request and retries after remount',async()=>{
 await load();api.saveExchange.mockRejectedValueOnce(new Error('connection lost'));await click('حفظ الاستبدال');const sent=api.saveExchange.mock.calls[0][0];
 await act(async()=>root.render(<div/>));await act(async()=>root.render(<CustomerExchanges/>));await click('إعادة محاولة الحفظ');
 expect(api.saveExchange.mock.calls[1][0]).toEqual(sent);expect(localStorage.length).toBe(0);
});
test('definitive rejection permits correction without losing selected order',async()=>{
 await load();api.saveExchange.mockRejectedValueOnce({response:{status:409,data:{detail:{not_applied:true,message:'تغيرت التكلفة'}}}});await click('حفظ الاستبدال');
 expect(host.textContent).toContain('تغيرت التكلفة');expect(localStorage.length).toBe(0);expect(field('شركة شحن البدل').disabled).toBe(false);await click('حفظ الاستبدال');expect(api.saveExchange.mock.calls[1][0].request_id).not.toBe(api.saveExchange.mock.calls[0][0].request_id);
});
test('read-only context and loading/error cannot create a replacement',async()=>{
 api.context.mockResolvedValue({session_scope:scope,status:'active',permissions:{move:false}});await act(async()=>root.render(<CustomerExchanges/>));expect(field('رقم الطلب القديم').disabled).toBe(true);expect(api.saveExchange).not.toHaveBeenCalled();
 await act(async()=>root.render(<div/>));api.context.mockRejectedValueOnce(new Error('offline'));await act(async()=>root.render(<CustomerExchanges/>));expect(host.textContent).toContain('خطأ في API');expect(api.saveExchange).not.toHaveBeenCalled();
});
test('existing incoming bank movement is linked with immutable amount and bank',async()=>{
 api.customerExchanges.mockResolvedValue({items:[row]});api.movements.mockResolvedValue({items:[{id:'m1',bank_kind:'bank',party_type:'bank',bank_id:'bank',bank_name:'bank',kind:'collection',direction:'incoming',amount:'30.00',reference:'paid-ref',allocations:[]}]});
 await act(async()=>root.render(<CustomerExchanges/>));await click('تسجيل مساهمة العميل');await input('تسجيل المساهمة','m1');expect(field('مبلغ مساهمة العميل').disabled).toBe(true);await click('حفظ العملية');
 expect(api.saveExchange.mock.calls[0][0].contribution).toEqual(expect.objectContaining({existing_movement_id:'m1',amount:'30.00',bank_id:'bank'}));
});
