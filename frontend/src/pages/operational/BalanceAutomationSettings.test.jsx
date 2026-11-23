import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import http from '../../lib/api';
import BalanceAutomationSettings from './BalanceAutomationSettings';

jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
const context = { actor: { id: 'owner', is_owner: true }, session_scope: 'owner-scope', permission_version: 1, status: 'active' };
const setting = patch => ({ schema_version: 1, enabled: false, interval_seconds: 7200,
  financial_coverage_verified: false, session_scope: 'owner-scope', consecutive_failures: 0,
  next_due_at: null, last_attempt_at: null, last_success_at: null, ...patch });
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let root, host;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  http.get.mockResolvedValue({ data: setting() }); http.post.mockResolvedValue({ data: setting({ enabled: true }) });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async value => act(async () => root.render(<BalanceAutomationSettings context={value || context}/>));
const button = label => [...host.querySelectorAll('button')].find(node => node.textContent === label);

test('opening settings only reads and an explicit enable requires saved readback', async () => {
  await render(); expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).not.toHaveBeenCalled();
  const readback = deferred(); http.get.mockReturnValueOnce(readback.promise);
  await act(async () => button('تفعيل الاحتساب التلقائي').click());
  expect(http.post).toHaveBeenCalledWith('/operational-balances/automation',
    { enabled: true, expected_session_scope: 'owner-scope' }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(host.textContent).toContain('الجدولة متوقفة'); expect(button('تفعيل الاحتساب التلقائي').disabled).toBe(true);
  await act(async () => readback.resolve({ data: setting({ enabled: true }) }));
  expect(host.textContent).toContain('الجدولة مفعّلة'); expect(button('إيقاف الجدولة').disabled).toBe(false);
  expect(host.textContent).toContain('راجع تنبيهات الجهات');
});
test('unknown mutation cannot be retried until an authoritative status read', async () => {
  await render(); http.post.mockRejectedValueOnce(new Error('timeout'));
  await act(async () => button('تفعيل الاحتساب التلقائي').click());
  expect(host.textContent).toContain('لم تتأكد نتيجة تغيير الجدولة');
  expect(button('تفعيل الاحتساب التلقائي').disabled).toBe(true); expect(http.post).toHaveBeenCalledTimes(1);
  http.get.mockResolvedValueOnce({ data: setting({ enabled: true }) });
  await act(async () => button('التحقق من حالة الجدولة').click());
  expect(button('إيقاف الجدولة').disabled).toBe(false); expect(http.post).toHaveBeenCalledTimes(1);
});
test('leaving settings aborts its read and a delayed old actor result cannot appear', async () => {
  const pending = deferred(); http.get.mockReturnValueOnce(pending.promise); await render();
  const signal = http.get.mock.calls[0][1].signal;
  await render({ ...context, actor: { id: 'employee', is_owner: false }, session_scope: 'employee-scope' });
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ data: setting({ enabled: true }) }));
  expect(host.textContent).toBe(''); expect(http.post).not.toHaveBeenCalled();
});
test('a mismatched session response never exposes a setting or enable action', async () => {
  http.get.mockResolvedValueOnce({ data: setting({ session_scope: 'different' }) }); await render();
  expect(host.textContent).toContain('تعذر قراءة حالة الجدولة');
  expect(button('تفعيل الاحتساب التلقائي')).toBeUndefined(); expect(http.post).not.toHaveBeenCalled();
});
test('hiding an in-flight setting change preserves an explicit unknown outcome until rechecked', async () => {
  await render(); const pending = deferred(); http.post.mockReturnValueOnce(pending.promise);
  await act(async () => button('تفعيل الاحتساب التلقائي').click());
  const signal = http.post.mock.calls[0][2].signal;
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(signal.aborted).toBe(true); expect(host.textContent).toContain('لم تتأكد نتيجة تغيير الجدولة');
  expect(button('تفعيل الاحتساب التلقائي').disabled).toBe(true);
  await act(async () => pending.resolve({ data: setting({ enabled: true }) }));
  expect(http.get).toHaveBeenCalledTimes(1); expect(http.post).toHaveBeenCalledTimes(1);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  http.get.mockResolvedValueOnce({ data: setting({ enabled: true }) });
  await act(async () => button('التحقق من حالة الجدولة').click());
  expect(button('إيقاف الجدولة').disabled).toBe(false);
});
