import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdvancedDashboard from "./AdvancedDashboard";
import api from "../lib/api";
import * as refreshConfig from "../lib/dashboardLiveRefresh";

jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("../lib/dashboardLiveRefresh", () => ({
    __esModule: true,
    ...jest.requireActual("../lib/dashboardLiveRefresh"),
    ABANDONED_CARTS_DASHBOARD_AUTO_SYNC: false,
}));
jest.mock("react-router-dom", () => ({ Link: ({ children }) => <span>{children}</span> }));
jest.mock("../hooks/useOrders", () => ({ useOrders: () => ({ orders: [], hasMore: false }) }));
jest.mock("../services/dashboardAdsSpend", () => ({ getDashboardAdsSpend: async () => null }));
jest.mock("../components/DashboardAdsSpendCard", () => () => null);
jest.mock("../components/LatestSoldProductsCard", () => () => null);
jest.mock("../components/AdvancedFilters", () => ({
    __esModule: true,
    default: ({ onChange }) => <button onClick={() => onChange({ from: "2026-10-02", to: "2026-10-02" })}>change period</button>,
    defaultFilters: () => ({ from: "2026-10-01", to: "2026-10-01" }),
    filtersToQueryString: () => "from_date=2026-10-01",
}));

let host, root;
const cartCalls = () => api.get.mock.calls.filter(([url]) => url.startsWith("/dashboard-v2/abandoned-carts/recent"));
const tick = async (ms) => act(async () => { jest.advanceTimersByTime(ms); });
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: {} });
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); jest.useRealTimers();
});
test("enabled configuration preserves initial request and four polling requests per minute", async () => {
    refreshConfig.ABANDONED_CARTS_DASHBOARD_AUTO_SYNC = true;
    await act(async () => root.render(<AdvancedDashboard />));
    expect(cartCalls()).toHaveLength(1);
    for (let i = 0; i < 4; i++) await tick(15000);
    expect(cartCalls()).toHaveLength(5);
    for (let i = 0; i < 16; i++) await tick(15000);
    expect(cartCalls()).toHaveLength(21);
});
test("frozen card never loads on mount, period, timers, focus, visibility or connection; summary still loads", async () => {
    refreshConfig.ABANDONED_CARTS_DASHBOARD_AUTO_SYNC = false;
    await act(async () => root.render(<AdvancedDashboard />));
    expect(cartCalls()).toHaveLength(0);
    expect(host.textContent).toContain("تم إيقاف التحديث التلقائي مؤقتًا لتحسين أداء لوحة التحكم.");
    await act(async () => host.querySelector("button").click());
    await act(async () => {
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("online"));
        document.dispatchEvent(new Event("visibilitychange"));
    });
    for (let i = 0; i < 20; i++) await tick(15000);
    expect(cartCalls()).toHaveLength(0);
    expect(api.get.mock.calls.some(([url]) => url.startsWith("/dashboard-v2?"))).toBe(true);
});
test("release configuration defaults to frozen", () => {
    expect(jest.requireActual("../lib/dashboardLiveRefresh").ABANDONED_CARTS_DASHBOARD_AUTO_SYNC).toBe(false);
});
