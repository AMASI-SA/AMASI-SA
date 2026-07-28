import api from "../lib/api";
import {
    getAdsManagerOverview,
    normalizeAdsManagerOverview,
    OBSERVE_ONLY_POLICY,
} from "./adsManager";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        patch: jest.fn(),
        delete: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
});

test("ads manager uses one GET request with bounded read filters", async () => {
    api.get.mockResolvedValue({
        data: {
            generated_at: "2026-07-28T12:00:00+03:00",
            range: {
                date_from: "2026-07-01",
                date_to: "2026-07-28",
                timezone: "Asia/Riyadh",
                provider: "meta",
            },
            metrics: {},
            coverage: {},
            providers: [],
            daily_spend: [],
            campaigns: [],
            campaign_pagination: { page: 2, limit: 100, total: 0, pages: 0 },
            insights: [],
            sources: [],
            policy: {
                mode: "observe_only",
                mutations_allowed: false,
                ai_can: [],
                ai_cannot: [],
                lifecycle_required_for_future_writes: [],
            },
        },
    });

    await getAdsManagerOverview({
        dateFrom: "2026-07-01",
        dateTo: "2026-07-28",
        provider: "meta",
        campaignQuery: `  ${"x".repeat(150)}  `,
        page: 2,
        limit: 500,
    });

    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/ads-manager/overview", {
        params: {
            date_from: "2026-07-01",
            date_to: "2026-07-28",
            provider: "meta",
            campaign_query: "x".repeat(120),
            page: 2,
            limit: 100,
        },
    });
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.delete).not.toHaveBeenCalled();
});

test("normalization is fail-closed against write policy and secret-bearing extras", () => {
    const sentinel = "SENTINEL-CREDENTIAL-NEVER-RENDER";
    const overview = normalizeAdsManagerOverview({
        generated_at: "2026-07-28T12:00:00+03:00",
        range: {
            date_from: "2026-07-01",
            date_to: "2026-07-28",
            timezone: "Asia/Riyadh",
            provider: "all",
        },
        metrics: {
            provider_reported_spend_sar: 1432.4,
            booked_ad_expense_sar: null,
            platform_reported_clicks: undefined,
        },
        coverage: {},
        providers: [{
            provider: "meta",
            provider_label: `Bearer ${sentinel}abcdefgh`,
            metrics: {
                provider_reported_spend_sar: 1432.4,
                booked_ad_expense_sar: null,
            },
            freshness: { status: "fresh" },
            campaign_coverage: { status: "available" },
            reconciliation: { status: "not_comparable" },
            actions: {
                create_campaign: {
                    enabled: true,
                    access_token: sentinel,
                },
            },
        }],
        daily_spend: [{
            date: "2026-07-28",
            meta: 20,
            access_token: sentinel,
        }],
        campaigns: [{
            provider: "meta",
            provider_label: "ميتا",
            campaign_id: "campaign-1",
            campaign_name: "حملة صيفية",
            spend_sar_equivalent: 20,
            revenue_reported: 12,
            revenue_sar_equivalent: 45,
            data_source: "provider_daily_facts",
            currency_evidence: "configured",
            edit_budget_url: `https://example.invalid/?token=${sentinel}`,
        }],
        campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
        insights: [],
        sources: [],
        policy: {
            mode: "manage",
            mutations_allowed: true,
            ai_can: ["create_campaign", "change_budget"],
            actions: [{ type: "pause", access_token: sentinel }],
        },
        mutations: [{ type: "delete_campaign", secret: sentinel }],
    });

    expect(overview.policy).toBe(OBSERVE_ONLY_POLICY);
    expect(overview.policy.mode).toBe("observe_only");
    expect(overview.policy.mutations_allowed).toBe(false);
    expect(overview.policy.advertising_mutations_enabled).toBe(false);
    expect(overview.actions).toBeUndefined();
    expect(overview.mutations).toBeUndefined();
    expect(overview.providers[0].actions).toBeUndefined();
    expect(overview.metrics.booked_ad_expense_sar).toBeNull();
    expect(overview.metrics.platform_reported_clicks).toBeNull();
    expect(overview.campaigns[0].revenue_sar_equivalent).toBe(45);
    expect(JSON.stringify(overview)).not.toContain(sentinel);
});

test("unknown metrics remain unavailable instead of becoming zero", () => {
    const overview = normalizeAdsManagerOverview({
        metrics: {
            provider_reported_spend_sar: null,
            booked_ad_expense_sar: "",
            platform_roas: "not-a-number",
            platform_reported_purchases: 0,
        },
        coverage: {},
    });

    expect(overview.metrics.provider_reported_spend_sar).toBeNull();
    expect(overview.metrics.booked_ad_expense_sar).toBeNull();
    expect(overview.metrics.platform_roas).toBeNull();
    expect(overview.metrics.platform_reported_purchases).toBe(0);
});

test("normalizes performance quality and reconciliation evidence without inventing ratios", () => {
    const overview = normalizeAdsManagerOverview({
        metrics: {
            provider_reported_spend_sar: 55607.19,
            booked_ad_expense_sar: 53674.28,
            platform_roas: 0.85,
        },
        coverage: { ratio_eligible_providers: 1 },
        providers: [{
            provider: "tiktok",
            provider_label: "تيك توك",
            metrics: {
                provider_reported_spend_sar: 40,
                platform_attributed_revenue_sar: null,
                platform_roas: null,
            },
            freshness: {
                status: "stale",
                observed_days: 5,
                requested_days: 28,
            },
            performance_coverage: {
                status: "stale",
                eligible_for_ratios: false,
                observed_days: 5,
                requested_days: 28,
                coverage_pct: 17.86,
                missing_spend_dates: [
                    "2026-07-06",
                    "not-a-date",
                    "2026-07-07",
                ],
                reasons: [
                    "missing_performance_dates",
                    "stale_performance",
                    "unknown_reason",
                ],
                detail: "بيانات تيك توك قديمة ولا تغطي الفترة كاملة.",
            },
            campaign_coverage: { status: "available" },
            reconciliation: {
                status: "not_comparable",
                comparison_basis: "aggregate_period_only",
                severity: "warning",
                action_required: true,
                provider_reported_spend_sar: 39503.18,
                booked_ad_expense_sar: 37570.27,
                gap_sar: 1932.91,
                gap_pct: 4.89,
                detail: "فرق إجمالي الفترة يحتاج مراجعة.",
            },
        }],
    });

    expect(overview.metrics.platform_roas).toBeNull();
    expect(overview.coverage.ratio_eligible_providers).toBe(0);
    expect(overview.providers[0].performance_coverage).toEqual({
        status: "stale",
        eligible_for_ratios: false,
        observed_days: 5,
        requested_days: 28,
        coverage_pct: 17.86,
        missing_spend_dates: ["2026-07-06", "2026-07-07"],
        reasons: ["missing_performance_dates", "stale_performance"],
        detail: "بيانات تيك توك قديمة ولا تغطي الفترة كاملة.",
    });
    expect(overview.providers[0].reconciliation).toEqual({
        status: "not_comparable",
        comparison_basis: "aggregate_period_only",
        severity: "warning",
        action_required: true,
        provider_reported_spend_sar: 39503.18,
        booked_ad_expense_sar: 37570.27,
        gap_sar: 1932.91,
        gap_pct: 4.89,
        detail: "فرق إجمالي الفترة يحتاج مراجعة.",
    });
});

test("legacy or malformed quality fields fall back conservatively", () => {
    const overview = normalizeAdsManagerOverview({
        coverage: {
            providers_total: 1,
            ratio_eligible_providers: 3,
        },
        providers: [{
            provider: "meta",
            freshness: {
                status: "fresh",
                observed_days: 4,
                requested_days: 10,
            },
            performance_coverage: {
                status: "trusted",
                eligible_for_ratios: true,
                coverage_pct: 900,
                reasons: ["unknown_reason"],
            },
            campaign_coverage: {},
            reconciliation: {
                status: "drift",
                comparison_basis: "guess",
                severity: "critical",
                action_required: "true",
            },
        }],
    });

    expect(overview.providers[0].performance_coverage).toMatchObject({
        status: "partial",
        eligible_for_ratios: false,
        observed_days: 4,
        requested_days: 10,
        coverage_pct: 40,
        missing_spend_dates: [],
        reasons: [],
    });
    expect(overview.providers[0].reconciliation).toMatchObject({
        status: "drift",
        comparison_basis: "unavailable",
        severity: "warning",
        action_required: false,
    });
    expect(overview.coverage.ratio_eligible_providers).toBe(0);
});

test("accepts the backend's bounded open-current-day coverage exception", () => {
    const overview = normalizeAdsManagerOverview({
        coverage: {
            providers_total: 1,
            ratio_eligible_providers: 1,
        },
        providers: [{
            provider: "meta",
            freshness: {
                status: "fresh",
                observed_days: 27,
                requested_days: 28,
            },
            performance_coverage: {
                status: "complete",
                eligible_for_ratios: true,
                observed_days: 27,
                requested_days: 28,
                coverage_pct: 96.43,
                reasons: [],
            },
            campaign_coverage: {},
            reconciliation: {},
        }],
    });

    expect(overview.providers[0].performance_coverage).toMatchObject({
        status: "complete",
        eligible_for_ratios: true,
        observed_days: 27,
        requested_days: 28,
        coverage_pct: 96.43,
    });
    expect(overview.coverage.ratio_eligible_providers).toBe(1);
});

test("fails closed when the one-day coverage gap is not fresh", () => {
    const overview = normalizeAdsManagerOverview({
        coverage: {
            providers_total: 1,
            ratio_eligible_providers: 1,
        },
        providers: [{
            provider: "meta",
            freshness: {
                status: "delayed",
                observed_days: 27,
                requested_days: 28,
            },
            performance_coverage: {
                status: "complete",
                eligible_for_ratios: true,
                observed_days: 27,
                requested_days: 28,
                coverage_pct: 96.43,
                reasons: [],
            },
            campaign_coverage: {},
            reconciliation: {},
        }],
    });

    expect(
        overview.providers[0].performance_coverage.eligible_for_ratios,
    ).toBe(false);
    expect(overview.coverage.ratio_eligible_providers).toBe(0);
});
