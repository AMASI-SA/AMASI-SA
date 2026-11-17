import http from '../../lib/api';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() }));
beforeEach(() => {
  jest.resetAllMocks();
  http.get.mockResolvedValue({ data: { items: [] } });
  http.post.mockResolvedValue({ data: { saved: true } });
  http.put.mockResolvedValue({ data: { access: { version: 4 } } });
  http.delete.mockResolvedValue({ data: { deleted: true } });
});

test('ledger reads encode resource identifiers and retain cancellation, page size, and focus', async () => {
  const config = { signal: new AbortController().signal };
  const query = { page: 1, page_size: 5, focus_id: 'backdated-entry' };
  await api.ledger('external_person', 'a/b', query, config);
  expect(http.get).toHaveBeenCalledWith('/operational-balances/workspace/ledger/external_person/a%2Fb', {
    timeout: 30000, signal: config.signal, params: query,
  });
  expect(http.post).not.toHaveBeenCalled();
});

test('an explicit write preserves the idempotency key, session binding, and typed source', async () => {
  const body = { request_id: 'one-intent', expected_session_scope: 'same-scope', party_type: 'external_person',
    party_id: 'external', side: 'for_us', amount: '50.00', source_type: 'employee_custody', source_id: 'employee-custody' };
  await api.entry(body);
  expect(http.post).toHaveBeenCalledWith('/operational-balances/workspace/entries', body, { timeout: 30000 });
  expect(http.get).not.toHaveBeenCalled();
});

test('permission save is an explicit version-checked PUT and preserves denial', async () => {
  const body = { expected_version: 3, enabled: true, operations: ['external.pay'], resources: [] };
  await api.saveUserAccess('staff/one', body);
  expect(http.put).toHaveBeenCalledWith('/operational-balances/access/users/staff%2Fone', body, { timeout: 30000 });
  const denied = { response: { status: 403 } };
  http.get.mockRejectedValueOnce(denied);
  await expect(api.context()).rejects.toBe(denied);
  expect(http.post).not.toHaveBeenCalled();
});

test('refund requests bind the order and confirmations do not add editable source fields', async () => {
  const request = { request_id: 'refund-intent', expected_session_scope: 'scope', order_number: '2044',
    amount: '50.00', method: 'bank', bank_id: 'bank', reason: 'رد جزئي' };
  await api.requestRefund(request);
  expect(http.post).toHaveBeenLastCalledWith('/operational-balances/workspace/refunds', request, { timeout: 30000 });
  const confirmation = { request_id: 'confirm-intent', expected_session_scope: 'scope', confirmed: true, reference: '', executed_at: '2026-11-17T10:00:00Z' };
  await api.confirmRefund('refund/one', confirmation);
  expect(http.post).toHaveBeenLastCalledWith('/operational-balances/workspace/refunds/refund%2Fone/confirm', confirmation, { timeout: 30000 });
  const proof = { request_id: 'match-intent', expected_session_scope: 'scope', evidence_id: 'fact', expected_evidence_hash: 'a'.repeat(64) };
  await api.reconcileRefund('refund/one', proof);
  expect(http.post).toHaveBeenLastCalledWith('/operational-balances/workspace/refunds/refund%2Fone/reconcile', proof, { timeout: 30000 });
});

test('pending-command registration and recovery use scoped authenticated endpoints', async () => {
  const record = { action: 'entry', target_id: null, command: { request_id: 'opaque', expected_session_scope: 'scope' } };
  await api.registerPending('opaque/id', record);
  expect(http.post).toHaveBeenLastCalledWith('/operational-balances/workspace/pending-commands/opaque%2Fid', record, { timeout: 30000 });
  const signal = new AbortController().signal;
  await api.pendingCommand('opaque/id', 'scope', { signal });
  expect(http.get).toHaveBeenLastCalledWith('/operational-balances/workspace/pending-commands/opaque%2Fid', { timeout: 30000, signal, params: { expected_session_scope: 'scope' } });
  await api.retirePending('opaque/id', 'scope');
  expect(http.delete).toHaveBeenLastCalledWith('/operational-balances/workspace/pending-commands/opaque%2Fid', { timeout: 30000, data: { expected_session_scope: 'scope' } });
});
