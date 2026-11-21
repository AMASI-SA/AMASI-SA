import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import http from '../../lib/api';
import ApprovedOrderIssues from './ApprovedOrderIssues';

jest.mock('react-router-dom', () => {
  globalThis.TextEncoder = require('util').TextEncoder;
  globalThis.TextDecoder = require('util').TextDecoder;
  return jest.requireActual('react-router');
});
jest.mock('../../lib/api', () => ({ __esModule: true, default: { get: jest.fn() } }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const driver = { kind:'store_driver', id:'driver', name:'موصّل المتجر', access:{movements:'all'} };
const context = { actor:{id:'staff'}, session_scope:'scope', permission_version:1, resources:[driver], operations:['courier.review'] };
const rows = Array.from({length:10},(_,i)=>({ order_id:String(i),order_number:String(9000+i),groups:[{
  ...driver,known:null,reasons:[{code:'driver_fee_evidence_conflict',message:'تحتاج أجرة التوصيل إلى مطابقة.'}],
}]}));
const result = (items,total,page=1)=>({ items,total,page,total_pages:Math.max(1,Math.ceil(total/8)),page_size:8,
    available:10,unidentified:[],as_of:'2026-11-19T10:00:00Z',started_at:'2026-11-01T00:00:00Z' });
let host,root,collections;
beforeEach(()=>{
  jest.clearAllMocks(); host=document.createElement('div'); document.body.appendChild(host); root=createRoot(host);
  Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
  Object.defineProperty(navigator,'onLine',{configurable:true,value:true});
  http.get.mockImplementation(async (_, {params})=>({data: params.q ? result(rows.slice(0,1),1) :
    result(rows.slice((params.page-1)*8,params.page*8),10,params.page)}));
  collections=jest.fn();
});
afterEach(async()=>{ await act(async()=>root.unmount());host.remove();jest.useRealTimers(); });
const render = async (next=context,resource=driver) => act(async()=>root.render(<MemoryRouter>
  <ApprovedOrderIssues context={next} resource={resource} identity="staff" onBack={()=>{}} onReviewCollections={collections}/>
</MemoryRouter>));
const click=async text=>act(async()=>{[...host.querySelectorAll('button')].find(b=>b.textContent.includes(text)).click();});
const type=async text=>act(async()=>{const node=host.querySelector('input[type="search"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,text);
  node.dispatchEvent(new Event('input',{bubbles:true}));
});
test('scoped fetch requests eight rows; opening full reason requests no additional data',async()=>{
  await render();
  expect(http.get).toHaveBeenCalledTimes(1);
  expect(http.get.mock.calls[0][1]).toMatchObject({signal:expect.any(AbortSignal),params:{kind:'store_driver',id:'driver',page:1}});
  expect(host.querySelectorAll('tbody tr')).toHaveLength(8);
  await click('عرض السبب');
  expect(host.textContent).toContain('الطلب #9000');
  expect(host.textContent).toContain('أثره على الرصيد');
  expect(http.get).toHaveBeenCalledTimes(1);
  await click('طلبات تحتاج مراجعة');await click('التالي');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
  await click('مراجعة إيصالات التحصيل');
  expect(collections).toHaveBeenCalledWith(driver,'pending');
});
test('search sends the last input only and own-only access makes no request',async()=>{
  jest.useFakeTimers();
  await render();await type('٩');await type('٩٠٠٠');
  expect(http.get).toHaveBeenCalledTimes(1);
  await act(async()=>{jest.advanceTimersByTime(450);});
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(http.get.mock.calls[1][1].params).toMatchObject({q:'٩٠٠٠',page:1});
  expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  const privateResource={...driver,access:{movements:'own'}};
  await render({...context,permission_version:2,resources:[privateResource]},privateResource);
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('غير متاحة ضمن صلاحياتك');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(0);
});

test('bank reviews show the current status and unposted impact on the authorized bank', async () => {
  const bank = {kind:'bank', id:'account:bank-1', name:'الراجحي', access:{movements:'all'}};
  const reason = {code:'operational_order_bank_status_ineligible',
    message:'الإيصال غير معتمد بسبب حالة الطلب · بانتظار المراجعة'};
  http.get.mockResolvedValue({data:result([{order_id:'salla-1', order_number:'9001',
    groups:[{...bank, known:null, reasons:[reason]}]}], 1)});
  await render({...context, resources:[bank]}, bank);
  expect(http.get.mock.calls[0][1].params).toEqual({kind:'bank',id:bank.id,page:1});
  expect(host.textContent).toContain('#9001');
  expect(host.textContent).toContain(reason.message);
  expect(host.textContent).not.toContain('مراجعة إيصالات التحصيل');
  await click('عرض السبب');
  expect(host.textContent).toContain('لم يُضف مبلغ هذا الإيصال إلى رصيد البنك.');
  expect(http.get).toHaveBeenCalledTimes(1);
});
