import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdvancedDashboard, { loadDashboardPeriodSnapshot } from "./AdvancedDashboard";
import api from "../lib/api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a> }));
let mockOrders = [];
jest.mock("../hooks/useOrders", () => ({ useOrders: () => ({ orders: mockOrders, hasMore: false }) }));
jest.mock("../services/dashboardAdsSpend", () => ({
    ...jest.requireActual("../services/dashboardAdsSpend"), getDashboardAdsSpend: jest.fn(async () => null),
}));
let mockRenderRealSpendCard = false;
let mockSpendCardProps;
jest.mock("../components/DashboardAdsSpendCard", () => (props) => {
    mockSpendCardProps = props;
    const RealCard = jest.requireActual("../components/DashboardAdsSpendCard").default;
    return mockRenderRealSpendCard ? <RealCard {...props} /> : <div data-testid="unchanged-spend-card" />;
});
jest.mock("../components/LatestSoldProductsCard", () => () => null);
jest.mock("../components/AdvancedFilters", () => ({
    __esModule: true,
    default: ({ onChange }) => <button onClick={() => onChange({ from: "2026-11-10", to: "2026-11-10", payment_methods: ["cash"], shipping_companies: ["other"] })}>change period</button>,
    defaultFilters: () => ({ from: "2026-11-11", to: "2026-11-11", payment_methods: ["mada"], shipping_companies: ["carrier"] }),
    filtersToQueryString: ({ from, to }) => `from_date=${from}&to_date=${to}`,
}));

const START = Date.parse("2026-11-11T18:00:00Z");
const paths = {
    top: "/dashboard-v2/cards/top-products",
    summary: "/dashboard-v2/cards/period-summary",
    executive: "/dashboard-v2/cards/executive",
};
const cadence = { top: 30 * 60 * 1000, summary: 5 * 60 * 60 * 1000, executive: 60 * 1000 };
const calls = (kind) => api.get.mock.calls.filter(([url]) => url.startsWith(`${paths[kind]}?`));
const snapshot = (kind) => ({ generated_at: new Date().toISOString(), next_refresh_at: new Date(Date.now() + cadence[kind]).toISOString(), refresh_interval_seconds: cadence[kind] / 1000 });
const product = (name = "منتج محفوظ") => ({
    identity: name, name, units_sold: 9, total_sales: 900, total_cost: 90, net_profit: 810, cost_status: "complete",
});
const productMetadata = { product_profit_summary: {}, salla_fallback_products_count: 0, missing_all_cost_products_count: 0 };
function responseFor(url) {
    if (url.startsWith(paths.top)) return { data: { product_cost_v2: { ...productMetadata, product_rows: [product()], missing_products_count: 2 }, snapshot: snapshot("top") } };
    if (url.startsWith(paths.summary)) return { data: { month_kpis: { total_orders: 123, total_sales: 4567 }, product_cost_v2: { missing_products_count: 4 }, ads_v2: { executive_breakdown: { providers: {}, total: {} } }, snapshot: snapshot("summary") } };
    if (url.startsWith(paths.executive)) return { data: { totals: { total_orders: 10, total_sales: 1000 }, snapshot: snapshot("executive") } };
    return { data: {} };
}
let host, root;
const render = async () => act(async () => { root.render(<AdvancedDashboard />); });
const tick = async (ms) => act(async () => { jest.advanceTimersByTime(ms); });
const click = async (label) => act(async () => { host.querySelector(`[aria-label="${label}"]`).click(); });
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.setSystemTime(START); jest.clearAllMocks();
    mockOrders = [];
    mockRenderRealSpendCard = false;
    mockSpendCardProps = null;
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    api.get.mockImplementation(async (url) => responseFor(url));
    getDashboardAdsSpend.mockResolvedValue(null);
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); jest.useRealTimers();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

function sharedSpend(amount, { day = "2026-11-11", complete = false } = {}) {
    return { date_from: day, date_to: day, timezone: "Asia/Riyadh",
        total_sar: complete ? amount : null, known_total_sar: amount,
        provider_totals_sar: { snapchat: 0, meta: 0, tiktok: 0, google: complete ? amount : null },
        providers: { google: { connected: true, amount_available: complete,
            status: complete ? "complete" : "incomplete", known_total_sar: amount } },
        spend_quality: { status: complete ? "complete" : "incomplete",
            amount_available: complete, amount_complete: complete },
        daily_spend: [], hourly_spend: [],
    };
}

test("available portfolio total stays identical after the real spend card's existing read without another request", async () => {
    mockRenderRealSpendCard = true;
    let amount = 100;
    getDashboardAdsSpend.mockImplementation(async () => sharedSpend(amount));
    await render();
    const cardTotal = () => host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent;
    const profitTotal = () => host.querySelector('[data-testid="advanced-profit-row-ads"]').textContent;
    expect(cardTotal()).toContain("100.00");
    expect(profitTotal()).toContain("100.00");
    const reads = getDashboardAdsSpend.mock.calls.length;
    const apiReads = api.get.mock.calls.length;
    amount = 200;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(cardTotal()).toContain("200.00");
    expect(profitTotal()).toContain("200.00");
    expect(host.textContent).toContain("تكلفة الطلب والعائد بانتظار اكتمال الصرف");
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(reads + 1);
    expect(api.get).toHaveBeenCalledTimes(apiReads);
    expect(api.post).not.toHaveBeenCalled();
});

test("older platform read cannot replace a newer same-period snapshot or its canonical complete total", async () => {
    const canonical = { totals: { total_sales: 1000, total_orders: 10,
        total_ads_cost: 200, ads_spend_data_complete: true },
        ads_v2: { spend_quality: { status: "complete", amount_complete: true } },
        snapshot: snapshot("executive") };
    api.get.mockImplementation(async (url) => url.startsWith(paths.executive)
        ? { data: canonical } : responseFor(url));
    getDashboardAdsSpend.mockResolvedValue(sharedSpend(200, { complete: true }));
    await render();
    const older = mockSpendCardProps.beginSnapshotRead();
    const newer = mockSpendCardProps.beginSnapshotRead();
    await act(async () => mockSpendCardProps.onSnapshot(sharedSpend(200, { complete: true }), newer));
    await act(async () => mockSpendCardProps.onSnapshot(sharedSpend(100), older));
    expect(mockSpendCardProps.snapshot.total_sar).toBe(200);
    const row = host.querySelector('[data-testid="advanced-profit-row-ads"]').textContent;
    expect(row).toContain("200.00");
    expect(row).not.toContain("غير مكتمل");
    expect(canonical.totals.total_ads_cost).toBe(200);
});

test("a previous-period snapshot cannot replace both cards after the selected period changes", async () => {
    getDashboardAdsSpend.mockImplementation(async ({ dateFrom }) => sharedSpend(
        dateFrom === "2026-11-11" ? 100 : 200, { day: dateFrom }));
    await render();
    const priorCallbacks = mockSpendCardProps;
    await act(async () => Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "change period").click());
    await act(async () => priorCallbacks.onSnapshot(sharedSpend(999), priorCallbacks.beginSnapshotRead()));
    expect(mockSpendCardProps.snapshot.date_from).toBe("2026-11-10");
    const row = host.querySelector('[data-testid="advanced-profit-row-ads"]').textContent;
    expect(row).toContain("200.00");
    expect(row).not.toContain("999.00");
});

test("independent cards show their snapshots, requested controls and their own manual refresh", async () => {
    await render();
    for (const kind of Object.keys(paths)) expect(calls(kind)).toHaveLength(1);
    expect(host.querySelector('[data-testid="advanced-top-products"]').textContent).toContain("900.00");
    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).toContain("4,567.00");
    const topButton = host.querySelector('[aria-label="تحديث المنتجات الأكثر مبيعًا"]');
    expect(topButton.textContent).toBe("");
    expect(topButton.classList.contains("rounded-full")).toBe(true);
    expect(topButton.classList.contains("h-11")).toBe(true);
    expect(topButton.querySelector("svg")).not.toBeNull();
    expect(topButton.title).toBe("تحديث المنتجات الأكثر مبيعًا");
    expect(host.querySelector('[aria-label="تحديث ملخص الفترة"]').textContent).toBe("تحديث");
    await click("تحديث المنتجات الأكثر مبيعًا");
    expect(calls("top")).toHaveLength(2);
    expect(calls("summary")).toHaveLength(1);
    expect(calls("executive")).toHaveLength(1);
    expect(new URLSearchParams(calls("top")[1][0].split("?")[1]).get("refresh")).toBe("true");
    await click("تحديث ملخص الفترة");
    expect(calls("summary")).toHaveLength(2);
    expect(calls("top")).toHaveLength(2);
    expect(calls("executive")).toHaveLength(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(1);
});

test("main polling, new orders and early focus cannot refresh the two cards or bypass sixty seconds", async () => {
    await render();
    mockOrders = [{ order_number: "new-order", total_amount: 1 }];
    await render();
    await act(async () => {
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("online"));
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(30_000);
    for (const kind of Object.keys(paths)) expect(calls(kind)).toHaveLength(1);
    await tick(30_000);
    expect(calls("executive")).toHaveLength(2);
    expect(calls("top")).toHaveLength(1);
    expect(calls("summary")).toHaveLength(1);
    await tick(cadence.top - 60_000);
    expect(calls("top")).toHaveLength(2);
    expect(calls("summary")).toHaveLength(1);
    await tick(cadence.summary - cadence.top - 1);
    expect(calls("summary")).toHaveLength(1);
    await tick(1);
    expect(calls("summary")).toHaveLength(2);
    for (const kind of Object.keys(paths)) {
        expect(calls(kind).every(([url]) => !url.includes("_refresh") && !new URLSearchParams(url.split("?")[1]).has("refresh"))).toBe(true);
    }
});

test("the existing global refresh explicitly updates all three saved sections once", async () => {
    await render();
    await click("تحديث بيانات الفترة");
    for (const kind of Object.keys(paths)) {
        expect(calls(kind)).toHaveLength(2);
        expect(new URLSearchParams(calls(kind)[1][0].split("?")[1]).get("refresh")).toBe("true");
    }
    expect(api.post).not.toHaveBeenCalled();
});

test("card loading is local, disables duplicate actions, spins, and leaves collapse state intact", async () => {
    await render();
    let resolveTop;
    api.get.mockImplementation((url) => url.startsWith(paths.top)
        ? new Promise((resolve) => { resolveTop = resolve; }) : Promise.resolve(responseFor(url)));
    const panel = host.querySelector('[data-testid="advanced-top-products"]');
    await act(async () => panel.querySelector(".dashboard-panel-toggle").click());
    await click("تحديث المنتجات الأكثر مبيعًا");
    const button = panel.querySelector('[aria-label="تحديث المنتجات الأكثر مبيعًا"]');
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.querySelector(".animate-spin")).not.toBeNull();
    expect(host.querySelector('[aria-label="تحديث ملخص الفترة"]').disabled).toBe(false);
    await click("تحديث المنتجات الأكثر مبيعًا");
    expect(calls("top")).toHaveLength(2);
    await act(async () => resolveTop(responseFor(paths.top)));
    expect(button.disabled).toBe(false);
    expect(panel.querySelector(".dashboard-panel-toggle").getAttribute("aria-expanded")).toBe("false");
});

test("initial executive/card failures leave both card refresh controls visible and recoverable", async () => {
    api.get.mockImplementation((url) => url.startsWith("/dashboard-v2/cards/")
        ? Promise.reject(new Error("unavailable")) : Promise.resolve(responseFor(url)));
    await render();
    expect(host.querySelector('[aria-label="تحديث المنتجات الأكثر مبيعًا"]').disabled).toBe(false);
    expect(host.querySelector('[aria-label="تحديث ملخص الفترة"]').disabled).toBe(false);
    expect(host.querySelector('[data-testid="advanced-top-products"]').textContent).toContain("تعذر تحديث المنتجات");
    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).toContain("تعذر تحديث ملخص الفترة");
    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).not.toContain("0 منتجًا");
    expect(host.querySelector('[data-testid="advanced-profit-summary"]')).toBeNull();
    api.get.mockImplementation(async (url) => responseFor(url));
    await click("تحديث المنتجات الأكثر مبيعًا");
    await click("تحديث ملخص الفترة");
    expect(host.querySelector('[data-testid="advanced-top-products"]').textContent).toContain("900.00");
    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).toContain("4,567.00");
});

test("malformed successful summary responses preserve known figures and never replace them with zero", async () => {
    await render();
    for (const invalid of [{}, { snapshot: snapshot("summary") }]) {
        api.get.mockImplementation((url) => Promise.resolve(url.startsWith(paths.summary) ? { data: invalid } : responseFor(url)));
        await click("تحديث ملخص الفترة");
        const text = host.querySelector('[data-testid="advanced-period-summary"]').textContent;
        expect(text).toContain("4,567.00");
        expect(text).toContain("123");
        expect(text).toContain("تعذر تحديث ملخص الفترة");
        expect(text).toContain("تم الاحتفاظ بآخر بيانات موثوقة لهذه الفترة");
    }
});

test("an explicit empty cohort and null sales remain valid observations", async () => {
    api.get.mockImplementation(async (url) => {
        if (url.startsWith(paths.top)) return { data: { product_cost_v2: { ...productMetadata, product_rows: [] }, snapshot: snapshot("top") } };
        if (url.startsWith(paths.summary)) return { data: { month_kpis: { total_orders: 0, total_sales: null }, product_cost_v2: { missing_products_count: 0 }, ads_v2: { executive_breakdown: { providers: {}, total: {} } }, snapshot: snapshot("summary") } };
        return responseFor(url);
    });
    await render();
    expect(host.querySelector('[data-testid="advanced-top-products"]').textContent).toContain("لا توجد منتجات مباعة في الفترة");
    expect(host.querySelector('[data-testid="advanced-top-products"] [role="alert"]')).toBeNull();
    const summary = host.querySelector('[data-testid="advanced-period-summary"]');
    expect(summary.textContent).toContain("غير مكتمل");
    expect(summary.querySelector('[role="alert"]')).toBeNull();
});

test("date changes use the selected filters for all cards and never show the prior period while pending", async () => {
    await render();
    const pending = [];
    api.get.mockImplementation((url) => url.startsWith("/dashboard-v2/cards/")
        ? new Promise((resolve) => pending.push({ url, resolve })) : Promise.resolve(responseFor(url)));
    await act(async () => Array.from(host.querySelectorAll("button")).find((button) => button.textContent === "change period").click());
    expect(pending).toHaveLength(3);
    for (const { url } of pending) {
        const query = new URLSearchParams(url.split("?")[1]);
        expect(query.get("from_date")).toBe("2026-11-10");
        expect(query.get("to_date")).toBe("2026-11-10");
        expect(query.get("payment_methods")).toBe("cash");
        expect(query.get("shipping_companies")).toBe("other");
        expect(query.has("refresh")).toBe(false);
    }
    expect(host.querySelector('[data-testid="advanced-top-products"]').textContent).not.toContain("900.00");
    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).not.toContain("4,567.00");
    await act(async () => pending.forEach(({ url, resolve }) => resolve(responseFor(url))));
});

test("the focused executive reader forces one same-filter recomputation when saved spend is newer", async () => {
    const next = { from: "2026-11-11", to: "2026-11-11", payment_methods: ["mada"], shipping_companies: ["carrier"], report_statuses: ["completed"] };
    const pending = { totals: { total_ads_cost: null }, ads_v2: { spend_quality: { status: "incomplete", amount_available: false } } };
    const complete = { totals: { total_ads_cost: 10, net_profit: 90, total_sales: 100 }, ads_v2: { spend_quality: { status: "complete", amount_available: true, amount_complete: true } } };
    const apiClient = { get: jest.fn()
        .mockResolvedValueOnce({ data: pending })
        .mockResolvedValueOnce({ data: complete }) };
    const setData = jest.fn();
    await loadDashboardPeriodSnapshot({ next, endpoint: paths.executive, apiClient, requestSequence: 1, isLatest: () => true,
        setData, setLoading: jest.fn(), setLoadError: jest.fn(),
        platformSpendLoader: jest.fn(async () => ({ date_from: next.from, date_to: next.to, total_sar: 10,
            provider_totals_sar: { snapchat: 10, meta: 0, tiktok: 0, google: 0 }, spend_quality: { status: "complete", amount_available: true, amount_complete: true } })),
    });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    const initial = new URLSearchParams(apiClient.get.mock.calls[0][0].split("?")[1]);
    const forced = new URLSearchParams(apiClient.get.mock.calls[1][0].split("?")[1]);
    expect(initial.has("refresh")).toBe(false);
    expect(forced.get("refresh")).toBe("true");
    forced.delete("refresh");
    expect(forced.toString()).toBe(initial.toString());
    expect(initial.get("report_statuses")).toBe("completed");
    expect(apiClient.get.mock.calls.every(([url]) => !url.includes("_refresh"))).toBe(true);
    for (const [, config] of apiClient.get.mock.calls) {
        expect(config.timeout).toBe(60_000);
        expect(config._mezanAuthDeadlineAt).toBe(START + 60_000);
    }
    expect(setData.mock.calls[0][0].totals.net_profit).toBe(90);
});
