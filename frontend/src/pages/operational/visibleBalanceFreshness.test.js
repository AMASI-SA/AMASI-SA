import { balanceExpiryDelay, balanceSnapshot, createBalanceFreshnessManager } from './visibleBalanceFreshness';

const NOW = Date.parse('2026-11-23T14:00:00Z');
const OLD = '2026-11-21T14:50:00Z';
const CURRENT = '2026-11-23T13:59:00Z';
const scope = 'scope-owner';
function health(patch = {}) {
  return { schema_version: 1, actor: { id: 'owner', is_owner: true }, session_scope: scope,
    status: 'active', as_of: OLD, revision: 10, generated_at: new Date(NOW).toISOString(),
    freshness: { state: 'stale', max_age_seconds: 7200 }, last_attempt: null,
    sources: [], issues: [], ...patch };
}
const receipt = { status: 'refreshed', session_scope: scope, as_of: CURRENT, revision: 11 };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
function setup({ reads = [health(), health({ as_of: CURRENT, revision: 11, freshness: { state: 'current', max_age_seconds: 7200 } })], response = receipt } = {}) {
  let clock = NOW;
  const read = jest.fn(); reads.forEach(value => read.mockResolvedValueOnce(value));
  const refresh = jest.fn().mockResolvedValue(response);
  const manager = createBalanceFreshnessManager({ read, refresh, now: () => clock });
  const onChange = jest.fn(), onConfirmed = jest.fn();
  const attach = (options = {}) => manager.attach({ actor: 'owner', scope, canRefresh: true, onChange, onConfirmed, ...options });
  return { read, refresh, manager, attach, onChange, onConfirmed, advance: ms => { clock += ms; } };
}

describe('authoritative source freshness', () => {
  test('uses real as_of; two hours exactly or a new Riyadh day is stale', () => {
    const snapshot = (as_of, generated_at) => balanceSnapshot(health({ as_of, generated_at }), scope, 'owner');
    expect(snapshot('2026-11-23T12:00:01Z', '2026-11-23T14:00:00Z').stale).toBe(false);
    expect(snapshot('2026-11-23T12:00:00Z', '2026-11-23T14:00:00Z').stale).toBe(true);
    expect(snapshot('2026-11-23T11:59:59Z', '2026-11-23T14:00:00Z').stale).toBe(true);
    expect(snapshot('2026-11-23T20:59:59Z', '2026-11-23T21:00:00Z').stale).toBe(true);
    expect(balanceSnapshot(health({ revision: 9999 }), scope, 'owner').stale).toBe(true);
  });
  test('local expiry chooses the earlier age threshold or next Riyadh day, using server time', () => {
    const snapshot = balanceSnapshot(health({ as_of: '2026-11-23T20:58:00Z', generated_at: '2026-11-23T20:59:00Z' }), scope, 'owner', NOW);
    expect(balanceExpiryDelay(snapshot, NOW)).toBe(60000);
    const age = balanceSnapshot(health({ as_of: '2026-11-23T12:01:00Z' }), scope, 'owner', NOW);
    expect(balanceExpiryDelay(age, NOW)).toBe(60000);
    expect(balanceExpiryDelay(age, NOW + 60000)).toBe(0);
  });
  test.each([
    { session_scope: 'other' }, { actor: { id: 'employee', is_owner: false } },
    { as_of: 'today' }, { as_of: '2026-11-24T00:00:00Z' }, { revision: -1 },
    { generated_at: 'invalid' }, { schema_version: 0 },
  ])('rejects mismatched or unverified metadata %j', patch => {
    expect(() => balanceSnapshot(health(patch), scope, 'owner')).toThrow();
  });
  test('shows saved metadata before the POST and invalidates only after confirmed saved success', async () => {
    const pending = deferred(), env = setup();
    env.refresh.mockReturnValueOnce(pending.promise);
    const run = env.attach().check();
    await tick();
    expect(env.onChange.mock.calls.some(([state]) => state.snapshot?.as_of === OLD && state.phase === 'refreshing')).toBe(true);
    expect(env.onConfirmed).not.toHaveBeenCalled();
    expect(env.refresh).toHaveBeenCalledWith(expect.objectContaining({ expectedSessionScope: scope }));
    pending.resolve(receipt); await run;
    expect(env.read).toHaveBeenCalledTimes(2);
    expect(env.onConfirmed).toHaveBeenCalledTimes(1);
    expect(env.onConfirmed.mock.calls[0][0].as_of).toBe(CURRENT);
  });
  test.each(['draft', 'frozen', 'accounting_active', 'unknown'])('does not refresh %s systems', async status => {
    const env = setup({ reads: [health({ status })] }); await env.attach().check();
    expect(env.refresh).not.toHaveBeenCalled();
  });
  test('does not POST for a fresh snapshot, missing baseline, or read-only observer', async () => {
    for (const [value, canRefresh] of [[health({ as_of: CURRENT }), true], [health({ as_of: null }), true], [health(), false]]) {
      const env = setup({ reads: [value] }); await env.attach({ canRefresh }).check();
      expect(env.refresh).not.toHaveBeenCalled();
    }
  });
  test('explicit owner source refresh may POST with fresh as_of; automatic open remains read-only', async () => {
    const fresh = health({ as_of: CURRENT });
    const updated = health({ as_of: '2026-11-23T14:00:00Z', revision: 11 });
    const env = setup({ reads: [fresh, fresh, updated], response: { ...receipt, as_of: updated.as_of } });
    const client = env.attach(); await client.check(); expect(env.refresh).not.toHaveBeenCalled();
    await client.check(true);
    expect(env.read).toHaveBeenCalledTimes(3); expect(env.refresh).toHaveBeenCalledTimes(1);
    expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
  test('unavailable diagnostic history blocks both automatic and explicit POST until verified', async () => {
    const blocked = health({ issues: [{ code: 'operational_health_unavailable', source: 'health', severity: 'warning',
      message: 'سجل المحاولات غير متاح', last_success_at: OLD }] });
    const env = setup({ reads: [blocked, blocked] }); const client = env.attach();
    await client.check(); await client.check(true);
    expect(env.refresh).not.toHaveBeenCalled(); expect(env.onChange.mock.calls.at(-1)[0].phase).toBe('unknown');
  });
  test('explicit retry reconciles an ambiguous attempt from a newly advanced fresh snapshot without another POST', async () => {
    const env = setup({ response: { refresh_warning: 'timeout' } }); const client = env.attach();
    await client.check(); env.advance(100000); await client.check(true);
    expect(env.refresh).toHaveBeenCalledTimes(1); expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
  test('one same-scope physical flight survives another consumer closing; observers do not own a POST', async () => {
    const pending = deferred(), env = setup(); env.refresh.mockReturnValueOnce(pending.promise);
    const first = env.attach(), second = env.attach(), observer = env.attach({ canRefresh: false });
    const run = first.check(); second.check(); await tick();
    expect(env.refresh).toHaveBeenCalledTimes(1);
    const signal = env.refresh.mock.calls[0][0].signal;
    first.close(); expect(signal.aborted).toBe(false);
    second.close(); expect(signal.aborted).toBe(true);
    pending.resolve(receipt); await run;
    expect(env.onConfirmed).not.toHaveBeenCalled(); observer.close();
  });
  test('an abort can commit server-side; automatic remount confirms newer saved state without another POST', async () => {
    const pending = deferred(), env = setup(); env.refresh.mockReturnValueOnce(pending.promise);
    const client = env.attach(), run = client.check(); await tick(); client.close();
    pending.resolve(receipt); await run;
    expect(env.onConfirmed).not.toHaveBeenCalled();
    await env.attach().check();
    expect(env.refresh).toHaveBeenCalledTimes(1);
    expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
  test('unknown outcome does not retry automatically or on revision-only changes; explicit retry waits 90s and re-reads', async () => {
    const env = setup({ reads: [health(), health({ revision: 12 }), health({ revision: 12 }), health({ revision: 12 }), health({ as_of: CURRENT, revision: 14 })],
      response: { refresh_warning: 'انتهت المهلة' } });
    const first = env.attach(); await first.check(); first.close();
    const second = env.attach(); await second.check(); await second.check(true);
    expect(env.refresh).toHaveBeenCalledTimes(1);
    env.advance(90000);
    env.refresh.mockResolvedValueOnce({ ...receipt, revision: 13 });
    await second.check(true);
    expect(env.read).toHaveBeenCalledTimes(5);
    expect(env.refresh).toHaveBeenCalledTimes(2);
    expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
  test('explicit retry cannot start until the previous physical POST settles even after 90s', async () => {
    const pending = deferred(), env = setup(); env.refresh.mockReturnValueOnce(pending.promise);
    const first = env.attach(), run = first.check(); await tick(); first.close(); env.advance(100000);
    const second = env.attach(); second.check(true); await tick();
    expect(env.read).toHaveBeenCalledTimes(1); expect(env.refresh).toHaveBeenCalledTimes(1);
    pending.resolve(receipt); await run; second.close();
  });
  test.each(['failed', 'unknown', 'running'])('durable %s attempt blocks automatic retry across a fresh client process', async outcome => {
    const env = setup({ reads: [health({ last_attempt: { started_at: '2026-11-23T13:00:00Z', finished_at: null, outcome, error_code: 'test_failure' } })] });
    await env.attach().check(); expect(env.refresh).not.toHaveBeenCalled();
  });
  test('durable failed attempt allows an explicit aged retry after a fresh server read', async () => {
    const stale = health({ last_attempt: { started_at: '2026-11-23T13:00:00Z', finished_at: '2026-11-23T13:01:00Z', outcome: 'failed', error_code: 'timeout' } });
    const env = setup({ reads: [stale, stale, health({ as_of: CURRENT, revision: 11 })] });
    const client = env.attach(); await client.check(); await client.check(true);
    expect(env.refresh).toHaveBeenCalledTimes(1); expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
  test.each([
    { refresh_warning: 'busy' }, { ...receipt, status: 'accounting_active' }, { ...receipt, session_scope: 'other' },
    { ...receipt, as_of: OLD }, { ...receipt, revision: 1 },
  ])('a fulfilled warning or invalid receipt is not success: %j', async response => {
    const env = setup({ response }); await env.attach().check();
    expect(env.onConfirmed).not.toHaveBeenCalled(); expect(env.read).toHaveBeenCalledTimes(1);
    expect(env.onChange.mock.calls.at(-1)[0].phase).toBe('unknown');
  });
  test('valid receipt followed by failed confirmation remains unknown', async () => {
    const env = setup({ reads: [health()] }); env.read.mockRejectedValueOnce(new Error('offline'));
    await env.attach().check(); expect(env.onConfirmed).not.toHaveBeenCalled();
    expect(env.onChange.mock.calls.at(-1)[0].phase).toBe('unknown');
  });
  test('aggregate recovery keeps every failing source and issue available to the persistent notice', async () => {
    const issue = { code: 'ads_source_stale', source: 'snapchat', severity: 'error', message: 'سناب متأخر', last_success_at: OLD };
    const source = { key: 'snapchat', label: 'سناب', state: 'failed', last_success_at: OLD, data_through: '2026-11-20', expected_through: '2026-11-22', message: 'رفض المصدر' };
    const env = setup({ reads: [health(), health({ as_of: CURRENT, revision: 11, freshness: { state: 'failed', max_age_seconds: 7200 }, sources: [source], issues: [issue] })] });
    await env.attach().check();
    const state = env.onChange.mock.calls.at(-1)[0];
    expect(state.snapshot.sources).toEqual([source]); expect(state.snapshot.issues).toEqual([issue]);
    expect(env.onConfirmed).toHaveBeenCalledTimes(1);
  });
});
