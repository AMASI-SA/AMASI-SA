import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { snapchatInlineColumns } from "./SnapchatInlineSettings";

describe("Snapchat inline settings", () => {
    let container, root;
    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        root = createRoot(container);
    });
    afterEach(async () => { await act(async () => root.unmount()); });
    async function render(settings, level = "ad_group", overrides = {}) {
        const columns = snapchatInlineColumns({ level, settingsByEntityId: { row: settings }, parentCampaign: { entity: { id: "parent", name: "Parent campaign" } }, ...overrides });
        await act(async () => root.render(<table><tbody><tr>{columns.map(column => <td key={column.key} data-column={column.key}>{column.render({ entity: { id: "row", campaign_id: "parent" } })}</td>)}</tr></tbody></table>));
        return container.textContent;
    }
    const complete = { unified_entity_id: "row", account_currency: "USD", daily_budget_micro: 125_000_000, bid_micro: 15_000_000, bid_strategy: "TARGET_COST", quality: { settings_status: "settings_complete" } };
    test("shows native daily budget, Target Cost and parent in the same row", async () => {
        expect(await render(complete)).toBe("125.00 USDTarget Cost15.00 USDParent campaign");
    });
    test("labels max bid and automatic bidding without inventing a target cost", async () => {
        expect(await render({ ...complete, bid_strategy: "LOWEST_COST_WITH_MAX_BID" })).toContain("Max Bid15.00 USD");
        const text = await render({ ...complete, bid_strategy: "AUTO_BID" });
        expect(text).toContain("مزايدة تلقائية");
        expect(text).not.toContain("15.00");
        expect(text).not.toContain("Target Cost");
    });
    test.each(["settings_stale", "settings_sync_failed", "settings_not_loaded"])("hides financial values when %s", async status => {
        const text = await render({ ...complete, quality: { settings_status: status } });
        expect(text).not.toContain("125.00");
        expect(text).not.toContain("15.00");
        expect(text).not.toContain("0.00");
    });
    test("does not substitute child budgets for unavailable campaign budget", async () => {
        const text = await render({ ...complete, daily_budget_micro: null, daily_budget_availability: "unsupported_at_provider_level", ad_squads_daily_budget_micro: 999_000_000 }, "campaign");
        expect(text).toBe("غير متاح على مستوى الحملة");
        expect(container.querySelectorAll("td")).toHaveLength(1);
    });
    test("requires exact entity identity and a known currency; keeps actual zero", async () => {
        expect(await render({ ...complete, unified_entity_id: "other" })).not.toContain("125.00");
        expect(await render({ ...complete, account_currency: null })).not.toContain("USD");
        expect(await render({ ...complete, daily_budget_micro: 0 })).toContain("0.00 USD");
        expect(await render({ ...complete, daily_budget_micro: null })).not.toContain("0.00 USD");
    });
    test("does not add budget settings to Ads", () => {
        expect(snapchatInlineColumns({ level: "ad" })).toEqual([]);
    });
});
