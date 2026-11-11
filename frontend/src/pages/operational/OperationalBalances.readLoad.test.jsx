import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import OperationalBalances from './OperationalBalances';
import {operationalApi as api} from './api';

let mockAuth = null;
jest.mock('../../context/AuthContext', () => ({useOptionalAuth: () => mockAuth}));
jest.mock('./api', () => ({
  operationalApi: {context: jest.fn(), reports: jest.fn(), audit: jest.fn()},
  entityKinds: [], messageFor: () => 'تعذر إتمام العملية', feeIncomplete: () => false,
}));
jest.mock('./OpeningBalances', () => ({OpeningBalances: () => null,
  Notice: ({error}) => error ? <p role="alert">{error}</p> : null}));
jest.mock('./DailyMovements', () => () => null);
jest.mock('./SupplierReturn', () => () => null);
jest.mock('./InventoryPurchases', () => () => null);
jest.mock('./InventoryAvailability', () => () => null);
jest.mock('./OpeningInventory', () => () => null);
jest.mock('./SalaryStart', () => () => null);
jest.mock('./MovementHistory', () => () => null);
jest.mock('./DomainDetails', () => () => null);
jest.mock('./CustomerPaymentTotals', () => () => null);
jest.mock('./CustomerWalletBalance', () => ({onSaved}) => <button onClick={onSaved}>تم حفظ المصدر</button>);
jest.mock('./OperationalCostExposure', () => () => null);

const context = (scope = 'owner:actor') => ({session_scope: scope, status: 'active',
  started_at: '2026-11-01T00:30:00+03:00', permissions: {view: true, reports: true, move: false, manage: true}});
const report = (value = '123.45') => ({parties: [], obligations: [], details: {}, issues: [],
  as_of: '2026-11-11T20:00:00Z', summaries: {SAR: {actual_liquidity: value}}});
const deferred = () => {let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject};};
let host, root, visible, online;
const render = async () => {await act(async () => root.render(<OperationalBalances/>));};
const advance = async duration => {await act(async () => jest.advanceTimersByTime(duration));};
const audit = () => [...host.querySelectorAll('details')].find(element => element.querySelector('summary')?.textContent === 'سجل التدقيق');
const toggleAudit = async open => {await act(async () => {
  const element = audit(); element.open = open; element.dispatchEvent(new Event('toggle'));
});};
const click = async label => {await act(async () => [...host.querySelectorAll('button')].find(element => element.textContent === label).click());};

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers(); jest.resetAllMocks(); mockAuth = null;
  visible = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  online = jest.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  api.context.mockResolvedValue(context()); api.reports.mockResolvedValue(report());
  api.audit.mockResolvedValue({items: [{message: 'سجل مصدر موثق', at: '2026-11-11T20:00:00Z'}]});
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); jest.restoreAllMocks(); jest.useRealTimers();
});

test('closed audit performs no reads or row rendering across normal report polling', async () => {
  await render(); await advance(30000); await advance(30000);
  expect(api.reports).toHaveBeenCalledTimes(3);
  expect(api.audit).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain('سجل مصدر موثق');
  expect(host.textContent).toContain('123.45 SAR');
  expect(host.textContent).toContain('آخر تحديث:');
});

test('opening audit reads once, closed rows disappear, and its manual refresh is independent', async () => {
  await render(); await toggleAudit(true);
  expect(api.audit).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('سجل مصدر موثق');
  await advance(60000);
  expect(api.audit).toHaveBeenCalledTimes(1);
  await click('تحديث سجل التدقيق');
  expect(api.audit).toHaveBeenCalledTimes(2);
  await toggleAudit(false); await advance(30000);
  expect(api.audit).toHaveBeenCalledTimes(2);
  expect(host.textContent).not.toContain('سجل مصدر موثق');
});

test('audit failure keeps a successful financial report and its saved date visible', async () => {
  api.audit.mockRejectedValue(new Error('audit read unavailable'));
  await render(); await toggleAudit(true);
  expect(audit().textContent).toContain('تعذر إتمام العملية');
  expect(host.textContent).toContain('123.45 SAR');
  expect(host.textContent).toContain('آخر تحديث:');
  expect(api.reports).toHaveBeenCalledTimes(1);
});

test.each(['hidden', 'offline'])('%s pages pause context and report polls and resume one read', async state => {
  await render();
  if (state === 'hidden') visible.mockReturnValue('hidden'); else online.mockReturnValue(false);
  await advance(90000);
  expect(api.context).toHaveBeenCalledTimes(1); expect(api.reports).toHaveBeenCalledTimes(1);
  visible.mockReturnValue('visible'); online.mockReturnValue(true);
  await act(async () => (state === 'hidden' ? document : window).dispatchEvent(new Event(state === 'hidden' ? 'visibilitychange' : 'online')));
  expect(api.context).toHaveBeenCalledTimes(2); expect(api.reports).toHaveBeenCalledTimes(2);
  expect(api.audit).not.toHaveBeenCalled();
});

test.each(['hidden', 'offline'])('initial %s entry waits for visibility and connectivity before reading', async state => {
  if (state === 'hidden') visible.mockReturnValue('hidden'); else online.mockReturnValue(false);
  await render(); await advance(60000);
  expect(api.context).not.toHaveBeenCalled(); expect(api.reports).not.toHaveBeenCalled();
  visible.mockReturnValue('visible'); online.mockReturnValue(true);
  await act(async () => (state === 'hidden' ? document : window).dispatchEvent(new Event(state === 'hidden' ? 'visibilitychange' : 'online')));
  expect(api.context).toHaveBeenCalledTimes(1); expect(api.reports).toHaveBeenCalledTimes(1);
});

test('a slow context read cannot overlap recurring polls', async () => {
  const first = deferred(); api.context.mockReturnValueOnce(first.promise);
  await render(); await advance(90000);
  expect(api.context).toHaveBeenCalledTimes(1); expect(api.reports).not.toHaveBeenCalled();
  await act(async () => first.resolve(context()));
  expect(api.reports).toHaveBeenCalledTimes(1);
  await advance(30000); expect(api.context).toHaveBeenCalledTimes(2);
});

test('a slow report read cannot overlap visible recurring polls', async () => {
  const first = deferred(); api.reports.mockReturnValueOnce(first.promise);
  await render(); await advance(90000);
  expect(api.reports).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve(report()));
  expect(host.textContent).toContain('123.45 SAR');
  await advance(30000); expect(api.reports).toHaveBeenCalledTimes(2);
});

test('mutation refresh waits for one pending read and rejects its older report revision', async () => {
  const old = deferred(), fresh = deferred();
  api.reports.mockResolvedValueOnce(report()).mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
  await render(); await advance(30000); await click('تم حفظ المصدر');
  expect(api.reports).toHaveBeenCalledTimes(2);
  await act(async () => old.resolve(report('999.00')));
  expect(api.reports).toHaveBeenCalledTimes(3);
  expect(host.textContent).not.toContain('999.00 SAR');
  expect(host.textContent).toContain('123.45 SAR');
  await act(async () => fresh.resolve(report('321.00')));
  expect(host.textContent).toContain('321.00 SAR');
});

test('closing and reopening pending audit never overlaps requests or displays closed-generation rows', async () => {
  const old = deferred(), fresh = deferred();
  api.audit.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
  await render(); await toggleAudit(true); await toggleAudit(false); await toggleAudit(true);
  expect(api.audit).toHaveBeenCalledTimes(1);
  await act(async () => old.resolve({items: [{message: 'نتيجة تدقيق قديمة'}]}));
  expect(api.audit).toHaveBeenCalledTimes(2); expect(host.textContent).not.toContain('نتيجة تدقيق قديمة');
  await act(async () => fresh.resolve({items: [{message: 'نتيجة تدقيق حديثة'}]}));
  expect(host.textContent).toContain('نتيجة تدقيق حديثة');
});

test('actor change immediately removes old content and ignores an old-scope response', async () => {
  const old = deferred();
  mockAuth = {user: {id: 'actor-a', owner_id: 'owner'}, authStatus: 'authenticated', loading: false};
  api.context.mockResolvedValueOnce(context('owner:actor-a')).mockResolvedValue(context('owner:actor-b'));
  api.reports.mockReturnValueOnce(old.promise).mockResolvedValue(report('222.00'));
  await render();
  mockAuth = {...mockAuth, user: {...mockAuth.user, id: 'actor-b'}};
  await render();
  expect(api.context).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('222.00 SAR');
  await act(async () => old.resolve(report('111.00')));
  expect(host.textContent).not.toContain('111.00 SAR');
  expect(host.textContent).toContain('222.00 SAR');
});

test('changed server session scope ignores a pending report from the earlier context', async () => {
  const old = deferred();
  api.context.mockResolvedValueOnce(context('session-a')).mockResolvedValue(context('session-b'));
  api.reports.mockReturnValueOnce(old.promise).mockResolvedValue(report('222.00'));
  await render(); await advance(30000);
  expect(api.context).toHaveBeenCalledTimes(2); expect(api.reports).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('222.00 SAR');
  await act(async () => old.resolve(report('111.00')));
  expect(host.textContent).not.toContain('111.00 SAR');
});

test('an earlier actor context response cannot replace the new actor scope', async () => {
  const old = deferred();
  mockAuth = {user: {id: 'actor-a', owner_id: 'owner'}, authStatus: 'authenticated', loading: false};
  api.context.mockReturnValueOnce(old.promise).mockResolvedValue(context('owner:actor-b'));
  await render();
  mockAuth = {...mockAuth, user: {...mockAuth.user, id: 'actor-b'}};
  await render(); expect(api.reports).toHaveBeenCalledTimes(1);
  await act(async () => old.resolve({...context('owner:actor-a'), permissions: {view: true, reports: false}}));
  expect(host.textContent).toContain('123.45 SAR');
  expect(api.context).toHaveBeenCalledTimes(2); expect(api.reports).toHaveBeenCalledTimes(1);
});
