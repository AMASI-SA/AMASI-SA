import React, { act } from "react";
import { createRoot } from "react-dom/client";
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
import QoyodUnsentHeaderAlert from "./QoyodUnsentHeaderAlert";
import api from "../lib/api";
import { useOptionalAuth } from "../context/AuthContext";

jest.mock("../lib/api", () => ({ get: jest.fn() }));
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
