import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { CampaignAdvisorCard } from "./AdvancedDashboard";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ children }) => <span>{children}</span> }));

let host, root;
const idle = { job: { status: "idle", active: false }, result: null };
const tick = async (ms) => act(async () => { jest.advanceTimersByTime(ms); });
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.resetAllMocks();
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/latest")
        ? { summary: "آخر تحليل محفوظ", available: true, recommendations: [] } : idle }));
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount()); host.remove(); jest.useRealTimers();
});

test("saved advice renders independently of comparison and no timer starts AI or comparison", async () => {
    const implementation = api.get.getMockImplementation();
    api.get.mockImplementation((url, ...args) => url.includes("unified-shadow")
        ? new Promise(() => {}) : implementation(url, ...args));
    await act(async () => root.render(<CampaignAdvisorCard />));
    expect(host.textContent).toContain("آخر تحليل محفوظ");
    expect(host.textContent).toContain("تحديث المراقبة");
    expect(host.textContent).toContain("تحديث المقارنة");
    const reads = api.get.mock.calls.length;
    await tick(10 * 60 * 1000);
    expect(api.get).toHaveBeenCalledTimes(reads);
    expect(api.post).not.toHaveBeenCalled();
});

test("manual monitoring is a separate explicit request and completion reloads saved recommendations", async () => {
    await act(async () => root.render(<CampaignAdvisorCard />));
    const started = new Date().toISOString();
    api.post.mockResolvedValue({ data: { ...idle, job: {
        kind: "monitor", status: "queued", active: true, requested_at: started } } });
    await act(async () => host.querySelector('[data-testid="campaign-monitor-job"] button').click());
    expect(api.post).toHaveBeenCalledWith("/ads-manager/ai-monitor/manual/refresh?days=3", null,
        expect.objectContaining({ signal: expect.anything() }));
    expect(host.querySelector('[data-testid="campaign-shadow-job"] button').disabled).toBe(true);
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/latest")
        ? { summary: "التحليل اليدوي الجديد", recommendations: [], available: true }
        : { job: { kind: "monitor", status: "complete", active: false, requested_at: started,
            finished_at: new Date(Date.now() + 1000).toISOString() }, result: { snapshot_id: "new" } } }));
    await tick(5000);
    expect(host.textContent).toContain("التحليل اليدوي الجديد");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="campaign-shadow-job"] button').disabled).toBe(false);
});
