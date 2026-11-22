import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import ApprovedFinance from './ApprovedFinance';
import { useOptionalAuth } from '../../context/AuthContext';
import { operationalApi } from './api';
import { simpleBalancesApi } from './simpleBalancesApi';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({useOptionalAuth:jest.fn()}));
jest.mock('./simpleBalancesApi', () => ({simpleBalancesApi:{bootstrap:jest.fn(),home:jest.fn(),directory:jest.fn(),selection:jest.fn(), ledger:jest.fn()}, simpleBalanceError:()=> 'خطأ'}));
jest.mock('./api', () => ({operationalApi:{addEntity:jest.fn()},requestId:()=> 'fixture-request',messageFor:()=> 'تعذر الحفظ'}));

const resource=(kind,id,name,amount)=>({kind,id,name,currency:'SAR',access:{balance:true,movements:'all',report:true},
  balance:{visible:true,side:'for_us',amount},operations:[]});
const bank=resource('bank','inma','الإنماء','90.00');
const wallet={...resource('cash','shipping','محفظة سله','654.00'),prepaid_shipping_cash:true};
const drawer=resource('cash','main','الصندوق الرئيسي','0.00');
const base={status:'active', permission_version:1,session_scope:'cash-scope',actor:{id:'owner',is_owner:true},
  operations:[],resources:[bank,wallet],capabilities:{create_entries:false},recent_movements:[]};
let host,root;
beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;
  window.scrollTo=jest.fn();
  HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  HTMLDialogElement.prototype.close=function(){this.open=false;};
  jest.clearAllMocks();
  host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  useOptionalAuth.mockReturnValue({user:{id:'owner',role:'owner'}});
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const render=async(view)=>act(async()=>root.render(<MemoryRouter initialEntries={['/operational-balances?view='+view]}><ApprovedFinance/></MemoryRouter>));
const button=text=>[...host.querySelectorAll('button')].find(el=>el.textContent===text);
function mockContext(value, summary={currency:'SAR',bank:'90.00',cash:null,total:'90.00',cash_count:0,cash_balance_visible:false}) {
  simpleBalancesApi.bootstrap.mockResolvedValue({...value,resources:[]});
  simpleBalancesApi.home.mockResolvedValue({summary,resources:value.resources.slice(0,3),recent_movements:[],has_order_issues:false,session_scope:value.session_scope,permission_version:value.permission_version});
  simpleBalancesApi.selection.mockImplementation(async(kind,id)=>({...value.resources.find(item=>item.kind===kind&&item.id===id),session_scope:value.session_scope,permission_version:value.permission_version}));
  simpleBalancesApi.directory.mockImplementation(async params=>{
    const items=value.resources.filter(item=>!params.kind||params.kind.split(',').includes(item.kind));
    return {items,page:1,page_size:24,total:items.length,pages:1,session_scope:value.session_scope,permission_version:value.permission_version};
  });
}

test('owner creates a SAR cash account and sees its refreshed ledger without recording money',async()=>{
  mockContext(base);
  simpleBalancesApi.selection.mockResolvedValue({...drawer,session_scope:base.session_scope,permission_version:base.permission_version});
  simpleBalancesApi.ledger.mockResolvedValue({items:[],total:0,page:1,page_size:8});
  operationalApi.addEntity.mockResolvedValue(drawer);
  await render('home');
  expect(host.querySelector('.hero').textContent).toContain('لا توجد صناديق نقدية بالريال');
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  await act(async()=>button('إضافة صندوق نقدي').click());
  const input=host.querySelector('dialog input');
  await act(async()=>{
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,drawer.name);
    input.dispatchEvent(new Event('input',{bubbles:true}));
  });
  await act(async()=>button('إضافة').click());
  expect(operationalApi.addEntity).toHaveBeenCalledWith('cash',{request_id:'fixture-request',name:drawer.name,currency:'SAR'});
  expect(simpleBalancesApi.bootstrap).toHaveBeenCalledTimes(2);
  expect(host.querySelector('h1').textContent).toBe(drawer.name);
  expect(host.textContent).toContain('تمت إضافة الصندوق الرئيسي');
});

test('the cash group has an add action with no existing accounts',async()=>{
  mockContext({...base,resources:[bank]});
  await render('assets');
  expect(button('إضافة صندوق نقدي')).toBeDefined();
  expect(button('الصناديق والمحافظ')).toBeDefined();
  await act(async()=>button('الصناديق والمحافظ').click());
  expect(host.textContent).toContain('لا توجد صناديق مسجّلة');
  expect(simpleBalancesApi.directory.mock.calls.at(-1)[0].kind).toBe('cash');
});

test.each([
  [[drawer],'0.00'],
  [[{...drawer,access:{balance:false},balance:null}],'عرض الرصيد يتطلب صلاحية'],
  [[wallet],'لا توجد صناديق نقدية متاحة لك'],
])('cash subtotal follows authorized physical balances %#',async(resources,expected)=>{
  const actor={id:'staff',is_owner:false};
  useOptionalAuth.mockReturnValue({user:actor});
  mockContext({...base,actor,resources:[bank,...resources]}, {currency:'SAR',bank:'90.00',cash:expected==='0.00'?'0.00':null,total:'90.00',
    cash_count:expected==='لا توجد صناديق نقدية متاحة لك'?0:1,cash_balance_visible:expected==='0.00'});
  await render('home');
  expect(host.querySelector('.hero .split').textContent).toContain(expected);
  expect(button('إضافة صندوق نقدي')).toBeUndefined();
});

test('provider cards show known partial totals and keep protected amounts and counts hidden',async()=>{
  const account={...resource('provider','paypal','باي بال','540.00'),
    provider_position:{complete:false,net:null,known_net:'50.10',paid_orders:5,currency:'SAR'}};
  const restricted={...account,id:'other',name:'مخفي',access:{balance:false,movements:'all',report:true}};
  const unknown={...account,id:'unknown',name:'بانتظار المطابقة',provider_position:{complete:false,known_net:null,currency:'SAR'}};
  mockContext({...base,resources:[account,restricted,unknown]});
  await render('assets');
  const cards=[...host.querySelectorAll('.resource')];
  expect(cards[0].textContent).toContain('50.10');
  expect(cards[0].textContent).toContain('الصافي المحتسب · يحتاج مطابقة');
  expect(cards[0].textContent).toContain('الطلبات المدفوعة · 5');
  expect(cards[0].textContent).not.toContain('540.00');
  expect(cards[1].textContent).not.toMatch(/50.10|540.00|الطلبات المدفوعة/);
  expect(cards[2].textContent).toContain('يحتاج مطابقة');
  expect(cards[2].textContent).not.toContain('0.00');
});
