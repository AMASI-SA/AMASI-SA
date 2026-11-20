import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ApprovedLedger from './ApprovedLedger';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('./simpleBalancesApi', () => ({simpleBalancesApi:{ ledger:jest.fn() }, simpleBalanceError:()=>'خطأ'}));

const provider = {kind:'provider', id:'paypal', name:'باي بال', currency:'SAR', access:{balance:true, report:true, movements:'all'}};
const ctx={resources:[provider],session_scope:'s',permission_version:1,ledger_start:'2026-11-01'};
const items=Array.from({length:10},(_,i)=>({id:String(i),order_number:String(100000+i),day:'2026-11-03',
  reference:'transaction-'+i, currency:'KWD', paid:'1.00', paid_sar:'12.00', settled:'0.00', remaining_fee:null, net:null,
  balance_currency:'SAR', reasons:['العمولة تحتاج مطابقة']}));
let host, root;
const button = name=>[...host.querySelectorAll('button')].find(b=>b.textContent===name);
async function click(name) {await act(async()=>button(name).click());}
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.append(host);
  root=createRoot(host); jest.clearAllMocks();
  api.ledger.mockImplementation(async (kind,id,p)=>{
    const rows=p.provider_orders?items.filter(r=>!p.q||r.reference===p.q):[];
    return {resource:provider, provider_orders:Boolean(p.provider_orders), page:p.page,page_size:8,
      total_pages:Math.max(1,Math.ceil(rows.length/8)),total:rows.length,items:rows.slice((p.page-1)*8,p.page*8),amounts:[]};
  });
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

test('opening a permitted provider reads eight order rows once and preserves a separate ledger view',async()=>{
  await act(async()=>root.render(<ApprovedLedger resource={provider} context={ctx} identity="owner" canOpenOrderDetails/>));
  expect(api.ledger.mock.calls.length).toBe(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({page:1,page_size:8,provider_orders:true});
  expect(host.querySelectorAll('tbody tr')).toHaveLength(8);
  expect(host.querySelector('tbody a').getAttribute('href')).toBe('/orders-v2/100000');
  expect(host.querySelector('tbody').textContent).toContain('KWD');
  expect(host.querySelector('tbody details').textContent).toContain('العمولة تحتاج مطابقة');
  await click('التالي');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(2);
  await click('كشف الحركات والتسويات');
  expect(api.ledger.mock.calls.at(-1)[2].provider_orders).toBeUndefined();
  expect(host.querySelector('tbody a')).toBeNull();
});

test('own-movements access never requests or renders the whole provider order book',async()=>{
  const limited={...provider,access:{...provider.access,movements:'own'}};
  await act(async()=>root.render(<ApprovedLedger resource={limited} context={{...ctx,resources:[limited]}} identity="staff"/>));
  expect(api.ledger.mock.calls[0][2].provider_orders).toBeUndefined();
  expect(button('طلبات باي بال')).toBeUndefined();
});

test('provider scope alone exposes only the scoped order number without a full order link',async()=>{
  await act(async()=>root.render(<ApprovedLedger resource={provider} context={ctx} identity="staff"/>));
  expect(host.querySelector('tbody').textContent).toContain('100000');
  expect(host.querySelector('tbody a')).toBeNull();
});

test('discloses excluded principal separately from calculated net without adding it to the balance', async()=>{
  const ProviderOrders=require('./ProviderOrders').default;
  const result={items:[],amounts:[{currency:'SAR',opening:'0.00',gross:'1567.66',refunded:'0.00',fees:'82.47',settled:'0.00',
    known_net:'1019.73',net:null,uncomputed_principal:'491.82',uncomputed_orders:2,uncomputed_principal_complete:true}]};
  await act(async()=>root.render(<ProviderOrders result={result} name="باي بال"/>));
  expect(host.querySelector('.note').textContent).toContain('491.82');
  expect(host.querySelector('.note').textContent).toContain('2 طلب');
  expect(host.querySelector('.note').textContent).toContain('غير مشمولة في الصافي المحتسب');
  expect(host.querySelector('tbody').textContent).toContain('1,019.73');
});
