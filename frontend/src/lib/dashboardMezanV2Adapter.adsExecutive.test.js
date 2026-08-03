import {
    dashboardExecutiveParams,
    hasDashboardExecutiveBreakdown,
    isDashboardV2Response,
    mergeDashboardExecutiveBreakdown,
    rewriteDashboardMezanV2Request,
} from "./dashboardMezanV2Adapter";


describe("Dashboard V2 inline advertising executive fallback", () => {
    test("marks only the Dashboard V2 request and preserves its URL", () => {
        const config = rewriteDashboardMezanV2Request({
            method: "get",
            url: "/dashboard-v2?from_date=2026-08-01&to_date=2026-08-03",
        });

        expect(config.url).toBe(
            "/dashboard-v2?from_date=2026-08-01&to_date=2026-08-03",
        );
        expect(config._mezanDashboardV2).toBe(true);
        expect(isDashboardV2Response({ config })).toBe(true);
    });

    test("forwards all dashboard filters to the focused fallback route", () => {
        const params = dashboardExecutiveParams({
            url: "/dashboard-v2?from_date=2026-08-01&to_date=2026-08-03&payment_methods=tabby%2Ctamara",
            params: { shipping_companies: "smsa" },
        });

        expect(params).toEqual({
            from_date: "2026-08-01",
            to_date: "2026-08-03",
            payment_methods: "tabby,tamara",
            shipping_companies: "smsa",
        });
    });

    test("merges a safe executive payload into ads_v2", () => {
        const dashboard = {
            totals: { total_ads_cost: 7604.02 },
            ads_v2: { total: 7604.02 },
        };
        const executive = {
            providers: {
                meta: {
                    spend_sar: 595,
                    salla_orders: 8,
                    salla_sales_sar: 2133.72,
                },
            },
            total: {
                spend_sar: 7604.02,
                salla_orders: 8,
                salla_sales_sar: 2133.72,
            },
            source_only: true,
            provider_write_reached: false,
            campaign_write_reached: false,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        };

        const merged = mergeDashboardExecutiveBreakdown(dashboard, executive);

        expect(merged.ads_v2.executive_breakdown).toEqual(executive);
        expect(hasDashboardExecutiveBreakdown(merged)).toBe(true);
        expect(merged.totals.total_ads_cost).toBe(7604.02);
    });

    test("rejects an unsafe fallback payload", () => {
        const dashboard = { ads_v2: { total: 100 } };
        const unsafe = {
            providers: {},
            total: {},
            source_only: true,
            accounting_write_reached: true,
        };

        expect(mergeDashboardExecutiveBreakdown(dashboard, unsafe)).toBe(dashboard);
        expect(hasDashboardExecutiveBreakdown(dashboard)).toBe(false);
    });
});
