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
    expect(advancedDashboardSource).toContain("<AdsCard ads={data?.ads_v2}");
    expect(advancedDashboardSource).not.toContain("getDashboardAdsSpend");
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
    expect(advancedDashboardSource).toContain("Keep the last good cart snapshot");
    expect(advancedDashboardSource).toContain(
        "/dashboard-v2/unified-marketing-shadow",
    );
    expect(advancedDashboardSource).toContain("القرارات غير مفعلة");
});
