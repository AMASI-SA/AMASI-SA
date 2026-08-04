import api from "../lib/api";
import { getAdsManagerOverview } from "./adsManager";
import {
    getDashboardAdsSpend,
    normalizeDashboardAdsHourlySpend,
} from "./dashboardAdsSpend";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn() },
}));

jest.mock("./adsManager", () => ({
    getAdsManagerOverview: jest.fn(),
}));

beforeEach(() => {
    jest.clearAllMocks();
});


test("normalizes and sorts Riyadh hourly provider spend", () => {
    const normalized = normalizeDashboardAdsHourlySpend({
        date: "2026-08-04",
        timezone: "Asia/Riyadh",
        granularity: "hour",
        available_hourly_providers: ["snapchat", "unsafe"],
        unavailable_hourly_providers: ["meta", "tiktok"],
        accounting_write_reached: true,
        hourly: [
            {
                date: "2026-08-04",
                hour_index: 3,
                hour: "03:00",
                snapchat: 50,
                meta: null,
                tiktok: null,
            },
            {
                date: "2026-08-04",
                hour_index: 0,
                hour: "00:00",
                snapchat: 125.25,
                meta: null,
                tiktok: null,
            },
        ],
    });

    expect(normalized.hourly.map((row) => row.hour)).toEqual(["00:00", "03:00"]);
    expect(normalized.hourly[0].snapchat).toBe(125.25);
    expect(normalized.hourly[0].meta).toBeNull();
    expect(normalized.available_hourly_providers).toEqual(["snapchat"]);
    expect(normalized.accounting_write_reached).toBe(false);
});


test("loads hourly facts only for a single selected day", async () => {
    getAdsManagerOverview.mockResolvedValue({
        daily_spend: [
            { date: "2026-08-04", snapchat: 100, meta: 20, tiktok: 5 },
        ],
    });
    api.get.mockResolvedValue({
        data: {
            date: "2026-08-04",
            timezone: "Asia/Riyadh",
            granularity: "hour",
            hourly: Array.from({ length: 24 }, (_, hourIndex) => ({
                date: "2026-08-04",
                hour_index: hourIndex,
                hour: `${String(hourIndex).padStart(2, "0")}:00`,
                snapchat: hourIndex === 8 ? 12.5 : 0,
                meta: null,
                tiktok: null,
            })),
        },
    });

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-04",
    });

    expect(api.get).toHaveBeenCalledWith(
        "/integrations-v2/dashboard/ads-hourly-spend",
        { params: { date: "2026-08-04" } },
    );
    expect(result.chart_granularity).toBe("hour");
    expect(result.hourly_spend).toHaveLength(24);
    expect(result.daily_spend[0].meta).toBe(20);
});


test("keeps the established daily chart for a multi-day range", async () => {
    getAdsManagerOverview.mockResolvedValue({
        daily_spend: [
            { date: "2026-08-03", snapchat: 10, meta: 20, tiktok: 5 },
            { date: "2026-08-04", snapchat: 15, meta: 25, tiktok: 7 },
        ],
    });

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-03",
        dateTo: "2026-08-04",
    });

    expect(api.get).not.toHaveBeenCalled();
    expect(result.chart_granularity).toBe("day");
    expect(result.hourly_spend).toEqual([]);
    expect(result.daily_spend).toHaveLength(2);
});
