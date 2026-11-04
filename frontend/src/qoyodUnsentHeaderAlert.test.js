import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useLocation } from "react-router-dom";
import { useAuth } from "./context/AuthContext";
import api from "./lib/api";
import QoyodUnsentHeaderAlert from "./components/QoyodUnsentHeaderAlert";

jest.mock("react-router-dom", () => ({
    useLocation: jest.fn(),
    Link: ({ to, children }) => <a href={to}>{children}</a>,
}));
jest.mock("./context/AuthContext", () => ({ useAuth: jest.fn() }));
jest.mock("./lib/api", () => ({ get: jest.fn(), post: jest.fn() }));

const countPath = "/integrations/qoyod/unsent-orders/header-count";
const financial = "/integrations-v2/qoyod";
const owner = { id: "owner-a", is_owner: true };
const asOf = "2026-11-04T03:00:00.000Z";
let root, container, hidden;

function pending() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

async function show(path, user = owner) {
    useAuth.mockReturnValue({ user });
    useLocation.mockReturnValue({ pathname: path });
    await act(async () => root.render(<QoyodUnsentHeaderAlert />));
}

beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers().setSystemTime(new Date(asOf));
    jest.resetAllMocks();
    hidden = jest.spyOn(document, "hidden", "get").mockReturnValue(false);
    api.get.mockResolvedValue({ data: { count: null, as_of: null } });
    container = document.createElement("div");
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    hidden.mockRestore();
    jest.useRealTimers();
});

test("operational routes and non-owner sessions make no badge requests", async () => {
    for (const path of ["/orders-v2", "/fulfillment-v2", "/inventory-receiving-v2", "/operations-dashboard"]) {
        await show(path);
    }
    for (const user of [null, { id: "employee", is_owner: false }, { is_owner: true }]) {
        await show(financial, user);
        await show("/dashboard-advanced", user);
    }
    await act(async () => jest.advanceTimersByTime(180_000));
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(container.textContent).toBe("");
});

test.each(["/legacy-dashboard", "/dashboard-v2", "/dashboard-advanced"])(
    "%s shows a link on a cold snapshot and polls only the small GET when visible", async path => {
        hidden.mockReturnValue(true);
        await show(path);
        await act(async () => jest.advanceTimersByTime(120_000));
        expect(api.get).not.toHaveBeenCalled();
        hidden.mockReturnValue(false);
        await act(async () => document.dispatchEvent(new Event("visibilitychange")));
        expect(container.textContent).toContain("غير متوفر");
        expect(container.querySelector("a").getAttribute("href")).toBe(financial);
        expect(container.querySelector("button")).toBeNull();
        await act(async () => jest.advanceTimersByTime(60_000));
        expect(api.get.mock.calls.map(call => call[0])).toEqual([countPath, countPath]);
        expect(api.post).not.toHaveBeenCalled();
    },
);

test("an explicit refresh supersedes an old GET and displays a timestamped zero", async () => {
    const old = pending(), fresh = pending();
    api.get.mockReturnValueOnce(old.promise);
    api.post.mockReturnValueOnce(fresh.promise);
    await show(financial);
    const button = container.querySelector("button");
    await act(async () => button.click());
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
    expect(api.post.mock.calls[0][0]).toBe(countPath + "/refresh");
    expect(button.disabled).toBe(true);
    await act(async () => jest.advanceTimersByTime(60_000));
    expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => fresh.resolve({ data: { count: 0, as_of: asOf } }));
    await act(async () => old.resolve({ data: { count: 99, as_of: asOf } }));
    expect(container.querySelector("strong").textContent).toBe((0).toLocaleString("ar-SA"));
    expect(container.querySelector("time").dateTime).toBe(asOf);
    expect(container.textContent).toContain("06:00"); // Riyadh
    expect(button.disabled).toBe(false);
});

test("old snapshots stay visible on partial reports and failed refreshes", async () => {
    const saved = { count: 4, as_of: "2026-11-03T03:00:00Z" };
    api.get.mockResolvedValue({ data: saved });
    api.post.mockResolvedValueOnce({ data: { count: null, as_of: null, partial: true } })
        .mockRejectedValueOnce(new Error("unavailable"));
    await show(financial);
    expect(container.textContent).toContain("قديم");
    const button = container.querySelector("button");
    await act(async () => button.click());
    expect(container.textContent).toContain("لم يكتمل");
    await act(async () => button.click());
    expect(container.textContent).toContain("تعذّر تحديث العدد");
    expect(container.querySelector("strong").textContent).toBe((4).toLocaleString("ar-SA"));
    expect(container.querySelector("time").dateTime).toBe(saved.as_of);
});

test("switching accounts aborts work, drops previous counts and ignores late replies", async () => {
    const late = pending();
    api.get.mockResolvedValueOnce({ data: { count: 8, as_of: asOf } });
    api.post.mockReturnValueOnce(late.promise);
    await show(financial);
    await act(async () => container.querySelector("button").click());
    await show(financial, { id: "owner-b", is_owner: true });
    expect(api.post.mock.calls[0][2].signal.aborted).toBe(true);
    await act(async () => late.resolve({ data: { count: 74, as_of: asOf } }));
    expect(container.textContent).toContain("غير متوفر");
    expect(container.querySelector("strong")).toBeNull();
    await show("/fulfillment-v2");
    expect(container.textContent).toBe("");
    await act(async () => jest.advanceTimersByTime(60_000));
    expect(api.get).toHaveBeenCalledTimes(2);
});

test("a later poll cannot rewind a saved result, and an unchanged result ages", async () => {
    api.get.mockResolvedValueOnce({ data: { count: 3, as_of: asOf } })
        .mockResolvedValue({ data: { count: 27, as_of: "2026-10-01T00:00:00Z" } });
    await show(financial);
    await act(async () => jest.advanceTimersByTime(3600_000));
    expect(container.querySelector("strong").textContent).toBe((3).toLocaleString("ar-SA"));
    expect(container.textContent).toContain("قديم");
});

test("a hidden tab aborts its pending passive read and does not start another until visible", async () => {
    const silent = pending();
    api.get.mockReturnValueOnce(silent.promise);
    await show(financial);
    hidden.mockReturnValue(true);
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(api.get.mock.calls[0][1].signal.aborted).toBe(true);
    await act(async () => jest.advanceTimersByTime(120_000));
    expect(api.get).toHaveBeenCalledTimes(1);
    hidden.mockReturnValue(false);
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => silent.resolve({ data: { count: 5, as_of: asOf } }));
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(container.querySelector("strong")).toBeNull();
});
