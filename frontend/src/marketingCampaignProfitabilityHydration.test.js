import {
  CAMPAIGN_PROFITABILITY_HYDRATION_POLICY,
  hydrateCampaignProfitability,
} from "./marketingCampaignProfitabilityHydration";

describe("Campaign profitability hydration", () => {
  test("joins profitability and stable created orders by exact identity", () => {
    const normalizedCampaigns = [
      {
        account_id: "account-1",
        campaign_id: "campaign-1",
        campaign_name: "حملة المبيعات",
        sales_sar: 2402.94,
        orders: 13,
      },
      {
        account_id: "account-1",
        campaign_id: "campaign-2",
        campaign_name: "حملة أخرى",
        sales_sar: 100,
        orders: 1,
      },
    ];
    const rawProfitability = {
      orders: 13,
      sales_sar: 2402.94,
      product_cost_sar: 900,
      ad_spend_sar: 650,
      contribution_profit_sar: 852.94,
      profit_margin_pct: 35.5,
      products: [{ identity: "product-1", name: "منتج 1" }],
    };

    const hydrated = hydrateCampaignProfitability(
      normalizedCampaigns,
      { sales_sar: 2502.94, orders: 14 },
      {
        campaigns: [
          {
            account_id: "account-1",
            campaign_id: "campaign-1",
            created_orders: 17,
            financial_orders: 13,
            cancelled_orders: 3,
            excluded_orders: 4,
            profitability: rawProfitability,
          },
          {
            account_id: "different-account",
            campaign_id: "campaign-2",
            created_orders: 99,
            profitability: { orders: 99 },
          },
        ],
        totals: {
          created_orders: 18,
          financial_orders: 14,
          cancelled_orders: 3,
          excluded_orders: 4,
          profitability: {
            product_cost_sar: 900,
            contribution_profit_sar: 852.94,
          },
        },
      },
    );

    expect(hydrated.campaigns[0].profitability).toBe(rawProfitability);
    expect(hydrated.campaigns[0]).toMatchObject({
      orders: 17,
      created_orders: 17,
      financial_orders: 13,
      cancelled_orders: 3,
      excluded_orders: 4,
      sales_sar: 2402.94,
    });
    expect(hydrated.campaigns[1].orders).toBe(1);
    expect(hydrated.campaigns[1].profitability).toBeUndefined();
    expect(hydrated.totals).toMatchObject({
      orders: 18,
      created_orders: 18,
      financial_orders: 14,
      cancelled_orders: 3,
      excluded_orders: 4,
      profitability: {
        product_cost_sar: 900,
        contribution_profit_sar: 852.94,
      },
    });
    expect(hydrated.hydrated_campaigns).toBe(1);
  });

  test("does not invent profitability when the raw report has none", () => {
    const campaigns = [{ account_id: "a", campaign_id: "c", sales_sar: 50 }];
    const totals = { sales_sar: 50 };
    const hydrated = hydrateCampaignProfitability(campaigns, totals, {});

    expect(hydrated.campaigns).toEqual(campaigns);
    expect(hydrated.totals).toBe(totals);
    expect(hydrated.hydrated_campaigns).toBe(0);
  });

  test("never overwrites platform metrics with Salla orders or profitability", () => {
    const campaigns = [{
      account_id: "a1",
      campaign_id: "c1",
      orders: 21,
      sales_sar: 3042.64,
    }];
    const totals = { orders: 21, sales_sar: 3042.64, roas: 1.66 };
    const hydrated = hydrateCampaignProfitability(
      campaigns,
      totals,
      {
        result_source: "platform",
        campaigns: [{
          account_id: "a1",
          campaign_id: "c1",
          created_orders: 18,
          profitability: { orders: 18, sales_sar: 2513.59 },
        }],
        totals: {
          created_orders: 18,
          profitability: { sales_sar: 2513.59 },
        },
      },
    );

    expect(hydrated.campaigns).toBe(campaigns);
    expect(hydrated.totals).toBe(totals);
    expect(hydrated.campaigns[0].orders).toBe(21);
    expect(hydrated.campaigns[0].sales_sar).toBe(3042.64);
    expect(hydrated.campaigns[0].profitability).toBeUndefined();
    expect(hydrated.source).toBe(
      "snapchat_platform_source_no_salla_hydration",
    );
  });

  test("declares an exact read-only hydration policy", () => {
    expect(CAMPAIGN_PROFITABILITY_HYDRATION_POLICY).toEqual({
      exact_account_campaign_key: true,
      preserves_normalized_campaign_metrics: true,
      hydrates_created_orders_all_statuses: true,
      keeps_financial_orders_separate: true,
      reads_raw_snapshot_only: true,
      provider_writes_allowed: false,
      platform_source_hydration_blocked: true,
    });
  });
});
