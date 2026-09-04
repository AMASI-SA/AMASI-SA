import api from "./lib/api";
import {
  applyEffectiveCampaignDelivery,
  campaignResultsSource,
  clearCampaignReportSnapshot,
  clearSnapchatRestoredReturnRange,
  getCampaignReportSnapshot,
  markSnapchatManualRange,
  prepareSnapchatAccountPage,
  rememberSnapchatAdsManagerReturnRange,
  setCampaignResultsSource,
  setSnapchatSelectedAccount,
  snapchatAvailableAccounts,
  snapchatRestoredReturnRange,
  snapchatSelectedAccountId,
} from "./marketingCampaignResultSource";

describe("campaign result source transport", () => {
  const originalAdapter = api.defaults.adapter;

  beforeEach(() => {
    window.history.replaceState({}, "", "/ads-manager?provider=snapchat");
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearSnapchatRestoredReturnRange();
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

  test("first Snapchat request preserves the selected business date", async () => {
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

    expect(response.config.params.from_date).toBe("2026-08-01");
    expect(response.config.params.to_date).toBe("2026-08-04");
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

  test("restores the last selected range before the first request after returning from a product", async () => {
    window.localStorage.setItem("mezan-snapchat-manager-account-v1", "us-account");
    expect(rememberSnapchatAdsManagerReturnRange({
      dateFrom: "2026-07-28",
      dateTo: "2026-08-04",
      accountId: "us-account",
    })).toMatchObject({
      date_from: "2026-07-28",
      date_to: "2026-08-04",
      account_id: "us-account",
    });
    prepareSnapchatAccountPage();

    api.defaults.adapter = async (config) => ({
      data: {
        result_source: "salla",
        selected_account_id: "us-account",
        account_timezone: "America/Los_Angeles",
        date_from: config.params.from_date,
        date_to: config.params.to_date,
        available_accounts: [{
          account_id: "us-account",
          account_name: "US",
          currency: "USD",
          timezone: "America/Los_Angeles",
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
        from_date: "2026-08-05",
        to_date: "2026-08-05",
        page: 1,
      },
    });

    expect(response.config.params.account_id).toBe("us-account");
    expect(response.config.params.from_date).toBe("2026-07-28");
    expect(response.config.params.to_date).toBe("2026-08-04");
    expect(snapchatRestoredReturnRange()).toMatchObject({
      date_from: "2026-07-28",
      date_to: "2026-08-04",
    });
  });

  test("persists the applied range with the selected account and source across refresh and product navigation", async () => {
    setSnapchatSelectedAccount("us-account");
    setCampaignResultsSource("platform", "snapchat");
    expect(markSnapchatManualRange({
      dateFrom: "2026-08-01",
      dateTo: "2026-08-09",
    })).toMatchObject({
      date_from: "2026-08-01",
      date_to: "2026-08-09",
      account_id: "us-account",
    });

    const persisted = JSON.parse(window.sessionStorage.getItem(
      "mezan-snapchat-manager-return-range-v1",
    ));
    expect(persisted).toMatchObject({
      date_from: "2026-08-01",
      date_to: "2026-08-09",
      account_id: "us-account",
    });
    expect(campaignResultsSource("snapchat")).toBe("platform");

    window.history.replaceState({}, "", "/products-v2?product=product-1&focus=cost");
    expect(snapchatRestoredReturnRange()).toBeNull();
    window.history.replaceState({}, "", "/ads-manager?provider=snapchat");
    prepareSnapchatAccountPage();

    api.defaults.adapter = async (config) => ({
      data: {
        result_source: config.params.result_source,
        selected_account_id: config.params.account_id,
        date_from: config.params.from_date,
        date_to: config.params.to_date,
        campaigns: [],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const response = await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: {
        from_date: "2026-08-12",
        to_date: "2026-08-12",
        result_source: "salla",
        page: 1,
      },
    });

    expect(response.config.params).toMatchObject({
      account_id: "us-account",
      from_date: "2026-08-01",
      to_date: "2026-08-09",
      result_source: "platform",
    });
    expect(snapchatRestoredReturnRange()).toMatchObject({
      date_from: "2026-08-01",
      date_to: "2026-08-09",
      account_id: "us-account",
    });
  });

  test("restores the persisted range from storage before a hard-refresh request", async () => {
    clearSnapchatRestoredReturnRange();
    window.localStorage.setItem("mezan-snapchat-manager-account-v1", "us-account");
    window.localStorage.setItem("mezan-marketing-results-source-v1:snapchat", "platform");
    window.sessionStorage.setItem(
      "mezan-snapchat-manager-return-range-v1",
      JSON.stringify({
        date_from: "2026-08-01",
        date_to: "2026-08-09",
        account_id: "us-account",
        saved_at: Date.now(),
      }),
    );
    prepareSnapchatAccountPage();

    api.defaults.adapter = async (config) => ({
      data: {
        result_source: config.params.result_source,
        selected_account_id: config.params.account_id,
        date_from: config.params.from_date,
        date_to: config.params.to_date,
        campaigns: [],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const response = await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: {
        from_date: "2026-08-12",
        to_date: "2026-08-12",
        result_source: "salla",
        page: 1,
      },
    });

    expect(response.config.params).toMatchObject({
      account_id: "us-account",
      from_date: "2026-08-01",
      to_date: "2026-08-09",
      result_source: "platform",
    });
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
