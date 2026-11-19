import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ApprovedLedger from './ApprovedLedger';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { supplierInvoice: jest.fn(), ledger: jest.fn(), movements: jest.fn(), statement: jest.fn() },
  simpleBalanceError: () => 'تعذر تحميل الكشف' }));
jest.mock('../../lib/dates', () => ({ todaySA: () => '2026-11-19' }));
global.IS_REACT_ACT_ENVIRONMENT = true;

const bank = { kind:'bank', id:'bank', name:'الإنماء', access:{balance:true, movements:'all', report:true, print:true}, currency:'SAR', order_issues_count:3 };
const ctx = { actor:{id:'owner', is_owner:true}, session_scope:'s', permission_version:1, ledger_start:'2026-11-03', resources:[bank] };
const rows = Array.from({length:11}, (_,i) => ({id:String(i), description:'حركة '+i, recorded_by:{id:'staff',name:'سارة'}, side:'for_us',
  party_name:'الإنماء', amount:'10.00', currency:'SAR', reference:'MV-'+i, business_date:'2026-11-12', balance_after:'150.00'}));
const body = {resource:bank, currency:'SAR', totals:{for_party:'20.00',for_us:'30.00'}, scope:'all', can_print:true,
  opening_balance:'140.00', closing_balance:'150.00', date_from:'2026-11-03', date_to:'2026-11-19', total:11 };
const paged = query => { const found = query.q ? rows.filter(r=>r.reference.includes(query.q)):rows;
  return {resource:bank, total:found.length, page:query.page, page_size:query.page_size, total_pages:Math.max(1,Math.ceil(found.length/8)),
    items:found.slice((query.page-1)*8,query.page*8), period:body}; };
let host, root;
beforeEach(() => {
  jest.clearAllMocks(); host=document.createElement('div'); document.body.appendChild(host); root=createRoot(host);
  api.ledger.mockImplementation(async (kind,id,params) => paged(params));
  api.movements.mockImplementation(async params => paged(params));
  api.statement.mockResolvedValue({...body,items:rows});
});
afterEach(async()=>{await act(async()=>root.unmount()); host.remove(); jest.useRealTimers();});
const render = async (extra={}) => act(async()=>root.render(<ApprovedLedger identity="owner" context={ctx} resource={bank} {...extra} />));
const click = async label=>act(async()=>{[...host.querySelectorAll('button')].find(b=>b.textContent.includes(label)).click();});
const type = async value=>act(async()=>{const input=host.querySelector('input[type="search"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,value);
  input.dispatchEvent(new Event('input',{bubbles:true}));});

test('one read for account, eight rows, typing is debounced and totals still describe the entire period',async()=>{
  jest.useFakeTimers();
  await render();
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({date_from:'2026-11-03',date_to:'2026-11-19',page:1,page_size:8});
  expect(api.statement).not.toHaveBeenCalled();
  expect(host.querySelectorAll('tbody tr')).toHaveLength(8);
  await click('التالي');
  expect(host.querySelectorAll('tbody tr')).toHaveLength(3);
  await type('M');await type('MV-10');
  expect(api.ledger).toHaveBeenCalledTimes(2);
  await act(async()=>{jest.advanceTimersByTime(451);});
  expect(api.ledger.mock.calls[2][2]).toMatchObject({q:'MV-10',page:1,page_size:8});
  expect(host.querySelectorAll('tbody tr')).toHaveLength(1);
  expect(host.querySelector('.metrics').textContent).toContain('150.00');
  await click('حركة 10');
  expect(host.querySelector('dialog').textContent).toContain('MV-10');
  expect(api.ledger).toHaveBeenCalledTimes(3);
});

test('statement print is a fresh full-period permissioned read independent of the on-screen page',async()=>{
  const print = window.print; let printed;
  window.print=jest.fn(()=>{printed=document.body.querySelectorAll('.sb-print-root tbody tr').length;});
  try {
    await render();
    await click('التالي');
    await click('طباعة الكشف');
    expect(api.statement).toHaveBeenCalledTimes(1);
    expect(api.statement.mock.calls[0][2]).toEqual({date_from:'2026-11-03',date_to:'2026-11-19',purpose:'print'});
    expect(printed).toBe(11);
  } finally { window.print=print; }
});

test('global tab does not fetch resource ledgers, and account with no movement permission starts no read',async()=>{
  await render({resource:null});
  expect(api.movements).toHaveBeenCalledTimes(1);
  expect(api.ledger).not.toHaveBeenCalled();
  await render({resource:{...bank,access:{movements:'none',use:true}},identity:'limited'});
  expect(api.ledger).not.toHaveBeenCalled();
  expect(host.querySelector('table')).toBeNull();
});

test('shows the approver next to the date and loads invoice lines only after opening the row',async()=>{
  const supplier = {...bank, kind:'supplier', id:'supplier', name:'أسواق الراجحي'};
  const row = {...rows[0], party_id:'supplier', invoice_id:'invoice1', description:'فاتورة استلام SI-26', amount:'26.00'};
  api.supplierInvoice.mockResolvedValue({id:'invoice1', currency:'SAR', total:'26.00', lines:[
    {name:'غلاف',quantity:2,unit:'13.00',total:'26.00',services:[]}]});
  api.ledger.mockImplementation(async (kind,id,params) => ({...paged(params),resource:supplier,total:1,total_pages:1,items:[row]}));
  await render({resource:supplier});
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(host.querySelector('tbody').textContent).toContain('11-12 · سارة');
  await click('فاتورة استلام SI-26');
  expect(api.supplierInvoice.mock.calls[0].slice(0,2)).toEqual(['supplier','invoice1']);
  expect(host.querySelector('dialog').textContent).toContain('غلاف');
  expect(host.querySelector('dialog').textContent).toContain('إجمالي الفاتورة');
});
