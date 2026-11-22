import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('../../lib/api', () => ({ delete: jest.fn().mockResolvedValue({ data: {} }) }));
jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: {
  bootstrap: jest.fn(), home: jest.fn(), directory: jest.fn(), options: jest.fn(), selection: jest.fn(), context: jest.fn(),
}, simpleBalanceError: () => 'تعذر تحميل البيانات.' }));

global.IS_REACT_ACT_ENVIRONMENT = true;
const flush = async () => { for (let n = 0; n < 16; n += 1) await Promise.resolve(); };
const bootstrap = id => ({ actor: { id, name: id, is_owner: true }, session_scope: 'scope-' + id, permission_version: 1,
  status: 'active', resources: [], operations: [], capabilities: { create_entries: false } });
const resource = (id, amount = '10.00') => ({ kind: 'bank', id, name: id, currency: 'SAR', access: { balance: true, movements: 'all' }, balance: { visible: true, side: 'for_us', amount } });
const home = (id = 'actor') => ({ session_scope: 'scope-' + id, permission_version: 1,
  summary: { currency: 'SAR', bank: '610.34', cash: '200.00', total: '810.34', cash_count: 2, cash_balance_visible: true },
  resources: [resource('بطاقة أولى')], recent_movements: [], has_order_issues: false });
const page = (items, current = 1, total = items.length) => ({ items, page: current, page_size: 24, total,
  pages: Math.max(1, Math.ceil(total / 24)), session_scope: 'scope-actor', permission_version: 1 });
let host, root;
beforeEach(() => {
  jest.clearAllMocks(); jest.useFakeTimers();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  window.scrollTo = jest.fn(); useOptionalAuth.mockReturnValue({ user: { id: 'actor' }, loading: false });
  api.bootstrap.mockResolvedValue(bootstrap('actor')); api.home.mockResolvedValue(home());
  api.directory.mockResolvedValue(page([])); api.options.mockResolvedValue(page([]));
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });
const open = async url => act(async () => { root.render(<MemoryRouter initialEntries={[url || '/operational-balances']}><ApprovedFinance/></MemoryRouter>); await flush(); });
const go = async view => act(async () => { host.querySelector(`nav.nav a[href$="view=${view}"]`).click(); await flush(); });

test('home requests only bootstrap and exact compact home, never totals its card subset', async () => {
  await open();
  expect(api.bootstrap).toHaveBeenCalledTimes(1); expect(api.home).toHaveBeenCalledTimes(1);
  expect(api.context).not.toHaveBeenCalled(); expect(api.directory).not.toHaveBeenCalled(); expect(api.options).not.toHaveBeenCalled();
  expect(host.querySelector('.hero .big').textContent).toContain('810.34');
  expect(host.querySelectorAll('.homecards .resource')).toHaveLength(1);
});

test('opening a directory directly never prepares home or other pages', async () => {
  await open('/operational-balances?view=parties');
  expect(api.directory).toHaveBeenCalledWith({ group: 'parties', page: 1, page_size: 24 }, expect.objectContaining({ demand: true }));
  expect(api.home).not.toHaveBeenCalled(); expect(api.context).not.toHaveBeenCalled(); expect(api.options).not.toHaveBeenCalled();
});

test('navigating away aborts a pending home and its late result cannot populate the directory', async () => {
  let finish;
  api.home.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await open();
  expect(host.querySelector('.side')).not.toBeNull(); expect(host.textContent).toContain('جارٍ تحميل ملخص');
  const signal = api.home.mock.calls[0][0].signal;
  await go('assets'); expect(signal.aborted).toBe(true);
  await act(async () => { finish(home()); await flush(); });
  expect(host.querySelector('.hero')).toBeNull(); expect(host.textContent).not.toContain('بطاقة أولى');
  expect(api.home).toHaveBeenCalledTimes(1); expect(api.directory).toHaveBeenCalledTimes(1);
});

test('directory pagination and search go to the server without loading a full catalog', async () => {
  api.directory.mockImplementation(async params => page([resource(params.q || `حساب صفحة ${params.page}`)], params.page, params.q ? 1 : 25));
  await open('/operational-balances?view=assets');
  await act(async () => { [...host.querySelectorAll('button')].find(button => button.textContent === 'التالي').click(); await flush(); });
  expect(api.directory.mock.calls.at(-1)[0]).toEqual({ group: 'assets', page: 2, page_size: 24 });
  const input = host.querySelector('input[type="search"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'بحث');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => { jest.advanceTimersByTime(350); await flush(); });
  expect(api.directory.mock.calls.at(-1)[0]).toEqual({ group: 'assets', page: 1, page_size: 24, q: 'بحث' });
  expect(host.textContent).not.toContain('حساب صفحة 2'); expect(api.context).not.toHaveBeenCalled();
});

test('an identity change drops the previous bootstrap and home binding', async () => {
  await open();
  api.bootstrap.mockResolvedValue(bootstrap('next')); api.home.mockResolvedValue(home('actor'));
  useOptionalAuth.mockReturnValue({ user: { id: 'next' }, loading: false });
  await act(async () => { root.render(<MemoryRouter><ApprovedFinance/></MemoryRouter>); await flush(); });
  expect(host.querySelector('.hero')).toBeNull(); expect(host.querySelector('[role="alert"]')).not.toBeNull();
});

test('a changed permission binding requires a fresh bootstrap on explicit retry', async () => {
  api.bootstrap.mockResolvedValueOnce(bootstrap('actor')).mockResolvedValue({...bootstrap('actor'),permission_version:2});
  api.home.mockResolvedValue({...home(),permission_version:2});
  await open();
  expect(host.querySelector('.hero')).toBeNull();
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  await act(async()=>{[...host.querySelectorAll('button')].find(button=>button.textContent==='إعادة المحاولة').click();await flush();});
  expect(api.bootstrap).toHaveBeenCalledTimes(2);
  expect(api.home).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.hero .big').textContent).toContain('810.34');
  expect(api.home.mock.calls[0][0].readDemand).not.toBe(api.home.mock.calls[1][0].readDemand);
});

test('a selected resource from a different scope is rejected before its ledger opens', async () => {
  api.selection.mockResolvedValue({...resource('scoped'),session_scope:'other-account',permission_version:1});
  await open('/operational-balances?view=ledger&kind=bank&id=scoped');
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.querySelector('.ledger')).toBeNull();
  expect(api.home).not.toHaveBeenCalled(); expect(api.directory).not.toHaveBeenCalled();
});

test('saved source time and display preparation time remain separate', async () => {
  api.home.mockResolvedValue({...home(),snapshot:{display_only:true,as_of:'2026-11-18T12:00:00Z',generated_at:'2026-11-22T09:00:00Z'}});
  await open();
  const metadata=host.querySelector('[aria-label="تاريخ البيانات المحفوظة"]');
  expect(metadata.textContent).toContain('آخر احتساب للمصادر');
  expect(metadata.textContent).toContain('تجهيز هذا العرض');
  expect(metadata.textContent).toContain('18');expect(metadata.textContent).toContain('22');
});
