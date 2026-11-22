import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedFinance from './ApprovedFinance';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import { useBalanceRead } from './simpleBalancesHooks';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('./simpleBalancesApi', () => ({
  simpleBalancesApi: { context: jest.fn() },
  simpleBalanceError: () => 'تعذر تحميل الأرصدة. أعد المحاولة.',
}));

global.IS_REACT_ACT_ENVIRONMENT = true;
const flush = async () => { for (let index = 0; index < 12; index += 1) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((accept, refuse) => { resolve = accept; reject = refuse; });
  return { promise, resolve, reject };
};
const contextFor = (id = 'first', name = 'الحساب الحالي') => ({
  actor: { id, name: 'موظف العمليات', is_owner: false },
  session_scope: 'scope-' + id, permission_version: 1, status: 'active', operations: [],
  capabilities: { create_entries: false }, recent_movements: [],
  resources: [{ kind: 'bank', id: 'bank-' + id, name, currency: 'SAR', operations: [],
    access: { balance: true, movements: 'all' }, balance: { visible: true, side: 'for_us', amount: '90.00' } }],
});

let host, root, visibility, online;
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  online = jest.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
  useOptionalAuth.mockReturnValue({ user: { id: 'first' }, loading: false });
  api.context.mockResolvedValue(contextFor());
  window.scrollTo = jest.fn();
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  jest.restoreAllMocks();
  jest.useRealTimers();
});

const open = (url = '/operational-balances') => act(async () => {
  root.render(<MemoryRouter initialEntries={[url]}><ApprovedFinance /></MemoryRouter>);
  await flush();
});
const elapse = milliseconds => act(async () => { jest.advanceTimersByTime(milliseconds); await flush(); });
const retry = () => act(async () => {
  [...host.querySelectorAll('button')].find(button => button.textContent === 'إعادة المحاولة').click();
  await flush();
});
const returnToPage = () => act(async () => {
  visibility.mockReturnValue('visible'); online.mockReturnValue(true);
  document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('online'));
  await flush();
});
const expectRecovery = () => {
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain('جارٍ تحميل الأرصدة');
  expect(host.querySelector('.hero')).toBeNull();
};

test.each(['/operational-balances', '/operational-balances?view=home'])(
  '%s ends a stalled context attempt after 30 seconds and recovers only on explicit retry', async url => {
    const pending = deferred();
    api.context.mockReturnValueOnce(pending.promise);
    await open(url);
    expect(api.context).toHaveBeenCalledTimes(1);
    expect(api.context.mock.calls[0][1]).toEqual({ homePreview: true });
    expect(host.textContent).toContain('جارٍ تحميل الأرصدة');
    await elapse(30000);
    expectRecovery();
    expect(host.querySelector('[role="alert"]').textContent).toContain('انتهت مهلة');
    expect(api.context.mock.calls[0][0].signal.aborted).toBe(true);
    await act(async () => { pending.resolve(contextFor('first', 'استجابة متأخرة')); await flush(); });
    await returnToPage();
    expect(api.context).toHaveBeenCalledTimes(1);
    expect(host.textContent).not.toContain('استجابة متأخرة');
    await retry();
    expect(api.context).toHaveBeenCalledTimes(2);
    expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  },
);

test('retry remains bounded while an aborted predecessor is still physically pending', async () => {
  const pending = deferred();
  api.context.mockReturnValueOnce(pending.promise);
  await open(); await elapse(30000);
  expectRecovery();
  await retry();
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('جارٍ تحميل الأرصدة');
  await elapse(30000);
  expectRecovery();
  expect(api.context).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(contextFor('first', 'بيانات المحاولة القديمة')); await flush(); });
  expect(host.textContent).not.toContain('بيانات المحاولة القديمة');
  expect(api.context).toHaveBeenCalledTimes(1);
  await retry();
  expect(api.context).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
});

test.each(['hidden', 'offline'])('initially %s entry exposes recovery without starting a deferred read', async cause => {
  if (cause === 'hidden') visibility.mockReturnValue('hidden');
  else online.mockReturnValue(false);
  await open();
  expectRecovery();
  expect(api.context).not.toHaveBeenCalled();
  await elapse(300000); await returnToPage();
  expectRecovery();
  expect(api.context).not.toHaveBeenCalled();
  await retry();
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
});

test.each(['hidden', 'offline'])('%s interruption leaves a paused attempt and never automatically repeats its request', async cause => {
  api.context.mockImplementationOnce(({ signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true });
  }));
  await open();
  await act(async () => {
    if (cause === 'hidden') {
      visibility.mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'));
    } else {
      online.mockReturnValue(false); window.dispatchEvent(new Event('offline'));
    }
    await flush();
  });
  expectRecovery();
  expect(api.context.mock.calls[0][0].signal.aborted).toBe(true);
  await returnToPage(); await elapse(300000);
  expect(api.context).toHaveBeenCalledTimes(1);
  await retry();
  expect(api.context).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
});

test('an identity change cannot expose the old result or wait indefinitely behind its request', async () => {
  const old = deferred();
  api.context.mockReturnValueOnce(old.promise).mockResolvedValue(contextFor('second', 'حساب المستخدم الحالي'));
  await open();
  useOptionalAuth.mockReturnValue({ user: { id: 'second' }, loading: false });
  await open();
  expect(api.context.mock.calls[0][0].signal.aborted).toBe(true);
  expect(api.context).toHaveBeenCalledTimes(1);
  await elapse(30000);
  expectRecovery();
  await act(async () => { old.resolve(contextFor('first', 'حساب خاص بالمستخدم السابق')); await flush(); });
  expect(host.textContent).not.toContain('حساب خاص بالمستخدم السابق');
  expect(api.context).toHaveBeenCalledTimes(1);
  await retry();
  expect(api.context).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain('حساب المستخدم الحالي');
  expect(host.textContent).not.toContain('حساب خاص بالمستخدم السابق');
});

test('an ordinary context rejection exposes retry without substituting financial data', async () => {
  api.context.mockRejectedValueOnce({ response: { status: 503 } });
  await open();
  expectRecovery();
  await retry();
  expect(api.context).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
});

test('a missing authenticated user id offers authentication recovery instead of a disabled balance loader', async () => {
  const retryAuth = jest.fn();
  useOptionalAuth.mockReturnValue({ user: {}, loading: false, retryAuth });
  await open();
  expectRecovery();
  expect(api.context).not.toHaveBeenCalled();
  await act(async () => host.querySelector('button').click());
  expect(retryAuth).toHaveBeenCalledTimes(1);
  expect(api.context).not.toHaveBeenCalled();
});

test('balance readers without the policy retain their existing hidden-page deferral', async () => {
  visibility.mockReturnValue('hidden');
  const read = jest.fn().mockResolvedValue('authorized result');
  function UnchangedReader() {
    const result = useBalanceRead(read, 'same-scope');
    return <output>{result.data || result.error || 'waiting'}</output>;
  }
  await act(async () => root.render(<UnchangedReader />));
  await elapse(300000);
  expect(read).not.toHaveBeenCalled();
  expect(host.textContent).toBe('waiting');
  await returnToPage();
  expect(read).toHaveBeenCalledTimes(1);
  expect(host.textContent).toBe('authorized result');
});
