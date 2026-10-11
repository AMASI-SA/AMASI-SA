import React, { act } from "react";
import { createRoot } from "react-dom/client";
import MarketingPlatformWorkspace from "./MarketingPlatformWorkspace";
import { getMarketingPerformance } from "../services/marketingPerformance";

jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn() }));
jest.mock("../services/marketingPerformance", () => ({
    ...jest.requireActual("../services/marketingPerformance"),
    getMarketingPerformance: jest.fn(),
}));
jest.mock("../components/marketing/AdsPerformanceExplorer", () => () => null);

let container, root;
beforeEach(() => {
    window.history.replaceState({}, "", "/ads-manager?provider=tiktok");
    getMarketingPerformance.mockReset().mockResolvedValue({ totals: {}, daily: [], accounts: [], campaigns: [], insights: [], connection: {} });
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("TikTok exposes its content workspace from the real marketing navigation", async () => {
    await act(async () => root.render(<MarketingPlatformWorkspace provider="tiktok" />));
    const contentTab = container.querySelector('[data-testid="marketing-platform-tab-content"]');
    expect(contentTab).not.toBeNull();
    expect(contentTab.textContent).toContain("المنشورات");
});

test("other platforms keep their existing navigation", async () => {
    await act(async () => root.render(<MarketingPlatformWorkspace provider="meta" />));
    expect(container.querySelector('[data-testid="marketing-platform-tab-content"]')).toBeNull();
});
