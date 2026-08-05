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
const filtersSource = fs.readFileSync(
    path.join(componentsDir, "AdvancedFilters.jsx"),
    "utf8",
);
const dashboardSource = fs.readFileSync(
    path.join(componentsDir, "../pages/Dashboard.jsx"),
    "utf8",
);


test("Layout delegates dashboard reports to the profit-summary placement", () => {
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


test("GA4, one live profit summary, and ads spend share one RTL report grid", () => {
    expect(placementSource).toContain(
        "'[data-testid=\"profit-summary-card\"]'",
    );
    expect(placementSource).toContain(
        'currentGrid.className = "mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3 xl:items-stretch"',
    );
    expect(placementSource).toContain(
        "currentGrid.append(currentGaHost, currentProfitHost, currentAdsHost)",
    );
    expect(placementSource).toContain(
        "currentProfitHost.replaceChildren(currentProfit)",
    );
    expect(placementSource).toContain(
        "const candidate = newestLiveProfitCandidate(document, currentProfitHost)",
    );
    expect(placementSource).toContain(
        "const outsideHost = candidates.filter((node) => node.parentElement !== profitHost)",
    );
    expect(placementSource).not.toContain(
        "const candidate = document.querySelector(PROFIT_SUMMARY_SELECTOR)",
    );
    expect(placementSource).not.toContain(
        "currentProfitHost.appendChild(candidate)",
    );
    expect(placementSource).toContain("<GoogleAnalyticsRealtimeCards />");
    expect(placementSource).toContain("<DashboardAdsSpendCard");
    expect(placementSource).toContain("fromDate={dateRange.fromDate}");
    expect(placementSource).toContain("toDate={dateRange.toDate}");
    expect(placementSource).toContain("<GoogleAnalyticsTrafficSourcesCard />");

    // Dashboard owns exactly one React ProfitSummaryCard. The placement may
    // relocate the newest live DOM node, but it must never render a second JSX
    // summary or leave the older moved node visible after a date refresh.
    expect((dashboardSource.match(/<ProfitSummaryCard\b/g) || [])).toHaveLength(1);
    expect(placementSource).toContain(
        "currentProfitHost.removeChild(currentProfit)",
    );
});


test("dashboard date filters expose one date source for profit and ads reports", () => {
    expect(filtersSource).toContain('data-testid="advanced-filters"');
    expect(filtersSource).toContain('data-date-preset={value.preset || ""}');
    expect(filtersSource).toContain('data-from-date={value.from || ""}');
    expect(filtersSource).toContain('data-to-date={value.to || value.from || ""}');
    expect(placementSource).toContain(
        'const FILTER_SELECTOR = \'[data-testid="advanced-filters"]\'',
    );
    expect(placementSource).toContain(
        'filters?.getAttribute("data-from-date")',
    );
    expect(placementSource).toContain(
        'filters?.getAttribute("data-to-date")',
    );
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
