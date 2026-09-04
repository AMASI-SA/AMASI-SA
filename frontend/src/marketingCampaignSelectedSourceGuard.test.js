jest.mock("./lib/api", () => ({
  __esModule: true,
  default: {
    interceptors: {
      response: { use: jest.fn() },
    },
    request: jest.fn(),
  },
}));

const mockSnapshot = jest.fn(() => null);

jest.mock("./marketingCampaignResultSource", () => ({
  campaignResultsSource: jest.fn(() => "salla"),
  getCampaignReportSnapshot: (...args) => mockSnapshot(...args),
}));

import {
  SELECTED_SOURCE_GUARD_POLICY,
  applySelectedSallaCampaignMetrics,
  finalizeSelectedCampaignSource,
  isSnapchatCampaignReportResponse,
  shouldRetrySelectedCampaignSource,
} from "./marketingCampaignSelectedSourceGuard";

function platformPayload() {
  return {
    result_source: "platform",
    totals: {
      spend_sar: 200,
      orders: 19,
      created_orders: 15,
      financial_orders: 12,
      cancelled_orders: 3,
      excluded_orders: 3,
      sales_sar: 3232.9,
      profitability: {
        sales_sar: 1548.27,
      },
    },
    accounts: [{ orders: 19, sales_sar: 3232.9 }],
    campaigns: [
      {
        account_id: "account-1",
        campaign_id: "campaign-1",
        spend_sar: 100,
        orders: 7,
        sales_sar: 900,
        salla_results: {
          orders: 3,
          created_orders: 3,
          financial_orders: 2,
          cancelled_orders: 1,
          excluded_orders: 1,
          sales_sar: 250,
        },
      },
      {
        account_id: "account-1",
        campaign_id: "campaign-2",
        spend_sar: 50,
        orders: 4,
        sales_sar: 700,
        salla_results: {
          orders: 2,
          created_orders: 2,
          financial_orders: 2,
          cancelled_orders: 0,
          excluded_orders: 0,
          sales_sar: 400,
        },
      },
    ],
  };
}

beforeEach(() => {
  mockSnapshot.mockReset();
  mockSnapshot.mockReturnValue(null);
});

test("recognizes only the Snapchat campaign report response", () => {
  expect(isSnapchatCampaignReportResponse({
    config: { url: "/integrations-v2/snapchat_ads/campaign-report" },
  })).toBe(true);
  expect(isSnapchatCampaignReportResponse({
    config: { url: "/integrations-v2/snapchat_ads/ad-squad-report" },
  })).toBe(false);
});

test("retries one stale platform response when Salla remains selected", () => {
  expect(shouldRetrySelectedCampaignSource(
    { result_source: "platform" },
    "salla",
    {},
  )).toBe(true);
  expect(shouldRetrySelectedCampaignSource(
    { result_source: "platform" },
    "platform",
    {},
  )).toBe(false);
  expect(shouldRetrySelectedCampaignSource(
    { result_source: "platform" },
    "salla",
    { _mezanSelectedSourceRetry: true },
  )).toBe(false);
});

test("replaces platform campaign orders and sales with explicit Salla facts", () => {
  const payload = platformPayload();
  applySelectedSallaCampaignMetrics(payload);

  expect(payload.result_source).toBe("salla");
  expect(payload.campaigns[0]).toEqual(expect.objectContaining({
    orders: 3,
    created_orders: 3,
    financial_orders: 2,
    cancelled_orders: 1,
    sales_sar: 250,
    cpa_sar: 33.333333,
    roas: 2.5,
    result_source: "salla",
  }));
  expect(payload.campaigns[1]).toEqual(expect.objectContaining({
    orders: 2,
    sales_sar: 400,
    cpa_sar: 25,
    roas: 8,
  }));
});

test("uses fixed Salla created orders and financial sales for period totals", () => {
  const payload = platformPayload();
  applySelectedSallaCampaignMetrics(payload);

  expect(payload.totals).toEqual(expect.objectContaining({
    orders: 15,
    created_orders: 15,
    financial_orders: 12,
    cancelled_orders: 3,
    sales_sar: 1548.27,
    cpa_sar: 13.333333,
    roas: 7.74135,
    result_source: "salla",
  }));
  expect(payload.accounts[0]).toEqual(expect.objectContaining({
    orders: 15,
    sales_sar: 1548.27,
    result_source: "salla",
  }));
  expect(payload.source.selected_result_source_guard).toEqual(expect.objectContaining({
    orders: "salla_results.created_orders",
    sales: "salla_results.sales_sar",
    spend: "snapchat",
    final_response_stage: true,
    snapshot_hydrated: true,
    read_only: true,
  }));
});

test("does not mutate either a completed response or a prior snapshot", () => {
  const payload = platformPayload();
  const snapshot = platformPayload();
  const response = {
    config: {
      url: "/integrations-v2/snapchat_ads/campaign-report",
      _mezanStaleCampaignResponseReplaced: true,
    },
    data: payload,
  };

  const finalized = finalizeSelectedCampaignSource(response, "salla", snapshot);

  expect(finalized).toBe(response);
  expect(finalized.config._mezanSallaSourceFinalized).toBeUndefined();
  expect(payload.campaigns[0]).toMatchObject({ orders: 7, sales_sar: 900 });
  expect(snapshot.campaigns[0]).toMatchObject({ orders: 7, sales_sar: 900 });
  expect(payload.result_source).toBe("platform");
  expect(snapshot.result_source).toBe("platform");
});

test("leaves platform mode unchanged", () => {
  const payload = platformPayload();
  const response = {
    config: { url: "/integrations-v2/snapchat_ads/campaign-report" },
    data: payload,
  };

  expect(finalizeSelectedCampaignSource(response, "platform", null)).toBe(response);
  expect(payload.result_source).toBe("platform");
  expect(payload.campaigns[0]).toMatchObject({ orders: 7, sales_sar: 900 });
});

test("does not fabricate campaign values when explicit Salla results are absent", () => {
  const payload = {
    result_source: "platform",
    totals: { spend_sar: 10, orders: 1, sales_sar: 20 },
    campaigns: [{ spend_sar: 10, orders: 1, sales_sar: 20 }],
  };
  applySelectedSallaCampaignMetrics(payload);

  expect(payload.campaigns[0]).toEqual({ spend_sar: 10, orders: 1, sales_sar: 20 });
});

test("declares the final-stage read-only source contract", () => {
  expect(SELECTED_SOURCE_GUARD_POLICY).toEqual({
    salla_orders_field: "salla_orders",
    salla_sales_field: "salla_sales_sar",
    spend_source: "snapchat_spend_sar",
    retries_stale_platform_response_once: true,
    runs_after_stale_response_guard: true,
    hydrates_raw_snapshot_after_final_response: false,
    provider_writes_allowed: false,
    accounting_writes_allowed: false,
  });
});
