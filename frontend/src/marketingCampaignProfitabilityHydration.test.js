import {
  CAMPAIGN_PROFITABILITY_HYDRATION_POLICY,
  hydrateCampaignProfitability,
} from "./marketingCampaignProfitabilityHydration";

describe("Campaign profitability hydration", () => {
  const campaigns = [{
    account_id: "account-1",
    campaign_id: "campaign-1",
    salla_orders: 1,
    salla_sales_sar: 132.92,
    snapchat_spend_sar: 50,
  }];
  const totals = {
    salla_matched_orders: 1,
    salla_sales_sar: 132.92,
    snapchat_spend_sar: 50,
  };

  test("does not inject Salla values from an older response", () => {
    const result = hydrateCampaignProfitability(campaigns, totals, {
      result_source: "salla",
      campaigns: [{
        account_id: "account-1",
        campaign_id: "campaign-1",
        salla_orders: 66,
        profitability: { orders: 66 },
      }],
    });
    expect(result.campaigns).toBe(campaigns);
    expect(result.totals).toBe(totals);
  });

  test("does not inject platform values from an older response", () => {
    const result = hydrateCampaignProfitability(campaigns, totals, {
      result_source: "platform",
      totals: { snapchat_spend_sar: 1060.85 },
    });
    expect(result.campaigns[0].snapchat_spend_sar).toBe(50);
    expect(result.totals.snapchat_spend_sar).toBe(50);
  });

  test("does not merge account or pagination generations", () => {
    const result = hydrateCampaignProfitability(campaigns, totals, {
      campaigns: [{ account_id: "other-account", campaign_id: "campaign-2" }],
    });
    expect(result.campaigns).toEqual(campaigns);
    expect(result.hydrated_campaigns).toBe(0);
    expect(result.order_semantics_hydrated_campaigns).toBe(0);
  });

  test("is safe without a snapshot", () => {
    const result = hydrateCampaignProfitability(campaigns, totals);
    expect(result.source).toBe("disabled_for_source_coherence");
  });

  test("declares snapshot hydration disabled", () => {
    expect(CAMPAIGN_PROFITABILITY_HYDRATION_POLICY).toEqual({
      enabled: false,
      response_generation_is_self_contained: true,
      reads_raw_snapshot: false,
      provider_writes_allowed: false,
      accounting_writes_allowed: false,
    });
  });
});
