import api from "./lib/api";
import {
  campaignResultsSource,
  clearCampaignReportSnapshot,
  getCampaignReportSnapshot,
  setCampaignResultsSource,
} from "./marketingCampaignResultSource";

describe("campaign result source transport", () => {
  const originalAdapter = api.defaults.adapter;

  beforeEach(() => {
    window.localStorage.clear();
    clearCampaignReportSnapshot("snapchat");
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
  });

  test("defaults to Salla and persists the selected source", () => {
    expect(campaignResultsSource("snapchat")).toBe("salla");
    expect(setCampaignResultsSource("platform", "snapchat")).toBe("platform");
    expect(campaignResultsSource("snapchat")).toBe("platform");
  });

  test("adds result_source to Snapchat campaign report and stores the raw snapshot", async () => {
    setCampaignResultsSource("platform", "snapchat");
    api.defaults.adapter = async (config) => ({
      data: {
        result_source: "platform",
        campaigns: [{ campaign_id: "campaign-1", display_currency: "USD" }],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });

    const response = await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: { page: 1 },
    });

    expect(response.config.params.result_source).toBe("platform");
    expect(getCampaignReportSnapshot("snapchat")?.campaigns?.[0]?.display_currency).toBe("USD");
  });
});
