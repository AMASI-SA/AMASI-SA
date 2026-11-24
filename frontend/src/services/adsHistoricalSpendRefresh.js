import api from '../lib/api';
import { todaySA } from '../lib/dates';

export const HISTORICAL_SPEND_PROVIDERS = Object.freeze([
  { id: 'meta_ads', key: 'meta', label: 'Meta' },
  { id: 'tiktok_ads', key: 'tiktok', label: 'TikTok' },
  { id: 'google_ads', key: 'google', label: 'Google Ads' },
]);
const PATH = '/integrations-v2/dashboard/ads-platform-spend';
const DAY_MS = 86400000;
const STATUSES = new Set(['complete', 'partial', 'failed', 'skipped', 'incomplete']);
const safeCount = value => Number.isSafeInteger(value) && value >= 0;
const flagFields = ['provider_write_reached', 'campaign_write_reached', 'accounting_write_reached', 'qoyod_write_reached'];

function dayNumber(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value ? parsed / DAY_MS : null;
}

export function historicalSpendRange(dateFrom, dateTo, today = todaySA()) {
  const first = dayNumber(dateFrom), last = dayNumber(dateTo), current = dayNumber(today);
  if (first === null || last === null || current === null || last < first || last > current || last - first >= 31) {
    throw new Error('invalid_historical_spend_range');
  }
  return { dateFrom, dateTo, days: last - first + 1 };
}

function matches(value, range) {
  return value?.date_from === range.dateFrom && value?.date_to === range.dateTo;
}

export function normalizeHistoricalSpendRefresh(value, range) {
  const refresh = value?.refresh;
  if (!matches(value, range) || !matches(refresh, range) || refresh.source_only !== true
      || flagFields.some(field => refresh[field] !== false) || !Array.isArray(refresh.results)
      || refresh.results.length > 4) throw new Error('invalid_historical_spend_response');
  const seen = new Set();
  for (const row of refresh.results) {
    if (!row || ![...HISTORICAL_SPEND_PROVIDERS.map(provider => provider.id), 'snapchat_ads'].includes(row.provider)
        || seen.has(row.provider) || !STATUSES.has(row.status)) throw new Error('invalid_historical_spend_response');
    seen.add(row.provider);
  }
  const providers = HISTORICAL_SPEND_PROVIDERS.map(provider => {
    const row = refresh.results.find(item => item.provider === provider.id);
    if (!row || flagFields.some(field => row[field] === true) || row.source_only === false
        || (row.date_from !== undefined || row.date_to !== undefined) && !matches(row, range)) {
      throw new Error('invalid_historical_spend_response');
    }
    const counts = {};
    for (const field of ['accounts_attempted', 'accounts_complete', 'rows_saved', 'errors_count']) {
      if (row[field] !== undefined && !safeCount(row[field])) throw new Error('invalid_historical_spend_response');
      counts[field] = row[field] ?? null;
    }
    if (counts.accounts_attempted !== null && counts.accounts_complete !== null
        && counts.accounts_complete > counts.accounts_attempted) throw new Error('invalid_historical_spend_response');
    if (row.status === 'complete' && (counts.accounts_attempted === null || counts.accounts_attempted < 1
        || counts.accounts_complete !== counts.accounts_attempted || counts.errors_count !== 0
        || counts.rows_saved !== counts.accounts_attempted * range.days)) {
      throw new Error('invalid_historical_spend_response');
    }
    return { ...provider, status: row.status, ...counts,
      disabled: row.status === 'skipped' && row.reason === 'disabled' };
  });
  return { dateFrom: range.dateFrom, dateTo: range.dateTo, providers };
}

export async function refreshHistoricalAdSpend({ dateFrom, dateTo, signal }) {
  const range = historicalSpendRange(dateFrom, dateTo);
  const timeout = 120000;
  const response = await api.post(`${PATH}/refresh`, { date_from: dateFrom, date_to: dateTo },
    { signal, timeout, _mezanAuthDeadlineAt: Date.now() + timeout });
  return normalizeHistoricalSpendRefresh(response.data, range);
}

export async function readHistoricalAdSpend({ dateFrom, dateTo, signal }) {
  const range = historicalSpendRange(dateFrom, dateTo);
  const timeout = 8000;
  const response = await api.get(PATH, { params: { date_from: dateFrom, date_to: dateTo },
    signal, timeout, _mezanAuthDeadlineAt: Date.now() + timeout });
  const value = response.data;
  if (!matches(value, range) || !Array.isArray(value.daily_spend) || value.daily_spend.length > range.days) {
    throw new Error('invalid_historical_spend_readback');
  }
  const seen = new Set();
  for (const day of value.daily_spend) {
    if (!day || dayNumber(day.date) === null || day.date < dateFrom || day.date > dateTo || seen.has(day.date)) {
      throw new Error('invalid_historical_spend_readback');
    }
    seen.add(day.date);
    for (const provider of HISTORICAL_SPEND_PROVIDERS) {
      const amount = day[provider.key];
      if (amount !== null && amount !== undefined && (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0)) {
        throw new Error('invalid_historical_spend_readback');
      }
    }
  }
  return { dateFrom, dateTo, providers: HISTORICAL_SPEND_PROVIDERS.map(provider => ({ ...provider,
    requestedDays: range.days,
    availableDays: value.daily_spend.filter(day => typeof day[provider.key] === 'number').length,
  })) };
}
