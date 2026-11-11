import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useDashboardCardSnapshot from "./useDashboardCardSnapshot";
import {
    DASHBOARD_CARD_REQUEST_TIMEOUT_MS,
    PERIOD_SUMMARY_REFRESH_MS,
    TOP_PRODUCTS_REFRESH_MS,
    dashboardCardQuery,
} from "../services/dashboardCardSnapshot";

jest.mock("../lib/api", () => ({ get: jest.fn() }));

const INITIAL_TIME = Date.parse("2026-11-11T18:00:00Z");
const filters = { from: "2026-11-11", to: "2026-11-11", payment_methods: ["mada"], shipping_companies: ["carrier"] };
let host, root, apiClient, observed;
const payload = (marker, intervalMs = TOP_PRODUCTS_REFRESH_MS, nextAt = Date.now() + intervalMs) => ({
    marker,
    product_cost_v2: { product_rows: [], product_profit_summary: {}, salla_fallback_products_count: 0, missing_all_cost_products_count: 0, missing_products_count: 0, missing_products: [] },
    month_kpis: { total_orders: 0, total_sales: null },
    ads_v2: { executive_breakdown: { providers: {}, total: {} } },
    snapshot: { generated_at: new Date(nextAt - intervalMs).toISOString(), next_refresh_at: new Date(nextAt).toISOString(), refresh_interval_seconds: intervalMs / 1000 },
});
function Probe({ endpoint = "/dashboard-v2/cards/top-products", intervalMs = TOP_PRODUCTS_REFRESH_MS, selectedFilters = filters }) {
    observed = useDashboardCardSnapshot({ endpoint, intervalMs, filters: selectedFilters, apiClient });
    return <div><span>{observed.data?.marker || "empty"}</span><button onClick={observed.refresh}>refresh</button></div>;
}
const render = async (props = {}) => act(async () => { root.render(<Probe {...props} />); });
const tick = async (ms) => act(async () => { jest.advanceTimersByTime(ms); });
const event = async (target, name) => act(async () => { target.dispatchEvent(new Event(name)); });

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.setSystemTime(INITIAL_TIME);
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    apiClient = { get: jest.fn(async () => ({ data: payload("saved") })) };
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    jest.useRealTimers();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

test.each([
    ["/dashboard-v2/cards/top-products", 30 * 60 * 1000],
    ["/dashboard-v2/cards/period-summary", 5 * 60 * 60 * 1000],
])("%s uses its own deadline and never adds automatic cache-busting flags", async (endpoint, intervalMs) => {
    apiClient.get.mockImplementation(async () => ({ data: payload("saved", intervalMs) }));
    await render({ endpoint, intervalMs });
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    const [url, options] = apiClient.get.mock.calls[0];
    expect(url).toBe(`${endpoint}?${dashboardCardQuery(filters)}`);
    expect(options.timeout).toBe(DASHBOARD_CARD_REQUEST_TIMEOUT_MS);
    expect(options.timeout).toBe(60_000);
    expect(options._mezanAuthDeadlineAt).toBe(INITIAL_TIME + 60_000);
    expect(options.signal.aborted).toBe(false);
    await tick(intervalMs - 1);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    for (const [request] of apiClient.get.mock.calls) {
        const query = new URLSearchParams(request.split("?")[1]);
        expect(query.has("_refresh")).toBe(false);
        expect(query.has("refresh")).toBe(false);
    }
});

test("a server-cached snapshot keeps its original expiry instead of restarting the interval", async () => {
    apiClient.get.mockResolvedValue({ data: payload("cached", TOP_PRODUCTS_REFRESH_MS, INITIAL_TIME + 3 * 60 * 1000) });
    await render();
    await tick(3 * 60 * 1000 - 1);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
});

test("focus, visible, online and equivalent filters do not bypass a fresh snapshot", async () => {
    await render();
    await event(window, "focus");
    await event(window, "online");
    await event(document, "visibilitychange");
    await render({ selectedFilters: { ...filters, payment_methods: ["mada", "mada"] } });
    await tick(29 * 60 * 1000);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
});

test("manual refresh works before the deadline and sends only its explicit force flag", async () => {
    await render();
    apiClient.get.mockResolvedValue({ data: payload("manual") });
    await act(async () => host.querySelector("button").click());
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    const [url] = apiClient.get.mock.calls[1];
    expect(new URLSearchParams(url.split("?")[1]).get("refresh")).toBe("true");
    expect(url).not.toContain("_refresh");
    expect(observed.data.marker).toBe("manual");
});

test("automatic and repeated manual refresh join the one pending card read", async () => {
    let resolve;
    apiClient.get.mockImplementation(() => new Promise((done) => { resolve = done; }));
    await render();
    await tick(PERIOD_SUMMARY_REFRESH_MS);
    await act(async () => { host.querySelector("button").click(); host.querySelector("button").click(); });
    await event(window, "focus");
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(observed.loading).toBe(true);
    await act(async () => resolve({ data: payload("completed") }));
    expect(observed.loading).toBe(false);
    expect(observed.data.marker).toBe("completed");
});

test.each(["hidden", "offline"])("due automatic requests pause while %s and resume once", async (condition) => {
    await render();
    if (condition === "hidden") Object.defineProperty(document, "hidden", { configurable: true, value: true });
    else Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    await tick(TOP_PRODUCTS_REFRESH_MS);
    await event(window, "focus");
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    await event(window, "online");
    await event(document, "visibilitychange");
    expect(apiClient.get).toHaveBeenCalledTimes(2);
});

test("an initially offline mount stays readable and loads when connectivity returns", async () => {
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    await render();
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(observed.loading).toBe(false);
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    await event(window, "online");
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(observed.data.marker).toBe("saved");
});

test("same-period failure keeps the saved snapshot and a manual retry remains available", async () => {
    await render();
    apiClient.get.mockRejectedValueOnce(new Error("network"));
    await act(async () => host.querySelector("button").click());
    expect(observed.data.marker).toBe("saved");
    expect(observed.error).toBe("تعذر تحديث بيانات البطاقة");
    expect(observed.loading).toBe(false);
    await event(window, "focus");
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    await act(async () => host.querySelector("button").click());
    expect(apiClient.get).toHaveBeenCalledTimes(3);
    expect(observed.error).toBeNull();
});

test("a synchronously rejected transport releases admission for a manual retry", async () => {
    apiClient.get.mockImplementationOnce(() => { throw new Error("transport configuration"); });
    await render();
    expect(observed.loading).toBe(false);
    expect(observed.error).toBe("تعذر تحديث بيانات البطاقة");
    await act(async () => host.querySelector("button").click());
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(observed.data.marker).toBe("saved");
});

test.each([
    { ...filters, from: "2026-11-10", to: "2026-11-10" },
    { ...filters, payment_methods: ["cod"] },
    { ...filters, shipping_companies: ["another"] },
    { ...filters, report_statuses: ["completed"] },
])("a new filter scope empties prior figures and fences an older pending result: %p", async (selectedFilters) => {
    await render();
    let oldResolve, newResolve;
    apiClient.get.mockImplementationOnce(() => new Promise((resolve) => { oldResolve = resolve; }));
    await act(async () => host.querySelector("button").click());
    const oldSignal = apiClient.get.mock.calls[1][1].signal;
    apiClient.get.mockImplementationOnce(() => new Promise((resolve) => { newResolve = resolve; }));
    await render({ selectedFilters });
    expect(observed.data).toBeNull();
    expect(oldSignal.aborted).toBe(true);
    await act(async () => newResolve({ data: payload("new-scope") }));
    await act(async () => oldResolve({ data: payload("late-old-scope") }));
    expect(observed.data.marker).toBe("new-scope");
    expect(observed.error).toBeNull();
});

test("a failed new range never restores the previous range and invalid payloads stay unknown", async () => {
    await render();
    apiClient.get.mockResolvedValueOnce({ data: null });
    await render({ selectedFilters: { from: "2026-11-09", to: "2026-11-09" } });
    expect(observed.data).toBeNull();
    expect(observed.loading).toBe(false);
    expect(observed.error).toBe("تعذر تحديث بيانات البطاقة");
});

test.each([
    ["/dashboard-v2/cards/top-products", TOP_PRODUCTS_REFRESH_MS],
    ["/dashboard-v2/cards/period-summary", PERIOD_SUMMARY_REFRESH_MS],
])("%s rejects empty or metadata-only successes and keeps the configured error cadence", async (endpoint, intervalMs) => {
    apiClient.get.mockResolvedValue({ data: payload("saved", intervalMs) });
    await render({ endpoint, intervalMs });
    for (const malformed of [{}, { snapshot: payload("only-meta", intervalMs).snapshot }]) {
        apiClient.get.mockResolvedValueOnce({ data: malformed });
        await act(async () => host.querySelector("button").click());
        expect(observed.data.marker).toBe("saved");
        expect(observed.error).toBe("تعذر تحديث بيانات البطاقة");
    }
    const prior = apiClient.get.mock.calls.length;
    await tick(intervalMs - 1);
    await event(window, "focus");
    expect(apiClient.get).toHaveBeenCalledTimes(prior);
    await tick(1);
    expect(apiClient.get).toHaveBeenCalledTimes(prior + 1);
    expect(observed.error).toBeNull();
});

test.each([
    { generated_at: "invalid", next_refresh_at: "2026-11-11T19:00:00Z", refresh_interval_seconds: 1800 },
    { generated_at: "2026-11-11T18:00:00Z", next_refresh_at: "invalid", refresh_interval_seconds: 1800 },
    { generated_at: "2026-11-11T18:00:00Z", next_refresh_at: "2026-11-11T18:30:00Z", refresh_interval_seconds: NaN },
    { generated_at: "2026-11-11T18:00:00Z", next_refresh_at: "2026-11-11T18:00:01Z", refresh_interval_seconds: 1800 },
])("invalid server snapshot metadata never replaces saved facts: %p", async (snapshot) => {
    await render();
    apiClient.get.mockResolvedValueOnce({ data: { ...payload("invalid"), snapshot } });
    await act(async () => host.querySelector("button").click());
    expect(observed.data.marker).toBe("saved");
    expect(observed.error).toBe("تعذر تحديث بيانات البطاقة");
});

test("unmount aborts pending transport and a new authenticated mount has no prior card state", async () => {
    await render();
    let resolve;
    apiClient.get.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await act(async () => host.querySelector("button").click());
    const signal = apiClient.get.mock.calls[1][1].signal;
    await act(async () => root.unmount());
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: payload("old-session") }));
    root = createRoot(host);
    let nextResolve;
    apiClient.get.mockImplementationOnce(() => new Promise((done) => { nextResolve = done; }));
    await render();
    expect(observed.data).toBeNull();
    await act(async () => nextResolve({ data: payload("new-session") }));
    expect(observed.data.marker).toBe("new-session");
});
