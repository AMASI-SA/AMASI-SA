import {
    ADS_DATE_RANGE_APPLIED_EVENT,
    APPLIED_RANGE_GUARD_WINDOW_MS,
    recentAppliedDashboardRange,
    rememberAppliedDashboardRange,
    resetAppliedDashboardRangeForTests,
    rewriteDashboardRequestToVisibleRange,
} from "./dashboardVisibleRangeRequestGuard";

jest.mock("./lib/api", () => ({
    __esModule: true,
    default: {
        interceptors: {
            request: { use: jest.fn() },
        },
    },
}));

beforeEach(() => {
    document.body.innerHTML = "";
    jest.clearAllMocks();
    resetAppliedDashboardRangeForTests();
});

test("keeps the authoritative request dates when no range was just applied", () => {
    document.body.innerHTML = `
        <div data-testid="advanced-filters"
             data-from-date="2026-08-05"
             data-to-date="2026-08-05"></div>
    `;
    const config = {
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-04&to_date=2026-08-04",
    };

    // DOM text must never replace the React request dates.
    expect(rewriteDashboardRequestToVisibleRange(config, document, 10_000))
        .toBe(config);
});

test("captures the date picker apply event", () => {
    window.dispatchEvent(new CustomEvent(ADS_DATE_RANGE_APPLIED_EVENT, {
        detail: { dateFrom: "2026-08-04", dateTo: "2026-08-04" },
    }));

    expect(recentAppliedDashboardRange()).toEqual({
        from: "2026-08-04",
        to: "2026-08-04",
    });
});

test("repairs a stale silent Dashboard request immediately after apply", () => {
    rememberAppliedDashboardRange(
        { dateFrom: "2026-08-04", dateTo: "2026-08-04" },
        1_000,
    );

    const next = rewriteDashboardRequestToVisibleRange({
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-05&to_date=2026-08-05&payment_methods=mada",
    }, document, 1_100);

    const params = new URLSearchParams(next.url.split("?")[1]);
    expect(params.get("from_date")).toBe("2026-08-04");
    expect(params.get("to_date")).toBe("2026-08-04");
    expect(params.get("payment_methods")).toBe("mada");
    expect(next.params).toBeUndefined();
    expect(next._mezanDashboardAppliedRangeGuard).toBe(true);
});

test("preserves params-object filters while repairing stale dates", () => {
    rememberAppliedDashboardRange(
        { dateFrom: "2026-08-01", dateTo: "2026-08-04" },
        2_000,
    );

    const next = rewriteDashboardRequestToVisibleRange({
        method: "get",
        url: "/dashboard-v2",
        params: {
            from_date: "2026-08-05",
            to_date: "2026-08-05",
            shipping_companies: "SMSA",
        },
    }, document, 2_100);

    const params = new URLSearchParams(next.url.split("?")[1]);
    expect(params.get("from_date")).toBe("2026-08-01");
    expect(params.get("to_date")).toBe("2026-08-04");
    expect(params.get("shipping_companies")).toBe("SMSA");
});

test("leaves the correct newly applied request unchanged", () => {
    rememberAppliedDashboardRange(
        { dateFrom: "2026-08-04", dateTo: "2026-08-04" },
        3_000,
    );
    const config = {
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-04&to_date=2026-08-04",
    };

    expect(rewriteDashboardRequestToVisibleRange(config, document, 3_100))
        .toBe(config);
});

test("does not permanently override later Dashboard filters", () => {
    rememberAppliedDashboardRange(
        { dateFrom: "2026-08-04", dateTo: "2026-08-04" },
        4_000,
    );
    const later = {
        method: "get",
        url: "/dashboard-v2?from_date=2026-08-05&to_date=2026-08-05",
    };

    expect(rewriteDashboardRequestToVisibleRange(
        later,
        document,
        4_000 + APPLIED_RANGE_GUARD_WINDOW_MS + 1,
    )).toBe(later);
});

test("ignores invalid ranges and unrelated endpoints", () => {
    expect(rememberAppliedDashboardRange({
        dateFrom: "invalid",
        dateTo: "2026-08-04",
    }, 5_000)).toBeNull();

    const settings = { method: "get", url: "/settings" };
    expect(rewriteDashboardRequestToVisibleRange(settings, document, 5_100))
        .toBe(settings);
});
