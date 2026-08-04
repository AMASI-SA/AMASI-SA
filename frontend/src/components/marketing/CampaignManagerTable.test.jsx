import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignManagerTable, {
    CAMPAIGN_MANAGER_DEFAULT_COLUMNS,
    campaignRowKey,
    campaignTotalsForColumn,
    sortCampaignRows,
} from "./CampaignManagerTable";

const campaigns = [
    {
        account_id: "a1",
        campaign_id: "c1",
        campaign_name: "حملة أقل صرف",
        status: "ACTIVE",
        spend_sar: 10,
        orders: 0,
        sales_sar: 0,
        roas: 0,
        profitability: {
            orders: 0,
            product_cost_sar: 0,
            contribution_profit_sar: -10,
            profit_margin_pct: null,
            products: [],
        },
    },
    {
        account_id: "a1",
        campaign_id: "c2",
        campaign_name: "حملة أعلى صرف",
        status: "PAUSED",
        spend_sar: 90,
        orders: 3,
        sales_sar: 270,
        roas: 3,
        profitability: {
            orders: 3,
            sales_sar: 270,
            product_cost_sar: 100,
            known_product_cost_sar: 100,
            ad_spend_sar: 90,
            contribution_profit_sar: 80,
            profit_margin_pct: 29.63,
            cost_status: "complete",
            missing_cost_orders: 0,
            products: [
                {
                    identity: "product-1",
                    name: "منتج رابح",
                    sku: "SKU-1",
                    units: 3,
                    sales_sar: 270,
                    product_cost_sar: 100,
                    allocated_ad_spend_sar: 90,
                    contribution_profit_sar: 80,
                    profit_margin_pct: 29.63,
                },
            ],
        },
    },
];

test("campaign manager includes Snapchat profitability columns", () => {
    expect(CAMPAIGN_MANAGER_DEFAULT_COLUMNS).toEqual(expect.arrayContaining([
        "name",
        "status",
        "delivery",
        "orders",
        "cpa",
        "roas",
        "sales",
        "product_cost",
        "profit",
        "profit_margin",
        "spend",
        "impressions",
        "cpm",
        "clicks",
        "cpc",
        "ctr",
        "budget",
        "account",
    ]));
});

test("campaign identity remains account-scoped", () => {
    expect(campaignRowKey(campaigns[0])).toBe("a1:c1");
    expect(campaignRowKey({ ...campaigns[0], account_id: "a2" })).toBe("a2:c1");
});

test("campaign rows sort by contribution profit without dropping zeros", () => {
    expect(sortCampaignRows(campaigns, { key: "spend", direction: "desc" })
        .map((row) => row.campaign_id)).toEqual(["c2", "c1"]);
    expect(sortCampaignRows(campaigns, { key: "profit", direction: "desc" })
        .map((row) => row.campaign_id)).toEqual(["c2", "c1"]);
    expect(sortCampaignRows(campaigns, { key: "orders", direction: "asc" })
        .map((row) => row.orders)).toEqual([0, 3]);
});

test("period totals include campaign profitability", () => {
    const totals = {
        spend_sar: 100,
        orders: 3,
        sales_sar: 270,
        roas: 2.7,
        cpa_sar: 33.333,
        impressions: 1000,
        swipes: 50,
        ctr_pct: 5,
        cpc_sar: 2,
        cpm_sar: 100,
        profitability: {
            product_cost_sar: 100,
            contribution_profit_sar: 70,
            profit_margin_pct: 25.93,
        },
    };
    expect(campaignTotalsForColumn(totals, "name")).toBe("إجمالي الفترة");
    expect(campaignTotalsForColumn(totals, "orders")).toBe("3");
    expect(campaignTotalsForColumn(totals, "roas")).toBe("2.70×");
    expect(campaignTotalsForColumn(totals, "product_cost")).toContain("100.00");
    expect(campaignTotalsForColumn(totals, "profit")).toContain("70.00");
    expect(campaignTotalsForColumn(totals, "profit_margin")).toBe("25.93%");
});

test("opens the product profitability details from the campaign profit cell", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.localStorage.clear();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
        root.render(
            <CampaignManagerTable
                platform="snapchat"
                platformLabel="سناب شات"
                campaigns={[campaigns[1]]}
                totals={{ profitability: {} }}
                pagination={{ page: 1, pages: 1, total: 1 }}
            />,
        );
    });

    const detailsButton = [...container.querySelectorAll("button")]
        .find((button) => button.textContent.includes("تفاصيل المنتجات"));
    expect(detailsButton).toBeTruthy();

    await act(async () => detailsButton.click());
    expect(document.querySelector('[data-testid="campaign-profitability-dialog"]')).not.toBeNull();
    expect(document.body.textContent).toContain("منتج رابح");
    expect(document.body.textContent).toContain("ربح المساهمة");

    const close = document.querySelector('button[aria-label="إغلاق تفاصيل الربحية"]');
    await act(async () => close.click());
    expect(document.querySelector('[data-testid="campaign-profitability-dialog"]')).toBeNull();

    await act(async () => root.unmount());
    container.remove();
});
