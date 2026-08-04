import {
  ADS_LIVE_WORKSPACE_POLICY,
  adsquadSortPreference,
  setAdsquadSortPreference,
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
