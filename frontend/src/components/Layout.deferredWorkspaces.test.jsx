import React, { act } from "react";
import { createRoot } from "react-dom/client";

const mockLoaded = [];
let mockLocation = { pathname: "/dashboard-advanced", search: "" };
jest.mock("react-router-dom", () => ({
    useLocation: () => mockLocation,
    NavLink: ({ children }) => <span>{children}</span>,
}));
jest.mock("./Sidebar", () => () => null);
jest.mock("./GlobalSearch", () => () => null);
jest.mock("./ui/sonner", () => ({ Toaster: () => null }));
jest.mock("./MezanLogo", () => ({ LogoIcon: () => null }));
jest.mock("./NotificationBell", () => () => null);
jest.mock("./GlobalIntegrationAlert", () => () => null);
jest.mock("./QoyodUnsentHeaderAlert", () => () => null);
jest.mock("./OrderUiEnhancements", () => () => null);
jest.mock("./DashboardAnalyticsPlacement", () => () => null);
jest.mock("./DashboardSnapchatAccountsPlacement", () => () => null);
jest.mock("./GoogleAdsAllPlatformsCard", () => () => null);
jest.mock("./MezanV2NavigationShell", () => ({
    __esModule: true,
    default: () => <nav data-testid="navigation">Navigation</nav>,
    isMezanV2Route: () => true,
}));
jest.mock("../services/marketingPerformance", () => ({
    isMarketingPerformanceProvider: value => ["google", "meta", "snapchat", "tiktok"].includes(value),
}));
jest.mock("../pages/WarehouseHierarchyWorkspace", () => {
    mockLoaded.push("warehouse"); return () => <div>warehouse</div>;
});
jest.mock("../pages/ProductIntakeWorkspace", () => {
    mockLoaded.push("intake"); return () => <div>intake</div>;
});
jest.mock("../pages/StoreOperationsAccessWorkspace", () => {
    mockLoaded.push("access"); return () => <div>access</div>;
});
jest.mock("../pages/StoreDrivers", () => {
    mockLoaded.push("drivers"); return () => <div>drivers</div>;
});
jest.mock("../pages/StoreDriverHandover", () => {
    mockLoaded.push("handover"); return () => <div>handover</div>;
});
jest.mock("../pages/StoreDeliveryCustomerService", () => {
    mockLoaded.push("customer-service"); return () => <div>customer-service</div>;
});
jest.mock("../pages/StoreDeliveryPaymentReview", () => {
    mockLoaded.push("payment-review"); return () => <div>payment-review</div>;
});
jest.mock("../pages/StoreDeliverySettlements", () => {
    mockLoaded.push("settlements"); return () => <div>settlements</div>;
});
jest.mock("../pages/MarketingPlatformWorkspace", () => {
    mockLoaded.push("marketing"); return ({ provider }) => <div>marketing {provider}</div>;
});

test("each workspace loads only when selected and preserves the navigation", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const Layout = require("./Layout").default;
    expect(mockLoaded).toEqual([]);
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
        await act(async () => root.render(<Layout><div>Dashboard</div></Layout>));
        expect(mockLoaded).toEqual([]);
        const navigation = host.querySelector("nav");
        const expected = [];
        const destinations = [
            ["/components-v2", "?workspace=warehouse", "warehouse"],
            ["/products-v2", "?workspace=intake", "intake"],
            ["/products-v2", "?workspace=access", "access"],
            ["/employees-v2", "?workspace=drivers", "drivers"],
            ["/fulfillment-v2", "?workspace=store-driver-handover", "handover"],
            ["/customer-intelligence", "?workspace=store-delivery", "customer-service"],
            ["/bank-transfer-review", "?workspace=store-delivery", "payment-review"],
            ["/settlements-overview", "?workspace=store-delivery", "settlements"],
            ["/ads-manager", "?provider=google", "marketing"],
        ];
        for (const [pathname, search, label] of destinations) {
            mockLocation = { pathname, search };
            await act(async () => root.render(<Layout><div>Dashboard</div></Layout>));
            expected.push(label);
            expect(mockLoaded).toEqual(expected);
            expect(host.textContent).toContain(label);
            expect(host.querySelector("nav")).toBe(navigation);
        }
    } finally {
        await act(async () => root.unmount());
        delete globalThis.IS_REACT_ACT_ENVIRONMENT;
    }
});
