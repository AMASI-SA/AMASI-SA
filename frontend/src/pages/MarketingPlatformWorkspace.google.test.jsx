import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import MarketingPlatformWorkspace from "./MarketingPlatformWorkspace";
import { getMarketingPerformance } from "../services/marketingPerformance";

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn() }));
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), interceptors: { request: { use: jest.fn() }, response: { use: jest.fn() } } } }));
jest.mock("../services/marketingPerformance", () => ({ ...jest.requireActual("../services/marketingPerformance"), getMarketingPerformance: jest.fn() }));
jest.mock("../components/marketing/AdsPerformanceExplorer", () => () => <div data-testid="generic-sar-explorer" />);
let container, root;
beforeEach(() => {
    window.history.replaceState({}, "", "/ads-manager?provider=google&tab=campaigns&account=123");
    getMarketingPerformance.mockReset().mockResolvedValue({ totals: {}, daily: [], accounts: [], campaigns: [], insights: [], connection: {} });
    api.get.mockReset().mockImplementation((url) => Promise.resolve({ data: url.endsWith("/readiness") ? { accounts: [{ account_id: "123", account_name: "أماسي", currency: "SAR" }], permissions: { owner: false, can_execute: true }, campaign_types: [{ type: "SEARCH", create: true, manage: true }] } : { accounts: [{ account_id: "123", account_name: "أماسي", currency: "SAR" }], items: [] } }));
    api.post.mockReset().mockResolvedValue({ data: { summary: "نتائج جوجل الأصلية", recommendations: [] } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("the existing Google campaign route renders its native editable workspace", async () => {
    await act(async () => root.render(<MarketingPlatformWorkspace provider="google" />));
    expect(container.querySelector('[data-testid="google-ads-workspace"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="generic-sar-explorer"]')).toBeNull();
    expect(container.querySelector('[aria-label="حساب إعلانات جوجل"]').value).toBe("123");
    expect(container.textContent).toContain("إنشاء حملة جوجل"); expect(api.post).not.toHaveBeenCalled();
});

test("the existing Google AI tab invokes native Google analysis rather than the old disabled panel", async () => {
    await act(async () => root.render(<MarketingPlatformWorkspace provider="google" />));
    await act(async () => container.querySelector('[data-testid="marketing-platform-tab-ai"]').click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/ai/analyze");
    expect(container.textContent).toContain("نتائج جوجل الأصلية"); expect(container.textContent).not.toContain("ولا ينفذ بعد");
    expect(api.post.mock.calls.every(([url]) => !url.includes("approve-and-execute"))).toBe(true);
});
