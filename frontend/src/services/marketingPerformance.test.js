import {
    isMarketingPerformanceProvider,
    normalizeSnapchatMarketingWorkspace,
} from "./marketingPerformance";

test("Snapchat report normalization preserves verified performance and blocks writes", () => {
    const result = normalizeSnapchatMarketingWorkspace({
        range: {
            date_from: "2026-08-01",
            date_to: "2026-08-01",
            timezone: "Asia/Riyadh",
        },
        connection: {
            status: "connected",
            provenance: "api_connection",
            accounts_count: 2,
        },
        totals: {
            spend_sar: 150,
            sales_sar: 500,
            orders: 5,
            impressions: 1500,
            swipes: 70,
            roas: 3.33,
            cpa_sar: 30,
            ctr_pct: 4.67,
            data_complete: true,
        },
        campaigns: [
            {
                account_id: "account-1",
                account_name: "أماسي الرياض",
                campaign_id: "campaign-1",
                campaign_name: "حملة أغسطس",
                status: "ACTIVE",
                spend_sar: 150,
                sales_sar: 500,
                orders: 5,
                impressions: 1500,
                swipes: 70,
                roas: 3.33,
                cpa_sar: 30,
                budget: { currency: "SAR", daily_native: 700 },
            },
        ],
        campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
        source: {
            performance_rows: 1,
            entity_rows: 1,
            identity_matches: 1,
            identity_coverage_pct: 100,
            row_limit_reached: false,
        },
        ai_readiness: {
            report_ready: true,
            campaign_identity_ready: true,
            spend_ready: true,
            orders_ready: true,
            sales_ready: true,
            ratios_ready: true,
            ai_analysis_ready: true,
            campaign_creation_enabled: true,
            campaign_management_enabled: true,
            required_lifecycle: ["proposal", "approval", "execution", "verification"],
        },
        policy: { mutations_allowed: true },
    });

    expect(result.totals).toMatchObject({
        spend_sar: 150,
        sales_sar: 500,
        orders: 5,
        roas: 3.33,
        cpa_sar: 30,
    });
    expect(result.campaigns[0]).toMatchObject({
        campaign_id: "campaign-1",
        campaign_name: "حملة أغسطس",
        spend_sar: 150,
        sales_sar: 500,
        orders: 5,
    });
    expect(result.ai_readiness.ai_analysis_ready).toBe(true);
    expect(result.ai_readiness.campaign_creation_enabled).toBe(false);
    expect(result.ai_readiness.campaign_management_enabled).toBe(false);
    expect(result.policy).toEqual({
        mode: "observe_only",
        mutations_allowed: false,
    });
});

test("marketing performance route identifiers stay separate from integration IDs", () => {
    ["snapchat", "tiktok", "meta", "google"].forEach((provider) => {
        expect(isMarketingPerformanceProvider(provider)).toBe(true);
    });
    ["snapchat_ads", "tiktok_ads", "meta_ads", "google_ads"].forEach((provider) => {
        expect(isMarketingPerformanceProvider(provider)).toBe(false);
    });
});
