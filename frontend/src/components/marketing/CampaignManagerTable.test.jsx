import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignManagerTable, {
    CAMPAIGN_MANAGER_DEFAULT_COLUMNS,
    CAMPAIGN_MANAGER_NATIVE_COLUMN_ORDER,
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
        snapchat_spend_sar: 10,
        salla_orders: 0,
        salla_sales_sar: 0,
        salla_roas: 0,
        salla_profitability: {
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
        snapchat_spend_sar: 90,
        salla_orders: 3,
        salla_sales_sar: 270,
        salla_roas: 3,
        salla_profitability: {
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
                    mezan_product_id: "mezan-product-1",
                    salla_product_id: "salla-product-1",
                    name: "منتج رابح",
                    sku: "SKU-1",
                    units: 3,
                    sales_sar: 270,
                    product_cost_sar: 100,
                    allocated_ad_spend_sar: 90,
                    contribution_profit_sar: 80,
                    profit_margin_pct: 29.63,
                    cost_status: "complete",
                },
                {
                    identity: "product-2",
                    mezan_product_id: "mezan-product-2",
                    salla_product_id: "salla-product-2",
                    name: "منتج ناقص التكلفة",
                    sku: "SKU-2",
                    units: 1,
                    sales_sar: 50,
                    product_cost_sar: 20,
                    allocated_ad_spend_sar: 10,
                    contribution_profit_sar: null,
                    profit_margin_pct: null,
                    cost_status: "salla_fallback",
                },
            ],
        },
    },
];

beforeEach(() => {
    window.localStorage.clear();
});

test("campaign manager owns the canonical Mezan 2 column order", () => {
    expect(CAMPAIGN_MANAGER_DEFAULT_COLUMNS).toEqual([
        "name",
        "status",
        "delivery",
        "orders",
        "cpa",
        "roas",
        "spend",
        "sales",
        "snapchat_purchases",
        "snapchat_value",
        "snapchat_roas",
        "product_cost",
        "profit",
        "profit_margin",
        "impressions",
        "paid_reach",
        "paid_frequency",
        "cpm",
        "clicks",
        "cpc",
        "ctr",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
        "budget",
        "account",
    ]);
    expect(CAMPAIGN_MANAGER_NATIVE_COLUMN_ORDER).toBe(CAMPAIGN_MANAGER_DEFAULT_COLUMNS);
    expect(CAMPAIGN_MANAGER_DEFAULT_COLUMNS.indexOf("spend") + 1)
        .toBe(CAMPAIGN_MANAGER_DEFAULT_COLUMNS.indexOf("sales"));
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
        .map((row) => row.salla_orders)).toEqual([0, 3]);
});

test("period totals include campaign profitability", () => {
    const totals = {
        snapchat_spend_sar: 100,
        salla_matched_orders: 3,
        salla_sales_sar: 270,
        salla_roas: 2.7,
        salla_cpa_sar: 33.333,
        impressions: 1000,
        paid_reach: 800,
        paid_frequency: 1.25,
        view_content: 90,
        add_to_cart: 25,
        start_checkout: 12,
        add_billing: 8,
        swipes: 50,
        ctr_pct: 5,
        cpc_sar: 2,
        cpm_sar: 100,
        salla_profitability: {
            product_cost_sar: 100,
            contribution_profit_sar: 70,
            profit_margin_pct: 25.93,
        },
    };
    expect(campaignTotalsForColumn(totals, "name")).toBe("إجمالي الفترة");
    expect(campaignTotalsForColumn(totals, "orders")).toBe("3");
    expect(campaignTotalsForColumn(totals, "roas")).toBe("2.70×");
    expect(campaignTotalsForColumn(totals, "paid_reach")).toBe("800");
    expect(campaignTotalsForColumn(totals, "paid_frequency")).toBe("1.25×");
    expect(campaignTotalsForColumn(totals, "add_to_cart")).toBe("25");
    expect(campaignTotalsForColumn(totals, "spend")).toContain("100.00");
    expect(campaignTotalsForColumn(totals, "sales")).toContain("270.00");
    expect(campaignTotalsForColumn(totals, "product_cost")).toContain("100.00");
    expect(campaignTotalsForColumn(totals, "profit")).toContain("70.00");
    expect(campaignTotalsForColumn(totals, "profit_margin")).toBe("25.93%");
});

test("renders native sticky name and status and separate spend and Salla sales cells", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
        root.render(
            <CampaignManagerTable
                platform="snapchat"
                platformLabel="سناب شات"
                campaigns={[campaigns[1]]}
                totals={{ snapchat_spend_sar: 90, salla_sales_sar: 270, salla_profitability: {} }}
                pagination={{ page: 1, pages: 1, total: 1 }}
            />,
        );
    });

    const table = container.querySelector('[data-testid="campaign-manager-table"]');
    expect(table.dataset.nativeColumnLayout).toBe("true");
    const ids = [...table.querySelectorAll("thead [data-column-id]")]
        .map((cell) => cell.dataset.columnId);
    expect(ids.slice(0, 8)).toEqual([
        "name", "status", "delivery", "orders", "cpa", "roas", "spend", "sales",
    ]);
    expect(table.querySelector('thead [data-column-id="name"]').className).toContain("sticky");
    expect(table.querySelector('thead [data-column-id="status"]').className).toContain("sticky");
    expect(table.querySelector('tbody [data-column-id="spend"]')).not.toBeNull();
    expect(table.querySelector('tbody [data-column-id="sales"]')).not.toBeNull();
    expect(table.querySelector('[data-mezan-sales-with-spend]')).toBeNull();
    expect(table.querySelector('[data-mezan-folded-spend-cell]')).toBeNull();

    await act(async () => root.unmount());
    container.remove();
});

test("platform source hides Salla profitability and labels provider purchase value", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
        root.render(
            <CampaignManagerTable
                platform="snapchat"
                platformLabel="سناب شات"
                resultSource="platform"
                campaigns={[{
                    ...campaigns[1],
                    snapchat_purchases: 21,
                    snapchat_purchase_value_sar: 3042.64,
                    salla_profitability: undefined,
                }]}
                totals={{ snapchat_purchases: 21, snapchat_purchase_value_sar: 3042.64 }}
                pagination={{ page: 1, pages: 1, total: 1 }}
            />,
        );
    });

    const table = container.querySelector('[data-testid="campaign-manager-table"]');
    const ids = [...table.querySelectorAll("thead [data-column-id]")]
        .map((cell) => cell.dataset.columnId);
    expect(ids).not.toContain("product_cost");
    expect(ids).not.toContain("profit");
    expect(ids).not.toContain("profit_margin");
    expect(table.textContent).toContain("قيمة مشتريات Snapchat");
    expect(table.textContent).not.toContain("تفاصيل المنتجات");

    await act(async () => root.unmount());
    container.remove();
});

test("opens product profitability details with official product cost links", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
        root.render(
            <CampaignManagerTable
                platform="snapchat"
                platformLabel="سناب شات"
                campaigns={[campaigns[1]]}
                totals={{ salla_profitability: {} }}
                pagination={{ page: 1, pages: 1, total: 1 }}
            />,
        );
    });

    const detailsButton = [...container.querySelectorAll("button")]
        .find((button) => button.textContent.includes("تفاصيل المنتجات"));
    expect(detailsButton).toBeTruthy();

    await act(async () => detailsButton.click());
    const dialog = document.querySelector('[data-testid="campaign-profitability-dialog"]');
    expect(dialog).not.toBeNull();
    expect(document.body.textContent).toContain("منتج رابح");
    expect(document.body.textContent).toContain("ربح المساهمة");

    const links = [...dialog.querySelectorAll('[data-testid="campaign-profitability-product-link"]')];
    expect(links).toHaveLength(2);
    expect(links[0].textContent).toBe("فتح المنتج");
    expect(links[0].getAttribute("href")).toContain("product=mezan-product-1");
    expect(links[0].getAttribute("href")).not.toContain("lookup_sku");
    expect(links[1].textContent).toBe("فتح المنتج وإضافة التكلفة");
    expect(links[1].getAttribute("href")).toContain("product=mezan-product-2");
    expect(dialog.textContent).toContain("التكلفة الحالية من سلة؛ أضف تكلفة ميزان");

    const close = document.querySelector('button[aria-label="إغلاق تفاصيل الربحية"]');
    await act(async () => close.click());
    expect(document.querySelector('[data-testid="campaign-profitability-dialog"]')).toBeNull();

    await act(async () => root.unmount());
    container.remove();
});
