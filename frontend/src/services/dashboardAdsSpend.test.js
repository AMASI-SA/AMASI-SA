import { loadDashboardPlatformSpend } from "../lib/dashboardPlatformSpendClient";
import {
    DASHBOARD_ADS_PROVIDERS,
    getDashboardAdsSpend,
    normalizeDashboardAdsHourlySpend,
    normalizeDashboardAdsSpend,
} from "./dashboardAdsSpend";

jest.mock("../lib/dashboardPlatformSpendClient", () => ({
    loadDashboardPlatformSpend: jest.fn(),
}));

beforeEach(() => {
    jest.clearAllMocks();
});


function fourPlatformPayload(overrides = {}) {
    return {
        date_from: "2026-08-05",
        date_to: "2026-08-05",
        timezone: "Asia/Riyadh",
        chart_granularity: "hour",
        provider_totals_sar: {
            snapchat: 100,
            meta: 20,
            tiktok: 5,
            google: 7.5,
        },
        total_sar: 132.5,
        providers: Object.fromEntries(
            DASHBOARD_ADS_PROVIDERS.map((provider) => [
                provider,
                {
                    provider,
                    integration_provider: provider === "google"
                        ? "google_ads"
                        : `${provider}_ads`,
                    connection_status: "connected",
                    connected: true,
                    daily_available: true,
                    hourly_available: true,
                    hourly_source: provider === "tiktok"
                        ? "make_daily_total_marker"
                        : "provider_native",
                    total_sar: 1,
                },
            ]),
        ),
        daily_spend: [
            {
                date: "2026-08-05",
                snapchat: 100,
                meta: 20,
                tiktok: 5,
                google: 7.5,
            },
        ],
        hourly_spend: [
            {
                date: "2026-08-05",
                hour_index: 3,
                hour: "03:00",
                snapchat: 30,
                meta: 4,
                tiktok: 1,
                google: 2,
            },
            {
                date: "2026-08-05",
                hour_index: 0,
                hour: "00:00",
                snapchat: 10,
                meta: 2,
                tiktok: 0,
                google: 1,
            },
        ],
        source_only: true,
        provider_write_reached: true,
        accounting_write_reached: true,
        ...overrides,
    };
}


test("normalizes and sorts all four Riyadh hourly provider series", () => {
    const normalized = normalizeDashboardAdsSpend(fourPlatformPayload());

    expect(normalized.hourly_spend.map((row) => row.hour))
        .toEqual(["00:00", "03:00"]);
    expect(normalized.hourly_spend[0]).toMatchObject({
        snapchat: 10,
        meta: 2,
        tiktok: 0,
        google: 1,
    });
    expect(Object.keys(normalized.providers)).toEqual(DASHBOARD_ADS_PROVIDERS);
    expect(normalized.provider_totals_sar.google).toBe(7.5);
    expect(normalized.providers.tiktok.hourly_source)
        .toBe("make_daily_total_marker");
    expect(normalized.total_sar).toBe(132.5);
    expect(normalized.provider_write_reached).toBe(false);
    expect(normalized.accounting_write_reached).toBe(false);
});


test("retains the backwards-compatible hourly normalizer", () => {
    const normalized = normalizeDashboardAdsHourlySpend({
        date: "2026-08-05",
        hourly: fourPlatformPayload().hourly_spend,
    });

    expect(normalized.date).toBe("2026-08-05");
    expect(normalized.granularity).toBe("hour");
    expect(normalized.hourly[0].google).toBe(1);
});


test("refreshes through the shared client and returns four provider facts", async () => {
    loadDashboardPlatformSpend.mockResolvedValue(fourPlatformPayload());

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenCalledWith({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
        maxAgeMs: 0,
    });
    expect(result.chart_granularity).toBe("hour");
    expect(result.hourly_spend[0].google).toBe(1);
    expect(result.refresh_error).toBe("");
});


test("falls back to saved shared facts when provider refresh cannot run", async () => {
    loadDashboardPlatformSpend
        .mockRejectedValueOnce(new Error("provider unavailable"))
        .mockResolvedValueOnce(fourPlatformPayload());

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenNthCalledWith(2, {
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: false,
        maxAgeMs: 0,
    });
    expect(result.total_sar).toBe(132.5);
    expect(result.refresh_error).toContain("provider unavailable");
});


test("reads a multi-day four-platform series without forcing refresh", async () => {
    loadDashboardPlatformSpend.mockResolvedValue(fourPlatformPayload({
        date_from: "2026-08-04",
        date_to: "2026-08-05",
        chart_granularity: "day",
        hourly_spend: [],
        daily_spend: [
            { date: "2026-08-04", snapchat: 1, meta: 2, tiktok: 3, google: 4 },
            { date: "2026-08-05", snapchat: 5, meta: 6, tiktok: 7, google: 8 },
        ],
    }));

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-05",
        refresh: false,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenCalledWith({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-05",
        refresh: false,
        maxAgeMs: 0,
    });
    expect(result.chart_granularity).toBe("day");
    expect(result.daily_spend).toHaveLength(2);
    expect(result.daily_spend[1].google).toBe(8);
});
