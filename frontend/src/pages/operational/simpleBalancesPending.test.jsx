import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { usePendingBalanceCommand } from './simpleBalancesPending';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { registerPending: jest.fn(), pendingCommand: jest.fn(), retirePending: jest.fn() },
  simpleBalanceError: error => error?.localMessage || 'تعذر التحقق من العملية' }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const context = patch => ({ actor: { id: 'staff' }, session_scope: 'scope', permission_version: 1, ...patch });
const failure = (status, code) => ({ response: { status, data: { detail: { code } } } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const body = amount => ({ amount, party_type: 'external_person', party_id: 'synthetic-party', side: 'for_us', note: 'ملاحظة توضيحية', business_date: '2026-11-01' });
let host, root, latest, execute, saved, retired, records, counter;
function Harness({ ctx = context(), run = execute, onSaved = saved, validateSaved = value => value.target === 'synthetic-party' }) {
  latest = usePendingBalanceCommand({ context: ctx, storageIdentity: JSON.stringify(['test-entry', ctx.actor.id, ctx.session_scope, 'synthetic-party']),
    validate: value => value.action === 'entry' && value.target_id === null && value.command.party_id === 'synthetic-party',
    execute: run, validateSaved, onSaved, onRetired: retired });
  return <p>{latest.busy ? 'busy' : latest.recovering ? 'recovering' : latest.blocked ? 'blocked' : 'ready'}</p>;
}
const render = async props => act(async () => root.render(<Harness {...props} />));
const submit = async amount => act(async () => latest.submit('entry', null, () => body(amount)));
const remount = async props => { await act(async () => root.render(null)); await render(props); };
beforeEach(() => {
  jest.clearAllMocks(); localStorage.clear(); records = new Map(); counter = 0;
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  Object.defineProperty(globalThis, 'TextEncoder', { configurable: true, value: require('util').TextEncoder });
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: {
    randomUUID: () => `00000000-0000-4000-8000-${String(++counter).padStart(12, '0')}`,
    subtle: { digest: async (algorithm, input) => Uint8Array.from(require('crypto').createHash('sha256').update(input).digest()).buffer },
  } });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  execute = jest.fn().mockResolvedValue({ saved: true, target: 'synthetic-party' }); saved = jest.fn(); retired = jest.fn();
  api.registerPending.mockImplementation(async (id, record) => { const value = { id, ...record }; records.set(id, value); return value; });
  api.pendingCommand.mockImplementation(async id => { if (records.has(id)) return records.get(id); throw failure(404, 'operational_pending_command_not_found'); });
  api.retirePending.mockImplementation(async id => { records.delete(id); return { id, deleted: true }; });
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

test('only a hashed slot and opaque UUID persist before registration; an ambiguous registration cannot send money', async () => {
  const registration = deferred(); let persistedBeforeRegistration;
  api.registerPending.mockImplementationOnce((id) => { persistedBeforeRegistration = localStorage.getItem(localStorage.key(0)) === id; return registration.promise; });
  await render(); let task;
  await act(async () => { task = latest.submit('entry', null, () => body('50.00')); });
  expect(localStorage.length).toBe(1);
  const key = localStorage.key(0), id = localStorage.getItem(key);
  expect(key).toMatch(/^mezan\.pending-command\.v1:[a-f0-9]{64}$/);
  expect(id).toMatch(/^[a-f0-9-]{36}$/); expect(key).not.toContain('synthetic-party'); expect(key).not.toContain('staff');
  expect(api.registerPending.mock.calls[0][0]).toBe(id);
  expect(persistedBeforeRegistration).toBe(true);
  expect(api.registerPending.mock.calls[0][1].command.request_id).toBe(id);
  expect(execute).not.toHaveBeenCalled();
  await act(async () => { registration.reject(new Error('registration response unknown')); await task; });
  expect(execute).not.toHaveBeenCalled(); expect(api.retirePending).not.toHaveBeenCalled();
  expect(localStorage.getItem(key)).toBe(id); expect(latest.pending.command.amount).toBe('50.00');
});

test('storage failure prevents both registration and financial execution', async () => {
  await render();
  const writing = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage unavailable'); });
  try {
    await submit('50.00');
    expect(api.registerPending).not.toHaveBeenCalled(); expect(execute).not.toHaveBeenCalled();
    expect(latest.error).toContain('تعذر حفظ مرجع العملية');
  } finally { writing.mockRestore(); }
});

test('a malformed registration acknowledgement never authorizes a financial POST', async () => {
  api.registerPending.mockImplementationOnce(async (id, record) => ({ id: 'wrong-record', ...record }));
  await render(); await submit('50.00');
  expect(execute).not.toHaveBeenCalled(); expect(api.retirePending).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(1); expect(latest.pending.command.request_id).toBe(localStorage.getItem(localStorage.key(0)));
});

test('a fresh mount only recovers the registered command; the explicit retry sends its original body and request ID', async () => {
  api.registerPending.mockImplementationOnce(async (id, record) => { records.set(id, { id, ...record }); throw new Error('lost registration reply'); });
  await render(); await submit('50.00');
  const original = [...records.values()][0];
  await remount();
  expect(api.pendingCommand).toHaveBeenCalledWith(original.id, 'scope', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(execute).not.toHaveBeenCalled(); expect(latest.pending).toEqual(original);
  await submit('999.00');
  expect(execute).toHaveBeenCalledTimes(1); expect(execute).toHaveBeenCalledWith(original);
  expect(counter).toBe(1); expect(saved).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(0);
});

test('a confirmed missing registration after refresh is explained and retains the same UUID when re-entered', async () => {
  api.registerPending.mockRejectedValueOnce(new Error('request did not reach the server'));
  await render(); await submit('50.00'); const id = localStorage.getItem(localStorage.key(0));
  await remount();
  expect(latest.pending).toBeNull(); expect(latest.error).toContain('لم يصل حفظ بيانات العملية');
  expect(latest.blocked).toBe(false); expect(execute).not.toHaveBeenCalled();
  await submit('40.00');
  expect(api.registerPending.mock.calls[1][0]).toBe(id);
  expect(execute.mock.calls[0][0].command).toEqual(expect.objectContaining({ request_id: id, amount: '40.00' }));
  expect(counter).toBe(1);
});

test('a forbidden recovery retains its marker and cannot execute until an explicit reauthorized read', async () => {
  execute.mockRejectedValueOnce(new Error('financial reply unknown'));
  await render(); await submit('50.00'); const key = localStorage.key(0), id = localStorage.getItem(key);
  api.pendingCommand.mockRejectedValueOnce(failure(403, 'operational_resource_forbidden'));
  await remount();
  expect(latest.blocked).toBe(true); expect(latest.recovering).toBe(false); expect(localStorage.getItem(key)).toBe(id);
  await submit('500.00'); expect(execute).toHaveBeenCalledTimes(1); expect(counter).toBe(1);
  await act(async () => latest.recover());
  expect(latest.blocked).toBe(false); expect(execute).toHaveBeenCalledTimes(1);
  await submit('500.00'); expect(execute).toHaveBeenCalledTimes(2);
  expect(execute.mock.calls[1][0].command.amount).toBe('50.00');
});

test('scope changes during registration reset busy and an old completion neither executes nor unlocks the new attempt', async () => {
  const oldRegistration = deferred(), newExecution = deferred();
  api.registerPending.mockReturnValueOnce(oldRegistration.promise);
  const newExecute = jest.fn().mockReturnValue(newExecution.promise), newSaved = jest.fn();
  await render(); let first, second;
  await act(async () => { first = latest.submit('entry', null, () => body('50.00')); });
  const oldValue = { id: api.registerPending.mock.calls[0][0], ...api.registerPending.mock.calls[0][1] };
  await render({ ctx: context({ actor: { id: 'other' }, session_scope: 'other-scope' }), run: newExecute, onSaved: newSaved });
  expect(latest.busy).toBe(false); expect(latest.recovering).toBe(false);
  await act(async () => { second = latest.submit('entry', null, () => body('25.00')); });
  expect(newExecute).toHaveBeenCalledTimes(1);
  await act(async () => { oldRegistration.resolve(oldValue); await first; });
  expect(execute).not.toHaveBeenCalled(); expect(saved).not.toHaveBeenCalled(); expect(latest.busy).toBe(true);
  await submit('999.00'); expect(newExecute).toHaveBeenCalledTimes(1);
  await act(async () => { newExecution.resolve({ saved: true, target: 'synthetic-party' }); await second; });
  expect(newSaved).toHaveBeenCalledTimes(1); expect(latest.busy).toBe(false);
});

test('permission changes during a financial POST do not publish its late result or leave the new view busy', async () => {
  const payment = deferred(); execute.mockReturnValueOnce(payment.promise);
  const originalValidation = jest.fn(value => value.target === 'synthetic-party');
  await render({ validateSaved: originalValidation }); let task;
  await act(async () => { task = latest.submit('entry', null, () => body('50.00')); });
  expect(execute).toHaveBeenCalledTimes(1);
  api.pendingCommand.mockRejectedValueOnce(failure(403, 'operational_resource_forbidden'));
  const newValidation = jest.fn(() => false);
  await render({ ctx: context({ permission_version: 2 }), validateSaved: newValidation });
  expect(latest.busy).toBe(false); expect(latest.blocked).toBe(true);
  await act(async () => { payment.resolve({ saved: true, target: 'synthetic-party' }); await task; });
  expect(originalValidation).toHaveBeenCalledTimes(1); expect(newValidation).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled(); expect(latest.busy).toBe(false); expect(latest.blocked).toBe(true);
});

test('a registration conflict restores the existing immutable command without executing the replacement', async () => {
  api.registerPending.mockImplementationOnce(async (id, record) => { records.set(id, { id, ...record }); throw new Error('lost reply'); });
  await render(); await submit('50.00');
  api.pendingCommand.mockRejectedValueOnce(failure(404, 'operational_pending_command_not_found'));
  await remount();
  api.registerPending.mockRejectedValueOnce(failure(409, 'operational_pending_command_conflict'));
  await submit('99.00');
  expect(latest.blocked).toBe(true); expect(execute).not.toHaveBeenCalled();
  await act(async () => latest.recover());
  expect(latest.pending.command.amount).toBe('50.00'); expect(execute).not.toHaveBeenCalled();
  await submit('99.00'); expect(execute.mock.calls[0][0].command.amount).toBe('50.00'); expect(counter).toBe(1);
});

test('a saved operation remains successful when retiring recovery metadata fails and cannot submit twice', async () => {
  api.retirePending.mockRejectedValueOnce(new Error('cleanup unavailable'));
  await render(); await submit('50.00'); await submit('500.00');
  expect(saved).toHaveBeenCalledTimes(1); expect(execute).toHaveBeenCalledTimes(1); expect(localStorage.length).toBe(1);
  expect(localStorage.getItem(localStorage.key(0))).toMatch(/^[a-f0-9-]{36}$/);
});

test('a retired marker is removed only after the server confirms retirement and the ledger is requested', async () => {
  execute.mockRejectedValueOnce(new Error('unknown financial reply'));
  await render(); await submit('50.00');
  api.pendingCommand.mockRejectedValueOnce(failure(410, 'operational_pending_command_retired'));
  await remount();
  expect(localStorage.length).toBe(0); expect(retired).toHaveBeenCalledTimes(1);
  expect(execute).toHaveBeenCalledTimes(1); expect(latest.blocked).toBe(true);
});
