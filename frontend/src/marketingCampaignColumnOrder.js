export const CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX = "mezan-campaign-manager-columns-v1:";
export const CAMPAIGN_COLUMN_ORDER_VERSION_KEY = "mezan-campaign-column-order-v2";

export const CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA = Object.freeze([
  "name",
  "status",
  "delivery",
  "orders",
  "cpa",
  "roas",
  "sales",
  "product_cost",
  "profit",
  "profit_margin",
  "spend",
  "impressions",
  "cpm",
  "clicks",
  "cpc",
  "ctr",
  "budget",
  "account",
]);

const PROFIT_COLUMNS = Object.freeze(["product_cost", "profit", "profit_margin"]);
const KNOWN_COLUMNS = new Set(CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA);
const PLATFORM_KEYS = Object.freeze(["snapchat", "meta", "tiktok", "google", "all"]);

export function placeRoasAfterCostPerPurchase(columns) {
  if (!Array.isArray(columns)) {
    return [...CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA];
  }

  const unique = [];
  const seen = new Set();
  columns.forEach((column) => {
    const id = String(column || "").trim();
    if (!KNOWN_COLUMNS.has(id) || seen.has(id)) return;
    seen.add(id);
    unique.push(id);
  });

  if (!unique.length) return [...CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA];
  if (!unique.includes("roas") || !unique.includes("cpa")) return unique;

  const withoutRoas = unique.filter((column) => column !== "roas");
  const cpaIndex = withoutRoas.indexOf("cpa");
  withoutRoas.splice(cpaIndex + 1, 0, "roas");
  return withoutRoas;
}

export function insertSnapchatProfitabilityColumns(columns) {
  const current = placeRoasAfterCostPerPurchase(columns);
  const withoutProfit = current.filter((column) => !PROFIT_COLUMNS.includes(column));
  const salesIndex = withoutProfit.indexOf("sales");
  const insertAt = salesIndex >= 0
    ? salesIndex + 1
    : Math.max(withoutProfit.indexOf("roas") + 1, 0);
  withoutProfit.splice(insertAt, 0, ...PROFIT_COLUMNS);
  return withoutProfit;
}

export function migrateCampaignColumnOrder(
  storage = typeof window !== "undefined" ? window.localStorage : null,
) {
  if (!storage) return false;
  let changed = false;

  PLATFORM_KEYS.forEach((platform) => {
    const key = `${CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX}${platform}`;
    let current = null;
    try {
      current = JSON.parse(storage.getItem(key) || "null");
    } catch {
      current = null;
    }

    const next = platform === "snapchat"
      ? insertSnapchatProfitabilityColumns(current)
      : placeRoasAfterCostPerPurchase(current).filter(
          (column) => !PROFIT_COLUMNS.includes(column),
        );
    const serialized = JSON.stringify(next);
    if (storage.getItem(key) !== serialized) {
      storage.setItem(key, serialized);
      changed = true;
    }
  });

  storage.setItem(
    CAMPAIGN_COLUMN_ORDER_VERSION_KEY,
    "snapchat-profitability-v1",
  );
  return changed;
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  try {
    migrateCampaignColumnOrder(window.localStorage);
  } catch {
    // Column ordering is presentation-only and must never block application boot.
  }
}
