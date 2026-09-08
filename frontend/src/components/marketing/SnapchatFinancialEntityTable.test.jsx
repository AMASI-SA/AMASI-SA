import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../../services/snapchatCampaignManagement", () => ({
    snapchatBidLabel: (strategy) => strategy === "TARGET_COST" ? "Target Cost" : strategy === "LOWEST_COST_WITH_MAX_BID" ? "Max Bid" : "Bid",
}));

import SnapchatFinancialEntityTable from "./SnapchatFinancialEntityTable";

function row(index) {
    return {
        entity: { id: `campaign-${index}`, name: `Campaign ${index}`, level: "campaign", active: true, status: "ACTIVE" },
        delivery: { spend: { amount: index, currency: "USD" }, spend_sar: { amount: index * 3.75, currency: "SAR" }, impressions: index * 10, views: index * 5, clicks: index },
        platform_outcomes: { conversions: index, roas: 1.2, add_to_cart: 2, start_checkout: 1 },
        commerce_outcomes: { status: "complete", orders: index, revenue: { amount: index * 10, currency: "SAR" }, roas: 2.1 },
        commerce_profitability: { product_cost: { amount: index * 2, currency: "SAR" }, contribution_profit: { amount: index * 4, currency: "SAR" }, profit_margin_pct: 40 },
        quality: { sync_status: "complete", coverage_status: "complete" },
    };
}

describe("Snapchat financial-first entity table", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("renders only the server page and keeps financial columns first", async () => {
        const report = {
            contract_version: "2",
            entity_level: "campaign",
            rows: Array.from({ length: 25 }, (_, index) => row(index + 1)),
            page: 1,
            page_size: 25,
            total: 5_000,
            filtered_total: 5_000,
            pages: 200,
            filters: { search: "", active_only: false },
            sort: { by: "default", direction: "desc" },
        };
        await act(async () => root.render(<SnapchatFinancialEntityTable report={report} />));

        expect(container.querySelectorAll('[data-testid^="snapchat-financial-row-"]')).toHaveLength(25);
        expect(container.textContent).not.toContain("Campaign 26");
        const headers = [...container.querySelectorAll("thead th")].map((node) => node.textContent);
        expect(headers.slice(0, 12)).toEqual([
            "الكيان", "الحالة", "طلبات سلة", "مبيعات سلة", "صرف Snapchat", "CPA سلة",
            "تكلفة المنتجات", "ربح المساهمة", "هامش الربح", "ROAS سلة", "مشتريات Snapchat", "ROAS Snapchat",
        ]);
        expect(container.querySelector('[data-testid="snapchat-server-pagination"]').textContent).toContain("1 من 200");
    });

    test("keeps engagement metrics accessible without primary wide columns", async () => {
        const report = {
            entity_level: "campaign",
            rows: [row(1)],
            page: 1,
            total: 1,
            filtered_total: 1,
            pages: 1,
            filters: {},
            sort: {},
        };
        await act(async () => root.render(<SnapchatFinancialEntityTable report={report} />));
        expect(container.querySelector('[data-testid="snapchat-secondary-metrics"]')).toBeNull();
        await act(async () => container.querySelector('button[aria-expanded="false"]').click());
        expect(container.querySelector('[data-testid="snapchat-secondary-metrics"]')).not.toBeNull();
        expect(container.textContent).toContain("ظهور Snapchat");
        expect(container.querySelector("table").className).not.toContain("2450");
    });

    test("preserves budget and bid semantics without inventing campaign budget", async () => {
        const report = { entity_level: "campaign", rows: [row(1)], page: 1, total: 1, filtered_total: 1, pages: 1, filters: {}, sort: {} };
        const settings = {
            "campaign-1": {
                account_currency: "USD",
                daily_budget_micro: null,
                daily_budget_availability: "unsupported_at_provider_level",
                ad_squad_daily_budget_sum_availability: "available",
                ad_squad_daily_budget_sum_account_currency: 125,
                ad_squad_bid_strategies: ["TARGET_COST"],
                quality: { settings_status: "settings_complete" },
            },
        };
        await act(async () => root.render(<SnapchatFinancialEntityTable report={report} settingsByEntityId={settings} />));
        expect(container.textContent).toContain("125.00 USD");
        expect(container.textContent).toContain("TARGET_COST");
        expect(container.textContent).not.toContain("0.00 USD");
    });

    test("labels TARGET_COST and Max Bid precisely and never turns AUTO_BID into Target Cost", async () => {
        const report = {
            entity_level: "ad_group",
            rows: [row(1)],
            page: 1,
            total: 1,
            filtered_total: 1,
            pages: 1,
            filters: {},
            sort: {},
        };
        report.rows[0].entity.level = "ad_group";
        const base = {
            account_currency: "USD",
            daily_budget_availability: "available",
            daily_budget_account_currency: 125,
            bid_account_currency: 15,
            quality: { settings_status: "settings_complete" },
        };

        await act(async () => root.render(
            <SnapchatFinancialEntityTable
                report={report}
                settingsByEntityId={{ "campaign-1": { ...base, bid_strategy: "TARGET_COST" } }}
            />,
        ));
        expect(container.textContent).toContain("TARGET_COST · Target Cost 15.00 USD");

        await act(async () => root.render(
            <SnapchatFinancialEntityTable
                report={report}
                settingsByEntityId={{ "campaign-1": { ...base, bid_strategy: "LOWEST_COST_WITH_MAX_BID" } }}
            />,
        ));
        expect(container.textContent).toContain("Max Bid 15.00 USD");

        await act(async () => root.render(
            <SnapchatFinancialEntityTable
                report={report}
                settingsByEntityId={{ "campaign-1": { ...base, bid_strategy: "AUTO_BID" } }}
            />,
        ));
        expect(container.textContent).toContain("AUTO_BID · دون Target Cost");
        expect(container.textContent).not.toContain("AUTO_BID · Target Cost 15.00 USD");
    });
});
