import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('../../lib/api', () => ({ delete: jest.fn().mockResolvedValue({ data: {} }) }));
jest.mock('../../lib/dates', () => ({ ...jest.requireActual('../../lib/dates'), todaySA: () => '2026-11-22' }));
jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: {
  bootstrap: jest.fn(), home: jest.fn(), directory: jest.fn(), options: jest.fn(), selection: jest.fn(), context: jest.fn(),
  ledger: jest.fn(), movements: jest.fn(), resource: jest.fn(), statement: jest.fn(),
  customerAdjustments: jest.fn(),
}, simpleBalanceError: () => 'تعذر تحميل البيانات.' }));

global.IS_REACT_ACT_ENVIRONMENT = true;
const flush = async () => { for (let n = 0; n < 16; n += 1) await Promise.resolve(); };
const bootstrap = id => ({ actor: { id, name: id, is_owner: true }, session_scope: 'scope-' + id, permission_version: 1,
  status: 'active', resources: [], operations: [], capabilities: { create_entries: false } });
const resource = (id, amount = '10.00') => ({ kind: 'bank', id, name: id, currency: 'SAR', access: { balance: true, movements: 'all' }, balance: { visible: true, side: 'for_us', amount } });
const ownerResource = (id, extra = {}) => ({ ...resource(id), operations: ['bank.transfer'],
  access: { use: true, balance: true, movements: 'all', report: true, print: true }, ...extra });
const home = (id = 'actor') => ({ session_scope: 'scope-' + id, permission_version: 1,
  summary: { currency: 'SAR', bank: '610.34', cash: '200.00', total: '810.34', cash_count: 2, cash_balance_visible: true },
  resources: [resource('بطاقة أولى')], recent_movements: [], has_order_issues: false });
const page = (items, current = 1, total = items.length) => ({ items, page: current, page_size: 24, total,
  pages: Math.max(1, Math.ceil(total / 24)), session_scope: 'scope-actor', permission_version: 1 });
const ledgerPage = (item, extra = {}) => ({ resource: item, items: [], total: 0, page: 1, page_size: 8, total_pages: 1,
  session_scope: 'scope-actor', permission_version: 1, owner_override: true, ...extra });
let host, root;
beforeEach(() => {
  jest.clearAllMocks(); jest.useFakeTimers();
  api.selection.mockReset(); api.ledger.mockReset(); api.movements.mockReset(); api.resource.mockReset(); api.statement.mockReset();
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

test('reviews use bootstrap capabilities and retain customer adjustment links without loading accounts', async () => {
  api.bootstrap.mockResolvedValue({...bootstrap('actor'),
    operations:['customer.refund_execute','customer.collection','courier.review'],
    capabilities:{review_orders:true,review_collections:true}});
  await open('/operational-balances?view=reviews');
  const links=[...host.querySelectorAll('main a')].map(anchor=>anchor.getAttribute('href'));
  for(const view of ['refund','customer-collections','issues','collections']) expect(links).toContain('/operational-balances?view='+view);
  expect(host.textContent).toContain('مبالغ العملاء بانتظار التأكيد');
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  for(const read of [api.home,api.directory,api.options,api.context,api.selection]) expect(read).not.toHaveBeenCalled();
});

test('demand provider cards retain snapshot dates and separate refund and collection amounts', async () => {
  const provider={...resource('تابي','500.00'),kind:'provider',access:{balance:true,movements:'all',report:true},order_issues_count:0,
    customer_adjustments:{currency:'SAR',refund_pending_count:2,refund_pending_amount:'100.00',collection_pending_count:1,collection_pending_amount:'25.00'}};
  api.directory.mockResolvedValue({...page([provider]),snapshot:{display_only:true,as_of:'2026-11-18T12:00:00Z',generated_at:'2026-11-22T09:00:00Z'}});
  await open('/operational-balances?view=assets');
  expect(host.querySelector('[aria-label="تاريخ البيانات المحفوظة"]').textContent).toContain('آخر احتساب للمصادر');
  const card=host.querySelector('.resource');
  expect(card.querySelector('.number').textContent).toContain('500.00');
  expect(card.querySelector('.adjustment-badge.refund').textContent).toContain('2 طلب · رد ينتظر التأكيد 100.00');
  expect(card.querySelector('.adjustment-badge.collection').textContent).toContain('1 طلب · تحصيل ينتظر التأكيد 25.00');
  expect(card.querySelector('.issue-badge')).toBeNull();
  expect(api.directory).toHaveBeenCalledTimes(1);expect(api.home).not.toHaveBeenCalled();expect(api.context).not.toHaveBeenCalled();
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
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), actor: { id: 'actor', is_owner: false } });
  api.selection.mockResolvedValue({...resource('scoped'),session_scope:'other-account',permission_version:1});
  await open('/operational-balances?view=ledger&kind=bank&id=scoped');
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.querySelector('.ledger')).toBeNull();
  expect(api.selection).toHaveBeenCalledTimes(1); expect(api.ledger).not.toHaveBeenCalled();
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

test('an owner opens one ledger without a selection read and exposes only its validated resource', async () => {
  const item = ownerResource('direct-bank', { name: 'حساب موثق من الكشف' });
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), ledger_start: '2026-11-01',
    operations: ['bank.transfer'], capabilities: { create_entries: true } });
  api.selection.mockImplementation(() => new Promise(() => {}));
  let finishLedger;
  api.ledger.mockImplementationOnce(() => new Promise(resolve => { finishLedger = resolve; }));

  await open('/operational-balances?view=ledger&kind=bank&id=direct-bank');
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.selection).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain(item.name);
  expect(host.textContent).not.toContain('سجّل حركة');
  expect(host.querySelector('.metrics')).toBeNull();

  await act(async () => { finishLedger(ledgerPage(item)); await flush(); });
  expect(host.textContent).toContain(item.name);
  expect(host.textContent).toContain('سجّل حركة');
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.selection).not.toHaveBeenCalled();
  expect(api.home).not.toHaveBeenCalled();
  expect(api.directory).not.toHaveBeenCalled();
  expect(api.movements).not.toHaveBeenCalled();
});

test.each([
  ['refund', 'مراجعة رد مبالغ العملاء', 'إرجاع مبلغ لعميل'],
  ['collection', 'مراجعة تحصيل المتبقي على العملاء', 'تحصيل متبقي طلب'],
])('one owner provider ledger reveals validated customer adjustments before opening %s review', async (direction, action, title) => {
  const summary = { currency: 'SAR', refund_pending_count: 2, refund_pending_amount: '100.00',
    collection_pending_count: 1, collection_pending_amount: '25.00' };
  const item = ownerResource('tabby', { kind: 'provider', name: 'تابي من الكشف الموثق',
    operations: ['provider.settle'], provider_position: { customer_adjustments: summary } });
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), operations: ['customer.refund_execute', 'customer.collection'] });
  api.customerAdjustments.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 8, total_pages: 1,
    summary: { ...summary, unknown_count: 0 } });
  let finishLedger;
  api.ledger.mockImplementationOnce(() => new Promise(resolve => { finishLedger = resolve; }));

  await open('/operational-balances?view=ledger&kind=provider&id=tabby');
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({ provider_orders: true, page: 1, page_size: 8 });
  expect(api.selection).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain(item.name);
  expect(host.querySelector('.adjustment-badges, .customer-adjustment-links')).toBeNull();
  expect(api.customerAdjustments).not.toHaveBeenCalled();

  await act(async () => { finishLedger(ledgerPage(item, { provider_orders: true,
    snapshot: { display_only: true, as_of: '2026-11-18T12:00:00Z', generated_at: '2026-11-22T09:00:00Z' } })); await flush(); });
  const actions = host.querySelector('[aria-label="رد المبالغ وتحصيل المتبقي"]');
  expect(host.querySelector('h1').textContent).toContain(item.name);
  expect(actions.querySelector('.adjustment-badge.refund').textContent).toContain('2 طلب · رد ينتظر التأكيد 100.00');
  expect(actions.querySelector('.adjustment-badge.collection').textContent).toContain('1 طلب · تحصيل ينتظر التأكيد 25.00');
  expect([...actions.querySelectorAll('button')].map(button => button.textContent)).toEqual([
    'مراجعة رد مبالغ العملاء', 'مراجعة تحصيل المتبقي على العملاء']);
  expect(host.querySelector('[aria-label="تاريخ البيانات المحفوظة"]').textContent).toContain('تجهيز هذا العرض');
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.selection).not.toHaveBeenCalled();
  expect(api.customerAdjustments).not.toHaveBeenCalled();

  await act(async () => { [...actions.querySelectorAll('button')].find(button => button.textContent === action).click(); await flush(); });
  expect(host.querySelector('h1').textContent).toBe(title);
  expect(host.querySelector('.trail b').textContent).toBe(title);
  expect(host.querySelector('nav.nav [aria-current="page"]').getAttribute('href')).toBe('/operational-balances?view=home');
  expect(api.customerAdjustments).toHaveBeenCalledTimes(1);
  expect(api.customerAdjustments.mock.calls[0][0]).toEqual({ direction, page: 1, page_size: 8 });
  expect(api.bootstrap).toHaveBeenCalledTimes(1);
  expect(api.ledger).toHaveBeenCalledTimes(1);
  for (const read of [api.selection, api.home, api.directory, api.options, api.context, api.movements, api.resource, api.statement]) expect(read).not.toHaveBeenCalled();
});

test.each([
  ['session', { session_scope: 'another-account' }],
  ['permission', { permission_version: 2 }],
  ['owner', { owner_override: false }],
  ['resource', { resource: ownerResource('different-bank', { name: 'حساب لا يجب عرضه' }) }],
])('an owner ledger with a mismatched %s cannot expose its resource or actions', async (label, change) => {
  const item = ownerResource('direct-bank', { name: 'حساب لا يجب عرضه' });
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), operations: ['bank.transfer'], capabilities: { create_entries: true } });
  api.ledger.mockResolvedValue(ledgerPage(item, change));
  await open('/operational-balances?view=ledger&kind=bank&id=direct-bank');
  expect(api.ledger).toHaveBeenCalledTimes(1); expect(api.selection).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain('حساب لا يجب عرضه');
  expect(host.textContent).not.toContain('سجّل حركة');
  expect(host.querySelector('.ledger, .metrics')).toBeNull();
});

test('a balance-only employee keeps selection and never requests a ledger', async () => {
  const item = { ...resource('balance-only', '78.15'), operations: [], access: { use: false, balance: true, movements: 'none', report: false, print: false } };
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), actor: { id: 'actor', is_owner: false } });
  api.selection.mockResolvedValue({ ...item, session_scope: 'scope-actor', permission_version: 1 });
  await open('/operational-balances?view=ledger&kind=bank&id=balance-only');
  expect(api.selection).toHaveBeenCalledTimes(1); expect(api.ledger).not.toHaveBeenCalled();
  expect(host.textContent).toContain(item.name); expect(host.textContent).toContain('78.15');
  expect(host.textContent).toContain('عرض الحركات غير متاح');
});

test('owner provider orders remain the default and changing the tab reads only that ledger view', async () => {
  const item = ownerResource('tabby', { kind: 'provider', name: 'تابي', operations: ['provider.settle'] });
  api.ledger.mockImplementation(async (kind, id, params) => ledgerPage(item, { provider_orders: params.provider_orders === true }));
  await open('/operational-balances?view=ledger&kind=provider&id=tabby');
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({ provider_orders: true, page: 1, page_size: 8 });
  expect(api.selection).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="تفاصيل المنصة"] [aria-pressed="true"]').textContent).toBe('طلبات تابي');
  await act(async () => { [...host.querySelectorAll('button')].find(button => button.textContent === 'كشف الحركات والتسويات').click(); await flush(); });
  expect(api.ledger).toHaveBeenCalledTimes(2);
  expect(api.ledger.mock.calls[1][2]).toEqual({ date_from: '2026-11-01', date_to: '2026-11-22', page: 1, page_size: 8 });
  expect(api.selection).not.toHaveBeenCalled();
});

test('owner receipt recovery binds the first ledger query and opens its confirmed movement', async () => {
  const item = ownerResource('tabby', { kind: 'provider', name: 'تابي' });
  const receipt = { id: 'recovered-receipt', description: 'حركة مستعادة', reference: 'MV-RECOVERED',
    amount: '17.00', currency: 'SAR', side: 'for_party', business_date: '2026-11-17' };
  api.ledger.mockResolvedValue(ledgerPage(item, { items: [receipt], page: 2, total: 9, total_pages: 2 }));
  await open({ pathname: '/operational-balances', search: '?view=ledger&kind=provider&id=tabby', state: {
    recoveredMovement: { kind: 'provider', id: 'tabby', movement: receipt.id, date: '2026-11-17', scope: 'scope-actor' },
  } });
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({ date_from: '2026-11-17', date_to: '2026-11-17', focus_id: receipt.id, page: 1, page_size: 8 });
  expect(host.querySelector('dialog').textContent).toContain('MV-RECOVERED');
  expect(api.selection).not.toHaveBeenCalled();
});

test('owner saved invoice filters the first ledger and retains permissioned supplier actions', async () => {
  const item = ownerResource('supplier', { kind: 'supplier', name: 'المورد الموثق', operations: ['supplier.accrual', 'supplier.payment'] });
  api.bootstrap.mockResolvedValue({ ...bootstrap('actor'), operations: ['supplier.accrual', 'supplier.payment'], capabilities: { create_entries: true } });
  api.ledger.mockResolvedValue(ledgerPage(item));
  await open({ pathname: '/operational-balances', search: '?view=ledger&kind=supplier&id=supplier', state: {
    supplierInvoice: { id: 'supplier', number: 'SI-2069', date: '2026-11-16' },
  } });
  expect(api.ledger).toHaveBeenCalledTimes(1);
  expect(api.ledger.mock.calls[0][2]).toEqual({ date_from: '2026-11-16', date_to: '2026-11-16', q: 'SI-2069', page: 1, page_size: 8 });
  expect(host.textContent).toContain('حُفظت فاتورة الشراء الآجل');
  expect(host.textContent).toContain('شراء آجل'); expect(host.textContent).toContain('تسديد مبلغ');
  expect(api.selection).not.toHaveBeenCalled();
});

test('a late owner ledger cannot replace a different resource after navigation', async () => {
  const first = ownerResource('first', { name: 'الحساب السابق' }), next = ownerResource('next', { name: 'الحساب الحالي' });
  let finishOld;
  api.ledger.mockImplementation((kind, id) => id === 'first' ? new Promise(resolve => { finishOld = resolve; }) : Promise.resolve(ledgerPage(next)));
  api.directory.mockResolvedValue(page([next]));
  await open('/operational-balances?view=ledger&kind=bank&id=first');
  const oldSignal = api.ledger.mock.calls[0][3].signal;
  await go('assets'); expect(oldSignal.aborted).toBe(true);
  await act(async () => { host.querySelector('.cards .resource').click(); await flush(); });
  expect(host.querySelector('h1').textContent).toContain(next.name);
  await act(async () => { finishOld(ledgerPage(first)); await flush(); });
  expect(host.textContent).not.toContain(first.name); expect(host.querySelector('h1').textContent).toContain(next.name);
  expect(api.ledger).toHaveBeenCalledTimes(2); expect(api.selection).not.toHaveBeenCalled();
});
