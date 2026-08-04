import {
  CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA,
  CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX,
  migrateCampaignColumnOrder,
  placeRoasAfterCostPerPurchase,
} from "./marketingCampaignColumnOrder";

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

describe("Campaign Manager ROAS column order", () => {
  test("places ROAS immediately after cost per purchase", () => {
    const current = [
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
    ];

    const next = placeRoasAfterCostPerPurchase(current);
    expect(next.indexOf("roas")).toBe(next.indexOf("cpa") + 1);
    expect(next).toEqual([
      "name",
      "status",
      "delivery",
      "orders",
      "cpa",
      "roas",
      "spend",
      "impressions",
      "cpm",
      "clicks",
      "cpc",
      "sales",
    ]);
  });

  test("preserves hidden columns instead of re-enabling them", () => {
    expect(placeRoasAfterCostPerPurchase([
      "name",
      "orders",
      "cpa",
      "spend",
    ])).toEqual([
      "name",
      "orders",
      "cpa",
      "spend",
    ]);
  });

  test("initializes every Ads Manager platform with the new default", () => {
    const storage = new MemoryStorage();
    expect(migrateCampaignColumnOrder(storage)).toBe(true);

    ["snapchat", "meta", "tiktok", "google", "all"].forEach((platform) => {
      const value = JSON.parse(
        storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}${platform}`),
      );
      expect(value).toEqual(CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA);
      expect(value.indexOf("roas")).toBe(value.indexOf("cpa") + 1);
    });
  });

  test("migrates an existing Snapchat column preference once without duplicates", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      `${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`,
      JSON.stringify(["name", "orders", "cpa", "spend", "roas", "roas", "ctr"]),
    );

    migrateCampaignColumnOrder(storage);
    expect(JSON.parse(
      storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`),
    )).toEqual(["name", "orders", "cpa", "roas", "spend", "ctr"]);
  });
});
