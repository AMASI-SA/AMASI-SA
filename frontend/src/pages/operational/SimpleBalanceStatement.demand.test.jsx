import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import http from '../../lib/api';
import SimpleBalanceStatement from './SimpleBalanceStatement';

// Keep the real statement API and demand transport. Only the HTTP boundary is fake.
jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const account = { kind: 'employee', id: 'synthetic-employee', name: 'موظف تجريبي', currency: 'SAR',
  access: { report: true, print: true, balance: true, movements: 'all' } };
const context = { actor: { id: 'synthetic-actor', is_owner: false }, session_scope: 'synthetic-scope',
  permission_version: 1, resources: [account] };
const period = { date_from: '2030-04-01', date_to: '2030-04-24' };
const report = (patch = {}) => ({ resource: account, ...period, scope: 'all', currency: 'SAR',
  opening_balance: '0.00', closing_balance: '301.00', totals: { for_party: '301.00', for_us: '0.00' },
  can_print: true, prepared_by: { id: 'synthetic-actor', name: 'مراجع تجريبي' }, generated_at: '2030-04-24T12:00:00Z',
  items: Array.from({ length: 301 }, (_, i) => ({ id: `synthetic-row-${i}`, amount: '1.00',
    side: 'for_party', business_date: '2030-04-23', balance_after: `${i + 1}.00` })), ...patch });
const preparing = key => ({ status: 202, data: { status: 'preparing', key, retry_after_ms: 1500,
  release_url: `/operational-balances/workspace/read-demand/${key}` } });
let host, root;
const render = async (ctx = context) => act(async () => root.render(<SimpleBalanceStatement
  context={ctx} identity="synthetic-actor" resource={account} initialFilters={period} />));
const click = async label => act(async () => [...host.querySelectorAll('button')]
  .find(button => button.textContent.includes(label)).click());
const tick = async () => act(async () => { jest.advanceTimersByTime(500); await Promise.resolve(); });

beforeEach(() => {
  jest.useFakeTimers(); jest.resetAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  http.get.mockResolvedValue({ status: 200, data: report() });
  http.delete.mockResolvedValue({ status: 200, data: {} });
  window.print = jest.fn();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); jest.useRealTimers(); });

test('an explicit statement waits for a saved background result and renders every returned row', async () => {
  http.get.mockResolvedValueOnce(preparing('view-claim'));
  await render(); expect(http.get).not.toHaveBeenCalled();
  await click('عرض الكشف');
  expect(host.querySelector('.sb-statement-document')).toBeNull();
  expect(host.textContent).toContain('جارٍ إعداد الكشف');
  await tick();
  expect(host.querySelectorAll('.sb-statement-table tbody tr')).toHaveLength(301);
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(http.get.mock.calls[0][1].params).toEqual({ ...period, purpose: 'view', demand: true });
  expect(http.get.mock.calls[1][1].headers['X-Mezan-Read-Poll']).toBe('1');
  expect(http.get.mock.calls[1][1].headers['X-Mezan-Read-Consumer'])
    .toBe(http.get.mock.calls[0][1].headers['X-Mezan-Read-Consumer']);
  expect(http.delete).toHaveBeenCalledWith('/operational-balances/workspace/read-demand/view-claim',
    expect.objectContaining({ params: { consumer_id: expect.any(String) } }));
  expect(http.post).not.toHaveBeenCalled(); expect(http.put).not.toHaveBeenCalled();
});

test('printing obtains its own authorized saved result before opening the print dialog', async () => {
  await render(); await click('عرض الكشف');
  http.get.mockResolvedValueOnce(preparing('print-claim'))
    .mockResolvedValueOnce({ status: 200, data: report({ closing_balance: '7.00',
      items: [{ id: 'fresh-print', amount: '7.00', side: 'for_party', note: 'نتيجة الطباعة الجديدة' }] }) });
  let printed;
  window.print.mockImplementation(() => { printed = document.querySelector('.sb-print-root')?.textContent; });
  await click('طباعة'); expect(window.print).not.toHaveBeenCalled();
  await tick();
  expect(window.print).toHaveBeenCalledTimes(1); expect(printed).toContain('نتيجة الطباعة الجديدة');
  expect(http.get.mock.calls[1][1].params).toEqual({ ...period, purpose: 'print', demand: true });
  expect(http.get.mock.calls[1][1].headers['X-Mezan-Read-Consumer'])
    .not.toBe(http.get.mock.calls[0][1].headers['X-Mezan-Read-Consumer']);
  expect(http.post).not.toHaveBeenCalled();
});

test.each(['hidden', 'unmounted', 'permission changed'])('%s releases preparation and stops polling', async mode => {
  http.get.mockResolvedValueOnce(preparing('cancel-claim'));
  await render(); await click('عرض الكشف');
  const signal = http.get.mock.calls[0][1].signal;
  if (mode === 'hidden') {
    await act(async () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
  } else if (mode === 'unmounted') {
    await act(async () => root.render(null));
  } else {
    await render({ ...context, permission_version: 2,
      resources: [{ ...account, access: { ...account.access, report: false } }] });
  }
  await act(async () => { jest.advanceTimersByTime(10000); await Promise.resolve(); });
  expect(signal.aborted).toBe(true); expect(http.get).toHaveBeenCalledTimes(1);
  expect(http.delete).toHaveBeenCalledTimes(1);
  expect(http.delete.mock.calls[0][0]).toBe('/operational-balances/workspace/read-demand/cancel-claim');
  expect(host.querySelector('.sb-statement-document')).toBeNull();
  expect(window.print).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});

test('authorization failure after preparation never exposes or prints a stale result', async () => {
  http.get.mockResolvedValueOnce(preparing('denied-claim'))
    .mockRejectedValueOnce({ response: { status: 403 } });
  await render(); await click('عرض الكشف'); await tick();
  expect(host.querySelector('.sb-statement-document')).toBeNull();
  expect(host.textContent).toContain('لا توجد صلاحية');
  expect(http.delete).toHaveBeenCalledTimes(1);
  expect(window.print).not.toHaveBeenCalled(); expect(http.post).not.toHaveBeenCalled();
});
