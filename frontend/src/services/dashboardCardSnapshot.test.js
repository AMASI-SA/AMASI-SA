import { dashboardCardQuery, nextDashboardCardReadAt, validDashboardCardSnapshot, TOP_PRODUCTS_REFRESH_MS, PERIOD_SUMMARY_REFRESH_MS } from "./dashboardCardSnapshot";
import { rewriteDashboardMezanV2Request } from "../lib/dashboardMezanV2Adapter";

test("the card cache key contains every filter in canonical order and has no force flag", () => {
    const query = new URLSearchParams(dashboardCardQuery({ from: "2026-11-11", payment_methods: ["cash", "mada", "cash"], shipping_companies: [" z ", "a"], report_statuses: "completed,under_review", refresh: true }));
    expect(Object.fromEntries(query)).toEqual({ from_date: "2026-11-11", to_date: "2026-11-11", payment_methods: "cash,mada", shipping_companies: "a,z", report_statuses: "completed,under_review" });
});

test("requested cadences and missing/invalid expiry fallbacks are bounded", () => {
    expect(TOP_PRODUCTS_REFRESH_MS).toBe(1_800_000);
    expect(PERIOD_SUMMARY_REFRESH_MS).toBe(18_000_000);
    const now = Date.parse("2026-11-11T18:00:00Z");
    expect(nextDashboardCardReadAt({}, TOP_PRODUCTS_REFRESH_MS, now)).toBe(now + TOP_PRODUCTS_REFRESH_MS);
    expect(nextDashboardCardReadAt({ snapshot: { next_refresh_at: "invalid" } }, TOP_PRODUCTS_REFRESH_MS, now)).toBe(now + TOP_PRODUCTS_REFRESH_MS);
    expect(nextDashboardCardReadAt({ snapshot: { next_refresh_at: "2036-01-01" } }, TOP_PRODUCTS_REFRESH_MS, now)).toBe(now + TOP_PRODUCTS_REFRESH_MS);
    expect(nextDashboardCardReadAt({ snapshot: { next_refresh_at: "2026-01-01" } }, TOP_PRODUCTS_REFRESH_MS, now)).toBe(now + 1000);
});

test.each(["top-products", "period-summary", "executive"])("%s requests bypass the legacy dashboard-wide response broker and automatic enrichment", (card) => {
    const config = { method: "get", url: `/dashboard-v2/cards/${card}?from_date=2026-11-11&to_date=2026-11-11` };
    expect(rewriteDashboardMezanV2Request(config)).toBe(config);
    expect(rewriteDashboardMezanV2Request(config)._mezanDashboardV2).toBeUndefined();
});

const metadata = (seconds) => ({ generated_at: "2026-11-11T18:00:00Z", next_refresh_at: new Date(Date.parse("2026-11-11T18:00:00Z") + seconds * 1000).toISOString(), refresh_interval_seconds: seconds });

test("top-product validation accepts explicit empty cohorts, zero and unknown financial amounts", () => {
    const costMetadata = { product_profit_summary: {}, salla_fallback_products_count: 0, missing_all_cost_products_count: 0 };
    const body = { product_cost_v2: { ...costMetadata, product_rows: [] }, snapshot: metadata(1800) };
    const validate = (value) => validDashboardCardSnapshot("/dashboard-v2/cards/top-products", value, TOP_PRODUCTS_REFRESH_MS);
    expect(validate(body)).toBe(true);
    expect(validate({ ...body, product_cost_v2: { ...costMetadata, product_rows: [{ units_sold: 0, total_sales: 0, total_cost: null, net_profit: null }] } })).toBe(true);
    expect(validate({ ...body, product_cost_v2: { ...costMetadata, product_rows: [{ units_sold: 1, total_sales: 10, total_cost: 20, net_profit: -10 }] } })).toBe(true);
    for (const cost of [{}, { product_rows: [] }, { ...costMetadata, product_rows: null }, { ...costMetadata, product_rows: [{}] }, { ...costMetadata, product_rows: [null] }, { ...body.product_cost_v2, salla_fallback_products_count: undefined }, { ...body.product_cost_v2, missing_all_cost_products_count: -1 }, { ...body.product_cost_v2, product_profit_summary: null }]) {
        expect(validate({ ...body, product_cost_v2: cost })).toBe(false);
    }
});

test("period validation requires actual month, cost and executive facts without rejecting explicit null sales", () => {
    const body = { month_kpis: { total_orders: 0, total_sales: null }, product_cost_v2: { missing_products_count: 0, missing_products: [] }, ads_v2: { executive_breakdown: { providers: {}, total: {} } }, snapshot: metadata(18000) };
    const validate = (value) => validDashboardCardSnapshot("/dashboard-v2/cards/period-summary", value, PERIOD_SUMMARY_REFRESH_MS);
    expect(validate(body)).toBe(true);
    expect(validate({ ...body, month_kpis: { total_orders: 0, total_sales: 0 } })).toBe(true);
    for (const change of [
        { month_kpis: {} }, { month_kpis: { total_orders: 1, total_sales: Infinity } },
        { product_cost_v2: {} }, { ads_v2: {} },
        { ads_v2: { executive_breakdown: { providers: null, total: {} } } },
        { ads_v2: { executive_breakdown: { providers: { snapchat: null }, total: {} } } },
        { snapshot: metadata(1800) },
    ]) expect(validate({ ...body, ...change })).toBe(false);
});
