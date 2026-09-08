import { loadDashboardPlatformSpend } from "../lib/dashboardPlatformSpendClient";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const HOUR_RE = /^(?:[01]\d|2[0-3]):00$/;
export const DASHBOARD_ADS_PROVIDERS = Object.freeze([
    "snapchat",
    "meta",
    "tiktok",
    "google",
]);
export const DASHBOARD_ADS_READ_MAX_AGE_MS = 45_000;

function nonnegative(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function safeDate(value) {
    return ISO_DATE_RE.test(value || "") ? value : null;
}

function normalizePoint(point = {}, hourly = false) {
    const base = {
        date: safeDate(point.date),
        ...Object.fromEntries(
            DASHBOARD_ADS_PROVIDERS.map((provider) => [
                provider,
                nonnegative(point?.[provider]),
            ]),
        ),
    };
    if (!hourly) return base;
    const hourIndex = Math.trunc(Number(point.hour_index));
    if (!Number.isInteger(hourIndex) || hourIndex < 0 || hourIndex > 23) {
        return null;
    }
    return {
        ...base,
        hour_index: hourIndex,
        hour: HOUR_RE.test(point.hour || "")
            ? point.hour
            : `${String(hourIndex).padStart(2, "0")}:00`,
    };
}

function normalizeProvider(provider, value = {}) {
    const row = value && typeof value === "object" ? value : {};
    return {
        provider,
        integration_provider: String(row.integration_provider || ""),
        connection_status: String(row.connection_status || "not_connected"),
        connected: row.connected === true,
        daily_available: row.daily_available === true,
        hourly_available: row.hourly_available === true,
        hourly_source: String(row.hourly_source || ""),
        total_sar: nonnegative(row.total_sar),
        data_quality: row.data_quality ? String(row.data_quality) : null,
        last_sync_at: row.last_sync_at ? String(row.last_sync_at) : null,
        data_delay_minutes: nonnegative(row.data_delay_minutes),
        amount_complete: row.amount_complete === true,
        amount_available: row.amount_available === true,
        provisional: row.provisional === true,
        requested_days: Math.max(0, Math.trunc(nonnegative(row.requested_days) || 0)),
        complete_days: Math.max(0, Math.trunc(nonnegative(row.complete_days) || 0)),
        missing_dates: Array.isArray(row.missing_dates)
            ? row.missing_dates.map(safeDate).filter(Boolean)
            : [],
        daily_coverage: Array.isArray(row.daily_coverage)
            ? row.daily_coverage.filter((item) => item && typeof item === "object")
            : [],
        provisional_subtotal_sar: nonnegative(row.provisional_subtotal_sar),
        last_mezan_check_at: row.last_mezan_check_at ? String(row.last_mezan_check_at) : null,
        last_provider_success_at: row.last_provider_success_at ? String(row.last_provider_success_at) : null,
        last_provider_value_changed_at: row.last_provider_value_changed_at
            ? String(row.last_provider_value_changed_at)
            : null,
    };
}

export function normalizeDashboardAdsSpend(payload = {}) {
    const value = payload && typeof payload === "object" ? payload : {};
    const providers = Object.fromEntries(
        DASHBOARD_ADS_PROVIDERS.map((provider) => [
            provider,
            normalizeProvider(provider, value?.providers?.[provider]),
        ]),
    );
    const daily = Array.isArray(value.daily_spend)
        ? value.daily_spend
            .map((point) => normalizePoint(point, false))
            .filter((point) => point?.date)
        : [];
    const hourly = Array.isArray(value.hourly_spend)
        ? value.hourly_spend
            .map((point) => normalizePoint(point, true))
            .filter(Boolean)
            .sort((left, right) => left.hour_index - right.hour_index)
        : [];
    const totals = Object.fromEntries(
        DASHBOARD_ADS_PROVIDERS.map((provider) => [
            provider,
            nonnegative(value?.provider_totals_sar?.[provider]),
        ]),
    );
    return {
        date_from: safeDate(value.date_from),
        date_to: safeDate(value.date_to),
        timezone: value.timezone === "Asia/Riyadh" ? value.timezone : "Asia/Riyadh",
        chart_granularity: value.chart_granularity === "hour" ? "hour" : "day",
        daily_spend: daily,
        hourly_spend: hourly,
        providers,
        provider_totals_sar: totals,
        total_sar: nonnegative(value.total_sar),
        known_total_sar: nonnegative(value.known_total_sar),
        spend_quality: value.spend_quality && typeof value.spend_quality === "object"
            ? value.spend_quality
            : null,
        refresh: value.refresh && typeof value.refresh === "object"
            ? value.refresh
            : null,
        source_only: value.source_only === true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

// Backwards-compatible export retained for the focused test suite.
export function normalizeDashboardAdsHourlySpend(payload = {}) {
    const normalized = normalizeDashboardAdsSpend({
        ...payload,
        hourly_spend: payload.hourly_spend || payload.hourly || [],
    });
    return {
        ...normalized,
        date: normalized.date_from || safeDate(payload.date),
        granularity: "hour",
        hourly: normalized.hourly_spend,
    };
}

function errorMessage(requestError, fallback) {
    const detail = requestError?.response?.data?.detail;
    return (
        (typeof detail === "object" ? detail?.message : detail)
        || requestError?.message
        || fallback
    );
}

export async function getDashboardAdsSpend({
    dateFrom,
    dateTo,
    refresh = false,
} = {}) {
    const safeFrom = safeDate(dateFrom);
    const safeTo = safeDate(dateTo || dateFrom);
    if (!safeFrom || !safeTo) {
        throw new Error("invalid_dashboard_ads_date_range");
    }

    let responseData = null;
    let refreshError = "";
    if (refresh) {
        try {
            responseData = await loadDashboardPlatformSpend({
                dateFrom: safeFrom,
                dateTo: safeTo,
                refresh: true,
                maxAgeMs: 0,
            });
        } catch (requestError) {
            refreshError = errorMessage(
                requestError,
                "تعذر تحديث إحدى منصات الإعلانات؛ سيتم عرض آخر بيانات محفوظة.",
            );
        }
    }

    if (!responseData) {
        responseData = await loadDashboardPlatformSpend({
            dateFrom: safeFrom,
            dateTo: safeTo,
            refresh: false,
            maxAgeMs: DASHBOARD_ADS_READ_MAX_AGE_MS,
        });
    }

    return {
        ...normalizeDashboardAdsSpend(responseData),
        refresh_error: refreshError,
    };
}
