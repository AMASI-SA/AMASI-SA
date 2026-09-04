import {
    adaptAdsManager,
    clampSnapchatRangeToAccountToday,
    isMarketingPerformanceProvider,
    normalizeSnapchatMarketingWorkspace,
    snapchatAccountLocalToday,
} from "./marketingPerformance";

test("Snapchat report normalization preserves verified performance and blocks writes", () => {
    const result = normalizeSnapchatMarketingWorkspace(
        {
            date_from: "2026-08-01",
            date_to: "2026-08-01",
            business_timezone: "Asia/Riyadh",
            totals: {
                spend_sar: 150,
                sales_sar: 500,
                orders: 5,
                impressions: 1500,
                swipes: 70,
                view_content: 80,
                add_to_cart: 20,
                start_checkout: 10,
                add_billing: 8,
                paid_reach: 1000,
                paid_frequency: 1.5,
                reach_frequency_scope: "exact_one_day_total",
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
                    data_status: "outside_date_range",
                    budget: { currency: "SAR", daily_native: 700 },
                },
            ],
            campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
            source: {
                selected_account_count: 2,
                performance_rows: 1,
                entity_rows: 1,
                identity_matches: 1,
                identity_coverage_pct: 100,
                row_limit_reached: false,
                platform_total_snapshot_ready: true,
                platform_direct_account_total_ready: true,
                platform_action_report_time: "impression",
                account_spend_source: "direct_ad_account_total",
                account_commercial_totals_source: "complete_campaign_breakdown_sum",
                requested_campaign_diagnostic: {
                    campaign_id: "missing-campaign",
                    reason: "provider_missing",
                    selected_account_id: "account-1",
                },
                campaign_exclusions: [{
                    campaign_id: "inactive-campaign",
                    reason: "inactive",
                }],
            },
            ai_readiness: {
                report_ready: true,
                campaign_identity_ready: true,
                spend_ready: true,
                orders_ready: true,
                sales_ready: true,
                ratios_ready: true,
                funnel_ready: true,
                ai_analysis_ready: true,
                campaign_creation_enabled: true,
                campaign_management_enabled: true,
                required_lifecycle: ["proposal", "approval", "execution", "verification"],
            },
        },
        {
            connection_status: "connected",
            connection_provenance: "api_connection",
            last_sync_at: "2026-08-01T16:55:00+00:00",
            data_delay_minutes: 5,
            health: { status: "healthy", score: 100 },
            accounts: [{ ad_account_id: "account-1" }],
        },
    );

    expect(result.connection).toMatchObject({
        status: "connected",
        provenance: "api_connection",
        accounts_count: 1,
    });
    expect(result.totals).toMatchObject({
        spend_sar: 150,
        sales_sar: null,
        orders: null,
        roas: null,
        cpa_sar: null,
        view_content: 80,
        add_to_cart: 20,
        start_checkout: 10,
        add_billing: 8,
        paid_reach: 1000,
        paid_frequency: 1.5,
        reach_frequency_scope: "exact_one_day_total",
    });
    expect(result.campaigns[0]).toMatchObject({
        campaign_id: "campaign-1",
        campaign_name: "حملة أغسطس",
        spend_sar: 150,
        sales_sar: null,
        orders: null,
        data_status: "outside_date_range",
    });
    expect(result.source).toMatchObject({
        platform_total_snapshot_ready: true,
        platform_direct_account_total_ready: true,
        platform_action_report_time: "impression",
        account_spend_source: "direct_ad_account_total",
        account_commercial_totals_source: "complete_campaign_breakdown_sum",
        requested_campaign_diagnostic: {
            campaign_id: "missing-campaign",
            reason: "provider_missing",
            selected_account_id: "account-1",
            evidence_account_id: null,
        },
        campaign_exclusions: [{
            campaign_id: "inactive-campaign",
            reason: "inactive",
        }],
    });
    expect(result.ai_readiness.ai_analysis_ready).toBe(true);
    expect(result.ai_readiness.funnel_ready).toBe(true);
    expect(result.ai_readiness.campaign_creation_enabled).toBe(false);
    expect(result.ai_readiness.campaign_management_enabled).toBe(false);
    expect(result.policy).toEqual({
        mode: "observe_only",
        mutations_allowed: false,
    });
});

test("Snapchat report dates are capped at the earliest account-local day", () => {
    const integration = {
        accounts: [
            { local_today: "2026-08-08" },
            { local_today: "2026-08-07" },
            { local_today: "invalid" },
        ],
    };
    const accountLocalToday = snapchatAccountLocalToday(integration);

    expect(accountLocalToday).toBe("2026-08-07");
    expect(clampSnapchatRangeToAccountToday(
        { dateFrom: "2026-08-08", dateTo: "2026-08-08" },
        accountLocalToday,
    )).toEqual({
        dateFrom: "2026-08-07",
        dateTo: "2026-08-07",
    });
    expect(clampSnapchatRangeToAccountToday(
        { dateFrom: "2026-08-01", dateTo: "2026-08-06" },
        accountLocalToday,
    )).toEqual({
        dateFrom: "2026-08-01",
        dateTo: "2026-08-06",
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


test("Meta Ads Manager adapter preserves campaign status objective and budget", () => {
    const result = adaptAdsManager("meta", {
        range: { date_from: "2026-08-01", date_to: "2026-08-01", timezone: "Asia/Riyadh" },
        providers: [
            {
                provider: "meta",
                connection_status: "connected",
                connection_provenance: "api_connection",
                last_sync_at: "2026-08-01T17:00:00+00:00",
                health_status: "healthy",
                health_score: 100,
                freshness: { data_delay_minutes: 5, observed_days: 1 },
                performance_coverage: { status: "complete", eligible_for_ratios: true },
                campaign_coverage: { status: "available", source_rows: 1 },
                metrics: {
                    provider_reported_spend_sar: 375,
                    platform_attributed_revenue_sar: 1125,
                    platform_reported_purchases: 5,
                    platform_reported_impressions: 10000,
                    platform_reported_clicks: 500,
                    platform_roas: 3,
                },
            },
        ],
        daily_spend: [{ date: "2026-08-01", meta: 375 }],
        campaigns: [
            {
                provider: "meta",
                account_id: "act-selected",
                campaign_id: "campaign-sales",
                campaign_name: "Sales Riyadh",
                status: "ACTIVE",
                delivery_status: "ACTIVE",
                objective: "OUTCOME_SALES",
                start_time: "2026-07-01T00:00:00+00:00",
                end_time: "2026-08-31T23:59:59+00:00",
                budget: { currency: "USD", daily_native: 250, lifetime_native: 1000 },
                spend_sar_equivalent: 375,
                revenue_sar_equivalent: 1125,
                purchases: 5,
                impressions: 10000,
                clicks: 500,
            },
        ],
        campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
        insights: [],
        coverage: { source_row_limit_reached: [] },
    });

    expect(result.campaigns[0]).toMatchObject({
        campaign_id: "campaign-sales",
        campaign_name: "Sales Riyadh",
        status: "ACTIVE",
        delivery_status: "ACTIVE",
        objective: "OUTCOME_SALES",
        budget: { currency: "USD", daily_native: 250, lifetime_native: 1000 },
        spend_sar: 375,
        sales_sar: 1125,
        orders: 5,
    });
});
