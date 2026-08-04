import api from "../lib/api";
import { getAdsManagerOverview } from "./adsManager";

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const HOUR_RE = /^(?:[01]\d|2[0-3]):00$/;
const PROVIDERS = Object.freeze(["snapchat", "meta", "tiktok"]);

function nonnegative(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function normalizeDashboardAdsHourlySpend(payload = {}) {
    const value = payload && typeof payload === "object" ? payload : {};
    const hourly = Array.isArray(value.hourly)
        ? value.hourly
            .map((point) => {
                const hourIndex = Math.trunc(Number(point?.hour_index));
                if (!Number.isInteger(hourIndex) || hourIndex < 0 || hourIndex > 23) {
                    return null;
                }
                const hour = HOUR_RE.test(point?.hour || "")
                    ? point.hour
                    : `${String(hourIndex).padStart(2, "0")}:00`;
                return {
                    date: ISO_DATE_RE.test(point?.date || "") ? point.date : null,
                    hour_index: hourIndex,
                    hour,
                    snapchat: nonnegative(point?.snapchat),
                    meta: nonnegative(point?.meta),
                    tiktok: nonnegative(point?.tiktok),
                    observed: point?.observed === true,
                    is_future: point?.is_future === true,
                };
            })
            .filter(Boolean)
            .sort((left, right) => left.hour_index - right.hour_index)
        : [];

    const available = Array.isArray(value.available_hourly_providers)
        ? value.available_hourly_providers.filter((provider) => PROVIDERS.includes(provider))
        : [];
    const unavailable = Array.isArray(value.unavailable_hourly_providers)
        ? value.unavailable_hourly_providers.filter((provider) => PROVIDERS.includes(provider))
        : [];

    return {
        date: ISO_DATE_RE.test(value.date || "") ? value.date : null,
        timezone: value.timezone === "Asia/Riyadh" ? value.timezone : "Asia/Riyadh",
        granularity: "hour",
        hourly,
        available_hourly_providers: available,
        unavailable_hourly_providers: unavailable,
        selected_snapchat_accounts: Math.max(0, Math.trunc(Number(value.selected_snapchat_accounts) || 0)),
        source_rows: Math.max(0, Math.trunc(Number(value.source_rows) || 0)),
        row_limit_reached: value.row_limit_reached === true,
        source_only: value.source_only === true,
        accounting_write_reached: false,
    };
}

export async function getDashboardAdsSpend({ dateFrom, dateTo } = {}) {
    const overviewPromise = getAdsManagerOverview({
        dateFrom,
        dateTo,
        provider: "all",
        page: 1,
        limit: 10,
    });
    const singleDay = Boolean(
        ISO_DATE_RE.test(dateFrom || "")
        && dateFrom === dateTo,
    );

    if (!singleDay) {
        const overview = await overviewPromise;
        return {
            ...overview,
            chart_granularity: "day",
            hourly_spend: [],
            hourly_source: null,
            hourly_error: "",
        };
    }

    const [overview, hourlyResult] = await Promise.all([
        overviewPromise,
        api.get("/integrations-v2/dashboard/ads-hourly-spend", {
            params: { date: dateFrom },
        })
            .then((response) => ({
                data: normalizeDashboardAdsHourlySpend(response.data),
                error: "",
            }))
            .catch((requestError) => {
                const detail = requestError?.response?.data?.detail;
                return {
                    data: normalizeDashboardAdsHourlySpend({ date: dateFrom }),
                    error: (
                        (typeof detail === "object" ? detail?.message : detail)
                        || requestError?.message
                        || "تعذر قراءة الصرف الساعي لمنصات الإعلانات."
                    ),
                };
            }),
    ]);

    return {
        ...overview,
        chart_granularity: "hour",
        hourly_spend: hourlyResult.data.hourly,
        hourly_source: hourlyResult.data,
        hourly_error: hourlyResult.error,
    };
}
