import {
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
    },
];

test("campaign manager ships the operational columns shown in Ads Manager", () => {
    expect(CAMPAIGN_MANAGER_DEFAULT_COLUMNS).toEqual(expect.arrayContaining([
        "name",
        "status",
        "delivery",
        "orders",
        "cpa",
        "spend",
        "impressions",
        "cpm",
        "clicks",
        "cpc",
        "roas",
        "sales",
        "ctr",
        "budget",
        "account",
    ]));
});

test("campaign identity remains account-scoped", () => {
    expect(campaignRowKey(campaigns[0])).toBe("a1:c1");
    expect(campaignRowKey({ ...campaigns[0], account_id: "a2" })).toBe("a2:c1");
});

test("campaign rows sort numerically without dropping real zero values", () => {
    expect(sortCampaignRows(campaigns, { key: "spend", direction: "desc" })
        .map((row) => row.campaign_id)).toEqual(["c2", "c1"]);
    expect(sortCampaignRows(campaigns, { key: "orders", direction: "asc" })
        .map((row) => row.orders)).toEqual([0, 3]);
});

test("period totals use the authoritative workspace totals", () => {
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
    };
    expect(campaignTotalsForColumn(totals, "name")).toBe("إجمالي الفترة");
    expect(campaignTotalsForColumn(totals, "orders")).toBe("3");
    expect(campaignTotalsForColumn(totals, "roas")).toBe("2.70×");
    expect(campaignTotalsForColumn(totals, "spend")).toContain("100.00");
});
