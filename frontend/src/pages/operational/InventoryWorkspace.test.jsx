import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import InventoryWorkspace from '../InventoryWorkspace';
import {operationalApi as api} from './api';
jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('./api', () => ({operationalApi:{context:jest.fn()}, messageFor:() => 'تعذر تحميل الصلاحيات'}));
jest.mock('./InventoryAvailability', () => ({context}) => <div data-testid="stock">{context.session_scope}</div>);
jest.mock('./StandaloneInventoryEntry', () => () => <div data-testid="opening" />);
jest.mock('./InventoryPurchases', () => ({mode='purchase'}) => <div data-testid={mode} />);
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  api.context.mockResolvedValue({session_scope:'owner',status:'active',permissions:{reports:true,move:true},can_manage_inventory_opening:true});
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllMocks(); });
const render = async (route='/inventory-v2') => act(async () => root.render(<MemoryRouter initialEntries={[route]}><InventoryWorkspace /></MemoryRouter>));
test('starts with stock only and preserves explicit deferred order integration', async () => {
  await render(); expect(host.querySelector('[data-testid="stock"]')).not.toBeNull();
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
test.each(['opening','purchase','purchases','stock'])('direct tab %s cannot bypass permissions', async tab => {
  api.context.mockResolvedValue({session_scope:'limited',permissions:{}});
  await render('/inventory-v2?tab='+tab);
  expect(host.querySelector('[data-testid]')).toBeNull();
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
  expect(host.querySelector('[data-testid]')).toBeNull();
});
