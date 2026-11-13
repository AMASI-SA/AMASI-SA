/**
 * Regression: homepage LatestOrders must use the same campaign hover component
 * as OrdersV2, without adding any hover-triggered request or extra polling.
 */
import fs from "fs";
import path from "path";

const dashboard = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");
const badge = fs.readFileSync(path.join(__dirname, "../components/CampaignHoverBadge.jsx"), "utf8");

test("latest orders uses shared account-local campaign tooltip", () => {
    expect(dashboard).toContain('import CampaignHoverBadge from "../components/CampaignHoverBadge"');
    expect(dashboard).toContain('import { getCampaignHoverBatch } from "../services/campaignHover"');
    expect(dashboard).toContain('function SourceBadge({ order, campaignReport })');
    expect(dashboard).toContain('<CampaignHoverBadge badge={badge} campaign={resolveOrderCampaign(order)} report={campaignReport} />');
    expect(dashboard).toContain('campaignReport={campaignHover[resolveOrderCampaign(order).campaignId]}');
});

test("dashboard prefetches only the eight visible campaigns", () => {
    const start = dashboard.indexOf("export function LatestOrders(");
    const end = dashboard.indexOf("\nfunction GaLive", start);
    const component = dashboard.slice(start, end);
    expect(component).toContain("(orders || []).slice(0, 8)");
    expect(component).toContain("getCampaignHoverBatch(campaignIdsKey.split");
    expect(component).not.toContain("onMouseEnter={");
    expect(component).not.toContain("setInterval(");
    expect(badge).toContain("onMouseEnter={show}");
    expect(badge).not.toContain("api.get(");
});
