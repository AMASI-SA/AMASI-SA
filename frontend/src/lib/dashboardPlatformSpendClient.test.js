import axios from "axios";
import {
    clearDashboardPlatformSpendCache,
    loadDashboardPlatformSpend,
} from "./dashboardPlatformSpendClient";

jest.mock("axios", () => ({
    get: jest.fn(),
    post: jest.fn(),
}));

beforeEach(() => {
    jest.clearAllMocks();
    clearDashboardPlatformSpendCache();
    localStorage.clear();
});

test("deduplicates concurrent selected-period refreshes and caches the result", async () => {
    localStorage.setItem("access_token", "token-1");
    let resolveRequest;
    axios.post.mockReturnValue(new Promise((resolve) => {
        resolveRequest = resolve;
    }));

    const first = loadDashboardPlatformSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-04",
        refresh: true,
    });
    const second = loadDashboardPlatformSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-04",
        refresh: true,
    });

    expect(axios.post).toHaveBeenCalledTimes(1);
    resolveRequest({
        data: {
            date_from: "2026-08-04",
            date_to: "2026-08-04",
            total_sar: 7378.7,
        },
    });

    await expect(first).resolves.toEqual(expect.objectContaining({ total_sar: 7378.7 }));
    await expect(second).resolves.toEqual(expect.objectContaining({ total_sar: 7378.7 }));

    const cached = await loadDashboardPlatformSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-04",
        refresh: true,
    });
    expect(cached.total_sar).toBe(7378.7);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post.mock.calls[0][2]).toEqual(expect.objectContaining({
        withCredentials: true,
        headers: { Authorization: "Bearer token-1" },
    }));
});

test("reads saved platform spend without triggering a provider refresh", async () => {
    axios.get.mockResolvedValue({
        data: {
            date_from: "2026-08-01",
            date_to: "2026-08-03",
            total_sar: 900,
        },
    });

    const result = await loadDashboardPlatformSpend({
        dateFrom: "2026-08-01",
        dateTo: "2026-08-03",
        refresh: false,
        maxAgeMs: 0,
    });

    expect(result.total_sar).toBe(900);
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.get).toHaveBeenCalledWith(
        expect.stringContaining("/integrations-v2/dashboard/ads-platform-spend"),
        expect.objectContaining({
            params: {
                date_from: "2026-08-01",
                date_to: "2026-08-03",
            },
        }),
    );
});

test("rejects invalid or reversed date ranges", async () => {
    await expect(loadDashboardPlatformSpend({
        dateFrom: "not-a-date",
        dateTo: "2026-08-04",
    })).rejects.toThrow("invalid_dashboard_platform_spend_range");

    await expect(loadDashboardPlatformSpend({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-04",
    })).rejects.toThrow("invalid_dashboard_platform_spend_range");
});
