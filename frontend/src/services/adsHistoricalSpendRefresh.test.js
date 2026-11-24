import api from '../lib/api';
import { historicalSpendRange, normalizeHistoricalSpendRefresh, readHistoricalAdSpend, refreshHistoricalAdSpend } from './adsHistoricalSpendRefresh';

jest.mock('../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock('../lib/dates', () => ({ todaySA: () => '2026-11-24' }));

const range = { dateFrom: '2026-11-20', dateTo: '2026-11-23', days: 4 };
const flags = { source_only: true, provider_write_reached: false, campaign_write_reached: false,
  accounting_write_reached: false, qoyod_write_reached: false };
function response() {
  return { date_from: range.dateFrom, date_to: range.dateTo,
    refresh: { ...flags, date_from: range.dateFrom, date_to: range.dateTo, status: 'partial', results: [
      { provider: 'snapchat_ads', status: 'incomplete', reason: 'canonical_dashboard_scheduler_required' },
      ...['meta_ads', 'tiktok_ads', 'google_ads'].map(provider => ({ provider, status: 'complete',
        date_from: range.dateFrom, date_to: range.dateTo, accounts_attempted: 1, accounts_complete: 1,
        rows_saved: 4, errors_count: 0, ...flags })),
    ] } };
}

beforeEach(() => jest.resetAllMocks());

test('validates real inclusive dates and the existing 31-day boundary', () => {
  expect(historicalSpendRange('2026-10-25', '2026-11-24').days).toBe(31);
  expect(historicalSpendRange('2026-11-24', '2026-11-24').days).toBe(1);
});

test.each([
  ['2026-10-24', '2026-11-24'], ['2026-11-25', '2026-11-25'],
  ['2026-11-24', '2026-11-20'], ['2026-02-30', '2026-03-01'], ['', '2026-11-23'],
])('rejects an invalid range before any request: %s to %s', async (dateFrom, dateTo) => {
  await expect(refreshHistoricalAdSpend({ dateFrom, dateTo })).rejects.toThrow('invalid_historical_spend_range');
  expect(api.post).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
});

test('explicitly posts only the chosen range through the existing authenticated API client', async () => {
  api.post.mockResolvedValue({ data: response() });
  const signal = new AbortController().signal;
  const result = await refreshHistoricalAdSpend({ ...range, signal });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith('/integrations-v2/dashboard/ads-platform-spend/refresh',
    { date_from: range.dateFrom, date_to: range.dateTo }, expect.objectContaining({ signal, timeout: 120000 }));
  expect(api.get).not.toHaveBeenCalled();
  expect(result.providers.map(row => row.id)).toEqual(['meta_ads', 'tiktok_ads', 'google_ads']);
  expect(result.providers.every(row => row.status === 'complete')).toBe(true);
});

test('keeps a disabled Google result separate from completed source rows', () => {
  const value = response();
  value.refresh.results[3] = { provider: 'google_ads', status: 'skipped', reason: 'disabled' };
  const google = normalizeHistoricalSpendRefresh(value, range).providers[2];
  expect(google).toMatchObject({ status: 'skipped', disabled: true, rows_saved: null, accounts_complete: null });
});

test.each(['foreign-range', 'missing-google', 'duplicate-google', 'unsafe-write', 'false-complete', 'invalid-count'])('rejects contradictory source evidence: %s', kind => {
  const value = response();
  if (kind === 'foreign-range') value.date_from = '2026-11-19';
  if (kind === 'missing-google') value.refresh.results.pop();
  if (kind === 'duplicate-google') value.refresh.results[0] = { ...value.refresh.results[3] };
  if (kind === 'unsafe-write') value.refresh.accounting_write_reached = true;
  if (kind === 'false-complete') value.refresh.results[3].rows_saved = 3;
  if (kind === 'invalid-count') value.refresh.results[3].errors_count = -1;
  expect(() => normalizeHistoricalSpendRefresh(value, range)).toThrow('invalid_historical_spend_response');
});

test('an uncertain POST failure is never retried or replaced with a successful saved GET', async () => {
  const error = new Error('connection interrupted');
  api.post.mockRejectedValue(error);
  await expect(refreshHistoricalAdSpend(range)).rejects.toBe(error);
  expect(api.post).toHaveBeenCalledTimes(1); expect(api.get).not.toHaveBeenCalled();
});

test('a separate saved read reports only days with a recorded amount, including explicit zero', async () => {
  api.get.mockResolvedValue({ data: { date_from: range.dateFrom, date_to: range.dateTo, daily_spend: [
    { date: '2026-11-20', meta: 2, tiktok: null, google: 0 },
    { date: '2026-11-21', meta: 3, tiktok: 4, google: null },
  ] } });
  const signal = new AbortController().signal;
  const result = await readHistoricalAdSpend({ ...range, signal });
  expect(result.providers.map(row => [row.availableDays, row.requestedDays])).toEqual([[2, 4], [1, 4], [1, 4]]);
  expect(api.get).toHaveBeenCalledWith('/integrations-v2/dashboard/ads-platform-spend', expect.objectContaining({
    params: { date_from: range.dateFrom, date_to: range.dateTo }, signal, timeout: 8000,
  }));
  expect(api.post).not.toHaveBeenCalled();
});

test('a duplicate saved day cannot inflate the available-day count', async () => {
  api.get.mockResolvedValue({ data: { date_from: range.dateFrom, date_to: range.dateTo, daily_spend: [
    { date: '2026-11-20', google: 5 }, { date: '2026-11-20', google: 5 },
  ] } });
  await expect(readHistoricalAdSpend(range)).rejects.toThrow('invalid_historical_spend_readback');
});
