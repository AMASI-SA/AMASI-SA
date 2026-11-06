import React, { act } from "react";
import { createRoot } from "react-dom/client";
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
import QoyodUnsentHeaderAlert from "./QoyodUnsentHeaderAlert";
import api from "../lib/api";
import { useOptionalAuth } from "../context/AuthContext";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.clearAllMocks();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    useOptionalAuth.mockReturnValue({ user: { role: "owner" } });
    api.get.mockResolvedValue({ data: { invoice_presence: { complete: false } } });
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount()); container.remove(); jest.useRealTimers();
});
const render = async () => act(async () => root.render(<QoyodUnsentHeaderAlert />));
test("employee pages make zero banner requests", async () => {
    useOptionalAuth.mockReturnValue({ user: { role: "employee" } });
    await render(); await act(async () => jest.advanceTimersByTime(3600000));
    expect(api.get).not.toHaveBeenCalled(); expect(container.textContent).toBe("");
});
test("owner only reads the small summary every five minutes and hidden tabs stop", async () => {
    await render();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/integrations/qoyod/unsent-summary", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    await act(async () => jest.advanceTimersByTime(299999)); expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => jest.advanceTimersByTime(1)); expect(api.get).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    await act(async () => jest.advanceTimersByTime(3600000)); expect(api.get).toHaveBeenCalledTimes(2);
});
test("slow summary requests never overlap and unmount aborts them", async () => {
    api.get.mockImplementation(() => new Promise(() => {}));
    await render(); await act(async () => jest.advanceTimersByTime(3600000));
    expect(api.get).toHaveBeenCalledTimes(1);
    const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.unmount()); expect(signal.aborted).toBe(true);
    root = createRoot(container);
});

test("a failed refresh keeps the successful count and timestamp with a stale label", async () => {
    api.get.mockResolvedValueOnce({ data: { invoice_presence: {
        complete: true, count: 8, verified_at: '2026-11-05T12:00:00Z',
        expires_at: '2099-01-01T00:00:00Z', stale: false,
    } } });
    await render();
    expect(container.querySelector('[data-testid="qoyod-unsent-header-alert-count"]').textContent).toBe((8).toLocaleString('ar-SA'));
    expect(container.textContent).toContain('آخر تحديث ناجح');
    api.get.mockRejectedValueOnce(new Error('offline'));
    await act(async () => jest.advanceTimersByTime(300000));
    expect(container.querySelector('[data-testid="qoyod-unsent-header-alert-count"]').textContent).toBe((8).toLocaleString('ar-SA'));
    expect(container.querySelector('[data-testid="qoyod-summary-stale"]')).not.toBeNull();
});

test("manual refresh uses the shared job endpoint and ignores repeated clicks while pending", async () => {
    let finish;
    api.post.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await render();
    const button = container.querySelector('button');
    await act(async () => button.click());
    expect(button.disabled).toBe(true);
    await act(async () => button.click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/integrations/qoyod/reconciliation-refresh');
    await act(async () => finish({ data: { accepted: true } }));
});
