import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import { AuthProvider } from "../context/AuthContext";
import MarketingPlatformWorkspace from "./MarketingPlatformWorkspace";
import { getMarketingPerformance } from "../services/marketingPerformance";
import planFixture from "../services/__fixtures__/googleCommercialPlanPriorContext.json";

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn() }));
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } } } }));
jest.mock("../services/marketingPerformance", () => ({ ...jest.requireActual("../services/marketingPerformance"), getMarketingPerformance: jest.fn() }));
jest.mock("../components/marketing/AdsPerformanceExplorer", () => () => <div data-testid="generic-sar-explorer" />);
let container, root, actor;
beforeEach(() => {
    actor = { id: "owner", role: "owner" };
    window.history.replaceState({}, "", "/ads-manager?provider=google&tab=campaigns&account=123");
    getMarketingPerformance.mockReset().mockResolvedValue({ totals: {}, daily: [], accounts: [], campaigns: [], insights: [], connection: {} });
    api.get.mockReset().mockImplementation((url) => {
        if (url === "/auth/me") return Promise.resolve({ data: { ...actor } });
        if (url === "/integrations-v2/google_ads/commercial-plans/seasonal-focus") return Promise.resolve({ data: JSON.parse(JSON.stringify(planFixture.initial_plan)) });
        return Promise.resolve({ data: url.endsWith("/readiness") ? { accounts: [{ account_id: "123", account_name: "أماسي", currency: "SAR" }], permissions: { owner: actor.role === "owner", can_execute: actor.role === "owner" }, campaign_types: [{ type: "SEARCH", create: true, manage: true }] } : { accounts: [{ account_id: "123", account_name: "أماسي", currency: "SAR" }], items: [] } });
    });
    api.post.mockReset().mockResolvedValue({ data: { summary: "نتائج جوجل الأصلية", recommendations: [] } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function renderAs(role) {
    actor = { id: role, role };
    await act(async () => root.render(<AuthProvider><MarketingPlatformWorkspace provider="google" /></AuthProvider>));
    expect(api.get.mock.calls.some(([url]) => url === "/auth/me")).toBe(true);
    expect(api.get.mock.calls.filter(([url]) => url === "/integrations-v2/google_ads/commercial-plans/seasonal-focus")).toHaveLength(role === "owner" ? 1 : 0);
}

test.each(["owner", "viewer"])("the existing Google campaign route renders its native workspace with real %s auth", async (role) => {
    await renderAs(role);
    expect(container.querySelector('[data-testid="google-ads-workspace"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="generic-sar-explorer"]')).toBeNull();
    expect(container.querySelector('[aria-label="حساب إعلانات جوجل"]').value).toBe("123");
    expect(container.textContent).toContain("إنشاء حملة جوجل"); expect(api.post).not.toHaveBeenCalled();
});

test.each(["owner", "viewer"])("the existing Google AI tab invokes native Google analysis with real %s auth", async (role) => {
    await renderAs(role);
    await act(async () => container.querySelector('[data-testid="marketing-platform-tab-ai"]').click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/ai/analyze");
    expect(container.textContent).toContain("نتائج جوجل الأصلية"); expect(container.textContent).not.toContain("ولا ينفذ بعد");
    expect(api.post.mock.calls.every(([url]) => !url.includes("approve-and-execute"))).toBe(true);
});
