import { mergeDashboardWithPlatformSpend } from "./dashboardPlatformSpendMerge";

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
            tiktok: null,
            google: 84.36,
        },
        total_sar: 7378.7,
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
        },
    );

    expect(result.totals.net_profit).toBe(500);
    expect(result.totals.net_sales).toBe(700);
    expect(result.totals.total_ads_cost).toBe(200);
});

test("does not present 29 of 30 Snapchat days as a final zero or profit", () => {
    const result = mergeDashboardWithPlatformSpend(
        {
            totals: {
                total_sales: 1000,
                total_orders: 10,
                total_product_cost: 100,
                total_ads_cost: 200,
                daily_ads_total: 200,
                daily_costs_total: 300,
                net_profit: 500,
                net_sales: 700,
            },
            net_sales_config: { deduct_ads: true },
        },
        {
            date_from: "2026-07-07",
            date_to: "2026-08-05",
            provider_totals_sar: {
                snapchat: null,
                meta: 20,
                tiktok: 5,
                google: 7.5,
            },
            total_sar: null,
            spend_quality: { amount_complete: false, status: "incomplete" },
        },
    );

    expect(result.totals.total_ads_cost).toBeNull();
    expect(result.totals.daily_ads_total).toBeNull();
    expect(result.totals.daily_costs_total).toBeNull();
    expect(result.totals.overall_roas).toBeNull();
    expect(result.totals.avg_cost_per_order).toBeNull();
    expect(result.totals.net_profit).toBeNull();
    expect(result.totals.net_sales).toBeNull();
    expect(result.ads_v2.total).toBeNull();
    expect(result.ads_v2.providers.snapchat.spend).toBeNull();
});
