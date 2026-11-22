import http from './api';
import { createReadDemand, demandRead } from './demandRead';

jest.mock('./api', () => ({ delete: jest.fn() }));
const flush = async () => { for (let n = 0; n < 8; n += 1) await Promise.resolve(); };
const pending = { status: 202, data: { status: 'preparing', key: 'projection-1', retry_after_ms: 500 } };
let visibility, online;
beforeEach(() => {
  jest.useFakeTimers(); jest.clearAllMocks(); http.delete.mockResolvedValue({ data: { released: true } });
  visibility = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  online = jest.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });

test('only an explicit preparation polls, with one consumer, then a ready result remains complete', async () => {
  const scope = createReadDemand(), controller = new AbortController();
  const request = jest.fn().mockResolvedValueOnce(pending).mockResolvedValue({ status: 200, data: { total: '810.34' } });
  const result = demandRead(request, { signal: controller.signal, readDemand: scope });
  await flush(); expect(request).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(500); await flush();
  await expect(result).resolves.toEqual({ total: '810.34' });
  expect(request).toHaveBeenCalledTimes(2);
  expect(request.mock.calls.map(([config]) => config.headers['X-Mezan-Read-Consumer'])).toEqual([scope.consumerId, scope.consumerId]);
  expect(request.mock.calls[0][0].headers['X-Mezan-Read-Poll']).toBeUndefined();
  expect(request.mock.calls[1][0].headers['X-Mezan-Read-Poll']).toBe('1');
  jest.advanceTimersByTime(60000); await flush(); expect(request).toHaveBeenCalledTimes(2);
  scope.dispose(); scope.dispose();
  await flush();
  expect(http.delete).toHaveBeenCalledTimes(1);
  expect(http.delete).toHaveBeenCalledWith('/operational-balances/workspace/read-demand/projection-1', expect.objectContaining({ params: { consumer_id: scope.consumerId } }));
});

test('aborting preparation clears the poll and releases only its own consumer', async () => {
  const scope = createReadDemand(), controller = new AbortController(), request = jest.fn().mockResolvedValue(pending);
  const result = demandRead(request, { signal: controller.signal, readDemand: scope });
  const failure = expect(result).rejects.toMatchObject({ name: 'AbortError' });
  await flush(); controller.abort(); await failure;
  jest.advanceTimersByTime(60000); await flush();
  expect(request).toHaveBeenCalledTimes(1); expect(http.delete).toHaveBeenCalledTimes(1);
});

test.each(['hidden', 'offline'])('%s starts no network request', async state => {
  if (state === 'hidden') visibility.mockReturnValue('hidden'); else online.mockReturnValue(false);
  const request = jest.fn();
  await expect(demandRead(request)).rejects.toMatchObject({ name: 'AbortError' });
  expect(request).not.toHaveBeenCalled();
});

test('a failed preparation is an error and cannot become an endless poll', async () => {
  const request = jest.fn().mockResolvedValue({ status: 200, data: { status: 'failed' } });
  await expect(demandRead(request)).rejects.toMatchObject({ response: { status: 503 } });
  jest.advanceTimersByTime(60000); await flush(); expect(request).toHaveBeenCalledTimes(1);
});

test('a family release URL is used without crossing into another origin', async () => {
  const scope = createReadDemand(), controller = new AbortController();
  const request = jest.fn().mockResolvedValue({ ...pending, data: { ...pending.data, release_url: '/preparation-work-v1/assembly/read-demand/key' } });
  const result = demandRead(request, { signal: controller.signal, readDemand: scope });
  const failure = expect(result).rejects.toMatchObject({ name: 'AbortError' });
  await flush(); controller.abort(); await failure;
  expect(http.delete.mock.calls[0][0]).toBe('/preparation-work-v1/assembly/read-demand/key');
  await expect(demandRead(() => Promise.resolve({ ...pending, data: { ...pending.data, release_url: 'https://elsewhere.invalid/release' } }))).rejects.toThrow('Invalid read demand');
});

test('a late preparing response after cancellation releases interest without polling', async () => {
  let finish;
  const request = jest.fn(() => new Promise(resolve => { finish = resolve; }));
  const controller = new AbortController(), scope = createReadDemand();
  const result = demandRead(request, { signal: controller.signal, readDemand: scope });
  const failure = expect(result).rejects.toMatchObject({ name: 'AbortError' });
  controller.abort(); finish(pending); await failure;
  expect(http.delete).toHaveBeenCalledTimes(1); expect(request).toHaveBeenCalledTimes(1);
});
