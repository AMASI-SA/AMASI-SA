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
    default: ({ onChange, value }) => <>
        <button onClick={() => onChange({ from: "2026-11-10", to: "2026-11-10", payment_methods: ["cash"], shipping_companies: ["other"] })}>change period</button>
        <button onClick={() => onChange({ ...value, payment_methods: [" cash ", "bank", "cash"],
            shipping_companies: ["other", "courier", "other"], report_statuses: ["reviewed", "in_progress", "reviewed"] })}>change cohort</button>
    </>,
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

test("shared saved spend still renders when the separate executive read fails", async () => {
    mockRenderRealSpendCard = true;
    getDashboardAdsSpend.mockResolvedValue(sharedSpend(200));
    api.get.mockImplementation(async (url) => {
        if (url.startsWith(paths.executive)) throw new Error("executive unavailable");
        return responseFor(url);
    });
    await render();
    expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent).toContain("200.00");
    expect(host.textContent).toContain("تعذر تحميل بيانات الفترة المحددة");
    expect(calls("executive")).toHaveLength(1);
    expect(api.post).not.toHaveBeenCalled();
});

test.each(["complete", "provisional"])("shared %s spend updates both totals and providers while canonical metrics wait for the existing read", async (status) => {
    mockRenderRealSpendCard = true;
    let savedAmount = 100;
    let canonicalAmount = 100;
    const quality = { status, amount_available: true, amount_complete: status === "complete",
        provisional: status === "provisional" };
    const platform = () => ({ ...sharedSpend(savedAmount, { complete: true }), spend_quality: quality });
    getDashboardAdsSpend.mockImplementation(async () => platform());
    api.get.mockImplementation(async (url) => url.startsWith(paths.executive) ? { data: {
        totals: { total_sales: 1000, total_orders: 10, total_ads_cost: canonicalAmount,
            ads_spend_data_complete: status === "complete", ads_spend_amount_available: true,
            ads_spend_provisional: status === "provisional", avg_cost_per_order: canonicalAmount / 10,
            overall_roas: 1000 / canonicalAmount, net_profit: 800 - canonicalAmount },
        ads_v2: { spend_quality: quality,
            breakdown: { snapchat: 0, meta: 0, tiktok: 0, google_transitional: canonicalAmount },
            executive_breakdown: { providers: { google: { spend_sar: canonicalAmount, salla_orders: 10,
                salla_sales_sar: 1000, actual_roas: 1000 / canonicalAmount } },
                total: { spend_sar: canonicalAmount, salla_orders: 10, salla_sales_sar: 1000,
                    actual_roas: 1000 / canonicalAmount } } }, snapshot: snapshot("executive"),
    } } : responseFor(url));
    await render();
    const profit = () => host.querySelector('[data-testid="advanced-profit-summary"]');
    const row = () => host.querySelector('[data-testid="advanced-profit-row-ads"]');
    expect(row().textContent).toContain("100.00");
    const initialApiReads = api.get.mock.calls.length;
    savedAmount = 200;
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent).toContain("200.00");
    expect(row().textContent).toContain("200.00");
    expect(profit().textContent).toContain("تكلفة الطلب والعائد بانتظار تحديث الملخص");
    expect(profit().textContent).not.toContain("700.00");
    expect(api.get).toHaveBeenCalledTimes(initialApiReads);
    await act(async () => row().click());
    expect(host.querySelector('[data-testid="ads-executive-spend-total"]').textContent).toContain("200.00");
    const rows = host.querySelectorAll('[data-testid="ads-executive-breakdown-table"] tbody tr');
    expect(rows[0].textContent).toContain("200.00");
    expect(rows[0].textContent).not.toContain("100.00");
    expect(rows[3].lastElementChild.textContent).toBe("—");
    expect(rows[4].lastElementChild.textContent).toBe("—");
    canonicalAmount = 200;
    await tick(60_000);
    expect(profit().textContent).not.toContain("بانتظار تحديث الملخص");
    expect(profit().textContent).toContain("20.00");
    expect(profit().textContent).toContain("5.00×");
    expect(profit().textContent).toContain("600.00");
});

test("an older platform response stays rejected while the newer read is still pending", async () => {
    getDashboardAdsSpend.mockResolvedValue(sharedSpend(100));
    await render();
    const older = mockSpendCardProps.beginSnapshotRead();
    const newer = mockSpendCardProps.beginSnapshotRead();
    await act(async () => mockSpendCardProps.onSnapshot(sharedSpend(200), older));
    expect(mockSpendCardProps.snapshot.known_total_sar).toBe(100);
    await act(async () => mockSpendCardProps.onSnapshot(sharedSpend(300), newer));
    expect(mockSpendCardProps.snapshot.known_total_sar).toBe(300);
});

test.each(["complete", "provisional"])("executive %s read never recalculates canonical profit or bank fees from the display snapshot", async (status) => {
    const canonical = { totals: { total_sales: 1000, total_ads_cost: 100, net_profit: 797,
        ad_bank_commission_fees: 3, ads_spend_amount_available: true },
        ads_v2: { spend_quality: { status, amount_available: true, provisional: status === "provisional" },
            breakdown: { snapchat: 100, meta: 0, tiktok: 0, google_transitional: 0 } } };
    const platform = { ...sharedSpend(200, { complete: true }), spend_quality: {
        status, amount_available: true, amount_complete: status === "complete", provisional: status === "provisional",
    } };
    const apiClient = { get: jest.fn().mockResolvedValue({ data: canonical }) };
    let result;
    await loadDashboardPeriodSnapshot({ next: { from: "2026-11-11", to: "2026-11-11" },
        endpoint: paths.executive, requestSequence: 1, isLatest: () => true, apiClient,
        platformSpendLoader: async () => platform, setData: (value) => { result = value; },
        setLoading: jest.fn(), setLoadError: jest.fn() });
    expect(result.totals).toBe(canonical.totals);
    expect(result.ads_v2).toBe(canonical.ads_v2);
    expect(result.totals.net_profit).toBe(797);
    expect(result.totals.ad_bank_commission_fees).toBe(3);
    expect(result.dashboard_platform_spend).toBe(platform);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
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

const HOVER_PATH = "/orders-v2/campaign-hover";
const HOVER_CAMPAIGN = "11111111-1111-4111-8111-111111111111";
const campaignOrders = () => [{ order_number: "campaign-order", created_at: "2026-11-11T18:00:00Z",
    status: "processing", source: { channel: "snapchat", campaign_id: HOVER_CAMPAIGN, campaign_name: "حملة الاختبار" },
    totals: { total: 500, total_sar: 500, currency: "SAR" }, items: [{ id: "piece", quantity: 1 }] }];
const hoverReads = () => api.get.mock.calls.filter(([url]) => url === HOVER_PATH);
function campaignResult(params, { amount = 10, ratio = 0.02, net = 235 } = {}) {
    return { data: { campaigns: { [HOVER_CAMPAIGN]: {
        status: "available", timezone: "America/Los_Angeles", date: "2026-11-11",
        orders: 1, pieces: 1, sales_sar: 500, spend_sar: 100, cpa_sar: 100, roas: 5,
        total_cost_sar: 155, product_cost_complete: true, costs_complete: true,
        operating_expenses_sar: amount, operating_expenses_ratio: ratio, operating_expenses_complete: true,
        net_profit_sar: net, net_profit_available: true,
        operating_basis: { source: "executive_selected_period", timezone: "Asia/Riyadh",
            date_from: params.operating_from_date, date_to: params.operating_to_date,
            payment_methods: params.operating_payment_methods?.split(",") || [],
            shipping_companies: params.operating_shipping_companies?.split(",") || [],
            report_statuses: params.operating_report_statuses?.split(",") || null },
    } } } };
}
function executiveCampaignResponse(url, operatingTotal = 20) {
    const response = responseFor(url);
    if (url.startsWith(paths.executive)) response.data.totals.operating_expenses_total = operatingTotal;
    return response;
}
async function openCampaign() {
    await act(async () => host.querySelector('[role="button"][aria-label^="مصدر الطلب"]').click());
}
function campaignCell(label) {
    const row = [...document.querySelector('[role="tooltip"]').querySelectorAll("tr")]
        .find((item) => item.querySelector("th").textContent === label);
    return row.querySelector("td").textContent;
}
async function selectCampaignFilters(label) {
    await act(async () => Array.from(host.querySelectorAll("button")).find((button) => button.textContent === label).click());
}

test("campaign operating basis keeps the visible executive selection through Riyadh midnight", async () => {
    jest.setSystemTime(new Date("2026-11-11T20:59:50Z"));
    mockOrders = campaignOrders();
    api.get.mockImplementation(async (url, config) => url === HOVER_PATH
        ? campaignResult(config.params) : executiveCampaignResponse(url));
    await render();
    await tick(400);
    await openCampaign();
    expect(campaignCell("المصروفات التشغيلية")).toBe("10.00 ر.س (2%)");
    jest.setSystemTime(new Date("2026-11-11T21:00:10Z"));
    mockOrders = mockOrders.map((order) => ({ ...order }));
    await render();
    await tick(400);
    expect(hoverReads()).toHaveLength(2);
    for (const [, { params }] of hoverReads()) {
        expect(params).toMatchObject({ operating_from_date: "2026-11-11", operating_to_date: "2026-11-11",
            operating_payment_methods: "mada", operating_shipping_companies: "carrier" });
    }
    expect(host.querySelector('[data-testid="advanced-profit-row-operating"]').textContent).toContain("2.00%");
    expect(campaignCell("المصروفات التشغيلية")).toBe("10.00 ر.س (2%)");
    expect(document.querySelector('[data-testid="campaign-operating-basis"]').textContent).toContain("2026-11-11");
    expect(document.querySelector('[data-testid="campaign-operating-basis"]').textContent).not.toContain("2026-11-12");
    expect(document.querySelector('[role="tooltip"]').textContent).toContain("America/Los_Angeles");
});

test("identical campaign IDs cannot show or request the old basis while a new executive period is pending", async () => {
    mockOrders = campaignOrders();
    api.get.mockImplementation(async (url, config) => url === HOVER_PATH
        ? campaignResult(config.params) : executiveCampaignResponse(url));
    await render();
    await tick(400);
    await openCampaign();
    expect(campaignCell("صافي الأرباح")).toBe("235.00 ر.س");
    let pendingExecutive;
    api.get.mockImplementation((url, config) => {
        if (url.startsWith(paths.executive)) return new Promise((resolve) => { pendingExecutive = { url, resolve }; });
        if (url === HOVER_PATH) return Promise.resolve(campaignResult(config.params));
        return Promise.resolve(responseFor(url));
    });
    await selectCampaignFilters("change period");
    expect(campaignCell("المصروفات التشغيلية")).toBe("غير مكتمل");
    expect(campaignCell("صافي الأرباح")).toBe("غير مكتمل");
    await tick(400);
    expect(hoverReads()).toHaveLength(1);
    expect(pendingExecutive).toBeDefined();
    await act(async () => pendingExecutive.resolve(executiveCampaignResponse(pendingExecutive.url)));
    await tick(400);
    expect(hoverReads()).toHaveLength(2);
    expect(hoverReads()[1][1].params).toMatchObject({ campaign_ids: HOVER_CAMPAIGN,
        operating_from_date: "2026-11-10", operating_to_date: "2026-11-10",
        operating_payment_methods: "cash", operating_shipping_companies: "other" });
    expect(hoverReads()[1][1].params.snapshot_revision).toBe(hoverReads()[0][1].params.snapshot_revision);
    expect(document.querySelector('[data-testid="campaign-operating-basis"]').textContent).toContain("2026-11-10");
    expect(campaignCell("صافي الأرباح")).toBe("235.00 ر.س");
});

test("campaign operating requests inherit every executive cohort selector with unchanged rows and totals", async () => {
    mockOrders = campaignOrders();
    api.get.mockImplementation(async (url, config) => url === HOVER_PATH
        ? campaignResult(config.params) : executiveCampaignResponse(url));
    await render();
    await tick(400);
    await selectCampaignFilters("change cohort");
    await tick(400);
    expect(hoverReads()).toHaveLength(2);
    const params = hoverReads()[1][1].params;
    expect(params).toMatchObject({ campaign_ids: HOVER_CAMPAIGN,
        operating_from_date: "2026-11-11", operating_to_date: "2026-11-11",
        operating_payment_methods: "bank,cash", operating_shipping_companies: "courier,other",
        operating_report_statuses: "in_progress,reviewed" });
    const executiveQuery = new URLSearchParams(calls("executive").at(-1)[0].split("?")[1]);
    for (const name of ["from_date", "to_date", "payment_methods", "shipping_companies", "report_statuses"]) {
        expect(params[`operating_${name}`]).toBe(executiveQuery.get(name));
    }
    expect(params.snapshot_revision).toBe(hoverReads()[0][1].params.snapshot_revision);
});

test("an older campaign response cannot replace the report for a newer visible executive period", async () => {
    mockOrders = campaignOrders();
    const pending = [];
    api.get.mockImplementation((url, config) => url === HOVER_PATH
        ? new Promise((resolve) => pending.push({ params: config.params, resolve }))
        : Promise.resolve(executiveCampaignResponse(url, url.includes("2026-11-10") ? 40 : 20)));
    await render();
    await tick(400);
    await selectCampaignFilters("change period");
    await tick(400);
    expect(pending).toHaveLength(2);
    await act(async () => pending[1].resolve(campaignResult(pending[1].params, { amount: 20, ratio: 0.04, net: 225 })));
    await openCampaign();
    expect(campaignCell("المصروفات التشغيلية")).toBe("20.00 ر.س (4%)");
    await act(async () => pending[0].resolve(campaignResult(pending[0].params)));
    expect(campaignCell("المصروفات التشغيلية")).toBe("20.00 ر.س (4%)");
    expect(campaignCell("صافي الأرباح")).toBe("225.00 ر.س");
    expect(document.querySelector('[data-testid="campaign-operating-basis"]').textContent).toContain("2026-11-10");
});
