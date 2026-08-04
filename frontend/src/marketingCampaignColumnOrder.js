export const CAMPAIGN_COLUMN_ORDER_STORAGE_PREFIX = "mezan-campaign-manager-columns-v1:";
export const CAMPAIGN_COLUMN_ORDER_VERSION_KEY = "mezan-campaign-column-order-v2";

export const CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES = Object.freeze([
  "name",
  "status",
  "delivery",
  "orders",
  "cpa",
  "roas",
  "spend",
  "sales",
  "product_cost",
  "profit",
  "profit_margin",
  "impressions",
  "cpm",
  "clicks",
  "cpc",
  "ctr",
  "budget",
  "account",
]);

// Backward-compatible export for existing tests and imports. ROAS remains
// directly after CPA, while Spend now sits between ROAS and Salla Sales.
export const CAMPAIGN_COLUMN_ORDER_ROAS_AFTER_CPA = (
  CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES
);

const PROFIT_COLUMNS = Object.freeze(["product_cost", "profit", "profit_margin"]);
const KNOWN_COLUMNS = new Set(CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES);
const PLATFORM_KEYS = Object.freeze(["snapchat", "meta", "tiktok", "google", "all"]);

export function placeRoasAfterCostPerPurchase(columns) {
  if (!Array.isArray(columns)) {
    return [...CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES];
  }

  const unique = [];
  const seen = new Set();
  columns.forEach((column) => {
    const id = String(column || "").trim();
    if (!KNOWN_COLUMNS.has(id) || seen.has(id)) return;
    seen.add(id);
    unique.push(id);
  });

  if (!unique.length) return [...CAMPAIGN_COLUMN_ORDER_SPEND_BEFORE_SALLA_SALES];
  if (!unique.includes("roas") || !unique.includes("cpa")) return unique;

  const withoutRoas = unique.filter((column) => column !== "roas");
  const cpaIndex = withoutRoas.indexOf("cpa");
  withoutRoas.splice(cpaIndex + 1, 0, "roas");
  return withoutRoas;
}

export function placeSpendBeforeSallaSales(columns) {
  const current = placeRoasAfterCostPerPurchase(columns);
  if (!current.includes("spend") || !current.includes("sales")) return current;

  const withoutSpend = current.filter((column) => column !== "spend");
  const salesIndex = withoutSpend.indexOf("sales");
  withoutSpend.splice(salesIndex, 0, "spend");
  return withoutSpend;
}

function profitabilityInsertIndex(columns) {
  for (const anchor of ["sales", "spend", "roas", "cpa"]) {
    const index = columns.indexOf(anchor);
    if (index >= 0) return index + 1;
  }
  return columns.length;
}

export function insertSnapchatProfitabilityColumns(columns) {
  const current = placeSpendBeforeSallaSales(columns);
  const withoutProfit = current.filter((column) => !PROFIT_COLUMNS.includes(column));
  withoutProfit.splice(profitabilityInsertIndex(withoutProfit), 0, ...PROFIT_COLUMNS);
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
      : placeSpendBeforeSallaSales(current).filter(
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
    "spend-before-salla-sales-v1",
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
