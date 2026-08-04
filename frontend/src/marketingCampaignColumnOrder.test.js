import {
  CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA,
  CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX,
  insertSnapchatProfitabilityColumns,
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

describe("Campaign Manager column order", () => {
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
  });

  test("inserts Snapchat profitability columns immediately after sales", () => {
    const next = insertSnapchatProfitabilityColumns([
      "name",
      "orders",
      "cpa",
      "roas",
      "sales",
      "spend",
    ]);
    expect(next).toEqual([
      "name",
      "orders",
      "cpa",
      "roas",
      "sales",
      "product_cost",
      "profit",
      "profit_margin",
      "spend",
    ]);
  });

  test("preserves hidden operational columns while enabling new Snapchat profit columns", () => {
    expect(insertSnapchatProfitabilityColumns([
      "name",
      "orders",
      "cpa",
      "spend",
    ])).toEqual([
      "name",
      "orders",
      "cpa",
      "product_cost",
      "profit",
      "profit_margin",
      "spend",
    ]);
  });

  test("initializes Snapchat with profit columns and keeps other platforms clean", () => {
    const storage = new MemoryStorage();
    expect(migrateCampaignColumnOrder(storage)).toBe(true);

    const snapchat = JSON.parse(
      storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`),
    );
    expect(snapchat).toEqual(CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA);
    expect(snapchat.indexOf("roas")).toBe(snapchat.indexOf("cpa") + 1);
    expect(snapchat.indexOf("product_cost")).toBe(snapchat.indexOf("sales") + 1);

    ["meta", "tiktok", "google", "all"].forEach((platform) => {
      const value = JSON.parse(
        storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}${platform}`),
      );
      expect(value).not.toContain("product_cost");
      expect(value).not.toContain("profit");
      expect(value).not.toContain("profit_margin");
    });
  });

  test("migrates an existing Snapchat preference without duplicates", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      `${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`,
      JSON.stringify(["name", "orders", "cpa", "spend", "roas", "roas", "ctr"]),
    );

    migrateCampaignColumnOrder(storage);
    expect(JSON.parse(
      storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`),
    )).toEqual([
      "name",
      "orders",
      "cpa",
      "roas",
      "product_cost",
      "profit",
      "profit_margin",
      "spend",
      "ctr",
    ]);
  });
});
