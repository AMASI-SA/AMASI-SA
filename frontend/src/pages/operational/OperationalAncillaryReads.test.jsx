import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import SupplierReturn from './SupplierReturn';
import DailyMovements from './DailyMovements';
import {EntityPicker} from './OpeningBalances';
import SalaryStart from './SalaryStart';
import OpeningInventory from './OpeningInventory';
import InventoryAvailability from './InventoryAvailability';
import InventoryPurchases from './InventoryPurchases';
import {operationalApi as api} from './api';

jest.mock('./api', () => ({operationalApi: {
  entities: jest.fn(), obligations: jest.fn(), salaryStartContext: jest.fn(), inventoryOpeningContext: jest.fn(),
  inventoryAvailability: jest.fn(), inventoryCatalog: jest.fn(), inventoryPurchases: jest.fn(), inventoryPurchaseEntry: jest.fn(),
  reports: jest.fn(), receipt: jest.fn(), supplierReturn: jest.fn(), context: jest.fn(), movement: jest.fn(),
}, entityKinds: [['supplier', 'موردين']], requestId: () => 'fixed-command', messageFor: () => 'تعذر التحميل'}));

const context = {session_scope: 'a'.repeat(64), status: 'active', started_at: '2026-11-01T00:00:00+03:00',
  permissions: {move: true, reports: true}, can_manage_salary_start: true, can_manage_inventory_opening: true};
const invoice = {id: 'invoice', supplier_id: 'supplier', supplier_name: 'مورد', invoice_number: 'INV1', invoice_date: '2026-11-01',
  obligation_id: 'invoice-debt', net: '10.00', tax: '0.00', gross: '10.00', settled: '0.00', outstanding: '10.00', lines: []};
const report = (amount = '5.00') => ({details: {facts: {'receipt:invoice': {amount}}},
  obligations: [{evidence_ids: ['receipt:invoice'], party_id: 'supplier', currency: 'SAR'}],
  parties: [{party_type: 'supplier', party_id: 'supplier', name: 'مورد'}]});
const readNames = ['entities', 'obligations', 'salaryStartContext', 'inventoryOpeningContext', 'inventoryAvailability',
  'inventoryCatalog', 'inventoryPurchases', 'inventoryPurchaseEntry', 'reports'];
const deferred = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
let host, root, visibility;
const render = async element => {await act(async () => root.render(element));};
const visible = async value => {visibility.mockReturnValue(value); await act(async () => document.dispatchEvent(new Event('visibilitychange')));};
const click = async text => {await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === text).click());};
const toggle = async open => {await act(async () => {const details = host.querySelector('details'); details.open = open; details.dispatchEvent(new Event('toggle'));});};
const change = async (label, value) => {await act(async () => {
  const input = [...host.querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label).querySelector('input,select');
  Object.getOwnPropertyDescriptor(input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
});};
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.resetAllMocks(); localStorage.clear(); sessionStorage.clear();
  visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  jest.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  api.entities.mockImplementation(kind => Promise.resolve({items: [{id: kind, name: kind, currency: 'SAR'}]}));
  api.obligations.mockResolvedValue({items: []}); api.inventoryCatalog.mockResolvedValue({items: []});
  api.inventoryPurchases.mockResolvedValue({items: [invoice], stock: []}); api.inventoryPurchaseEntry.mockResolvedValue(invoice);
  api.reports.mockResolvedValue(report()); api.context.mockResolvedValue(context); api.receipt.mockResolvedValue({id: 'evidence'});
});
afterEach(async () => {await act(async () => root.unmount()); host.remove(); jest.restoreAllMocks();});

test.each([
  ['entity picker', () => <EntityPicker kind="supplier" onChange={() => {}}/>, 'entities', 1],
  ['daily movement accounts', () => <DailyMovements context={context}/>, 'entities', 3],
  ['salary start', () => <SalaryStart context={context}/>, 'salaryStartContext', 1],
  ['opening inventory', () => <OpeningInventory context={context}/>, 'inventoryOpeningContext', 1],
  ['inventory availability', () => <InventoryAvailability context={context}/>, 'inventoryAvailability', 1],
  ['purchase entry', () => <InventoryPurchases context={context}/>, 'inventoryCatalog', 1],
  ['supplier payment', () => <InventoryPurchases context={context} mode="payment"/>, 'entities', 4],
])('%s defers hidden reads, aborts on hide and does not survive unmount', async (_, element, name, count) => {
  const pending = deferred(); api[name].mockImplementation(() => pending.promise);
  visibility.mockReturnValue('hidden'); await render(element());
  expect(readNames.reduce((n, key) => n + api[key].mock.calls.length, 0)).toBe(0);
  await visible('visible'); expect(api[name]).toHaveBeenCalledTimes(count);
  const signals = api[name].mock.calls.map(args => args[args.length - 1].signal);
  signals.forEach(signal => expect(signal).toBeInstanceOf(AbortSignal));
  await visible('hidden'); signals.forEach(signal => expect(signal.aborted).toBe(true));
  await act(async () => window.dispatchEvent(new Event('focus')));
  expect(api[name]).toHaveBeenCalledTimes(count);
  await act(async () => root.render(null));
  await act(async () => pending.resolve({items: [], stock: []}));
  await visible('visible'); expect(api[name]).toHaveBeenCalledTimes(count);
  expect(api.movement).not.toHaveBeenCalled(); expect(api.supplierReturn).not.toHaveBeenCalled();
});

test('closed supplier return does not read reports and reopening waits for the cancelled read', async () => {
  const first = deferred(); api.reports.mockImplementationOnce(() => first.promise);
  await render(<SupplierReturn/>); await act(async () => window.dispatchEvent(new Event('focus')));
  expect(api.reports).not.toHaveBeenCalled();
  await toggle(true); const signal = api.reports.mock.calls[0][0].signal;
  await toggle(false); expect(signal.aborted).toBe(true);
  await toggle(true); expect(api.reports).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve(report('99.00')));
  expect(api.reports).toHaveBeenCalledTimes(2); expect(host.textContent).not.toContain('99.00');
  expect(host.textContent).toContain('5.00');
  await act(async () => window.dispatchEvent(new Event('focus'))); expect(api.reports).toHaveBeenCalledTimes(2);
  await click('تحديث فواتير المورد'); expect(api.reports).toHaveBeenCalledTimes(3);
});

test('closing supplier details does not cancel an already submitted return', async () => {
  const saved = deferred(); api.supplierReturn.mockImplementation(() => saved.promise);
  await render(<SupplierReturn/>); await toggle(true);
  await change('فاتورة المورد', 'invoice'); await change('قيمة المرتجع', '3'); await change('سبب المرتجع', 'قبول موثق');
  await act(async () => {const input = host.querySelector('input[type="file"]');
    Object.defineProperty(input, 'files', {value: [new File(['proof'], 'proof.pdf', {type: 'application/pdf'})]});
    input.dispatchEvent(new Event('change', {bubbles: true}));
  });
  await click('حفظ المرتجع'); expect(api.supplierReturn).toHaveBeenCalledTimes(1);
  expect(api.supplierReturn.mock.calls[0]).toHaveLength(1);
  await toggle(false); await visible('hidden'); await act(async () => saved.resolve({ok: true}));
  expect(host.textContent).toContain('تم تسجيل قبول المورد للمرتجع');
  expect(api.supplierReturn).toHaveBeenCalledTimes(1); expect(api.reports).toHaveBeenCalledTimes(1);
});

test('submitted supplier payment finishes while hidden and its report read waits for visibility', async () => {
  const saved = deferred(); api.movement.mockImplementation(() => saved.promise);
  await render(<InventoryPurchases context={context} mode="payment"/>);
  await click('سداد الفاتورة INV1'); await change('مصدر السداد', 'bank:bank'); await change('مبلغ السداد', '3');
  await click('حفظ السداد'); expect(api.movement).toHaveBeenCalledTimes(1);
  expect(api.movement.mock.calls[0]).toHaveLength(1);
  const body = api.movement.mock.calls[0][0];
  expect(body.allocations).toEqual([{obligation_id: 'invoice-debt', amount: '3'}]);
  await visible('hidden'); await act(async () => saved.resolve({id: 'saved-movement'}));
  expect(localStorage.length).toBe(0); expect(api.inventoryPurchases).toHaveBeenCalledTimes(1);
  expect(api.movement).toHaveBeenCalledTimes(1);
  await visible('visible'); expect(api.inventoryPurchases).toHaveBeenCalledTimes(2);
  await act(async () => window.dispatchEvent(new Event('focus'))); expect(api.inventoryPurchases).toHaveBeenCalledTimes(2);
});
