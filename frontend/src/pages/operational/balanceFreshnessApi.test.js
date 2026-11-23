import http from '../../lib/api';
import { balanceFreshnessManager, readBalanceFreshness } from './balanceFreshnessApi';

// These are real repository modules. A virtual mock can acquire a different
// resolver identity after another CRA suite has loaded the physical module.
jest.mock('../../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock('../../lib/demandRead', () => ({ demandRead: jest.fn() }));
const OLD = '2026-11-21T14:50:00Z', CURRENT = '2026-11-23T13:59:00Z';
const health = (scope, patch = {}) => ({ schema_version: 1, actor: { id: 'owner', is_owner: true }, session_scope: scope,
  status: 'active', as_of: OLD, revision: 10, generated_at: '2026-11-23T14:00:00Z',
  freshness: { state: 'stale', max_age_seconds: 7200 }, last_attempt: null, sources: [], issues: [], ...patch });
let network;
beforeEach(() => {
  jest.clearAllMocks();
  network = jest.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => {
    throw new Error('Unexpected real network request in freshness API contract test');
  });
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});
afterEach(() => {
  try { expect(network).not.toHaveBeenCalled(); } finally { network.mockRestore(); }
});
test('health reader calls only the lightweight read-only endpoint with cancellation', async () => {
  const controller = new AbortController(); http.get.mockResolvedValueOnce({ data: health('read') });
  await readBalanceFreshness({ signal: controller.signal });
  expect(http.get).toHaveBeenCalledWith('/operational-balances/freshness', { timeout: 15000, signal: controller.signal });
  expect(http.post).not.toHaveBeenCalled();
});
test('actual API adapter preserves the server visible/scope body and 90-second refresh timeout', async () => {
  const scope = 'api-contract';
  http.get.mockResolvedValueOnce({ data: health(scope) }).mockResolvedValueOnce({ data: health(scope, { as_of: CURRENT, revision: 11 }) });
  http.post.mockResolvedValueOnce({ data: { status: 'refreshed', session_scope: scope, as_of: CURRENT, revision: 11 } });
  const onConfirmed = jest.fn();
  const client = balanceFreshnessManager.attach({ actor: 'owner', scope, canRefresh: true, onChange: jest.fn(), onConfirmed });
  await client.check();
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(http.post).toHaveBeenCalledWith('/operational-balances/refresh', { visible: true, expected_session_scope: scope },
    expect.objectContaining({ timeout: 90000, signal: expect.any(AbortSignal) }));
  expect(http.get).toHaveBeenCalledTimes(2); expect(onConfirmed).toHaveBeenCalledTimes(1); client.close();
});
test('existing refresh helper lease-warning resolution cannot report success', async () => {
  const scope = 'api-lease'; http.get.mockResolvedValueOnce({ data: health(scope) });
  http.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: 'operational_refresh_in_progress' } } } });
  const onConfirmed = jest.fn(), onChange = jest.fn();
  const client = balanceFreshnessManager.attach({ actor: 'owner', scope, canRefresh: true, onChange, onConfirmed });
  await client.check();
  expect(http.post).toHaveBeenCalledTimes(1); expect(http.get).toHaveBeenCalledTimes(1);
  expect(onConfirmed).not.toHaveBeenCalled(); expect(onChange.mock.calls.at(-1)[0].phase).toBe('unknown'); client.close();
});
