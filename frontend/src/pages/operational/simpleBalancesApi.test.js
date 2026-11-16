import http from '../../lib/api';
import { simpleBalancesApi as api } from './simpleBalancesApi';

jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
beforeEach(() => {
  jest.resetAllMocks();
  http.get.mockResolvedValue({ data: { items: [] } });
  http.post.mockResolvedValue({ data: { saved: true } });
  http.put.mockResolvedValue({ data: { access: { version: 4 } } });
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
