const fs = require("fs");
const path = require("path");

const placementSource = fs.readFileSync(
    path.join(__dirname, "DashboardAnalyticsPlacement.jsx"),
    "utf8",
);


test("obsolete monthly, analysis-history and salary cards are removed from Dashboard view", () => {
    expect(placementSource).toContain(
        "'[data-testid=\"dashboard-salary-accrual-section\"]'",
    );
    expect(placementSource).toContain('"الأداء الشهري"');
    expect(placementSource).toContain('"آخر التحاليل"');
    expect(placementSource).toContain("pruneLegacyDashboardSections(document)");
    expect(placementSource).toContain(
        'node.style.setProperty("display", "none", "important")',
    );
});
