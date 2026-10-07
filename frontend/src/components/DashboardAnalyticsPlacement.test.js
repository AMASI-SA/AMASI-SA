import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { AbandonedCartsCard, useDashboardCarts } from "../pages/AdvancedDashboard";
jest.mock("react-router-dom", () => ({ Link: ({ children }) => <>{children}</> }));

const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

const dashboardCompatibilitySource = read("pages/Dashboard.jsx");
const advancedDashboardSource = read("pages/AdvancedDashboard.jsx");
const frontendIndexSource = read("index.js");
const analyticsPlacementSource = read("components/DashboardAnalyticsPlacement.jsx");
const snapchatPlacementSource = read("components/DashboardSnapchatAccountsPlacement.jsx");


test("all retired dashboard compatibility routes redirect to the advanced dashboard", () => {
    expect(dashboardCompatibilitySource).toContain(
        '<Navigate to="/dashboard-advanced" replace />',
    );
    expect(dashboardCompatibilitySource).not.toContain("AdvancedDashboard");
    expect(dashboardCompatibilitySource).not.toMatch(/api\.(get|post|put|patch|delete)\(/);
    expect(dashboardCompatibilitySource).not.toContain("setInterval(");
    expect(dashboardCompatibilitySource).not.toContain("setTimeout(");
    expect(dashboardCompatibilitySource).not.toContain("useEffect(");
    expect(dashboardCompatibilitySource).not.toContain("useState(");
});


test("legacy dashboard placement components mount no observers, portals, or polling", () => {
    for (const source of [analyticsPlacementSource, snapchatPlacementSource]) {
        expect(source).toContain("return null;");
        expect(source).not.toContain("MutationObserver");
        expect(source).not.toContain("createPortal");
        expect(source).not.toContain("requestAnimationFrame");
        expect(source).not.toContain("setInterval(");
        expect(source).not.toMatch(/api\.(get|post|put|patch|delete)\(/);
    }
});


test("the advanced dashboard owns GA4, ads, payment fees, and profit details", () => {
    expect(advancedDashboardSource).toContain("<AdsExecutiveBreakdownTable");
    expect(advancedDashboardSource).toContain("buildPaymentFeeRows(rows)");
    expect(advancedDashboardSource).toContain('testid="advanced-profit-payment-details"');
    expect(advancedDashboardSource).toContain('data-testid="advanced-ga-active-chart"');
    expect(advancedDashboardSource).toContain("<GaLive data={ga} />");
    expect(advancedDashboardSource).toContain("<DashboardAdsSpendCard");
    expect(advancedDashboardSource).toContain("getDashboardAdsSpend");
    expect(advancedDashboardSource).toContain("mergeDashboardWithPlatformSpend");
    expect(advancedDashboardSource).not.toContain("chartData");
    expect(frontendIndexSource).not.toContain("dashboardExecutivePlatformSpendInterceptor");
});


test("the advanced dashboard retains governed date refresh and latest-snapshot behavior", () => {
    expect(advancedDashboardSource).toContain(
        'const response = await apiClient.get(`/dashboard-v2?${query.toString()}`',
    );
    expect(advancedDashboardSource).toContain("isLatest(requestSequence)");
    expect(advancedDashboardSource).not.toContain("setData(null)");
    expect(advancedDashboardSource).toContain("requestSequenceRef");
    expect(advancedDashboardSource).toContain("backgroundRefreshInFlightRef");
    expect(advancedDashboardSource).toContain("DASHBOARD_AUTO_REFRESH_MS");
    expect(advancedDashboardSource).toContain(
        "/dashboard-v2/unified-marketing-shadow",
    );
    expect(advancedDashboardSource).toContain("القرارات غير مفعلة");
    expect(advancedDashboardSource).toContain("التغطية غير مكتملة");
    expect(advancedDashboardSource).toContain("مصالح مباشرة مع Snapchat");
});

test("cart refresh retains the last successful snapshot and timestamp while pending and after failure", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const user = { id: "placement-user", tenant_id: "placement-tenant" };
    let rejectRefresh;
    const client = { get: jest.fn()
        .mockResolvedValueOnce({ data: { items: [{ cart_id: "saved-cart", customer_name: "Saved snapshot", items: [], total: 10 }], pagination: {} } })
        .mockImplementationOnce(() => new Promise((resolve, reject) => { rejectRefresh = reject; })) };
    function Harness() {
        const state = useDashboardCarts("2026-10-05", "2026-10-05", client, { user });
        return <AbandonedCartsCard {...state} onRefresh={state.refresh} />;
    }
    try {
        await act(async () => root.render(<Harness />));
        expect(client.get).not.toHaveBeenCalled();
        const button = () => host.querySelector('[data-testid="refresh-carts"]');
        await act(async () => button().click());
        const timestamp = host.querySelector("time").dateTime;
        await act(async () => button().click());
        expect(button().disabled).toBe(true);
        expect(host.textContent).toContain("Saved snapshot");
        expect(host.querySelector("time").dateTime).toBe(timestamp);
        await act(async () => rejectRefresh(new Error("offline")));
        expect(host.textContent).toContain("Saved snapshot");
        expect(host.querySelector("time").dateTime).toBe(timestamp);
        expect(host.querySelector('[role="alert"]')).not.toBeNull();
        expect(button().disabled).toBe(false);
        expect(client.get).toHaveBeenCalledTimes(2);
    } finally {
        await act(async () => root.unmount());
        host.remove();
        delete global.IS_REACT_ACT_ENVIRONMENT;
    }
});
