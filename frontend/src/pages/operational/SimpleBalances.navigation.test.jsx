import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import SimpleBalances from './SimpleBalances';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useOptionalAuth } from '../../context/AuthContext';

jest.mock('react-router-dom', () => {
  globalThis.TextEncoder = require('util').TextEncoder;
  globalThis.TextDecoder = require('util').TextDecoder;
  // CRA's Jest resolver predates react-router-dom's /dom export. These are
  // the same real routing primitives re-exported by react-router-dom.
  return jest.requireActual('react-router');
});
jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { context: jest.fn(), movements: jest.fn(), ledger: jest.fn(), history: jest.fn(), statement: jest.fn(), parties: jest.fn() }, simpleBalanceError: error => error?.localMessage || 'تعذر تحميل البيانات' }));
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('./SimpleBalanceReviews', () => () => <p>مراجعات</p>);
global.IS_REACT_ACT_ENVIRONMENT = true;
const employee = () => ({ kind: 'employee', id: 'employee', name: 'خالد', currency: 'SAR', access: { use: true, report: true, print: true, movements: 'all', balance: true }, balance: { visible: true, amount: '2000.00', side: 'for_party' }, operations: ['employee.payment'] });
const bank = () => ({ ...employee(), kind: 'bank', id: 'bank', name: 'بنك الإنماء', balance: { visible: true, amount: '300.00', side: 'for_us' } });
const context = () => ({ actor: { id: 'staff', name: 'محمد', is_owner: false }, session_scope: 'scope', permission_version: 1, status: 'active', operations: ['employee.payment'], resources: [employee(), bank()], capabilities: {} });
const row = (id, patch = {}) => ({ id, description: `الحركة ${id}`, business_date: '2026-11-18', recorded_at: '2026-11-18T11:35:00Z', recorded_by: { name: 'محمد' }, amount: '100.00', side: 'for_party', balance_after: '2000.00', party_type: 'employee', party_id: 'employee', party_name: 'خالد', ...patch });
const page = (number = 1, items = [row('one')], patch = {}) => ({ resource: employee(), items, page: number, page_size: 5, total: items.length, total_pages: 1, ...patch });
function Probe() { const location = useLocation(), navigate = useNavigate(); return <><output data-testid="location">{location.search}</output><button type="button" onClick={() => navigate(-1)} aria-label="رجوع المتصفح">اختبار الرجوع</button></>; }
let host, root;
const render = async search => act(async () => root.render(<MemoryRouter initialEntries={[`/operational-balances/daily-movements${search || ''}`]}><SimpleBalances /><Probe /></MemoryRouter>));
const renderBrowser = async search => {
  window.history.replaceState(null, '', `/operational-balances/daily-movements${search || ''}`);
  await act(async () => root.render(<BrowserRouter><SimpleBalances /><Probe /></BrowserRouter>));
};
const traverseBrowser = async action => act(async () => {
  await new Promise((resolve, reject) => {
    const finish = () => { clearTimeout(timeout); resolve(); };
    const timeout = setTimeout(() => { window.removeEventListener('popstate', finish); reject(new Error('Browser history did not traverse')); }, 1000);
    window.addEventListener('popstate', finish, { once: true });
    action();
  });
});
const click = async (text, within = host) => act(async () => {
  const button = [...within.querySelectorAll('button')].find(item => !item.disabled && item.textContent.includes(text));
  if (!button) throw new Error(`Missing ${text}`); button.click();
});
const selectAccount = async value => act(async () => {
  const select = host.querySelector('.sb-daily-tools select');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, value);
  select.dispatchEvent(new Event('change', { bubbles: true }));
});
beforeEach(() => {
  jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  useOptionalAuth.mockReturnValue({ user: { id: 'staff' } });
  api.context.mockResolvedValue(context());
  api.ledger.mockImplementation((kind, id, query) => Promise.resolve(page(query.page, [row('one')], { resource: context().resources.find(item => item.kind === kind && item.id === id) })));
  api.movements.mockImplementation(query => Promise.resolve(page(query.page, [row(`page-${query.page}`, { party_type: 'bank', party_id: 'bank', party_name: 'بنك الإنماء' })], { total: 6, total_pages: 2 })));
  api.parties.mockResolvedValue({ items: context().resources });
  api.statement.mockImplementation((kind, id, query) => Promise.resolve({ resource: employee(), ...query, items: [row('statement')], scope: 'all', currency: 'SAR', opening_balance: '1900.00', closing_balance: '2000.00', totals: { for_party: '100.00', for_us: '0.00' }, can_print: true }));
  window.print = jest.fn();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('account filtering and its page survive a real router Back from movement details', async () => {
  await render(); await selectAccount(JSON.stringify(['bank', 'bank'])); await click('التالي');
  const beforeOpen = api.movements.mock.calls.length;
  await act(async () => host.querySelector('.sb-row-button').click());
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  expect(api.movements).toHaveBeenCalledTimes(beforeOpen);
  expect(host.querySelector('[data-testid="location"]').textContent).toContain('dialog=movement');
  await act(async () => host.querySelector('[aria-label="رجوع المتصفح"]').click());
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(host.querySelector('.sb-daily-tools select').value).toBe(JSON.stringify(['bank', 'bank']));
  expect(api.movements).toHaveBeenLastCalledWith({ page: 2, page_size: 5, kind: 'bank', id: 'bank' }, expect.any(Object));
});

test('the latest automatic row opens monthly history while manual payments remain separate', async () => {
  const latest = row('salary-latest', { automatic_history: 'salary', history_count: 12 });
  api.ledger.mockResolvedValue(page(1, [latest, row('cash-one', { description: 'سحب أول', side: 'for_us' }), row('cash-two', { description: 'سحب ثان', side: 'for_us' })]));
  api.history.mockImplementation((kind, id, query) => Promise.resolve(page(query.page, [row(`day-${query.page}`)], { automatic_history: 'salary', current_month: '2026-11', current_month_total: '1000.00', history_count: 12, total: 10, total_pages: 2, previous_months: [{ month: '2026-10', count: 2, amount: '200.00', currency: 'SAR' }] })));
  await render('?tab=parties&kind=employee&id=employee');
  expect(host.querySelectorAll('[data-testid="simple-movement-row"]')).toHaveLength(3);
  expect(host.textContent).toContain('سحب أول'); expect(host.textContent).toContain('سحب ثان');
  await click('آخر راتب مسجل');
  let modal = host.querySelector('[role="dialog"]');
  expect(modal.textContent).toContain('2026-10'); expect(modal.textContent).toContain('200 SAR');
  expect(api.ledger).toHaveBeenCalledTimes(1);
  await click('التالي', modal);
  expect(api.history).toHaveBeenLastCalledWith('employee', 'employee', { page: 2, page_size: 5 }, expect.any(Object));
  modal = host.querySelector('[role="dialog"]'); await act(async () => modal.querySelector('.sb-row-button').click());
  expect(host.querySelector('[role="dialog"]').textContent).toContain('تفاصيل الحركة');
  await click('رجوع', host.querySelector('[role="dialog"]'));
  expect(host.querySelector('[role="dialog"]').textContent).toContain('سجل الرواتب');
  expect(host.querySelector('[data-testid="location"]').textContent).toContain('dialog_page=2');
  await click('رجوع', host.querySelector('[role="dialog"]'));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

test('BrowserRouter returns from detail via browser Back then closes history via its Back button', async () => {
  api.ledger.mockResolvedValue(page(1, [row('salary-latest', { automatic_history: 'salary', history_count: 12 })]));
  api.history.mockImplementation((kind, id, query) => Promise.resolve(page(query.page, [row(`day-${query.page}`, { automatic_history: 'salary', recorded_by: null })], { automatic_history: 'salary', current_month: '2026-11', current_month_total: '1000.00', history_count: 12, total: 10, total_pages: 2, previous_months: [] })));
  await renderBrowser('?tab=parties&kind=employee&id=employee');
  const origin = window.location.search;
  await click('آخر راتب مسجل');
  await click('التالي', host.querySelector('[role="dialog"]'));
  expect(host.querySelector('[role="dialog"]').textContent).toContain('سجّلها: النظام تلقائيًا');
  const historyUrl = window.location.search;
  await act(async () => host.querySelector('[role="dialog"] .sb-row-button').click());
  expect(host.querySelector('[role="dialog"]').textContent).toContain('تفاصيل الحركة');
  await traverseBrowser(() => window.history.back());
  expect(window.location.search).toBe(historyUrl);
  expect(host.querySelector('[role="dialog"]').textContent).toContain('سجل الرواتب');
  await traverseBrowser(() => host.querySelector('[role="dialog"] > header button').click());
  expect(window.location.search).toBe(origin);
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.style.overflow).not.toBe('hidden');
});

test('a statement dialog suspends the background ledger and requests data only after submit', async () => {
  await render('?tab=parties&kind=employee&id=employee&date_from=2026-11-01&date_to=2026-11-19');
  expect(host.querySelector('.sb-header').textContent).toContain('خالد');
  expect(host.querySelector('.sb-header').textContent).toContain('+ إضافة');
  await click('كشف فترة');
  expect(api.statement).not.toHaveBeenCalled(); expect(api.ledger).toHaveBeenCalledTimes(1);
  await click('عرض الكشف', host.querySelector('[role="dialog"]'));
  expect(api.statement).toHaveBeenCalledTimes(1); expect(api.ledger).toHaveBeenCalledTimes(1);
  await act(async () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

test.each(['sections', 'reports'])('inactive %s panel never starts the daily or account ledger', async panel => {
  await render(`?panel=${panel}`);
  expect(api.movements).not.toHaveBeenCalled(); expect(api.ledger).not.toHaveBeenCalled(); expect(api.statement).not.toHaveBeenCalled();
});

test('a removed filter is denied rather than silently falling back to all accounts', async () => {
  await render('?tab=movements&account_kind=bank&account_id=removed');
  expect(api.movements).not.toHaveBeenCalled(); expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test('an old account read is aborted and cannot populate the newly selected account', async () => {
  let resolveFirst;
  api.movements.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve; }));
  await render(); const signal = api.movements.mock.calls[0][1].signal;
  await selectAccount(JSON.stringify(['bank', 'bank']));
  expect(signal.aborted).toBe(true);
  await act(async () => resolveFirst(page(1, [row('private-old', { amount: '99999.00' })])));
  expect(host.textContent).not.toContain('99,999');
});
