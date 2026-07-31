const DASHBOARD_PATH = "/dashboard";
const SNAP_SUMMARY_PATH = "/dashboard/snapchat-summary";
const SNAP_ACCOUNTS_PATH = "/snapchat/accounts-summary";
const SNAP_DAILY_PATH = "/snapchat/daily-spend";

function pathOnly(config = {}) {
    const raw = String(config?.url || "");
    return raw.split("?", 1)[0].replace(/\/+$/, "");
}

function queryParams(config = {}) {
    const params = new URLSearchParams();
    const raw = String(config?.url || "");
    const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
    new URLSearchParams(query).forEach((value, key) => params.set(key, value));
    if (config?.params && typeof config.params === "object") {
        Object.entries(config.params).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== "") {
                params.set(key, String(value));
            }
        });
    }
    return params;
}

function finiteNonnegative(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function safeEnvelope(payload) {
    return payload?.source_only === true
        && payload?.provider_write_reached !== true
        && payload?.campaign_write_reached !== true
        && payload?.accounting_write_reached !== true
        && payload?.qoyod_write_reached !== true;
}

export function rewriteDashboardMezanV2Request(config = {}) {
    const method = String(config?.method || "get").toLowerCase();
    const path = pathOnly(config);

    if (method === "get" && path === SNAP_SUMMARY_PATH) {
        return {
            ...config,
            url: "/integrations-v2/snapchat_ads/dashboard-summary",
            _mezanSnapDashboardV2: true,
        };
    }
    if (method === "get" && path === SNAP_ACCOUNTS_PATH) {
        return {
            ...config,
            url: "/integrations-v2/snapchat_ads/accounts-dashboard-summary",
            _mezanSnapDashboardV2: true,
        };
    }
    if (method === "get" && path === SNAP_DAILY_PATH) {
        const date = queryParams(config).get("date") || "";
        return {
            ...config,
            method: "post",
            url: "/integrations-v2/snapchat_ads/sync",
            params: undefined,
            data: {
                days: 1,
                ...(date ? { from_date: date, to_date: date } : {}),
            },
            _mezanSnapDailyCompatibility: true,
            _mezanSnapDailyDate: date,
        };
    }
    if (method === "get" && path === DASHBOARD_PATH) {
        return {
            ...config,
            _mezanDashboardAuthoritativeMerge: true,
        };
    }
    return config;
}

export function isSnapDailyCompatibilityResponse(response = {}) {
    return response?.config?._mezanSnapDailyCompatibility === true;
}

export function isDashboardAuthoritativeResponse(response = {}) {
    return response?.config?._mezanDashboardAuthoritativeMerge === true;
}

export function dashboardAuthoritativeParams(config = {}) {
    const source = queryParams(config);
    const params = {};
    for (const key of ["from_date", "to_date"]) {
        const value = source.get(key);
        if (value) params[key] = value;
    }
    return params;
}

export function toLegacySnapDailySpend(summary = {}, date = "") {
    if (!safeEnvelope(summary)) {
        throw new Error("unsafe_snapchat_v2_performance_summary");
    }
    const spend = finiteNonnegative(summary.spend_sar);
    return {
        date: date || summary.date_to || summary.date_from || null,
        spend,
        spend_sar: spend,
        spend_native: spend,
        native_currency: "SAR",
        fx_rate: 1,
        ad_account_timezone: "Asia/Riyadh",
        source: "snapchat_v2_selected_accounts",
        selected_account_count: Number(summary.selected_account_count || 0),
        source_only: true,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

export function mergeDashboardAuthoritativeSummary(
    legacyPayload = {},
    authoritative = {},
) {
    if (!safeEnvelope(authoritative)) return legacyPayload;
    const totals = legacyPayload?.totals && typeof legacyPayload.totals === "object"
        ? legacyPayload.totals
        : {};
    const oldAds = finiteNonnegative(totals.total_ads_cost);
    const newAds = finiteNonnegative(authoritative.total_ads_cost);
    const sales = finiteNonnegative(totals.total_sales);
    const orders = finiteNonnegative(totals.total_orders);
    const nextTotals = {
        ...totals,
        total_ads_cost: Math.round(newAds * 100) / 100,
        overall_roas: newAds > 0
            ? Math.round((sales / newAds) * 100) / 100
            : null,
        avg_cost_per_order: orders > 0
            ? Math.round((newAds / orders) * 100) / 100
            : null,
        ads_cost_breakdown_v2: authoritative.breakdown || {},
        dashboard_source_contract_v2: authoritative.source_contract || {},
    };
    if (Number.isFinite(Number(totals.net_profit))) {
        nextTotals.net_profit = Math.round(
            (Number(totals.net_profit) + oldAds - newAds) * 100,
        ) / 100;
    }
    return {
        ...legacyPayload,
        totals: nextTotals,
        authoritative_ads_v2: {
            date_from: authoritative.date_from || null,
            date_to: authoritative.date_to || null,
            breakdown: authoritative.breakdown || {},
            providers: authoritative.providers || {},
            source_only: true,
        },
    };
}
