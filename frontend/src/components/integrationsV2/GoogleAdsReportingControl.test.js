const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
    path.join(__dirname, "GoogleAdsReportingControl.jsx"),
    "utf8",
);

test("Google Ads control is owner-scoped, bounded, and source-only in wording", () => {
    expect(source).toContain('data-testid="google-ads-reporting-control"');
    expect(source).toContain('data-testid="google-ads-account-selection"');
    expect(source).toContain('data-testid="google-ads-save-selection"');
    expect(source).toContain('data-testid="google-ads-reporting-sync"');
    expect(source).toContain("syncGoogleAdsReporting(7)");
    expect(source).toContain("قراءة GAQL فقط");
    expect(source).toContain("تحديث تلقائي كل 5 دقائق");
});
