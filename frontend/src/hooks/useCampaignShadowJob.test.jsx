import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useCampaignShadowJob from "./useCampaignShadowJob";

let host, root, state;
const api = { get: jest.fn(), post: jest.fn() };
const idle = { job: { status: "idle", active: false }, result: { saved: "old" } };
const running = { ...idle, job: { status: "running", active: true, kind: "comparison" } };
const completed = { job: { status: "complete", active: false }, result: { saved: "new" } };
const tick = async (ms) => act(async () => { jest.advanceTimersByTime(ms); });
function Harness() {
    state = useCampaignShadowJob(api, "/comparison");
    return <button onClick={state.refresh}>refresh</button>;
}
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.resetAllMocks();
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
    api.get.mockResolvedValue({ data: idle });
});
afterEach(async () => {
    if (root) await act(async () => root.unmount());
    host.remove(); jest.useRealTimers();
});

test("opening and waiting only reads the cache once; work requires an explicit click", async () => {
    await act(async () => root.render(<Harness />));
    await tick(10 * 60 * 1000);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(state.snapshot.result.saved).toBe("old");
});

test("explicit work posts once and polls status until completion without losing the cache", async () => {
    api.post.mockResolvedValue({ data: running });
    await act(async () => root.render(<Harness />));
    await act(async () => host.querySelector("button").click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(state.snapshot.result.saved).toBe("old");
    api.get.mockResolvedValueOnce({ data: running }).mockResolvedValueOnce({ data: completed });
    await tick(5000); await tick(5000);
    expect(state.snapshot.result.saved).toBe("new");
    const reads = api.get.mock.calls.length;
    await tick(10 * 60 * 1000);
    expect(api.get).toHaveBeenCalledTimes(reads);
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("pending reads cannot overlap, and unmount aborts the read", async () => {
    let resolve;
    api.get.mockImplementation(() => new Promise(done => { resolve = done; }));
    await act(async () => root.render(<Harness />));
    await act(async () => host.querySelector("button").click());
    await tick(60000);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    const signal = api.get.mock.calls[0][1].signal;
    await act(async () => root.unmount()); root = null;
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: running }));
    await tick(60000);
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("a lost POST response retains cached evidence and inspects status without resubmitting", async () => {
    api.post.mockRejectedValue(new Error("response lost"));
    await act(async () => root.render(<Harness />));
    await act(async () => host.querySelector("button").click());
    expect(state.snapshot.result.saved).toBe("old");
    expect(state.error).toContain("تعذر تأكيد");
    api.get.mockResolvedValue({ data: completed });
    await tick(5000); await tick(60000);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(state.snapshot.result.saved).toBe("new");
});

test("active polling stops after five minutes and manual resume only reads existing work", async () => {
    api.get.mockResolvedValue({ data: running });
    await act(async () => root.render(<Harness />));
    for (let i = 0; i < 60; i++) await tick(5000);
    expect(state.pollingPaused).toBe(true);
    const reads = api.get.mock.calls.length;
    await tick(60000);
    expect(api.get).toHaveBeenCalledTimes(reads);
    await act(async () => host.querySelector("button").click());
    expect(api.get).toHaveBeenCalledTimes(reads + 1);
    expect(api.post).not.toHaveBeenCalled();
    expect(state.pollingPaused).toBe(false);
});
