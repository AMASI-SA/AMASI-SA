import {
  CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA,
  CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES,
  CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX,
  insertSnapchatProfitabilityColumns,
  migrateCampaignColumnOrder,
  placeRoasAfterCostPerPurchase,
  placeSpendBeforeSallaSales,
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

  test("places amount spent immediately before Salla sales", () => {
    const next = placeSpendBeforeSallaSales([
      "name",
      "orders",
      "cpa",
      "roas",
      "sales",
      "product_cost",
      "profit",
      "profit_margin",
      "spend",
      "ctr",
    ]);
    expect(next).toEqual([
      "name",
      "orders",
      "cpa",
      "roas",
      "spend",
      "sales",
      "product_cost",
      "profit",
      "profit_margin",
      "ctr",
    ]);
    expect(next.indexOf("spend")).toBe(next.indexOf("sales") - 1);
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
      "spend",
      "sales",
      "product_cost",
      "profit",
      "profit_margin",
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
      "spend",
      "product_cost",
      "profit",
      "profit_margin",
    ]);
  });

  test("initializes Snapchat with spend before sales and keeps other platforms clean", () => {
    const storage = new MemoryStorage();
    expect(migrateCampaignColumnOrder(storage)).toBe(true);

    const snapchat = JSON.parse(
      storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`),
    );
    expect(snapchat).toEqual(CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES);
    expect(snapchat).toEqual(CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA);
    expect(snapchat.indexOf("roas")).toBe(snapchat.indexOf("cpa") + 1);
    expect(snapchat.indexOf("spend")).toBe(snapchat.indexOf("sales") - 1);
    expect(snapchat.indexOf("product_cost")).toBe(snapchat.indexOf("sales") + 1);

    ["meta", "tiktok", "google", "all"].forEach((platform) => {
      const value = JSON.parse(
        storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}${platform}`),
      );
      expect(value.indexOf("spend")).toBe(value.indexOf("sales") - 1);
      expect(value).not.toContain("product_cost");
      expect(value).not.toContain("profit");
      expect(value).not.toContain("profit_margin");
    });
  });

  test("migrates an existing Snapchat preference without duplicates", () => {
    const storage = new MemoryStorage();
    storage.setItem(
      `${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`,
      JSON.stringify([
        "name",
        "orders",
        "cpa",
        "sales",
        "product_cost",
        "profit",
        "profit_margin",
        "spend",
        "roas",
        "roas",
        "ctr",
      ]),
    );

    migrateCampaignColumnOrder(storage);
    expect(JSON.parse(
      storage.getItem(`${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}snapchat`),
    )).toEqual([
      "name",
      "orders",
      "cpa",
      "roas",
      "spend",
      "sales",
      "product_cost",
      "profit",
      "profit_margin",
      "ctr",
    ]);
  });
});
