import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CarrierAccountSummary from './CarrierAccountSummary';

const carrier={kind:'courier',id:'aramex',name:'أرامكس',currency:'SAR',access:{balance:true,movements:'all'}};
const snapshot={scope:'saved_snapshot_to_date',currency:'SAR',complete:true,as_of:'2026-11-21T10:00:00Z',
  receivable:'7700.00',payable:'2300.00',net_position:'5400.00',shipping_confirmed:'2000.00',return_confirmed:'300.00',
  fees_confirmed:'2300.00',fees_paid:'0.00',fees_outstanding:'2300.00',cod_confirmed:'10000.00',
  cod_received:'2300.00',cod_outstanding:'7700.00',customer_collection_confirmed:null,
  cash_received:'2300.00',cash_paid:'0.00',unallocated_received:'0.00',unallocated_paid:'0.00',
  opening_receivable:'0.00',opening_payable:'0.00',other_receivable:'0.00',other_payable:'0.00',
  expected_cod:'1500.00',expected_shipping:'34.50',expected_return:'17.25'};
let root,host;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const render=async(summary=snapshot,resource=carrier)=>act(async()=>root.render(<CarrierAccountSummary summary={summary} resource={resource}/>));
const row=label=>[...host.querySelectorAll('tr')].find(n=>n.querySelector('th')?.textContent===label);

test('shows saved cumulative carrier source values and keeps estimates, returns, cash and customer proof separate',async()=>{
  await render();
  expect(host.textContent).toContain('مستقل عن تصفية الفترة');
  expect(row('أجور بوليصات الراجع المثبتة').textContent).toContain('300.00');
  expect(row('أجور الشحن الصادر المثبتة').textContent).toContain('2,000.00');
  expect(row('تحصيل مثبت متبقٍ لدى الشركة').textContent).toContain('7,700.00');
  expect(row('وصل فعليًا إلى البنك أو الصندوق').textContent).toContain('2,300.00');
  expect(row('ما جمعته الشركة من العملاء بدليل تحصيل').textContent).toContain('غير متاح');
  expect(host.textContent).toContain('أجور راجع متوقعة غير مثبتة');
  expect(host.textContent).toContain('لا ينشئ تسوية');
  expect(host.querySelector('button,form')).toBeNull();
});

test('unallocated settlement keeps allocation unknown rather than displaying zero or inventing an offset',async()=>{
  await render({...snapshot,complete:false,cod_received:null,cod_outstanding:null,fees_paid:null,fees_outstanding:null});
  expect(row('تحصيل مستلم وموزع على الطلبات').textContent).toContain('غير متاح');
  expect(row('الأجور المتبقية بعد التوزيع').textContent).toContain('غير متاح');
  expect(row('وصل فعليًا إلى البنك أو الصندوق').textContent).toContain('2,300.00');
  expect(host.textContent).toContain('المبلغ غير المتاح ليس صفرًا');
  expect(host.textContent).toContain('المثبت لنا لدى الشركة');
  expect(host.textContent).toContain('المثبت علينا للشركة');
  expect(host.textContent).not.toContain('إجمالي ما لنا لدى الشركة');
});

test.each([{balance:false,movements:'all'},{balance:true,movements:'own'},{balance:true,movements:'none'}])('denies summary for insufficient resource access %j',async access=>{
  await render(snapshot,{...carrier,access});expect(host.textContent).toBe('');
});

test.each([{...snapshot,currency:'USD'},{...snapshot,scope:'period'}])('rejects a different summary currency or scope',async summary=>{
  await render(summary);expect(host.querySelector('[role="alert"]')).not.toBeNull();expect(host.querySelector('table')).toBeNull();
});

test('missing summary never infers aggregate figures from a ledger page',async()=>{
  await render(null);expect(host.textContent).toContain('لا يُستنتج من الحركات الظاهرة');expect(host.querySelector('table')).toBeNull();
});
