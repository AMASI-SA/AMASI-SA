export const TOP_PRODUCTS_REFRESH_MS = 30 * 60 * 1000;
export const PERIOD_SUMMARY_REFRESH_MS = 5 * 60 * 60 * 1000;
export const EXECUTIVE_REFRESH_MS = 60 * 1000;
export const DASHBOARD_CARD_REQUEST_TIMEOUT_MS = 60 * 1000;

function record(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function financialAmount(value) {
    return value === null || ((typeof value === "number" || typeof value === "string" && value.trim() !== "")
        && Number.isFinite(Number(value)));
}

/** Reject malformed success bodies before their absent facts can display as zero. */
export function validDashboardCardSnapshot(endpoint, payload, intervalMs) {
    if (!record(payload) || !record(payload.snapshot)) return false;
    const meta = payload.snapshot;
    const generatedAt = typeof meta.generated_at === "string" ? Date.parse(meta.generated_at) : NaN;
    const nextAt = typeof meta.next_refresh_at === "string" ? Date.parse(meta.next_refresh_at) : NaN;
    if (!Number.isFinite(generatedAt) || !Number.isFinite(nextAt)
        || Math.abs(nextAt - generatedAt - intervalMs) > 10
        || meta.refresh_interval_seconds !== intervalMs / 1000) return false;
    const cost = payload.product_cost_v2;
    if (!record(cost)) return false;
    if (endpoint === "/dashboard-v2/cards/top-products") {
        return record(cost.product_profit_summary)
            && Number.isSafeInteger(cost.salla_fallback_products_count) && cost.salla_fallback_products_count >= 0
            && Number.isSafeInteger(cost.missing_all_cost_products_count) && cost.missing_all_cost_products_count >= 0
            && Array.isArray(cost.product_rows) && cost.product_rows.every((row) => record(row)
            && typeof row.units_sold === "number" && Number.isFinite(row.units_sold) && row.units_sold >= 0
            && ["total_sales", "total_cost", "net_profit"].every((field) => Object.hasOwn(row, field) && financialAmount(row[field])));
    }
    if (endpoint === "/dashboard-v2/cards/period-summary") {
        const month = payload.month_kpis;
        const executive = payload.ads_v2?.executive_breakdown;
        return record(month)
            && Number.isSafeInteger(month.total_orders) && month.total_orders >= 0
            && Object.hasOwn(month, "total_sales") && financialAmount(month.total_sales)
            && Number.isSafeInteger(cost.missing_products_count) && cost.missing_products_count >= 0
            && (cost.missing_products === undefined || Array.isArray(cost.missing_products))
            && record(executive) && record(executive.providers) && record(executive.total)
            && Object.values(executive.providers).every(record);
    }
    return false;
}

function selectedValues(value) {
    const values = Array.isArray(value) ? value : String(value || "").split(",");
    return [...new Set(values.map((item) => String(item).trim()).filter(Boolean))].sort().join(",");
}

/** All dimensions of a saved card snapshot; refresh flags never form its key. */
export function dashboardCardQuery(filters = {}) {
    const query = new URLSearchParams();
    if (filters.from) query.set("from_date", filters.from);
    if (filters.to || filters.from) query.set("to_date", filters.to || filters.from);
    for (const name of ["payment_methods", "shipping_companies", "report_statuses"]) {
        const value = selectedValues(filters[name]);
        if (value) query.set(name, value);
    }
    return query.toString();
}

export function nextDashboardCardReadAt(payload, intervalMs, now = Date.now()) {
    const next = Date.parse(payload?.snapshot?.next_refresh_at || "");
    // A cached response retains the server's original expiry across remounts.
    // Bound malformed/future metadata and avoid an immediate retry loop.
    return Number.isFinite(next)
        ? Math.max(now + 1000, Math.min(next, now + intervalMs))
        : now + intervalMs;
}
