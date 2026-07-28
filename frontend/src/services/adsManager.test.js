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
