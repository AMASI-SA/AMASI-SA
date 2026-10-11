import {
  ADS_LIVE_WORKSPACE_POLICY,
  adsquadSortPreference,
  setAdsquadSortPreference,
  enhanceAdsLiveWorkspace,
} from "./marketingAdsLiveWorkspaceEnhancer";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

describe("Ads live workspace policy", () => {
  afterEach(() => {
    document.body.replaceChildren();
    window.history.replaceState({}, "", "/");
  });

  test.each([
    ["/ads-manager?provider=tiktok&tab=content", false],
    ["/ads-manager?provider=tiktok", true],
    ["/ads-manager?provider=snapchat", true],
  ])("keeps explicit publisher links while preserving campaign defaults: %s", (url, opensCampaigns) => {
    window.history.replaceState({}, "", url);
    document.body.innerHTML = '<section data-testid="marketing-platform-workspace"><button data-testid="marketing-platform-tab-campaigns" aria-pressed="false">Campaigns</button></section>';
    const click = jest.fn();
    document.querySelector("button").addEventListener("click", click);
    expect(enhanceAdsLiveWorkspace()).toBe(true);
    expect(enhanceAdsLiveWorkspace()).toBe(true);
    expect(click).toHaveBeenCalledTimes(opensCampaigns ? 1 : 0);
    expect(window.location.pathname + window.location.search).toBe(url);
  });

  test("stores only supported Ad Squad sort choices", () => {
    const storage = new MemoryStorage();
    expect(adsquadSortPreference(storage)).toBe("newest");
    expect(setAdsquadSortPreference("spend", storage)).toBe("spend");
    expect(adsquadSortPreference(storage)).toBe("spend");
    expect(setAdsquadSortPreference("invalid", storage)).toBe("newest");
    expect(adsquadSortPreference(storage)).toBe("newest");
  });

  test("defaults Ads Manager to campaigns and refreshes visible reports every minute", () => {
    expect(ADS_LIVE_WORKSPACE_POLICY).toMatchObject({
      default_tab: "campaigns",
      auto_refresh_ms: 60000,
      refresh_only_when_visible: true,
      adsquad_default_sort: "newest",
      adsquad_sorting_page_size: 100,
      provider_mutations_allowed: false,
    });
  });
});
