import {
    dashboardAuthoritativeParams,
    mergeDashboardAuthoritativeSummary,
    rewriteDashboardMezanV2Request,
    toLegacySnapDailySpend,
} from "./dashboardMezanV2Adapter";


test("rewrites legacy Snapchat Dashboard reads to V2", () => {
    expect(rewriteDashboardMezanV2Request({
        method: "get",
        url: "/dashboard/snapchat-summary",
    }).url).toBe("/integrations-v2/snapchat_ads/dashboard-summary");

    expect(rewriteDashboardMezanV2Request({
        method: "get",
        url: "/snapchat/accounts-summary",
    }).url).toBe("/integrations-v2/snapchat_ads/accounts-dashboard-summary");
});


test("rewrites the old Snapchat daily refresh into a V2 sync", () => {
    const result = rewriteDashboardMezanV2Request({
        method: "get",
        url: "/snapchat/daily-spend?date=2026-07-31",
    });
    expect(result.method).toBe("post");
    expect(result.url).toBe("/integrations-v2/snapchat_ads/sync");
    expect(result.data).toEqual({
        days: 1,
        from_date: "2026-07-31",
        to_date: "2026-07-31",
    });
    expect(result._mezanSnapDailyCompatibility).toBe(true);
});


test("marks only the exact Dashboard read for authoritative merge", () => {
    const dashboard = rewriteDashboardMezanV2Request({
        method: "get",
        url: "/dashboard?from_date=2026-07-31&to_date=2026-07-31",
    });
    expect(dashboard._mezanDashboardAuthoritativeMerge).toBe(true);
    expect(dashboardAuthoritativeParams(dashboard)).toEqual({
        from_date: "2026-07-31",
        to_date: "2026-07-31",
    });

    const meta = rewriteDashboardMezanV2Request({
        method: "get",
        url: "/dashboard/meta-summary",
    });
    expect(meta._mezanDashboardAuthoritativeMerge).toBeUndefined();
});


test("replaces old ad costs and recalculates dependent Dashboard KPIs", () => {
    const result = mergeDashboardAuthoritativeSummary(
        {
            totals: {
                total_sales: 1000,
                total_orders: 10,
                total_ads_cost: 100,
                net_profit: 500,
                overall_roas: 10,
                avg_cost_per_order: 10,
            },
        },
        {
            total_ads_cost: 250,
            breakdown: { snapchat_v2: 100, meta_v2: 150 },
            source_contract: { snapchat: "v2", meta: "v2" },
            source_only: true,
            provider_write_reached: false,
            campaign_write_reached: false,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        },
    );

    expect(result.totals.total_ads_cost).toBe(250);
    expect(result.totals.overall_roas).toBe(4);
    expect(result.totals.avg_cost_per_order).toBe(25);
    expect(result.totals.net_profit).toBe(350);
    expect(result.totals.ads_cost_breakdown_v2.meta_v2).toBe(150);
});


test("converts selected Snapchat V2 performance to the old daily shape", () => {
    const result = toLegacySnapDailySpend({
        spend_sar: 123.45,
        selected_account_count: 2,
        source_only: true,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    }, "2026-07-31");
    expect(result.spend).toBe(123.45);
    expect(result.source).toBe("snapchat_v2_selected_accounts");
    expect(result.selected_account_count).toBe(2);
});
