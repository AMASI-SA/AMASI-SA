const mockResponseUse = jest.fn();
const mockLoadDashboardPlatformSpend = jest.fn();
const mockMergeDashboardWithPlatformSpend = jest.fn();

jest.mock("./lib/api", () => ({
    __esModule: true,
    default: {
        interceptors: {
            response: {
                use: (...args) => mockResponseUse(...args),
            },
        },
    },
}));

jest.mock("./lib/dashboardPlatformSpendClient", () => ({
    loadDashboardPlatformSpend: (...args) => mockLoadDashboardPlatformSpend(...args),
}));

jest.mock("./lib/dashboardPlatformSpendMerge", () => ({
    mergeDashboardWithPlatformSpend: (...args) => mockMergeDashboardWithPlatformSpend(...args),
}));

beforeEach(() => {
    jest.clearAllMocks();
    jest.resetModules();
    jest.useRealTimers();
});

function dashboardResponse() {
    return {
        config: {
            method: "get",
            url: "/dashboard-v2?from_date=2026-08-04&to_date=2026-08-04",
        },
        data: { totals: { total_ads_cost: 3001.72 } },
    };
}

test("enriches the selected Dashboard V2 range from saved spend without provider refresh", async () => {
    jest.isolateModules(() => {
        require("./dashboardExecutivePlatformSpendInterceptor");
    });
    expect(mockResponseUse).toHaveBeenCalledTimes(1);
    const handler = mockResponseUse.mock.calls[0][0];
    const response = dashboardResponse();
    const platform = {
        date_from: "2026-08-04",
        date_to: "2026-08-04",
        total_sar: 7378.7,
    };
    const merged = { totals: { total_ads_cost: 7378.7 } };
    mockLoadDashboardPlatformSpend.mockResolvedValue(platform);
    mockMergeDashboardWithPlatformSpend.mockReturnValue(merged);

    const result = await handler(response);

    expect(mockLoadDashboardPlatformSpend).toHaveBeenCalledWith({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-04",
        refresh: false,
        maxAgeMs: 30_000,
    });
    expect(mockMergeDashboardWithPlatformSpend).toHaveBeenCalledWith(
        response.data,
        platform,
    );
    expect(result.data).toBe(merged);
});

test("returns the core Dashboard response after the strict saved-spend wait ceiling", async () => {
    jest.useFakeTimers();
    jest.isolateModules(() => {
        require("./dashboardExecutivePlatformSpendInterceptor");
    });
    const module = require("./dashboardExecutivePlatformSpendInterceptor");
    const handler = mockResponseUse.mock.calls[0][0];
    const response = dashboardResponse();
    mockLoadDashboardPlatformSpend.mockReturnValue(new Promise(() => {}));

    const resultPromise = handler(response);
    jest.advanceTimersByTime(module.FAST_SAVED_SPEND_WAIT_MS + 1);

    await expect(resultPromise).resolves.toBe(response);
    expect(mockMergeDashboardWithPlatformSpend).not.toHaveBeenCalled();
});

test("leaves unrelated responses untouched", async () => {
    jest.isolateModules(() => {
        require("./dashboardExecutivePlatformSpendInterceptor");
    });
    const handler = mockResponseUse.mock.calls[0][0];
    const response = {
        config: { method: "get", url: "/settings" },
        data: { ok: true },
    };

    await expect(handler(response)).resolves.toBe(response);
    expect(mockLoadDashboardPlatformSpend).not.toHaveBeenCalled();
});
