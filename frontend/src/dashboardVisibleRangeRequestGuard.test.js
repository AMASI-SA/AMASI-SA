import api from "./lib/api";
import {
    rewriteDashboardRequestToVisibleRange,
    visibleDashboardRange,
} from "./dashboardVisibleRangeRequestGuard";

jest.mock("./lib/api", () => ({
    __esModule: true,
    default: {
        interceptors: {
            request: { use: jest.fn() },
        },
    },
}));

function renderVisibleRange(from, to = from) {
    document.body.innerHTML = `
        <div
            data-testid="advanced-filters"
            data-from-date="${from}"
            data-to-date="${to}"
        ></div>
    `;
}

beforeEach(() => {
    document.body.innerHTML = "";
    jest.clearAllMocks();
});

test("reads the date range currently visible in AdvancedFilters", () => {
    renderVisibleRange("2026-08-04", "2026-08-04");
    expect(visibleDashboardRange()).toEqual({
        from: "2026-08-04",
        to: "2026-08-04",
    });
});

test("rewrites a stale Dashboard V2 request to the visible selected date", () => {
    renderVisibleRange("2026-08-04", "2026-08-04");

    const next = rewriteDashboardRequestToVisibleRange({
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-05&to_date=2026-08-05&payment_methods=mada",
    });

    const params = new URLSearchParams(next.url.split("?")[1]);
    expect(next.url.split("?")[0]).toBe("/dashboard-v2");
    expect(params.get("from_date")).toBe("2026-08-04");
    expect(params.get("to_date")).toBe("2026-08-04");
    expect(params.get("payment_methods")).toBe("mada");
    expect(next.params).toBeUndefined();
    expect(next._mezanDashboardVisibleRangeGuard).toBe(true);
});

test("rewrites stale params-object dates and preserves other filters", () => {
    renderVisibleRange("2026-08-01", "2026-08-04");

    const next = rewriteDashboardRequestToVisibleRange({
        method: "get",
        url: "/dashboard-v2",
        params: {
            from_date: "2026-08-05",
            to_date: "2026-08-05",
            shipping_companies: "SMSA",
        },
    });

    const params = new URLSearchParams(next.url.split("?")[1]);
    expect(params.get("from_date")).toBe("2026-08-01");
    expect(params.get("to_date")).toBe("2026-08-04");
    expect(params.get("shipping_companies")).toBe("SMSA");
});

test("leaves a matching Dashboard request unchanged", () => {
    renderVisibleRange("2026-08-04", "2026-08-04");
    const config = {
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-04&to_date=2026-08-04",
    };
    expect(rewriteDashboardRequestToVisibleRange(config)).toBe(config);
});

test("does not affect unrelated requests or invalid visible dates", () => {
    renderVisibleRange("invalid", "2026-08-04");
    const dashboard = { method: "get", url: "/dashboard-v2?from_date=2026-08-05" };
    expect(rewriteDashboardRequestToVisibleRange(dashboard)).toBe(dashboard);

    renderVisibleRange("2026-08-04", "2026-08-04");
    const settings = { method: "get", url: "/settings" };
    expect(rewriteDashboardRequestToVisibleRange(settings)).toBe(settings);
    expect(api.interceptors.request.use).toHaveBeenCalledTimes(1);
});
