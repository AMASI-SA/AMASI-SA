import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { createBalanceFreshnessManager } from './visibleBalanceFreshness';
import { useVisibleBalanceFreshness } from './useVisibleBalanceFreshness';
import BalanceFreshnessNotice, { hasBalanceFreshnessWarning } from './BalanceFreshnessNotice';

jest.mock('./balanceFreshnessApi', () => ({ balanceFreshnessManager: {} }));
const OLD = '2026-11-21T14:50:00Z', CURRENT = '2026-11-23T13:59:00Z';
const scope = 'owner-scope';
const context = { actor: { id: 'owner', is_owner: true }, session_scope: scope, permission_version: 1, status: 'active' };
const health = (patch = {}) => ({ schema_version: 1, actor: { id: 'owner', is_owner: true },
  session_scope: scope, status: 'active', as_of: OLD, revision: 10, generated_at: '2026-11-23T14:00:00Z',
  freshness: { state: 'stale', max_age_seconds: 7200 }, last_attempt: null, sources: [], issues: [], ...patch });
const receipt = { status: 'refreshed', session_scope: scope, as_of: CURRENT, revision: 11 };
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
let root, host, visibility;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
function setup() {
  const read = jest.fn().mockResolvedValue(health());
  const refresh = jest.fn().mockResolvedValue({ refresh_warning: 'مصدر غير متاح' });
  const onConfirmed = jest.fn();
  const manager = createBalanceFreshnessManager({ read, refresh, now: () => Date.parse('2026-11-23T14:00:00Z') });
  let state;
  function Harness(props) {
    state = useVisibleBalanceFreshness({ context, identity: 'signed-owner', canRefresh: true, manager, onConfirmed, ...props });
    return <><p data-testid="saved">الأرصدة المحفوظة</p><BalanceFreshnessNotice state={state}
      onCheck={state.canRefresh ? state.retry : undefined} /></>;
  }
  const render = async (props = {}) => act(async () => { root.render(<Harness {...props} />); await flush(); });
  return { read, refresh, onConfirmed, render, state: () => state };
}

test('saved balances render immediately while freshness and refresh are pending', async () => {
  const env = setup(), pending = deferred(); env.read.mockReturnValueOnce(pending.promise);
  await env.render();
  expect(host.querySelector('[data-testid="saved"]').textContent).toBe('الأرصدة المحفوظة');
  expect(env.refresh).not.toHaveBeenCalled();
  await act(async () => { pending.resolve(health()); await flush(); });
  expect(env.refresh).toHaveBeenCalledTimes(1);
});
test('non-owner never reads owner metadata or attempts financial refresh', async () => {
  const env = setup(); await env.render({ context: { ...context, actor: { id: 'staff', is_owner: false } } });
  expect(env.read).not.toHaveBeenCalled(); expect(env.refresh).not.toHaveBeenCalled();
});
test('entry/payment/modal eligibility permits only a lightweight health read', async () => {
  const env = setup(); await env.render({ canRefresh: false });
  expect(env.read).toHaveBeenCalledTimes(1); expect(env.refresh).not.toHaveBeenCalled();
  expect(host.querySelector('button')).toBeNull();
});
test('a safe view navigation does not cancel or duplicate the scope-owned refresh', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  await env.render({ visiblePage: 'home' });
  const signal = env.refresh.mock.calls[0][0].signal;
  await env.render({ visiblePage: 'ledger' });
  expect(env.read).toHaveBeenCalledTimes(1); expect(signal.aborted).toBe(false);
  await act(async () => { pending.resolve({ refresh_warning: 'busy' }); await flush(); });
});
test('opening a form aborts the POST and prevents a late receipt from invalidating the form', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  await env.render(); const signal = env.refresh.mock.calls[0][0].signal;
  await env.render({ canRefresh: false }); expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(receipt); await flush(); });
  expect(env.onConfirmed).not.toHaveBeenCalled();
});
test('a child-owned ledger dialog also aborts the financial attempt and closing it does not retry unknown', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  await env.render(); const signal = env.refresh.mock.calls[0][0].signal;
  const dialog = document.createElement('dialog');
  await act(async () => { dialog.setAttribute('open', ''); document.body.appendChild(dialog); await flush(); });
  expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(receipt); await flush(); });
  await act(async () => { dialog.remove(); await flush(); });
  expect(env.refresh).toHaveBeenCalledTimes(1); expect(env.onConfirmed).not.toHaveBeenCalled();
});
test('hidden page has no request; hiding a financial attempt aborts and return never retries unknown automatically', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  visibility = 'hidden'; await env.render(); expect(env.read).not.toHaveBeenCalled();
  await act(async () => { visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')); await flush(); });
  expect(env.refresh).toHaveBeenCalledTimes(1);
  const signal = env.refresh.mock.calls[0][0].signal;
  await act(async () => { visibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange')); await flush(); });
  expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(receipt); await flush(); });
  await act(async () => { visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')); await flush(); });
  expect(env.read).toHaveBeenCalledTimes(2); expect(env.refresh).toHaveBeenCalledTimes(1);
  expect(env.onConfirmed).not.toHaveBeenCalled();
  expect(env.state().phase).toBe('unknown');
});
test('scope or permission change rejects old results and does not erase same-scope unknown protection', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  await env.render();
  await env.render({ context: { ...context, permission_version: 2 } });
  await act(async () => { pending.resolve(receipt); await flush(); });
  await env.render({ context: { ...context, permission_version: 3 } });
  expect(env.refresh).toHaveBeenCalledTimes(1); expect(env.onConfirmed).not.toHaveBeenCalled();
  expect(env.state().phase).toBe('unknown');
});
test('logout aborts and erases visible prior-actor status', async () => {
  const env = setup(), pending = deferred(); env.refresh.mockReturnValueOnce(pending.promise);
  await env.render(); const signal = env.refresh.mock.calls[0][0].signal;
  await env.render({ context: null, identity: 'signed-out' });
  expect(signal.aborted).toBe(true); expect(env.state().snapshot).toBeNull();
  await act(async () => { pending.resolve(receipt); await flush(); });
  expect(env.onConfirmed).not.toHaveBeenCalled();
});
test('only verified reread invalidates visible saved reads, while source failure remains visible', async () => {
  const env = setup();
  env.refresh.mockResolvedValue(receipt);
  env.read.mockResolvedValueOnce(health()).mockResolvedValueOnce(health({ as_of: CURRENT, revision: 11,
    freshness: { state: 'failed', max_age_seconds: 7200 },
    sources: [{ key: 'snapchat', label: 'سناب', state: 'failed', last_success_at: OLD, message: 'مصدر سناب لم يتعافَ' }],
  }));
  await env.render();
  expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  expect(hasBalanceFreshnessWarning(env.state())).toBe(true);
  expect(host.textContent).toContain('مصدر سناب لم يتعافَ');
});
