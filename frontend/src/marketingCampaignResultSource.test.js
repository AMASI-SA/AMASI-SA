import api from "./lib/api";
import {
  applyEffectiveCampaignDelivery,
  campaignResultsSource,
  clearCampaignReportSnapshot,
  getCampaignReportSnapshot,
  markSnapchatManualRange,
  prepareSnapchatAccountPage,
  setCampaignResultsSource,
  setSnapchatSelectedAccount,
  snapchatAvailableAccounts,
  snapchatSelectedAccountId,
} from "./marketingCampaignResultSource";

describe("campaign result source transport", () => {
  const originalAdapter = api.defaults.adapter;

  beforeEach(() => {
    window.localStorage.clear();
    clearCampaignReportSnapshot("snapchat");
    prepareSnapchatAccountPage();
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
  });

  test("defaults to Salla and persists the selected source", () => {
    expect(campaignResultsSource("snapchat")).toBe("salla");
    expect(setCampaignResultsSource("platform", "snapchat")).toBe("platform");
    expect(campaignResultsSource("snapchat")).toBe("platform");
  });

  test("first Snapchat request lets backend resolve today in account timezone", async () => {
    api.defaults.adapter = async (config) => ({
      data: {
        result_source: "salla",
        selected_account_id: "riyadh-account",
        account_timezone: "Asia/Riyadh",
        date_from: "2026-08-04",
        date_to: "2026-08-04",
        available_accounts: [{
          account_id: "riyadh-account",
          account_name: "Riyadh",
          currency: "SAR",
          timezone: "Asia/Riyadh",
          local_today: "2026-08-04",
        }],
        campaigns: [],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const response = await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: {
        from_date: "2026-08-01",
        to_date: "2026-08-04",
        page: 1,
      },
    });

    expect(response.config.params.from_date).toBeUndefined();
    expect(response.config.params.to_date).toBeUndefined();
    expect(response.config.params.result_source).toBe("salla");
    expect(snapchatSelectedAccountId()).toBe("riyadh-account");
    expect(snapchatAvailableAccounts()[0].timezone).toBe("Asia/Riyadh");
  });

  test("adds selected account and preserves an explicitly applied range", async () => {
    setSnapchatSelectedAccount("us-account");
    markSnapchatManualRange();
    api.defaults.adapter = async (config) => ({
      data: {
        result_source: "platform",
        selected_account_id: "us-account",
        account_timezone: "America/Los_Angeles",
        date_from: "2026-08-03",
        date_to: "2026-08-03",
        available_accounts: [{
          account_id: "us-account",
          account_name: "US",
          currency: "USD",
          timezone: "America/Los_Angeles",
          local_today: "2026-08-03",
        }],
        campaigns: [{ campaign_id: "campaign-1", display_currency: "USD" }],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const response = await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: {
        from_date: "2026-08-03",
        to_date: "2026-08-03",
        page: 1,
      },
    });

    expect(response.config.params.account_id).toBe("us-account");
    expect(response.config.params.from_date).toBe("2026-08-03");
    expect(response.config.params.to_date).toBe("2026-08-03");
    expect(getCampaignReportSnapshot("snapchat")?.campaigns?.[0]?.display_currency).toBe("USD");
  });

  test("delivery block never changes the configured active campaign switch", () => {
    const payload = {
      campaigns: [{
        campaign_id: "campaign-2",
        status: "ACTIVE",
        configured_status: "ACTIVE",
        delivery_state: "NOT_DELIVERING",
        delivery_reason_code: "ACCOUNT_PAYMENT_BLOCKED",
        delivery_label: "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد",
      }],
    };

    applyEffectiveCampaignDelivery(payload);

    expect(payload.campaigns[0].configured_status).toBe("ACTIVE");
    expect(payload.campaigns[0].status).toBe("ACTIVE");
    expect(payload.campaigns[0].delivery_state).toBe("NOT_DELIVERING");
    expect(payload.campaigns[0].delivery_status).toContain("لا تسليم");
  });

  test("campaign daily budget reason remains delivery-only", () => {
    const payload = {
      campaigns: [{
        campaign_id: "campaign-3",
        status: "ACTIVE",
        delivery_state: "NOT_DELIVERING",
        effective_delivery_code: "CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
        effective_delivery_label: "لا تسليم — خارج الميزانية اليومية",
      }],
    };

    applyEffectiveCampaignDelivery(payload);

    expect(payload.campaigns[0].status).toBe("ACTIVE");
    expect(payload.campaigns[0].delivery_status).toContain("خارج الميزانية اليومية");
  });
});
