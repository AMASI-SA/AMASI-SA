import { normalizeAdsManagerOverview } from "./adsManager";


test("unified ads manager retains Snapchat campaign rows", () => {
    const result = normalizeAdsManagerOverview({
        generated_at: "2026-08-03T00:00:00+00:00",
        range: {
            date_from: "2026-08-01",
            date_to: "2026-08-03",
            timezone: "Asia/Riyadh",
            provider: "all",
        },
        metrics: {},
        coverage: {},
        providers: [],
        daily_spend: [],
        campaigns: [
            {
                provider: "snapchat",
                provider_label: "Snapchat",
                account_id: "snap-account-1",
                campaign_id: "snap-campaign-1",
                campaign_name: "حملة سناب",
                status: "ACTIVE",
                budget: {
                    currency: "USD",
                    daily_native: 100,
                    lifetime_native: null,
                },
                spend_reported: 50,
                spend_currency: "USD",
                spend_sar_equivalent: 187.5,
                revenue_reported: 120,
                revenue_sar_equivalent: 450,
                purchases: 2,
                impressions: 10000,
                clicks: 200,
                roas: 2.4,
                cpa_reported: 25,
                cpc_reported: 0.25,
                cpm_reported: 5,
                ctr_pct: 2,
                spend_share_pct: 100,
                last_observed_date: "2026-08-03",
                data_source: "mezan_snapchat_performance_daily_v2",
                currency_evidence: "account_metadata",
            },
        ],
        campaign_pagination: {
            page: 1,
            limit: 25,
            total: 1,
            pages: 1,
        },
        insights: [],
        sources: [],
    });

    expect(result.campaigns).toHaveLength(1);
    expect(result.campaigns[0]).toMatchObject({
        provider: "snapchat",
        campaign_id: "snap-campaign-1",
        spend_sar_equivalent: 187.5,
    });
});
