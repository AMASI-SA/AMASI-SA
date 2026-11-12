import {visibleOperationalRead, refreshVisibleSnapshot} from './visibleOperationalRead';

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
