import { mergeDashboardWithPlatformSpend, needsCanonicalDashboardSpendRefresh } from "./dashboardPlatformSpendMerge";

test("aligns executive ad cost, ROAS, CPA and profit to the selected four-platform period", () => {
    const dashboard = {
        totals: {
            total_sales: 8645.03,
            total_orders: 42,
            total_product_cost: 2837,
            total_ads_cost: 3001.72,
            daily_ads_total: 3001.72,
            daily_costs_total: 5838.72,
            net_sales: 1000,
            net_profit: 300.86,
            overall_roas: 2.88,
            avg_cost_per_order: 71.47,
        },
        net_sales_config: { deduct_ads: true },
        ads_v2: {
            total: 3001.72,
            breakdown: {
                snapchat: 2300,
                meta: 701.72,
                tiktok: 0,
                google_transitional: 0,
            },
            providers: {
                snapchat: { orders: 20, revenue: 5000, spend: 2300 },
                meta: { orders: 5, revenue: 1000, spend: 701.72 },
                tiktok: { orders: 0, revenue: 0, spend: 0 },
            },
            executive_breakdown: {
                providers: {
                    snapchat: {
                        provider: "snapchat",
                        salla_orders: 30,
                        salla_sales_sar: 6000,
                        platform_reported_orders: 20,
                        spend_sar: 2300,
                    },
                    meta: {
                        provider: "meta",
                        salla_orders: 10,
                        salla_sales_sar: 2000,
                        platform_reported_orders: 5,
                        spend_sar: 701.72,
                    },
                    tiktok: {
                        provider: "tiktok",
                        salla_orders: 2,
                        salla_sales_sar: 645.03,
                        platform_reported_orders: 0,
                        spend_sar: 0,
                    },
                    google: {
                        provider: "google",
                        salla_orders: 0,
                        salla_sales_sar: 0,
                        platform_reported_orders: null,
                        spend_sar: 0,
                    },
                },
                total: {},
                coverage: {},
            },
        },
        source_contract: {},
    };
    const platformSpend = {
        date_from: "2026-08-04",
        date_to: "2026-08-04",
        timezone: "Asia/Riyadh",
        provider_totals_sar: {
            snapchat: 6670.2,
            meta: 624.14,
            tiktok: 0,
            google: 84.36,
        },
        total_sar: 7378.7,
        spend_quality: { status: "complete", amount_available: true, amount_complete: true },
    };

    const result = mergeDashboardWithPlatformSpend(dashboard, platformSpend);

    expect(result.totals.total_ads_cost).toBe(7378.7);
    expect(result.totals.daily_ads_total).toBe(7378.7);
    expect(result.totals.daily_costs_total).toBe(10215.7);
    expect(result.totals.overall_roas).toBe(1.17);
    expect(result.totals.avg_cost_per_order).toBe(175.68);
    expect(result.totals.net_profit).toBe(-4076.12);
    expect(result.totals.net_sales).toBe(-3376.98);
    expect(result.totals.google_ads_spend).toBe(84.36);
    expect(result.ads_v2.total).toBe(7378.7);
    expect(result.ads_v2.breakdown.google_transitional).toBe(84.36);
    expect(result.ads_v2.executive_breakdown.total.spend_sar).toBe(7378.7);
    expect(result.ads_v2.executive_breakdown.total.platform_reported_orders).toBeNull();
    expect(result.ads_v2.platform_spend_period).toEqual({
        date_from: "2026-08-04",
        date_to: "2026-08-04",
        timezone: "Asia/Riyadh",
    });
    expect(result.source_contract.advertising)
        .toBe("dashboard_four_platform_spend_v1:selected_period");
});

test("does not double-adjust profit when dashboard already uses the same ad total", () => {
    const result = mergeDashboardWithPlatformSpend(
        {
            totals: {
                total_sales: 1000,
                total_orders: 10,
                total_product_cost: 100,
                total_ads_cost: 200,
                net_profit: 500,
                net_sales: 700,
            },
            net_sales_config: { deduct_ads: true },
        },
        {
            date_from: "2026-08-05",
            date_to: "2026-08-05",
            provider_totals_sar: {
                snapchat: 200,
                meta: 0,
                tiktok: 0,
                google: 0,
            },
            total_sar: 200,
            spend_quality: { status: "complete", amount_available: true, amount_complete: true },
        },
    );

    expect(result.totals.net_profit).toBe(500);
    expect(result.totals.net_sales).toBe(700);
    expect(result.totals.total_ads_cost).toBe(200);
});


function provisionalSpend(snapchat = 307.11) {
    return { date_from: "2026-10-26", date_to: "2026-10-26", total_sar: snapchat + 18.39,
        provider_totals_sar: { snapchat, meta: 18.39, tiktok: 0, google: 0 },
        spend_quality: { status: "provisional", amount_available: true, amount_complete: false, provisional: true } };
}


function incompleteDashboard() {
    return { totals: { total_sales: 1000, total_ads_cost: null, net_profit: null, net_sales: null,
        daily_costs_total: null, ads_spend_amount_available: false, ads_spend_data_complete: false },
        ads_v2: { total: null, spend_quality: { status: "incomplete", amount_available: false, amount_complete: false } } };
}


test.each([null, undefined, false, "", NaN])("unknown platform total %p and known subtotal cannot fabricate profit", (total) => {
    const dashboard = incompleteDashboard();
    const platform = { ...provisionalSpend(), total_sar: total, known_total_sar: 307.11,
        provider_totals_sar: { snapchat: null, meta: 18.39, tiktok: 0, google: 0 },
        spend_quality: { status: "incomplete", amount_available: false, provisional: false } };
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals).toBe(dashboard.totals);
    expect(result.totals.net_profit).toBeNull();
    expect(result.totals.net_sales).toBeNull();
    expect(result.totals.total_ads_cost).toBeNull();
    expect(result.ads_v2.spend_quality.amount_available).toBe(false);
    expect(needsCanonicalDashboardSpendRefresh(dashboard, platform)).toBe(false);
});


test.each([0, 307.11])("available provisional Snapchat %s requires a canonical recomputation over an unknown base", (amount) => {
    const dashboard = incompleteDashboard();
    const platform = provisionalSpend(amount);
    expect(needsCanonicalDashboardSpendRefresh(dashboard, platform)).toBe(true);
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals).toBe(dashboard.totals);
    expect(result.ads_v2).toBe(dashboard.ads_v2);
    expect(result.totals.net_profit).toBeNull();
});


test("preserves canonical provisional profit, fee basis, and quality rather than overlaying a newer amount", () => {
    const dashboard = { totals: { total_ads_cost: 325.5, net_profit: 600.23, ad_bank_commission_fees: 6.14,
        ads_spend_amount_available: true, ads_spend_data_complete: false, ads_spend_provisional: true },
        ads_v2: { total: 325.5, spend_quality: provisionalSpend().spend_quality } };
    const platform = provisionalSpend(400);
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals).toBe(dashboard.totals);
    expect(result.ads_v2).toBe(dashboard.ads_v2);
    expect(result.totals.net_profit).toBe(600.23);
    expect(needsCanonicalDashboardSpendRefresh(dashboard, platform)).toBe(false);
});


test("a complete spend update never treats null prior profit, sales, or product cost as zero", () => {
    const dashboard = { totals: { total_ads_cost: 10, net_profit: null, net_sales: null,
        total_sales: null, total_product_cost: null }, ads_v2: { spend_quality: { status: "complete", amount_available: true } } };
    const platform = { ...provisionalSpend(20), spend_quality: { status: "complete", amount_available: true, amount_complete: true } };
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals.total_ads_cost).toBe(38.39);
    expect(result.totals.net_profit).toBeNull();
    expect(result.totals.net_sales).toBeNull();
    expect(result.totals.daily_costs_total).toBeNull();
    expect(result.totals.overall_roas).toBeNull();
});


test("an available-looking payload with an unknown provider amount preserves the complete canonical snapshot", () => {
    const dashboard = { totals: { total_ads_cost: 100, net_profit: 500 }, ads_v2: { total: 100 } };
    const platform = { ...provisionalSpend(), provider_totals_sar: { snapchat: null, meta: 18.39, tiktok: 0, google: 0 } };
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals).toBe(dashboard.totals);
    expect(result.ads_v2).toBe(dashboard.ads_v2);
});


test("a complete platform response cannot replace provisional operational Snapchat metrics with zeros", () => {
    const dashboard = { totals: { total_ads_cost: 325.5, net_profit: 600.23, ads_spend_amount_available: true },
        ads_v2: { total: 325.5, spend_quality: provisionalSpend().spend_quality,
            providers: { snapchat: { spend: 307.11, orders: null, revenue: null,
                impressions: null, clicks: null, cpa: null, roas: null, platform_reported_metrics_complete: false } },
            executive_breakdown: { providers: { snapchat: { platform_reported_orders: null,
                salla_orders: 3, salla_sales_sar: 500, platform_cost_per_order_sar: null } },
                total: { platform_reported_orders: null }, coverage: { platform_cpa_denominator_complete: false } } } };
    const platform = { ...provisionalSpend(400), spend_quality: { status: "complete", amount_available: true, amount_complete: true } };
    const result = mergeDashboardWithPlatformSpend(dashboard, platform);
    expect(result.totals).toBe(dashboard.totals);
    expect(result.ads_v2).toBe(dashboard.ads_v2);
    expect(result.ads_v2.providers.snapchat.orders).toBeNull();
    expect(result.ads_v2.providers.snapchat.cpa).toBeNull();
    expect(result.ads_v2.providers.snapchat.roas).toBeNull();
    expect(result.ads_v2.executive_breakdown.providers.snapchat.platform_reported_orders).toBeNull();
    expect(result.ads_v2.executive_breakdown.providers.snapchat.salla_orders).toBe(3);
    expect(result.ads_v2.executive_breakdown.providers.snapchat.salla_sales_sar).toBe(500);
    expect(result.ads_v2.executive_breakdown.coverage.platform_cpa_denominator_complete).toBe(false);
    expect(needsCanonicalDashboardSpendRefresh(dashboard, platform)).toBe(true);
    const completeCanonical = { ...dashboard, ads_v2: { ...dashboard.ads_v2, spend_quality: platform.spend_quality } };
    expect(mergeDashboardWithPlatformSpend(completeCanonical, platform).ads_v2).toBe(completeCanonical.ads_v2);
});
