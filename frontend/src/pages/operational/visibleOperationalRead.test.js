import {visibleOperationalRead, refreshVisibleSnapshot, logOperationalRefreshTimeoutTimings} from './visibleOperationalRead';

const flush = async () => { for (let index = 0; index < 8; index += 1) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; };
let visibility, online;
beforeEach(() => {
  visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  online = jest.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});
afterEach(() => jest.restoreAllMocks());

test('hidden mount does no work; one visible completion never polls or refreshes on focus/visibility', async () => {
  visibility.mockReturnValue('hidden');
  const read = jest.fn().mockResolvedValue('saved'), onData = jest.fn();
  const stop = visibleOperationalRead(read, {onData});
  await flush(); expect(read).not.toHaveBeenCalled();
  visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'));
  await flush(); expect(read).toHaveBeenCalledTimes(1); expect(onData).toHaveBeenCalledWith('saved');
  visibility.mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'));
  visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online'));
  await flush(); expect(read).toHaveBeenCalledTimes(1); stop();
});

test('hiding aborts a pending read and its result; cleanup prevents later visibility work', async () => {
  const pending = deferred(), read = jest.fn().mockReturnValue(pending.promise), onData = jest.fn();
  const stop = visibleOperationalRead(read, {onData}); await flush();
  const signal = read.mock.calls[0][0].signal;
  visibility.mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'));
  expect(signal.aborted).toBe(true); pending.resolve('obsolete'); await flush();
  expect(onData).not.toHaveBeenCalled(); expect(read).toHaveBeenCalledTimes(1);
  stop(); visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'));
  await flush(); expect(read).toHaveBeenCalledTimes(1);
});

test('replacing a pending read waits for its cancellation and never displays its old scope', async () => {
  const old = deferred(), lane = {}, onOld = jest.fn(), onNew = jest.fn();
  const first = jest.fn().mockReturnValue(old.promise), second = jest.fn().mockResolvedValue('new');
  const stopFirst = visibleOperationalRead(first, {onData: onOld}, lane); await flush(); stopFirst();
  const stopSecond = visibleOperationalRead(second, {onData: onNew}, lane); await flush();
  expect(first.mock.calls[0][0].signal.aborted).toBe(true); expect(second).not.toHaveBeenCalled();
  old.resolve('old'); await flush();
  expect(onOld).not.toHaveBeenCalled(); expect(second).toHaveBeenCalledTimes(1); expect(onNew).toHaveBeenCalledWith('new'); stopSecond();
});

test('offline reads wait for a user page to become active', async () => {
  online.mockReturnValue(false); const read = jest.fn().mockResolvedValue('ready');
  const stop = visibleOperationalRead(read); await flush(); expect(read).not.toHaveBeenCalled();
  online.mockReturnValue(true); window.dispatchEvent(new Event('online')); await flush();
  expect(read).toHaveBeenCalledTimes(1); stop();
});

test.each(['operational_refresh_in_progress', 'operational_capacity_requires_archive', 'operational_source_setup_incomplete'])('recoverable %s keeps a warning for the saved snapshot without retry', async code => {
  const api = {refreshBalances: jest.fn().mockRejectedValue({response: {status: 409, data: {detail: {code, message: 'سبب المصدر'}}}})};
  expect((await refreshVisibleSnapshot(api, {})).refresh_warning).toBeTruthy();
  expect(api.refreshBalances).toHaveBeenCalledTimes(1);
});

test.each([401, 403])('permission failure %s never falls back to financial data', async status => {
  const failure = {response: {status}}, api = {refreshBalances: jest.fn().mockRejectedValue(failure)};
  await expect(refreshVisibleSnapshot(api, {})).rejects.toBe(failure);
});

test('abort during refresh blocks any subsequent report fetch', async () => {
  const pending = deferred(), controller = new AbortController(), api = {refreshBalances: jest.fn().mockReturnValue(pending.promise)};
  const result = refreshVisibleSnapshot(api, {signal: controller.signal});
  controller.abort(); pending.resolve({status: 'refreshed'});
  await expect(result).rejects.toHaveProperty('name', 'AbortError');
});

test('refresh timeout evidence logs only allowed finite stage fields, once for that request', async () => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {});
  const timings = [{stage: 'source_orders', duration_ms: 12.345, outcome: 'complete', owner_id: 'PRIVATE-OWNER'},
    {stage: 'aggregate', duration_ms: 85000, outcome: 'cancelled', message: 'PRIVATE-MESSAGE'},
    ...[true, null, '12', -1, NaN, Infinity, 300001].map(duration_ms => ({stage: 'snapshot_load', duration_ms, outcome: 'complete'})),
    {stage: 'PRIVATE-STAGE', duration_ms: 12, outcome: 'complete'}, {stage: 'aggregate', duration_ms: 12, outcome: 'PRIVATE-OUTCOME'}, null];
  const api = {refreshBalances: jest.fn().mockRejectedValue({response: {status: 408, data: {detail: {
    code: 'operational_refresh_timeout', stage_timings: timings, secret: 'PRIVATE-DETAIL'}}}, config: {headers: {Authorization: 'PRIVATE-TOKEN'}}})};
  expect((await refreshVisibleSnapshot(api, {})).refresh_warning).toBeTruthy();
  expect(info).toHaveBeenCalledTimes(1);
  expect(info).toHaveBeenCalledWith('operational_refresh_timeout_stage_timings', [
    {stage: 'source_orders', duration_ms: 12.345, outcome: 'complete'},
    {stage: 'aggregate', duration_ms: 85000, outcome: 'cancelled'},
  ]);
  expect(JSON.stringify(info.mock.calls)).not.toContain('PRIVATE');
  window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')); await flush();
  expect(info).toHaveBeenCalledTimes(1);
  expect(api.refreshBalances).toHaveBeenCalledTimes(1);
});

test('timing evidence keeps at most the final 64 valid stages, including terminal cancellation', () => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {});
  const stage_timings = Array.from({length: 70}, (_, index) => ({stage: 'source_orders', duration_ms: index, outcome: 'complete'}));
  stage_timings.push({stage: 'aggregate', duration_ms: 300000, outcome: 'cancelled'});
  logOperationalRefreshTimeoutTimings({stage_timings});
  expect(info.mock.calls[0][1]).toHaveLength(64);
  expect(info.mock.calls[0][1][0].duration_ms).toBe(7);
  expect(info.mock.calls[0][1].at(-1)).toEqual({stage: 'aggregate', duration_ms: 300000, outcome: 'cancelled'});
  info.mockClear();
  [undefined, {}, {stage_timings: {}}, {stage_timings: [null, {stage: 'private'}]}].forEach(logOperationalRefreshTimeoutTimings);
  expect(info).not.toHaveBeenCalled();
});

test.each([[500, 'operational_refresh_timeout'], [408, 'operational_source_read_timeout']])('status %s and code %s do not log refresh timing evidence', async (status, code) => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {});
  const api = {refreshBalances: jest.fn().mockRejectedValue({response: {status, data: {detail: {code,
    stage_timings: [{stage: 'aggregate', duration_ms: 1, outcome: 'cancelled'}]}}}})};
  await refreshVisibleSnapshot(api, {});
  expect(info).not.toHaveBeenCalled();
});

test.each(['hidden', 'aborted'])('late timeout after %s is not logged or converted to saved-read permission', async cause => {
  const info = jest.spyOn(console, 'info').mockImplementation(() => {}), controller = new AbortController(), pending = deferred();
  const api = {refreshBalances: jest.fn().mockReturnValue(pending.promise)};
  const result = refreshVisibleSnapshot(api, {signal: controller.signal});
  if (cause === 'hidden') visibility.mockReturnValue('hidden'); else controller.abort();
  const failure = {response: {status: 408, data: {detail: {code: 'operational_refresh_timeout',
    stage_timings: [{stage: 'aggregate', duration_ms: 1, outcome: 'cancelled'}]}}}};
  pending.reject(failure);
  await expect(result).rejects.toBe(failure);
  expect(info).not.toHaveBeenCalled();
});

describe('a bounded financial read owns only one visible attempt', () => {
  const policy = {singleAttemptTimeoutMs: 150000};
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  test('an initially hidden page defers without spending its active budget', async () => {
    visibility.mockReturnValue('hidden');
    const read = jest.fn().mockResolvedValue('ready'), onData = jest.fn(), onError = jest.fn();
    const stop = visibleOperationalRead(read, {onData, onError}, {}, policy);
    jest.advanceTimersByTime(300000); await flush();
    expect(read).not.toHaveBeenCalled(); expect(onError).not.toHaveBeenCalled();
    visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'));
    await flush();
    expect(read).toHaveBeenCalledTimes(1); expect(onData).toHaveBeenCalledWith('ready');
    stop();
  });

  test.each(['visibility', 'connection'])('%s interruption consumes the attempt rather than restarting it repeatedly', async cause => {
    const read = jest.fn(({signal}) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), {once: true});
    }));
    const onPaused = jest.fn(), onData = jest.fn(), onError = jest.fn(), onEnd = jest.fn();
    const stop = visibleOperationalRead(read, {onPaused, onData, onError, onEnd}, {}, policy);
    await flush();
    if (cause === 'visibility') {
      visibility.mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'));
    } else {
      online.mockReturnValue(false); window.dispatchEvent(new Event('offline'));
    }
    await flush();
    for (let index = 0; index < 30; index += 1) {
      visibility.mockReturnValue('visible'); online.mockReturnValue(true);
      document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('online'));
      window.dispatchEvent(new Event('focus')); await flush();
    }
    jest.advanceTimersByTime(300000); await flush();
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0][0].signal.aborted).toBe(true);
    expect(onPaused).toHaveBeenCalledTimes(1); expect(onEnd).toHaveBeenCalledTimes(1);
    expect(onData).not.toHaveBeenCalled(); expect(onError).not.toHaveBeenCalled();
    stop();
  });

  test('a promise that ignores abort cannot keep presentation loading past the deadline', async () => {
    const pending = deferred(), lane = {}, read = jest.fn().mockReturnValue(pending.promise);
    const onData = jest.fn(), onError = jest.fn(), onEnd = jest.fn();
    const stop = visibleOperationalRead(read, {onData, onError, onEnd}, lane, policy);
    await flush(); const physicalRequest = lane.pending;
    jest.advanceTimersByTime(149999); await flush(); expect(onError).not.toHaveBeenCalled();
    jest.advanceTimersByTime(1); await flush();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].code).toBe('OPERATIONAL_VISIBLE_READ_TIMEOUT');
    expect(onEnd).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0][0].signal.aborted).toBe(true);
    expect(lane.pending).toBe(physicalRequest);
    pending.resolve('late'); await flush();
    expect(onData).not.toHaveBeenCalled(); expect(lane.pending).toBeNull();
    stop();
  });

  test('waiting for cancelled physical work has the same deadline and never releases its reservation early', async () => {
    const old = deferred(), lane = {}, first = jest.fn().mockReturnValue(old.promise);
    const stopFirst = visibleOperationalRead(first, {}, lane, policy); await flush(); stopFirst();
    const physicalRequest = lane.pending;
    const second = jest.fn().mockResolvedValue('second'), onError = jest.fn(), onData = jest.fn();
    const stopSecond = visibleOperationalRead(second, {onError, onData}, lane, policy); await flush();
    jest.advanceTimersByTime(150000); await flush();
    expect(second).not.toHaveBeenCalled(); expect(onError).toHaveBeenCalledTimes(1);
    expect(lane.pending).toBe(physicalRequest);
    old.resolve('old'); await flush();
    expect(onData).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled(); expect(lane.pending).toBeNull();
    stopSecond();
    const third = jest.fn().mockResolvedValue('manual retry');
    const stopThird = visibleOperationalRead(third, {onData}, lane, policy); await flush();
    expect(third).toHaveBeenCalledTimes(1); expect(onData).toHaveBeenCalledWith('manual retry'); stopThird();
  });

  test('lane waiting and the subsequent request share one auth-chain deadline', async () => {
    const old = deferred(), lane = {};
    const stopOld = visibleOperationalRead(() => old.promise, {}, lane, policy); await flush(); stopOld();
    const startedAt = Date.now(), request = deferred(), read = jest.fn().mockReturnValue(request.promise), onError = jest.fn();
    const stop = visibleOperationalRead(read, {onError}, lane, policy);
    jest.advanceTimersByTime(50000); old.resolve('old'); await flush();
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0][0]._mezanAuthDeadlineAt).toBe(startedAt + 150000);
    jest.advanceTimersByTime(99999); await flush(); expect(onError).not.toHaveBeenCalled();
    jest.advanceTimersByTime(1); await flush(); expect(onError).toHaveBeenCalledTimes(1);
    stop(); request.resolve('late'); await flush();
  });

  test('a hidden confirmation follow-up is consumed immediately instead of deferred', async () => {
    visibility.mockReturnValue('hidden');
    const read = jest.fn().mockResolvedValue('ready'), onPaused = jest.fn();
    const stop = visibleOperationalRead(read, {onPaused}, {}, {...policy, deferUntilVisible: false});
    await flush();
    visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange')); await flush();
    expect(onPaused).toHaveBeenCalledTimes(1); expect(read).not.toHaveBeenCalled(); stop();
  });

  test('success and cleanup leave no timer or activity-triggered requests', async () => {
    const read = jest.fn().mockResolvedValue('ready'), onData = jest.fn(), onError = jest.fn(), onPaused = jest.fn();
    const stop = visibleOperationalRead(read, {onData, onError, onPaused}, {}, policy); await flush();
    visibility.mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'));
    visibility.mockReturnValue('visible'); document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('online')); window.dispatchEvent(new Event('focus'));
    jest.advanceTimersByTime(300000); await flush();
    expect(read).toHaveBeenCalledTimes(1); expect(onData).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled(); expect(onPaused).not.toHaveBeenCalled(); stop();
    const pending = deferred();
    const stoppedRead = jest.fn().mockReturnValue(pending.promise);
    const clean = visibleOperationalRead(stoppedRead, {onData, onError, onPaused}, {}, policy); await flush(); clean();
    expect(stoppedRead.mock.calls[0][0].signal.aborted).toBe(true);
    jest.advanceTimersByTime(300000); pending.resolve('late after unmount'); await flush();
    expect(onData).toHaveBeenCalledTimes(1); expect(onError).not.toHaveBeenCalled(); expect(onPaused).not.toHaveBeenCalled();
    expect(jest.getTimerCount()).toBe(0);
  });
});
