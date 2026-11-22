import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { operationalApi } from './api';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext',()=>({useOptionalAuth:jest.fn()}));
jest.mock('./simpleBalancesApi',()=>({simpleBalancesApi:{context:jest.fn(),parties:jest.fn(),movements:jest.fn()},simpleBalanceError:()=> 'تعذر التحميل'}));
jest.mock('./api',()=>({operationalApi:{financialPosition:jest.fn(),refreshBalances:jest.fn()}}));
global.IS_REACT_ACT_ENVIRONMENT=true;
const base='/operational-balances/daily-movements';
const r=(kind,id,name,amount='50.00')=>({kind,id,name,currency:'SAR',access:{balance:true,movements:'all'},balance:{visible:true,side:'for_us',amount},operations:[]});
const ctx={actor:{id:'employee',name:'سارة',is_owner:false},session_scope:'unit',permission_version:1,status:'active',operations:[],capabilities:{create_entries:false},
 resources:[r('bank','1','بنك الإنماء','90.00'),{...r('cash','2','صندوق سلة','500.00'),prepaid_shipping_cash:true},r('supplier','3','مورد التفصيل'),r('external_person','4','ورشة تغليف')],
 recent_movements:[]};
let host,root;
beforeEach(()=>{jest.clearAllMocks();useOptionalAuth.mockReturnValue({user:{id:'employee'},loading:false});window.scrollTo=jest.fn();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);api.context.mockResolvedValue(ctx);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('home does one read, excludes prepaid shipping, and the authorized directory searches the same context locally',async()=>{
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=home']}><ApprovedFinance/></MemoryRouter>));
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(api.context.mock.calls[0][1]).toEqual({homePreview:true});
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  expect(host.querySelector('nav').textContent).not.toContain('المركز المالي');
  expect(host.textContent).not.toContain('حركة مالية جديدة');
  const link=[...host.querySelectorAll('nav a')].find(a=>a.textContent==='الأشخاص والجهات');
  expect(link.getAttribute('href')).toBe(base+'?view=parties');
  await act(async()=>link.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,button:0})));
  const input=host.querySelector('input[type=search]');
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'التفصيل');input.dispatchEvent(new Event('input',{bubbles:true}));});
  expect(host.querySelectorAll('.resource')).toHaveLength(1);
  expect(host.querySelector('.resource').getAttribute('href')).toContain('kind=supplier&id=3');
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(api.parties).not.toHaveBeenCalled();
  expect(api.movements).not.toHaveBeenCalled();
});

test('an employee assigned only the finance home sees no links to ungranted statements or entry', async () => {
  useOptionalAuth.mockReturnValue({user:{id:'employee',web_page_access:{pages:[{id:'balances.home',section:'balances',to:'/operational-balances',mode:'read'}]}},loading:false});
  api.context.mockResolvedValue({...ctx,capabilities:{create_entries:true}});
  await act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances']}><ApprovedFinance/></MemoryRouter>));
  expect(host.querySelectorAll('nav[aria-label="صفحات الأرصدة"] a')).toHaveLength(1);
  expect(host.querySelector('a[href*="view=entry"], a[href*="view=tools"], a[href*="view=ledger"], a[href*="view=log"]')).toBeNull();
  expect(api.context).toHaveBeenCalledTimes(1);
});

test('the existing supplier operations link opens an independent invoice for accrual staff', async () => {
  api.context.mockResolvedValue({...ctx,operations:[{key:'supplier.accrual'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.accrual']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=special-entry&kind=supplier&id=3']}><ApprovedFinance/></MemoryRouter>));
  expect(host.querySelector('h1').textContent).toBe('فاتورة شراء آجل');
  expect(host.textContent).toContain('مورد التفصيل');
  expect(host.querySelector('fieldset select').value).toBe('service');
  expect(host.textContent).not.toContain('المتبقي للمورد بعد الحفظ');
  expect(api.movements).not.toHaveBeenCalled();
});

test('credit purchase is a single choice between payment and transfer and opens the supplier list', async () => {
  api.context.mockResolvedValue({...ctx,operations:[{key:'supplier.accrual'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.accrual']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=entry']}><ApprovedFinance/></MemoryRouter>));
  const choices=[...host.querySelectorAll('.quick.flow .quickbtn')];
  expect(choices.map(button=>button.textContent)).toEqual(['استلمت مبلغ','دفعت مبلغ','شراء آجل من مورد','حوّلت بين حسابين']);
  expect(host.querySelectorAll('a[href*="mode=purchase"]')).toHaveLength(0);
  await act(async()=>choices[2].click());
  expect(host.querySelector('h1').textContent).toBe('شراء آجل من مورد');
  expect(host.querySelector('a.review-item').textContent).toContain('مورد التفصيل');
});

test('a payment-only user cannot open the supplier purchase form by URL', async () => {
  api.context.mockResolvedValue({...ctx,operations:[{key:'supplier.payment'}],capabilities:{create_entries:true},
    resources:[{...ctx.resources[2],access:{use:true,movements:'own'},operations:['supplier.payment']}]});
  await act(async()=>root.render(<MemoryRouter initialEntries={[base+'?view=special-entry&kind=supplier&id=3&mode=purchase']}><ApprovedFinance/></MemoryRouter>));
  expect(host.textContent).toContain('هذه الصفحة غير متاحة ضمن صلاحياتك');
  expect(host.querySelector('form')).toBeNull();
});

function ownerReports() {
  const context = {...ctx, actor:{id:'owner',name:'مالك المتجر',is_owner:true}, ledger_start:'2026-11-01'};
  useOptionalAuth.mockReturnValue({user:{id:'owner',role:'owner',is_owner:true},loading:false});
  api.context.mockImplementation(async (config, options) => options.directoryOnly
    ? {...context,directory_only:true,resources:context.resources.map(({balance,...resource})=>resource)}
    : context);
  api.movements.mockResolvedValue({items:[],page:0,page_size:8,total:0,total_pages:0});
  operationalApi.financialPosition.mockResolvedValue({schema_version:1,authority:'operational_balance_report',
    view:'summary',status:'active',revision:7,session_scope:context.session_scope,
    started_at:'2026-11-01T00:00:00+03:00',as_of:'2026-11-22T08:00:00+03:00',permissions:{reports:true},
    currency_order:['SAR'],forecast_pending:true,currencies:{SAR:{metrics:{liquidity:'431.29'},
      current_rows:[],profit_rows:[],debt_rows:[],tables:[],completeness:{actual:true}}},issues:[],dependencies:[]});
  return context;
}
const openFinance = view => act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances?view='+view]}><ApprovedFinance/></MemoryRouter>));
const openFinanceTab = view => act(async()=>host.querySelector('nav[aria-label="صفحات الأرصدة"] a[href="/operational-balances?view='+view+'"]').click());

test('refund context is isolated from the home preview and is dropped on returning to the home', async () => {
  ownerReports();
  function NavigationProbe() {
    const navigate = useNavigate();
    return <><button id="open-refund" onClick={()=>navigate('/operational-balances?view=refund')}>Refund</button>
      <button id="open-home" onClick={()=>navigate('/operational-balances?view=home')}>Home</button><ApprovedFinance/></>;
  }
  await act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances?view=home']}><NavigationProbe/></MemoryRouter>));
  expect(api.context.mock.calls[0][1]).toEqual({homePreview:true});
  await act(async()=>host.querySelector('#open-refund').click());
  expect(api.context.mock.calls.at(-1)[1]).toEqual({homePreview:false,directoryOnly:true,refundContext:true});
  await act(async()=>host.querySelector('#open-home').click());
  expect(api.context.mock.calls.at(-1)[1]).toEqual({homePreview:true});
  expect(api.context).toHaveBeenCalledTimes(3);
  expect(api.movements).not.toHaveBeenCalled();
});

test.each(['reports','entry','log'])('opening reports from %s reads directory context once and the actual saved report separately', async start => {
  ownerReports();
  await openFinance(start);
  if (start !== 'reports') {
    expect(operationalApi.financialPosition).not.toHaveBeenCalled();
    await openFinanceTab('reports');
  }
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(api.context.mock.calls[0][1]).toEqual({homePreview:false,directoryOnly:true});
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(operationalApi.financialPosition.mock.calls[0][0].params).toEqual({view:'summary'});
  expect(host.querySelector('.report-metric').textContent).toContain('431.29');
  expect(api.movements).toHaveBeenCalledTimes(start === 'log' ? 1 : 0);
  expect(api.parties).not.toHaveBeenCalled();
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test('home to reports and back reuses its authorized preview without a second context calculation', async () => {
  ownerReports();
  await openFinance('home');
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  await openFinanceTab('reports');
  expect(host.querySelector('.report-metric').textContent).toContain('431.29');
  await openFinanceTab('home');
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(api.context.mock.calls[0][1]).toEqual({homePreview:true});
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(api.movements).not.toHaveBeenCalled();
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

test.each(['employee','different-actor'])('reports never reads the owner snapshot for %s context', async scenario => {
  const context = ownerReports();
  if (scenario === 'employee') {
    useOptionalAuth.mockReturnValue({user:{id:'employee',role:'employee'},loading:false});
    api.context.mockResolvedValue({...context,actor:{id:'employee',is_owner:false}});
  } else api.context.mockResolvedValue({...context,actor:{id:'different-owner',is_owner:true}});
  await openFinance('reports');
  expect(operationalApi.financialPosition).not.toHaveBeenCalled();
  expect(host.querySelector('.report-metric')).toBeNull();
});
