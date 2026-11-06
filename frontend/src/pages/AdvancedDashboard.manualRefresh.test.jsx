import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdvancedDashboard from "./AdvancedDashboard";
import api from "../lib/api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";
import { listOrders, getOrder, listLatestSoldProductOrders } from "../services/orderEngine";

jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("../services/dashboardAdsSpend", () => ({
    ...jest.requireActual("../services/dashboardAdsSpend"), getDashboardAdsSpend: jest.fn(),
}));
jest.mock("../services/orderEngine", () => ({
    ORDER_PAGE_SIZE: 50, listOrders: jest.fn(), getOrder: jest.fn(),
    listLatestSoldProductOrders: jest.fn(),
}));
jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a {...props} href={to}>{children}</a>,
}));
jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    LineChart: ({ children }) => <div>{children}</div>,
    CartesianGrid: () => null, Legend: () => null, Line: () => null,
    Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));
jest.mock("../components/AdvancedFilters", () => ({
    __esModule: true,
    default: ({ onChange }) => <button onClick={() => onChange({ from: "2026-11-05", to: "2026-11-05" })}>change period</button>,
    defaultFilters: () => ({ from: "2026-11-06", to: "2026-11-06" }),
    filtersToQueryString: filters => new URLSearchParams({ from_date: filters.from, to_date: filters.to }).toString(),
}));

let host, root, currentOrders;
const count = (path) => api.get.mock.calls.filter(([url]) => url.startsWith(path)).length;
const sources = () => [count("/dashboard-v2?"), count("/integrations-v2/google_analytics_4/realtime-dashboard"),
    count("/ads-manager/ai-monitor/latest"), count("/dashboard-v2/abandoned-carts/recent"),
    getDashboardAdsSpend.mock.calls.length, listLatestSoldProductOrders.mock.calls.length];
const tick = async ms => act(async () => { jest.advanceTimersByTime(ms); });
const click = async label => act(async () => {
    const button = host.querySelector(`[aria-label="${label}"]`);
    expect(button).not.toBeNull();
    button.click();
});

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    currentOrders = [{ order_number: "1001", status: "new", total_amount: 100 }];
    listOrders.mockImplementation(async () => ({ items: currentOrders, nextCursor: null }));
    getOrder.mockResolvedValue(null);
    listLatestSoldProductOrders.mockResolvedValue({ items: [], nextCursor: null });
    getDashboardAdsSpend.mockResolvedValue(null);
    api.get.mockImplementation(async url => ({ data: url.startsWith("/dashboard-v2?")
        ? { totals: { total_orders: 1, total_sales: 100, net_profit: null },
            month_kpis: { total_orders: 1, total_sales: 100 }, product_cost_v2: { product_rows: [] } }
        : url.includes("abandoned-carts/recent") ? { items: [], abandoned_count: 0, recovered_count: 0 }
            : {} }));
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove();
    jest.clearAllTimers(); jest.useRealTimers();
});

test("cards load once on open and never reread on elapsed time, focus, reconnection or visibility", async () => {
    await act(async () => root.render(<AdvancedDashboard />));
    const initial = sources();
    expect(initial.slice(0, 4)).toEqual([1, 1, 1, 1]);
    for (let i = 0; i < 36; i += 1) await tick(10_000);
    await act(async () => {
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("online"));
        document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(sources()).toEqual(initial);
});

test("Recent Orders retains its automatic refresh without reloading financial or analytics cards", async () => {
    await act(async () => root.render(<AdvancedDashboard />));
    const initial = sources();
    expect(listOrders).toHaveBeenCalledTimes(1);
    currentOrders = [{ order_number: "1002", status: "new", total_amount: 200 }];
    await tick(10_000);
    expect(listOrders).toHaveBeenCalledTimes(2);
    expect(host.querySelector('[data-testid="advanced-latest-orders"]').textContent).toContain("1002");
    expect(sources()).toEqual(initial);
});

test("each circular card refresh reads its own source; shared financial cards keep one canonical snapshot", async () => {
    await act(async () => root.render(<AdvancedDashboard />));
    for (const label of ["ملخص الفترة", "الملخص التنفيذي للأرباح", "المنتجات الأكثر مبيعًا"]) {
        const before = sources();
        await click(`تحديث بطاقة ${label}`);
        expect(sources()).toEqual(before.map((n, i) => i === 0 || i === 4 ? n + 1 : n));
    }
    for (const [label, sourceIndex] of [
        ["Google Analytics 4 — مباشر", 1], ["المستخدمون النشطون الآن", 1],
        ["المستشار الذكي للإعلانات", 2], ["السلات المتروكة", 3],
    ]) {
        const before = sources();
        await click(`تحديث بطاقة ${label}`);
        expect(sources()).toEqual(before.map((n, i) => i === sourceIndex ? n + 1 : n));
    }
    const before = sources();
    await click("تحديث أحدث المنتجات المباعة");
    expect(sources()).toEqual(before.map((n, i) => i === 5 ? n + 1 : n));
});

test("leaving the page stops page-owned reads, including Recent Orders", async () => {
    await act(async () => root.render(<AdvancedDashboard />));
    await act(async () => root.render(<div>Another page</div>));
    const initial = sources(); const initialOrders = listOrders.mock.calls.length;
    await tick(30 * 60_000);
    await act(async () => {
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("online"));
        document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(sources()).toEqual(initial);
    expect(listOrders).toHaveBeenCalledTimes(initialOrders);
});
