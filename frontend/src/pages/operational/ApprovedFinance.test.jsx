import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext',()=>({useOptionalAuth:jest.fn()}));
jest.mock('./simpleBalancesApi',()=>({simpleBalancesApi:{context:jest.fn(),parties:jest.fn(),movements:jest.fn()},simpleBalanceError:()=> 'تعذر التحميل'}));
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
