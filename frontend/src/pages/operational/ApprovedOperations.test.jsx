import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ApprovedOperations, { ApprovedPosition } from './ApprovedOperations';
import { operationalApi } from './api';
import ShippingCompanySettings from '../ShippingCompanySettings';
import http from '../../lib/api';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../lib/api', () => ({ get: jest.fn(), put: jest.fn() }));
jest.mock('./CourierTariffApproval', () => () => null);
jest.mock('./api', () => ({ operationalApi: { financialPosition: jest.fn(), refreshBalances: jest.fn() } }));
const bank = { id:'bank', name:'بنك الإنماء', kind:'bank', currency:'SAR', access:{balance:true}, balance:{visible:true,amount:'100.12',side:'for_us'} };
const cash = { ...bank, id:'cash', name:'صندوق المتجر', kind:'cash', balance:{visible:true,amount:'21.05',side:'for_us'} };
const prepaid = { ...cash, id:'shipping', prepaid_shipping_cash:true, name:'محفظة الشحن' };
const ad = { ...bank, id:'ad', kind:'ad_account', name:'حساب سناب', ad_wallet_balance:'20.31', currency:'USD' };
const context = { actor:{is_owner:true}, resources:[bank,cash,prepaid,ad], session_scope:'a', permission_version:1, status:'active', operations:[], capabilities:{manage_access:true} };
const report = {
  view:'summary', session_scope:'a', schema_version:1, status:'active', authority:'operational_balance_report', as_of:'2026-11-19T12:00:00Z', permissions:{reports:true},
  currencies:{
    SAR:{metrics:{liquidity:'142.22'},debt_rows:[{id:'providers',receivable:'70.00'}],debt_totals:{payable:null},completeness:{actual:false}},
    USD:{metrics:{liquidity:'0.00'},debt_rows:[{id:'providers',receivable:'5.20'}],debt_totals:{payable:'1.00'},completeness:{actual:true}},
  },
};
let host,root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host=document.createElement('div'); document.body.appendChild(host); root=createRoot(host);
  Object.defineProperty(document,'visibilityState',{ configurable:true,value:'visible' });
  operationalApi.financialPosition.mockReset().mockResolvedValue(report);
  http.get.mockReset().mockResolvedValue({data:{shipping_companies:[]}});http.put.mockClear();
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const show = (element, path='/operational-balances')=>act(async()=>root.render(<MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>));
const links = ()=>[...host.querySelectorAll('a')].map(anchor=>anchor.getAttribute('href'));

it('links to real specialist tools and separate upload/history sections without loading data', async()=>{
  await show(<ApprovedOperations context={context}/>);
  for(const path of ['/operational-balances/provider-statements#upload','/operational-balances/provider-statements#history',
    '/operational-balances/provider-fees','/shipping-settings-v2?from=operational-tools','/operational-balances/financial-position?view=drivers','/operational-balances/financial-position?view=couriers',
    '/operational-balances/financial-position?view=accounts','/operational-balances?view=specialized&section=salary-start',
    '/operational-balances/customer-returns','/operational-balances/customer-exchanges','/inventory-v2?tab=purchases','/operational-balances/access']) expect(links()).toContain(path);
  expect(links()).not.toContain('/operational-balances?view=specialized&section=openings');
  expect(operationalApi.financialPosition).not.toHaveBeenCalled();
  await show(<ApprovedOperations context={{...context,status:'draft'}}/>);
  expect(links()).toContain('/operational-balances?view=specialized&section=openings');
  expect(links()).not.toContain('/operational-balances?view=specialized&section=salary-start');
});

it('limits employee links to granted actions within their existing route and denies owner data',async()=>{
  const staff={...context,actor:{is_owner:false},operations:['customer.refund_request','courier.review']};
  await show(<ApprovedOperations context={staff}/>,'/operational-balances/daily-movements');
  expect(links()).toEqual(['/operational-balances/daily-movements?view=refund']);
  await show(<ApprovedOperations context={{...staff,resources:[{...bank,kind:'store_driver',access:{movements:'all'}}]}}/>);
  expect(links()).toContain('/operational-balances/daily-movements?view=collections');
  await show(<ApprovedPosition context={staff} identity="employee"/>);
  expect(host.textContent).toContain('متاح لمالك الحساب');
  expect(operationalApi.financialPosition).not.toHaveBeenCalled();
});

it('reads one saved summary, excludes prepaid shipping cash, retains unknown debts and native-currency wallets',async()=>{
  const open=jest.fn();
  await show(<ApprovedPosition context={context} identity="owner" onOpen={open}/>);
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(operationalApi.financialPosition).toHaveBeenCalledWith(expect.objectContaining({params:{view:'summary'}}));
  const metrics=[...host.querySelectorAll('.metric')].map(el=>el.textContent);
  expect(metrics[0]).toContain('121.17');expect(metrics[1]).toContain('70.00');expect(metrics[2]).toContain('غير متاح');
  const buttons=[...host.querySelectorAll('.mz-position-line')];
  await act(async()=>buttons.find(b=>b.textContent.includes('بنك الإنماء')).click());
  expect(open).toHaveBeenCalledWith(bank);
  expect(buttons.find(b=>b.textContent.includes('حساب سناب')).textContent).toContain('20.31 USD');
  const selector=host.querySelector('select');
  await act(async()=>{selector.value='USD';selector.dispatchEvent(new Event('change',{bubbles:true}));});
  expect(host.querySelectorAll('.metric')[1].textContent).toContain('5.20');
  expect(operationalApi.financialPosition).toHaveBeenCalledTimes(1);
  expect(operationalApi.refreshBalances).not.toHaveBeenCalled();
});

it('discards previously visible financial figures when identity changes or permission is missing',async()=>{
  await show(<ApprovedPosition context={context} identity="first"/>);
  expect(host.querySelectorAll('.metric')).toHaveLength(3);
  operationalApi.financialPosition.mockResolvedValueOnce({...report,permissions:{reports:false}});
  await show(<ApprovedPosition context={{...context,session_scope:'changed'}} identity="second"/>);
  expect(host.querySelectorAll('.metric')).toHaveLength(0);expect(host.querySelector('[role=alert]')).not.toBeNull();
  operationalApi.financialPosition.mockResolvedValueOnce(report);
  await show(<ApprovedPosition context={{...context,session_scope:'third-scope'}} identity="third"/>);
  expect(host.querySelectorAll('.metric')).toHaveLength(0);
});


it.each([
  ['/shipping-settings-v2?from=operational-tools',true,'/operational-balances?view=tools'],
  ['/shipping-settings-v2',true,'/operational-balances'],
  ['/shipping/settings',false,'/shipping/ledger'],
])('shipping settings preserves the caller’s return path at %s', async (path,mezan2,back) => {
  await show(<ShippingCompanySettings mezan2={mezan2}/>,path);
  expect(host.querySelector('[data-testid=settings-back]').getAttribute('href')).toBe(back);
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.put).not.toHaveBeenCalled();
});

it('shows SAR debt with the native USD amount and keeps prepaid funds separate',async()=>{
  const resources = [
    {...ad, ad_amounts:{currency:'SAR',prepaid:'0.00',payable:'383.63',native:{currency:'USD',prepaid:'0.00',payable:'100.00'}}},
    {...ad, id:'prepaid',name:'تيك توك',currency:'SAR',ad_amounts:{currency:'SAR',prepaid:'420.00',payable:'0.00'}},
  ];
  await show(<ApprovedPosition context={{...context,resources}} identity="owner"/>);
  const rows = [...host.querySelectorAll('.mz-position-line')].map(el=>el.textContent);
  expect(rows[0]).toContain('علينا 383.63 ر.س');
  expect(rows[0]).toContain('100.00 USD');
  expect(rows[1]).toContain('رصيد مقدم 420.00 ر.س');
  expect(rows[1]).not.toContain('علينا');
});

it('shows owner-only PayPal receipts apart from outstanding provider balances',async()=>{
  const cashOnly = {...context,resources:[bank],paypal_customer_payments:{orders:2,paid:'152.11'}};
  await show(<ApprovedPosition context={cashOnly} identity="owner" />);
  const row = host.querySelector('a.mz-position-line');
  expect(row.textContent).toContain('باي بال');
  expect(row.textContent).toContain('152.11 ر.س');
  expect(row.getAttribute('href')).toContain('view=providers');
  await show(<ApprovedPosition context={{...cashOnly,actor:{is_owner:false}}} identity="staff" scoped />);
  expect(host.textContent).not.toContain('باي بال');
});
