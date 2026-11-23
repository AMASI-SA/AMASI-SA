import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import GlobalBalanceFreshness from './GlobalBalanceFreshness';
import { readBalanceFreshness } from './balanceFreshnessApi';

let mockAuth, mockLocation, mockV2Route;
jest.mock('react-router-dom', () => ({ useLocation: () => mockLocation }));
jest.mock('../../context/AuthContext', () => ({ useAuth: () => mockAuth }));
jest.mock('../../components/MezanV2NavigationShell', () => ({ isMezanV2Route: () => mockV2Route }));
jest.mock('./balanceFreshnessApi', () => ({ readBalanceFreshness: jest.fn() }));
const OLD = '2026-11-21T14:50:00Z';
const health = (patch = {}) => ({ schema_version: 1, actor: { id: 'owner', is_owner: true }, session_scope: 'scope',
  status: 'active', as_of: OLD, revision: 10, generated_at: '2026-11-23T14:00:00Z',
  freshness: { state: 'failed', max_age_seconds: 7200 }, last_attempt: null,
  sources: [{ key: 'imile', label: 'iMile', state: 'failed', last_success_at: OLD, message: 'توقف مصدر الشحن' }],
  issues: [], ...patch });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let root, host;
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
const render = async () => act(async () => { root.render(<GlobalBalanceFreshness />); await flush(); });
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  mockAuth = { user: { id: 'owner', is_owner: true }, loading: false, authStatus: 'authenticated' };
  mockLocation = { pathname: '/dashboard-v2', key: 'home' };
  mockV2Route = true;
  readBalanceFreshness.mockReset().mockResolvedValue(health());
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });

test('persistent global banner reads only lightweight metadata, with source reason and last success', async () => {
  await render(); expect(readBalanceFreshness).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).toContain('iMile'); expect(host.textContent).toContain('توقف مصدر الشحن');
  expect(host.textContent).toContain('آخر نجاح');
});
test('legacy fixed controls receive clearance while V2 notice placement stays compact', async () => {
  await render(); expect(host.querySelector('.balance-freshness-legacy-controls')).toBeNull();
  mockV2Route = false; mockLocation = { pathname: '/history', key: 'legacy' }; await render();
  expect(host.querySelector('.balance-freshness-legacy-controls')).not.toBeNull();
  mockV2Route = true; mockLocation = { pathname: '/dashboard-v2', key: 'v2' }; await render();
  expect(host.querySelector('.balance-freshness-legacy-controls')).toBeNull();
});
test.each(['failed', 'unknown', 'running'])('durable %s outcome remains visible even with aggregate current', async outcome => {
  readBalanceFreshness.mockResolvedValueOnce(health({ as_of: '2026-11-23T13:59:00Z', freshness: { state: 'current', max_age_seconds: 7200 },
    sources: [], last_attempt: { started_at: '2026-11-23T13:58:00Z', finished_at: null, outcome, error_code: null } }));
  await render(); expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).toContain('آخر محاولة');
});
test('quick hide/show leaves an interrupted check visibly unverified, never checking forever', async () => {
  const pending = deferred(); readBalanceFreshness.mockReturnValueOnce(pending.promise);
  await render();
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange')); await flush();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange')); await flush();
  });
  expect(host.textContent).toContain('توقفت قراءة حالة التحديث');
  expect(host.querySelector('button').disabled).toBe(false);
  await act(async () => { pending.resolve(health()); await flush(); });
  expect(readBalanceFreshness).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('توقفت قراءة حالة التحديث');
});
test('failed reads and 404 retain known warning; a pending retry does not hide it', async () => {
  await render(); const pending = deferred();
  readBalanceFreshness.mockReturnValueOnce(pending.promise);
  mockLocation = { ...mockLocation, key: 'next' }; await render();
  expect(host.textContent).toContain('توقف مصدر الشحن');
  await act(async () => { pending.resolve(health({ schema_version: 0 })); await flush(); });
  expect(host.textContent).toContain('تعذر التحقق'); expect(host.textContent).toContain('توقف مصدر الشحن');
  readBalanceFreshness.mockRejectedValueOnce({ response: { status: 404 } });
  mockLocation = { ...mockLocation, key: 'third' }; await render();
  expect(host.querySelector('[role="alert"]')).not.toBeNull(); expect(host.textContent).toContain('توقف مصدر الشحن');
});
test('only verified recovery removes the global warning', async () => {
  await render();
  readBalanceFreshness.mockResolvedValueOnce(health({ as_of: '2026-11-23T13:59:00Z', revision: 11,
    freshness: { state: 'current', max_age_seconds: 7200 },
    sources: [{ key: 'imile', label: 'iMile', state: 'current', last_success_at: '2026-11-23T13:59:00Z', message: '' }],
  }));
  mockLocation = { ...mockLocation, key: 'recovered' }; await render();
  expect(host.querySelector('[role="alert"]')).toBeNull();
});
test('unconfigured inactive source is not a false alert, while a separate failure issue still warns', async () => {
  const inactive = health({ as_of: '2026-11-23T13:59:00Z', freshness: { state: 'current', max_age_seconds: 7200 },
    sources: [{ key: 'snapchat', label: 'سناب', state: 'inactive', last_success_at: null, message: 'غير مهيأ' }] });
  readBalanceFreshness.mockResolvedValueOnce(inactive); await render();
  expect(host.textContent).toBe('');
  readBalanceFreshness.mockResolvedValueOnce({ ...inactive, issues: [{ code: 'source_disabled', source: 'snapchat',
    severity: 'warning', message: 'توقف مصدر كان مفعلاً', last_success_at: OLD }] });
  mockLocation = { ...mockLocation, key: 'stopped' }; await render();
  expect(host.querySelector('[role="alert"]')).not.toBeNull(); expect(host.textContent).toContain('توقف مصدر كان مفعلاً');
});
test('a page left open becomes stale at the local deadline without GET polling or a financial POST', async () => {
  jest.useFakeTimers(); jest.setSystemTime(new Date('2026-11-23T14:00:00Z'));
  readBalanceFreshness.mockResolvedValueOnce(health({ as_of: '2026-11-23T12:01:00Z',
    freshness: { state: 'current', max_age_seconds: 7200 }, sources: [] }));
  await render(); expect(host.querySelector('[role="alert"]')).toBeNull();
  await act(async () => { jest.advanceTimersByTime(60000); await flush(); });
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).toContain('مرّ موعد تحديث الأرصدة');
  expect(readBalanceFreshness).toHaveBeenCalledTimes(1);
  await act(async () => { jest.advanceTimersByTime(24 * 60 * 60 * 1000); await flush(); });
  expect(readBalanceFreshness).toHaveBeenCalledTimes(1);
});
test('staff, anonymous sessions, and the dedicated workspace never start global checks', async () => {
  mockAuth = { ...mockAuth, user: { id: 'staff', is_owner: false } }; await render();
  mockAuth = { ...mockAuth, user: null }; await render();
  mockAuth = { user: { id: 'owner', is_owner: true }, loading: false, authStatus: 'authenticated' };
  mockLocation = { pathname: '/operational-balances', key: 'balances' }; await render();
  expect(readBalanceFreshness).not.toHaveBeenCalled(); expect(host.textContent).toBe('');
});
test('server permission denial drops previously displayed owner-only health', async () => {
  await render(); expect(host.textContent).toContain('iMile');
  readBalanceFreshness.mockRejectedValueOnce({ response: { status: 403 } });
  mockLocation = { ...mockLocation, key: 'denied' }; await render();
  expect(host.textContent).toBe('');
});
test('actor changes drop old in-flight data and clear the previous actor banner immediately', async () => {
  const pending = deferred(); readBalanceFreshness.mockReturnValueOnce(pending.promise);
  await render();
  const signal = readBalanceFreshness.mock.calls[0][0].signal;
  mockAuth = { ...mockAuth, user: { id: 'new-owner', is_owner: true } };
  readBalanceFreshness.mockResolvedValueOnce(health({ actor: { id: 'new-owner', is_owner: true }, session_scope: 'new-scope', sources: [],
    as_of: '2026-11-23T13:59:00Z', freshness: { state: 'current', max_age_seconds: 7200 } }));
  await render(); expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(health()); await flush(); });
  expect(host.textContent).not.toContain('iMile'); expect(host.querySelector('[role="alert"]')).toBeNull();
});
