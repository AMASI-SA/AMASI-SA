import { AD_MANAGER_NATIVE_COLUMN_ORDER } from "./AdManagerTable";

test("Ad manager owns the canonical native column order", () => {
  expect(AD_MANAGER_NATIVE_COLUMN_ORDER).toEqual([
    "name",
    "status",
    "review",
    "delivery",
    "ad_squad",
    "campaign",
    "orders",
    "cpa",
    "roas",
    "spend",
    "sales",
    "impressions",
    "clicks",
    "ctr",
  ]);
  expect(AD_MANAGER_NATIVE_COLUMN_ORDER.indexOf("spend") + 1)
    .toBe(AD_MANAGER_NATIVE_COLUMN_ORDER.indexOf("sales"));
});
