import fs from "fs";
import path from "path";
const dashboard = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");
const orders = fs.readFileSync(path.join(__dirname, "OrdersV2.jsx"), "utf8");
const hook = fs.readFileSync(path.join(__dirname, "../hooks/useCampaignHover.js"), "utf8");
test("both surfaces use the revision-aware shared hook", () => {
  expect(dashboard).toContain("useCampaignHover(orders, { limit: 8, visibleCount: 8, totals })");
  expect(orders).toContain("useCampaignHover(orders, { limit: 20, totals: resultSummary })");
  expect(orders).not.toContain("orderTotals");
  expect(dashboard).toContain("<CampaignHoverBadge");
  expect(dashboard).not.toContain("}, [campaignIdsKey]);");
  expect(orders).not.toContain("}, [campaignIdsKey]);");
});
test("no hover fetch or independent polling is added", () => {
  expect(hook).not.toContain("setInterval(");
  expect(hook).not.toContain("onMouseEnter");
  expect(hook).toContain("snapshotRevision: snapshot.revision");
  expect(hook).toContain("clearTimeout(timer)");
});
