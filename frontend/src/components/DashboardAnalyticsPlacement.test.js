const fs = require("fs");
const path = require("path");

const componentsDir = __dirname;
const layoutSource = fs.readFileSync(
    path.join(componentsDir, "Layout.jsx"),
    "utf8",
);
const placementSource = fs.readFileSync(
    path.join(componentsDir, "DashboardAnalyticsPlacement.jsx"),
    "utf8",
);
const dashboardSource = fs.readFileSync(
    path.join(componentsDir, "../pages/Dashboard.jsx"),
    "utf8",
);


test("Layout delegates GA4 cards to the profit-summary placement", () => {
    expect(layoutSource).toContain(
        'import DashboardAnalyticsPlacement from "./DashboardAnalyticsPlacement"',
    );
    expect(layoutSource).toContain(
        'const isLegacyDashboard = location.pathname === "/legacy-dashboard"',
    );
    expect(layoutSource).toContain(
        'const isMezanV2Dashboard = location.pathname === "/dashboard-v2"',
    );
    expect(layoutSource).toContain(
        "const showsDashboardAnalytics = isLegacyDashboard || isMezanV2Dashboard",
    );
    expect(layoutSource).toContain(
        "<DashboardAnalyticsPlacement active={showsDashboardAnalytics} />",
    );
    expect(layoutSource).not.toContain(
        'import GoogleAnalyticsRealtimeCards from "./GoogleAnalyticsRealtimeCards"',
    );
    expect(layoutSource).not.toContain(
        'import GoogleAnalyticsTrafficSourcesCard from "./GoogleAnalyticsTrafficSourcesCard"',
    );
});


test("GA4 cards are inserted immediately after the executive profit summary", () => {
    expect(placementSource).toContain(
        "'[data-testid=\"profit-summary-card\"]'",
    );
    expect(placementSource).toContain(
        'profitSummary.insertAdjacentElement("afterend", currentHost)',
    );
    expect(placementSource).toContain("<GoogleAnalyticsRealtimeCards />");
    expect(placementSource).toContain("<GoogleAnalyticsTrafficSourcesCard />");
});


test("Mezan V2 hides legacy-only salary and analysis sections", () => {
    expect(dashboardSource).toContain(
        '!isMezanV2 && !hiddenCards.includes("salary_accrual_card")',
    );
    expect(dashboardSource).toContain(
        "{!isMezanV2 && (<>",
    );
    expect(dashboardSource).toContain(
        'data-testid="dashboard-monthly-performance-section"',
    );
    expect(dashboardSource).toContain(
        'data-testid="dashboard-recent-analyses-section"',
    );
});


test("Mezan V2 uses independent full Snapchat account cards", () => {
    expect(dashboardSource).toContain(
        'endpoint="/dashboard-v2/snapchat-accounts-summary"',
    );
    expect(dashboardSource).toContain('variant="separated"');
    expect(dashboardSource).toContain("{!isMezanV2 && snapSummary && (");
});
