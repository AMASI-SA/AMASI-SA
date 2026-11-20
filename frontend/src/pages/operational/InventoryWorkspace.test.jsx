import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import InventoryWorkspace from '../InventoryWorkspace';
import {operationalApi as api} from './api';
import {loadInventoryStocktakeContext} from '../../services/inventoryStocktakes';
jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('./api', () => ({operationalApi:{context:jest.fn()}, messageFor:() => 'تعذر تحميل الصلاحيات'}));
jest.mock('./InventoryAvailability', () => ({context,view}) => <div data-testid="stock" data-view={view}>{context.session_scope}</div>);
jest.mock('../../services/inventoryStocktakes',()=>({loadInventoryStocktakeContext:jest.fn(),loadInventoryStocktakeMovements:jest.fn()}));
jest.mock('../../components/inventory/suite/InventoryMovementsView',()=>({allowed})=>allowed?<div data-testid="movements"/>:null);
jest.mock('../inventory/StocktakeWorkbench',()=>({mode})=><div data-testid="count-workbench" data-mode={mode}/>);
jest.mock('./StandaloneInventoryEntry', () => () => <div data-testid="opening" />);
jest.mock('./InventoryPurchases', () => ({mode='purchase'}) => <div data-testid={mode} />);
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  loadInventoryStocktakeContext.mockRejectedValue({response:{status:403}});
  api.context.mockResolvedValue({session_scope:'owner',status:'active',permissions:{reports:true,move:true},can_manage_inventory_opening:true});
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllMocks(); });
const render = async (route='/inventory-v2') => act(async () => root.render(<MemoryRouter initialEntries={[route]}><InventoryWorkspace /></MemoryRouter>));
test('starts with the native daily center projection and preserves inventory readiness controls', async () => {
  await render(); expect(host.querySelector('[data-testid="stock"]').getAttribute('data-view')).toBe('home');
  expect(host.querySelector('[data-testid="purchase"]')).toBeNull();
  expect(host.textContent).toContain('ربط هذه المرحلة بحجز وصرف طلبات العملاء مؤجل');
  expect(api.context).toHaveBeenCalledTimes(1);
});
test('opens the existing purchase and opening screens only on explicit navigation', async () => {
  await render();
  await act(async () => [...host.querySelectorAll('button')].find(b=>b.textContent==='شراء مخزون').click());
  expect(host.querySelector('[data-testid="purchase"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="stock"]')).toBeNull();
  await act(async () => [...host.querySelectorAll('button')].find(b=>b.textContent==='إضافة المخزون').click());
  expect(host.querySelector('[data-testid="opening"]')).not.toBeNull();
});
test.each(['opening','purchase','purchases','stock','stocktake','adjustments','movements'])('direct tab %s cannot bypass permissions', async tab => {
  api.context.mockResolvedValue({session_scope:'limited',permissions:{}});
  await render('/inventory-v2?tab='+tab);
  expect(host.querySelector('[data-testid="stock"], [data-testid="opening"], [data-testid="purchase"], [data-testid="reports"], [data-testid="count-workbench"], [data-testid="movements"]')).toBeNull();
  expect(host.textContent).toContain('غير متاحة ضمن صلاحيات المخزون');
});
test('read-only user can review invoices without mounting purchase form', async () => {
  api.context.mockResolvedValue({session_scope:'reader',permissions:{reports:true}});
  await render('/inventory-v2?tab=purchases');
  expect(host.querySelector('[data-testid="reports"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="purchase"]')).toBeNull();
});
test('context failure does not mount inventory writers', async () => {
  api.context.mockRejectedValue(new Error('offline')); await render();
  expect(host.textContent).toContain('تعذر تحميل الصلاحيات');
  expect(host.querySelector('[data-testid="stock"], [data-testid="opening"], [data-testid="purchase"], [data-testid="reports"], [data-testid="count-workbench"], [data-testid="movements"]')).toBeNull();
});

test('stock bookmark retains the existing reader presentation', async () => {
  await render('/inventory-v2?tab=stock');
  expect(host.querySelector('[data-testid="stock"]').getAttribute('data-view')).toBe('stock');
});
test.each(['read','write'])('web stock grant %s never loads management context or mounts a writer', async accessMode => {
  await act(async()=>root.render(<MemoryRouter initialEntries={['/inventory-v2?tab=opening']}><InventoryWorkspace accessMode={accessMode}/></MemoryRouter>));
  expect(api.context).not.toHaveBeenCalled();expect(loadInventoryStocktakeContext).not.toHaveBeenCalled();
  expect(host.querySelector('[data-testid="stock"]').textContent).toBe('mezan-web-stock');
  expect(host.querySelector('[data-testid="opening"]')).toBeNull();
  expect(host.querySelector('[data-testid="purchase"]')).toBeNull();
});
test('web stock reader can review aggregate reports with the same scoped reader only', async () => {
  await act(async()=>root.render(<MemoryRouter initialEntries={['/inventory-v2?tab=reports']}><InventoryWorkspace accessMode="read"/></MemoryRouter>));
  expect(api.context).not.toHaveBeenCalled();expect(loadInventoryStocktakeContext).not.toHaveBeenCalled();
  expect(host.querySelector('[data-testid="stock"]').getAttribute('data-view')).toBe('reports');
});

test('independent native history grant works when legacy operational context is denied',async()=>{
  api.context.mockRejectedValue({response:{status:403}});
  loadInventoryStocktakeContext.mockResolvedValue({schema_version:'g47-stocktake-v1',capabilities:{view:true,count:false,review:false,post:false}});
  await render('/inventory-v2?tab=movements');
  expect(host.querySelector('[data-testid="movements"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="stock"]')).toBeNull();expect(host.querySelector('[data-testid="opening"]')).toBeNull();expect(host.querySelector('[data-testid="purchase"]')).toBeNull();
  expect(host.querySelector('[aria-label="أقسام المخزون"]').textContent).toBe('الجردحركة المخزون');
});
test('malformed native context cannot enable a history tab or legacy stock writers',async()=>{
  loadInventoryStocktakeContext.mockResolvedValue({capabilities:{view:true,count:true}});
  await render('/inventory-v2?tab=movements');
  expect(host.querySelector('[data-testid="movements"]')).toBeNull();expect(host.textContent).toContain('تعذر التحقق من صلاحيات');
});

test('count-only native reader can open saved counts without legacy context or writers',async()=>{
  api.context.mockRejectedValue({response:{status:403}});
  loadInventoryStocktakeContext.mockResolvedValue({schema_version:'g47-stocktake-v1',capabilities:{view:true,count:false,review:false,post:false}});
  await render('/inventory-v2?tab=stocktake');
  expect(host.querySelector('[data-testid="count-workbench"]').getAttribute('data-mode')).toBe('stocktake');
  expect(host.querySelector('[data-testid="opening"]')).toBeNull();expect(host.querySelector('[data-testid="stock"]')).toBeNull();
});
test('generic owner inventory permissions cannot promote the native adjustment review grant',async()=>{
  loadInventoryStocktakeContext.mockResolvedValue({schema_version:'g47-stocktake-v1',capabilities:{view:true,count:true,review:false,post:false}});
  await render('/inventory-v2?tab=adjustments');
  expect(host.querySelector('[data-testid="count-workbench"]')).toBeNull();expect(host.textContent).toContain('غير متاحة ضمن صلاحيات المخزون');
});
test('fresh explicit review capability opens native adjustment review only',async()=>{
  loadInventoryStocktakeContext.mockResolvedValue({schema_version:'g47-stocktake-v1',capabilities:{view:true,count:false,review:true,post:false}});
  await render('/inventory-v2?tab=adjustments');
  expect(host.querySelector('[data-testid="count-workbench"]').getAttribute('data-mode')).toBe('adjustments');
});
test('web read grant never fetches or mounts the native count workspace from its bookmark',async()=>{
  await act(async()=>root.render(<MemoryRouter initialEntries={['/inventory-v2?tab=stocktake']}><InventoryWorkspace accessMode="read"/></MemoryRouter>));
  expect(loadInventoryStocktakeContext).not.toHaveBeenCalled();expect(api.context).not.toHaveBeenCalled();expect(host.querySelector('[data-testid="count-workbench"]')).toBeNull();
});
