import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { operationalApi } from './api';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext',()=>({useOptionalAuth:jest.fn()}));
jest.mock('./simpleBalancesApi',()=>({simpleBalancesApi:{bootstrap:jest.fn(),home:jest.fn(),directory:jest.fn(),selection:jest.fn(),options:jest.fn(),parties:jest.fn(),movements:jest.fn(),customerAdjustments:jest.fn()},simpleBalanceError:()=> 'تعذر التحميل'}));
jest.mock('./api',()=>({operationalApi:{financialPosition:jest.fn(),refreshBalances:jest.fn()}}));
global.IS_REACT_ACT_ENVIRONMENT=true;
const base='/operational-balances/daily-movements';
const r=(kind,id,name,amount='50.00')=>({kind,id,name,currency:'SAR',access:{balance:true,movements:'all'},balance:{visible:true,side:'for_us',amount},operations:[]});
const ctx={actor:{id:'employee',name:'سارة',is_owner:false},session_scope:'unit',permission_version:1,status:'active',operations:[],capabilities:{create_entries:false},
 resources:[r('bank','1','بنك الإنماء','90.00'),{...r('cash','2','صندوق سلة','500.00'),prepaid_shipping_cash:true},r('supplier','3','مورد التفصيل'),r('external_person','4','ورشة تغليف')],
 recent_movements:[]};
let host,root;
function mockContext(value) {
  api.bootstrap.mockResolvedValue({...value,resources:[],capabilities:{...value.capabilities,read_movements:true,purchase_suppliers:value.operations.some(row=>(row.key||row)==='supplier.accrual')}});
  api.home.mockResolvedValue({session_scope:value.session_scope,permission_version:value.permission_version,
    summary:{currency:'SAR',bank:'90.00',cash:null,total:'90.00',cash_count:0,cash_balance_visible:false},resources:value.resources.filter(row=>['bank','cash','provider'].includes(row.kind)).slice(0,3),recent_movements:[],has_order_issues:false});
  api.selection.mockImplementation(async(kind,id)=>({...value.resources.find(row=>row.kind===kind&&row.id===id),session_scope:value.session_scope,permission_version:value.permission_version}));
  const page=items=>({items,page:1,page_size:24,total:items.length,pages:1,session_scope:value.session_scope,permission_version:value.permission_version});
  api.directory.mockImplementation(async params=>page(value.resources.filter(row=>params.group==='parties'?!['bank','cash','provider'].includes(row.kind):['bank','cash','provider'].includes(row.kind)).filter(row=>!params.q||row.name.includes(params.q))));
  api.options.mockResolvedValue(page(value.resources.filter(row=>row.kind==='supplier')));
}
beforeEach(()=>{jest.clearAllMocks();useOptionalAuth.mockReturnValue({user:{id:'employee'},loading:false});window.scrollTo=jest.fn();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);mockContext(ctx);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('home reads a compact summary and directory search requests only the selected server page',async()=>{
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=home']}><ApprovedFinance/></MemoryRouter>));
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.home).toHaveBeenCalledTimes(1);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  expect(host.querySelector('nav').textContent).not.toContain('المركز المالي');
  expect(host.textContent).not.toContain('حركة مالية جديدة');
  const link=[...host.querySelectorAll('nav a')].find(a=>a.textContent==='الأشخاص والجهات');
  expect(link.getAttribute('href')).toBe(base+'?view=parties');
  await act(async()=>link.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,button:0})));
  const input=host.querySelector('input[type=search]');
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'التفصيل');input.dispatchEvent(new Event('input',{bubbles:true}));});
  await act(async()=>new Promise(resolve=>setTimeout(resolve,360)));
  expect(host.querySelectorAll('.resource')).toHaveLength(1);
  expect(host.querySelector('.resource').getAttribute('href')).toContain('kind=supplier&id=3');
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.directory.mock.calls.at(-1)[0]).toEqual({group:'parties',q:'التفصيل',page:1,page_size:24});
  expect(api.parties).not.toHaveBeenCalled();
  expect(api.movements).not.toHaveBeenCalled();
});

test('an employee assigned only the finance home sees no links to ungranted statements or entry', async () => {
  useOptionalAuth.mockReturnValue({user:{id:'employee',web_page_access:{pages:[{id:'balances.home',section:'balances',to:'/operational-balances',mode:'read'}]}},loading:false});
  mockContext({...ctx,capabilities:{create_entries:true}});
  await act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances']}><ApprovedFinance/></MemoryRouter>));
  expect(host.querySelectorAll('nav[aria-label="صفحات الأرصدة"] a')).toHaveLength(1);
  expect(host.querySelector('a[href*="view=entry"], a[href*="view=tools"], a[href*="view=ledger"], a[href*="view=log"]')).toBeNull();
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
});

test('the existing supplier operations link opens an independent invoice for accrual staff', async () => {
  mockContext({...ctx,operations:[{key:'supplier.accrual'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.accrual']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=special-entry&kind=supplier&id=3']}><ApprovedFinance/></MemoryRouter>));
  expect(host.querySelector('h1').textContent).toBe('فاتورة شراء آجل');
  expect(host.textContent).toContain('مورد التفصيل');
  expect(host.querySelector('fieldset select').value).toBe('service');
  expect(host.textContent).not.toContain('المتبقي للمورد بعد الحفظ');
  expect(api.movements).not.toHaveBeenCalled();
});

test('credit purchase is a single choice between payment and transfer and opens the supplier list', async () => {
  mockContext({...ctx,operations:[{key:'supplier.accrual'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.accrual']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=entry']}><ApprovedFinance/></MemoryRouter>));
  const choices=[...host.querySelectorAll('.quick.flow .quickbtn')];
  expect(choices.map(button=>button.textContent)).toEqual(['استلمت مبلغ','دفعت مبلغ','شراء آجل من مورد','حوّلت بين حسابين']);
  expect(host.querySelectorAll('a[href*="mode=purchase"]')).toHaveLength(0);
  await act(async()=>choices[2].click());
  expect(host.querySelector('h1').textContent).toBe('شراء آجل من مورد');
  expect(host.querySelector('select[aria-label="المورد"]').textContent).toContain('مورد التفصيل');
  expect(api.options).toHaveBeenCalledWith(expect.objectContaining({operation:'supplier.accrual',kind:'supplier',role:'recipient',page:1,page_size:24}),expect.any(Object));
});

test('a payment-only user cannot open the supplier purchase form by URL', async () => {
  mockContext({...ctx,operations:[{key:'supplier.payment'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.payment']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=special-entry&kind=supplier&id=3&mode=purchase']}><ApprovedFinance/></MemoryRouter>));
  expect(host.textContent).toContain('هذه الصفحة غير متاحة ضمن صلاحياتك');
  expect(host.querySelector('form')).toBeNull();
});

function ownerReports() {
  const context = {...ctx, actor:{id:'owner',name:'مالك المتجر',is_owner:true}, ledger_start:'2026-11-01'};
  useOptionalAuth.mockReturnValue({user:{id:'owner',role:'owner',is_owner:true},loading:false});
  mockContext(context);
  api.movements.mockResolvedValue({items:[],page:0,page_size:8,total:0,total_pages:0});
  operationalApi.financialPosition.mockResolvedValue({schema_version:1,authority:'operational_balance_report',
    view:'summary',status:'active',revision:7,session_scope:context.session_scope,permission_version:context.permission_version,
    started_at:'2026-11-01T00:00:00+03:00',as_of:'2026-11-22T08:00:00+03:00',permissions:{reports:true},
    currency_order:['SAR'],forecast_pending:true,currencies:{SAR:{metrics:{liquidity:'431.29'},
      current_rows:[],profit_rows:[],debt_rows:[],tables:[],completeness:{actual:true}}},issues:[],dependencies:[]});
  return context;
}
const openFinance = view => act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances?view='+view]}><ApprovedFinance/></MemoryRouter>));
const openFinanceTab = view => act(async()=>host.querySelector('nav[aria-label="صفحات الأرصدة"] a[href="/operational-balances?view='+view+'"]').click());


test('refund read isolation across navigation uses one bootstrap', async () => {
  const owner = ownerReports();
  mockContext({...owner, operations:[{key:'customer.refund_read'}]});

  api.customerAdjustments.mockResolvedValue({
    items:[], page:1, page_size:8, total:0, total_pages:1,
    summary:{
      refund_pending_amount:'0.00',
      refund_pending_count:0,
      collection_pending_amount:'0.00',
      collection_pending_count:0,
      unknown_count:0
    }
  });

  function NavigationProbe() {
    const navigate = useNavigate();
    return <>
      <button id="open-refund-test"
        onClick={() => navigate('/operational-balances?view=refund')}>
        Refund
      </button>
      <button id="open-home-test"
        onClick={() => navigate('/operational-balances?view=home')}>
        Home
      </button>
      <ApprovedFinance/>
    </>;
  }

  await act(async () => root.render(
    <MemoryRouter initialEntries={['/operational-balances?view=home']}>
      <NavigationProbe/>
    </MemoryRouter>
  ));

  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.home).toHaveBeenCalledTimes(1);
  expect(api.customerAdjustments).not.toHaveBeenCalled();

  await act(async () => {
    host.querySelector('#open-refund-test').click();
  });

  expect(host.textContent).toContain('إرجاع مبلغ لعميل');
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  expect(api.home).toHaveBeenCalledTimes(1);
  expect(api.directory).not.toHaveBeenCalled();
  expect(api.movements).not.toHaveBeenCalled();
  expect(api.bootstrap).toHaveBeenCalledTimes(1);

  await act(async () => {
    host.querySelector('#open-home-test').click();
  });

  expect(api.home).toHaveBeenCalledTimes(2);
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
});

test.each(['reports','entry','log'])('opening reports from %s reads bootstrap once and the saved report only on its page', async start => {
  ownerReports();
  await openFinance(start);
  if (start !== 'reports') {
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    await openFinanceTab('reports');
  }
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.home).not.toHaveBeenCalled();
  expect(api.directory).not.toHaveBeenCalled();
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(operationalApi.financialPosition.mock.calls[0][0].params).toEqual({view:'summary'});
  expect(host.querySelector('.report-metric').textContent).toContain('431.29');
  expect(api.movements).toHaveBeenCalledTimes(start === 'log' ? 1 : 0);
  expect(api.parties).not.toHaveBeenCalled();
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test('returning to home requests its current projection without rebuilding bootstrap or hidden reports', async () => {
  ownerReports();
  await openFinance('home');
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  await openFinanceTab('reports');
  expect(host.querySelector('.report-metric').textContent).toContain('431.29');
  await openFinanceTab('home');
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.home).toHaveBeenCalledTimes(2);
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(api.movements).not.toHaveBeenCalled();
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test.each(['employee','different-actor'])('reports never reads the owner snapshot for %s context', async scenario => {
  const context = ownerReports();
  if (scenario === 'employee') {
    useOptionalAuth.mockReturnValue({user:{id:'employee',role:'employee'},loading:false});
    mockContext({...context,actor:{id:'employee',is_owner:false}});
  } else mockContext({...context,actor:{id:'different-owner',is_owner:true}});
  await openFinance('reports');
  expect(operationalApi.financialPosition).not.toHaveBeenCalled();
  expect(host.querySelector('.report-metric')).toBeNull();
});
