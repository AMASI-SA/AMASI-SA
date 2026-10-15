const fs = require("fs");
const path = require("path");

const placementSource = fs.readFileSync(
    path.join(__dirname, "DashboardAnalyticsPlacement.jsx"),
    "utf8",
);
const dashboardSource = fs.readFileSync(
    path.join(__dirname, "../pages/Dashboard.jsx"),
    "utf8",
);


test("obsolete monthly, analysis-history, and salary cards are absent with the retired dashboard runtime", () => {
    expect(dashboardSource).toContain(
        '<Navigate to="/dashboard-advanced" replace />',
    );
    expect(dashboardSource).not.toContain("AdvancedDashboard");
    expect(dashboardSource).not.toContain("sourceMode");
    expect(dashboardSource).not.toContain('data-testid="dashboard-salary-accrual-section"');
    expect(dashboardSource).not.toContain('data-testid="dashboard-monthly-performance-section"');
    expect(dashboardSource).not.toContain('data-testid="dashboard-recent-analyses-section"');

    expect(placementSource).toContain("return null;");
    expect(placementSource).not.toContain("MutationObserver");
    expect(placementSource).not.toContain("pruneLegacyDashboardSections(document)");
});
