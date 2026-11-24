import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import HistoricalAdSpendRefreshControl from './HistoricalAdSpendRefreshControl';
import { readHistoricalAdSpend, refreshHistoricalAdSpend } from '../../services/adsHistoricalSpendRefresh';

jest.mock('../../lib/dates', () => ({ ...jest.requireActual('../../lib/dates'), todaySA: () => '2026-11-24' }));
jest.mock('../../services/adsHistoricalSpendRefresh', () => ({
  ...jest.requireActual('../../services/adsHistoricalSpendRefresh'),
  readHistoricalAdSpend: jest.fn(), refreshHistoricalAdSpend: jest.fn(),
}));

let host, root, user, ownerSequence = 0;
const button = id => host.querySelector(`[data-testid="historical-ad-spend-${id}"]`);
const receipt = (dateFrom = '2026-11-20', dateTo = '2026-11-23') => ({ dateFrom, dateTo, providers: [
  { id: 'meta_ads', label: 'Meta', status: 'complete', accounts_attempted: 1, accounts_complete: 1, rows_saved: 4, errors_count: 0 },
  { id: 'tiktok_ads', label: 'TikTok', status: 'partial', accounts_attempted: 2, accounts_complete: 1, rows_saved: 4, errors_count: 1 },
  { id: 'google_ads', label: 'Google Ads', status: 'skipped', disabled: true, accounts_attempted: null, accounts_complete: null, rows_saved: null, errors_count: null },
] });
const readback = () => ({ dateFrom: '2026-11-20', dateTo: '2026-11-23', providers: [
  { id: 'google_ads', label: 'Google Ads', availableDays: 4, requestedDays: 4 },
] });

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks(); sessionStorage.clear();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  user = { id: `historical-owner-${++ownerSequence}`, role: 'owner' };
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  refreshHistoricalAdSpend.mockResolvedValue(receipt()); readHistoricalAdSpend.mockResolvedValue(readback());
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); delete global.IS_REACT_ACT_ENVIRONMENT;
});
async function mount(props = {}) {
  await act(async () => root.render(<HistoricalAdSpendRefreshControl user={user} sessionReady {...props} />));
}
async function date(label, value) {
  const input = host.querySelector(`[aria-label="${label}"]`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function chooseRange() {
  await date('بداية فترة مزامنة الإنفاق', '2026-11-20');
  await date('نهاية فترة مزامنة الإنفاق', '2026-11-23');
}

test.each([{ role: 'employee' }, { role: 'meta_reviewer' }, { role: 'owner', id: '' }])('hides the control for a non-owner or absent identity: %j', async row => {
  await mount({ user: { ...user, ...row } });
  expect(host.textContent).toBe('');
  expect(refreshHistoricalAdSpend).not.toHaveBeenCalled(); expect(readHistoricalAdSpend).not.toHaveBeenCalled();
});

test('an unverified session cannot display or invoke the control', async () => {
  await mount({ sessionReady: false });
  expect(host.textContent).toBe('');
  expect(refreshHistoricalAdSpend).not.toHaveBeenCalled();
});

test('mounting and choosing dates perform no reads or writes; one click sends the exact range', async () => {
  await mount(); await chooseRange();
  expect(refreshHistoricalAdSpend).not.toHaveBeenCalled(); expect(readHistoricalAdSpend).not.toHaveBeenCalled();
  await act(async () => button('sync').click());
  expect(refreshHistoricalAdSpend).toHaveBeenCalledWith(expect.objectContaining({ dateFrom: '2026-11-20', dateTo: '2026-11-23' }));
  expect(refreshHistoricalAdSpend).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain('مزامنة جزئية');
  expect(host.textContent).toContain('متوقفة أو غير مهيأة؛ لم تُنفذ');
  expect(host.textContent).toContain('أعداد الصفوف أدلة مصدر فقط');
  expect(readHistoricalAdSpend).not.toHaveBeenCalled();
});

test('invalid and overlong date ranges keep the write button disabled', async () => {
  await mount();
  await date('بداية فترة مزامنة الإنفاق', '2026-10-24');
  expect(button('sync').disabled).toBe(true);
  await date('بداية فترة مزامنة الإنفاق', '2026-11-25');
  expect(button('sync').disabled).toBe(true);
  expect(refreshHistoricalAdSpend).not.toHaveBeenCalled();
});

test('double clicks cannot admit a second source request while the first is active', async () => {
  let resolve;
  refreshHistoricalAdSpend.mockImplementation(() => new Promise(done => { resolve = done; }));
  await mount(); await chooseRange();
  await act(async () => { button('sync').click(); button('sync').click(); });
  expect(refreshHistoricalAdSpend).toHaveBeenCalledTimes(1);
  await act(async () => resolve(receipt()));
  expect(button('sync').disabled).toBe(false);
});

test('a failed POST stays visibly unknown after a successful saved read and is not retried', async () => {
  refreshHistoricalAdSpend.mockRejectedValue(new Error('response lost'));
  await mount(); await chooseRange();
  await act(async () => button('sync').click());
  expect(button('unknown')).not.toBeNull(); expect(button('result')).toBeNull();
  await act(async () => button('read').click());
  expect(button('saved').textContent).toContain('4 من 4');
  expect(button('unknown')).not.toBeNull();
  expect(refreshHistoricalAdSpend).toHaveBeenCalledTimes(1);
});

test('unmount aborts observation of a write and remount shows the unresolved owner/range marker', async () => {
  let resolve;
  refreshHistoricalAdSpend.mockImplementation(() => new Promise(done => { resolve = done; }));
  await mount(); await chooseRange(); await act(async () => button('sync').click());
  const signal = refreshHistoricalAdSpend.mock.calls[0][0].signal;
  await act(async () => root.unmount());
  expect(signal.aborted).toBe(true);
  root = createRoot(host); await mount();
  expect(button('unknown').textContent).toContain('2026-11-20 إلى 2026-11-23');
  await act(async () => resolve(receipt()));
  expect(button('result')).toBeNull(); expect(button('unknown')).not.toBeNull();
  expect(refreshHistoricalAdSpend).toHaveBeenCalledTimes(1);
});

test('an owner change aborts an active read and cannot display the prior owner result', async () => {
  let resolve;
  readHistoricalAdSpend.mockImplementation(() => new Promise(done => { resolve = done; }));
  await mount(); await chooseRange(); await act(async () => button('read').click());
  const signal = readHistoricalAdSpend.mock.calls[0][0].signal;
  user = { id: 'different-historical-owner', role: 'owner' };
  await mount(); expect(signal.aborted).toBe(true);
  await act(async () => resolve(readback()));
  expect(button('saved')).toBeNull(); expect(button('unknown')).toBeNull();
});

test('hiding the page cancels an unnecessary saved read without starting a write', async () => {
  readHistoricalAdSpend.mockImplementation(() => new Promise(() => {}));
  await mount(); await act(async () => button('read').click());
  const signal = readHistoricalAdSpend.mock.calls[0][0].signal;
  await act(async () => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(signal.aborted).toBe(true);
  expect(refreshHistoricalAdSpend).not.toHaveBeenCalled();
});
