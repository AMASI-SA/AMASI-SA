import React, { act } from "react";
import { createRoot } from "react-dom/client";
import MezanProductsAutoSync from "./MezanProductsAutoSync";
import { useOptionalAuth } from "../context/AuthContext";
import { managedWebPages } from "../lib/webPageAccess";
import { getRecentProductsSyncNextAt, syncRecentProductsV2 } from "../services/mezanProductsV2";

jest.mock("react-router-dom", () => ({ useLocation: () => ({ key: "catalogue", search: "" }) }));
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("../lib/webPageAccess", () => ({ managedWebPages: jest.fn() }));
jest.mock("../services/mezanProductsV2", () => ({
    RECENT_PRODUCTS_SYNC_INTERVAL_MS: 3_600_000,
    getRecentProductsSyncNextAt: jest.fn(),
    syncRecentProductsV2: jest.fn(),
}));
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("../components/products/ProductGoogleTaxonomyPilotPanel", () => () => null);
jest.mock("./MezanProductsWorkspace", () => () => <input aria-label="draft cost" defaultValue="" />);

let host, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.clearAllMocks();
    useOptionalAuth.mockReturnValue({ user: { id: "owner-a" } });
    managedWebPages.mockReturnValue([]);
    getRecentProductsSyncNextAt.mockReturnValue(null);
    syncRecentProductsV2.mockResolvedValue({ ok: true, created: 1 });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    jest.clearAllTimers();
    jest.useRealTimers();
});

test("owner catalogue auto refresh is hourly and preserves the open draft", async () => {
    await act(async () => root.render(<MezanProductsAutoSync />));
    const editor = host.querySelector("input");
    editor.value = "unsaved cost";
    await act(async () => jest.advanceTimersByTime(3_599_999));
    expect(syncRecentProductsV2).not.toHaveBeenCalled();
    await act(async () => jest.advanceTimersByTime(1));
    expect(syncRecentProductsV2).toHaveBeenCalledTimes(1);
    expect(syncRecentProductsV2).toHaveBeenLastCalledWith({ ownerId: "owner-a" });
    expect(host.querySelector("input")).toBe(editor);
    expect(editor.value).toBe("unsaved cost");
});

test("visibility uses automatic server admission and never forces provider work", async () => {
    await act(async () => root.render(<MezanProductsAutoSync />));
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
        jest.advanceTimersByTime(3_600_000);
    });
    expect(syncRecentProductsV2).not.toHaveBeenCalled();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(syncRecentProductsV2).toHaveBeenLastCalledWith({ ownerId: "owner-a" });
});

test("offline timers do no work and a changed authenticated owner gets its own scope", async () => {
    await act(async () => root.render(<MezanProductsAutoSync />));
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    await act(async () => jest.advanceTimersByTime(3_600_000));
    expect(syncRecentProductsV2).not.toHaveBeenCalled();
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    useOptionalAuth.mockReturnValue({ user: { id: "owner-b" } });
    await act(async () => root.render(<MezanProductsAutoSync />));
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(syncRecentProductsV2).toHaveBeenLastCalledWith({ ownerId: "owner-b" });
});

test("employee catalogue does not acquire owner automatic synchronization", async () => {
    managedWebPages.mockReturnValue([{ id: "products.catalog", mode: "read" }]);
    await act(async () => root.render(<MezanProductsAutoSync />));
    await act(async () => {
        jest.advanceTimersByTime(7_200_000);
        document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(syncRecentProductsV2).not.toHaveBeenCalled();
});

test("a listing that finishes three seconds after mount is refreshed at its real hourly deadline", async () => {
    const initial = Date.parse("2026-11-11T12:00:00Z");
    jest.setSystemTime(initial);
    const actual = jest.requireActual("../services/mezanProductsV2");
    const api = require("../lib/api").default;
    let lastSuccess = null;
    const providerStarts = [];
    api.get.mockResolvedValue({ data: { items: [] } });
    api.post.mockImplementation(() => {
        if (lastSuccess !== null && Date.now() < lastSuccess + 3_600_000) {
            return Promise.resolve({ data: {
                ok: true, skipped: true, reason: "up_to_date",
                next_retry_at: new Date(lastSuccess + 3_600_000).toISOString(),
            } });
        }
        providerStarts.push(Date.now());
        return new Promise(resolve => setTimeout(() => {
            lastSuccess = Date.now();
            resolve({ data: { ok: true, skipped: false, created: 1,
                next_sync_at: new Date(lastSuccess + 3_600_000).toISOString() } });
        }, 3000));
    });
    syncRecentProductsV2.mockImplementation(actual.syncRecentProductsV2);
    getRecentProductsSyncNextAt.mockImplementation(actual.getRecentProductsSyncNextAt);
    let listing;
    await act(async () => {
        root.render(<MezanProductsAutoSync />);
        listing = actual.listWorkspaceProducts();
    });
    await act(async () => jest.advanceTimersByTime(3000));
    await listing;
    expect(providerStarts).toEqual([initial]);
    await act(async () => jest.advanceTimersByTime(3_597_000));
    expect(providerStarts).toEqual([initial]);
    await act(async () => jest.advanceTimersByTime(2999));
    expect(providerStarts).toEqual([initial]);
    await act(async () => jest.advanceTimersByTime(1));
    expect(providerStarts).toEqual([initial, initial + 3_603_000]);
    await act(async () => jest.advanceTimersByTime(3000));
});
