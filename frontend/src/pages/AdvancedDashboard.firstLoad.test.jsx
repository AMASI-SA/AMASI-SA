import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdvancedDashboard, { loadDashboardPeriodSnapshot } from "./AdvancedDashboard";
import api from "../lib/api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";

jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("../services/dashboardAdsSpend", () => ({ getDashboardAdsSpend: jest.fn() }));
jest.mock("../hooks/useOrders", () => ({ useOrders: () => ({ orders: [], hasMore: false }) }));
jest.mock("../components/DashboardAdsSpendCard", () => () => null);
jest.mock("../components/LatestSoldProductsCard", () => () => null);
jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => require("react").createElement("a", { ...props, href: to }, children),
}));
jest.mock("../components/AdvancedFilters", () => ({
    __esModule: true,
    default: () => null,
    defaultFilters: () => ({ from: "2026-11-06", to: "2026-11-06" }),
    filtersToQueryString: (filters) => new URLSearchParams({
        from_date: filters.from, to_date: filters.to,
    }).toString(),
}));

const range = { from: "2026-11-06", to: "2026-11-06" };
const initialSnapshot = () => ({
    totals: { total_orders: 7, total_sales: 1234.5, total_ads_cost: null,
        net_profit: null, net_sales: null, ads_spend_amount_available: false },
    month_kpis: { total_orders: 7, total_sales: 1234.5 },
    product_cost_v2: {
        product_rows: [{ identity: "visible-product", units_sold: 3, total_sales: 1234.5,
            total_cost: 123, net_profit: null, cost_status: "complete" }],
        product_profit_summary: { product_count: 1, total_cost: 123 },
    },
    ads_v2: { total: null, spend_quality: { status: "incomplete", amount_available: false } },
});
const completeSpend = () => ({
    date_from: range.from, date_to: range.to, total_sar: 100,
    provider_totals_sar: { snapchat: 100, meta: 0, tiktok: 0, google: 0 },
    spend_quality: { status: "complete", amount_available: true, amount_complete: true },
});
const canonicalSnapshot = () => ({
    ...initialSnapshot(),
    totals: { total_orders: 7, total_sales: 1234.5, total_ads_cost: 100,
        net_profit: 567.89, net_sales: 678.91, ads_spend_amount_available: true },
    ads_v2: { total: 100, spend_quality: completeSpend().spend_quality },
});

let host, root, pending;
function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    pending.push(() => resolve(null));
    return { promise, resolve };
}
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.clearAllMocks();
    pending = [];
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    api.get.mockImplementation((url) => Promise.resolve({
        data: url.startsWith("/dashboard-v2?") ? initialSnapshot() : {},
    }));
    getDashboardAdsSpend.mockResolvedValue(null);
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => {
        root.unmount();
        pending.forEach((resolve) => resolve());
        await flush();
    });
    host.remove();
    jest.clearAllTimers();
    jest.useRealTimers();
});

test("summary figures and top products appear before an unrelated spend read finishes", async () => {
    const spend = deferred();
    getDashboardAdsSpend.mockReturnValue(spend.promise);
    await act(async () => root.render(<AdvancedDashboard />));

    expect(host.querySelector('[data-testid="advanced-period-summary"]').textContent).toContain("1,234.50");
    expect(host.querySelector('[data-testid="advanced-top-product-row-visible-product"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="تحديث بيانات الفترة"] .animate-spin')).toBeNull();
    expect(api.get.mock.calls.filter(([url]) => url.startsWith("/dashboard-v2?"))).toHaveLength(1);
    await act(async () => spend.resolve(null));
});

test("core data is usable while profit waits for the same-period backend recomputation", async () => {
    const canonical = deferred();
    const first = initialSnapshot();
    const next = canonicalSnapshot();
    const apiClient = { get: jest.fn((url) => url.includes("spend-recompute")
        ? canonical.promise : Promise.resolve({ config: { url }, data: first })) };
    const setData = jest.fn(), setLoading = jest.fn();
    const request = loadDashboardPeriodSnapshot({ next: range, requestSequence: 1,
        isLatest: () => true, apiClient, platformSpendLoader: async () => completeSpend(),
        setData, setLoading, setLoadError: jest.fn(), now: () => 1 });
    await flush();

    expect(setData).toHaveBeenCalledWith(first);
    expect(setLoading).toHaveBeenLastCalledWith(false);
    expect(setData.mock.calls[0][0].totals.net_profit).toBeNull();
    expect(apiClient.get).toHaveBeenCalledTimes(2);

    canonical.resolve({ data: next });
    await request;
    expect(setData.mock.calls.at(-1)[0].totals.net_profit).toBe(567.89);
    expect(setData.mock.calls.at(-1)[0].totals.net_sales).toBe(678.91);
});

test("a failed follow-up read keeps the already delivered core snapshot visible", async () => {
    const first = initialSnapshot(), setData = jest.fn(), setLoadError = jest.fn();
    const apiClient = { get: jest.fn((url) => url.includes("spend-recompute")
        ? Promise.reject(new Error("temporary follow-up failure"))
        : Promise.resolve({ config: { url }, data: first })) };
    await loadDashboardPeriodSnapshot({ next: range, requestSequence: 1, isLatest: () => true,
        apiClient, platformSpendLoader: async () => completeSpend(),
        setData, setLoading: jest.fn(), setLoadError, now: () => 1 });
    expect(setData).toHaveBeenCalledWith(first);
    expect(setLoadError.mock.calls.every(([message]) => message === null)).toBe(true);
});

test("unmounting prevents a late spend read from starting another summary request", async () => {
    const spend = deferred();
    getDashboardAdsSpend.mockReturnValue(spend.promise);
    await act(async () => root.render(<AdvancedDashboard />));
    await act(async () => root.unmount());
    await act(async () => { spend.resolve(completeSpend()); await flush(); });
    expect(api.get.mock.calls.filter(([url]) => url.startsWith("/dashboard-v2?"))).toHaveLength(1);
});
