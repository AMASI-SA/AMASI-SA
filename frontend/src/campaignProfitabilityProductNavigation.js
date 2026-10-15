import {
  listWorkspaceProducts,
  syncRecentProductsV2,
} from "./services/mezanProductsV2";
import { rememberSnapchatAdsManagerReturnRange } from "./marketingCampaignResultSource";

const LOOKUP_SKU_PARAM = "lookup_sku";
const LOOKUP_NAME_PARAM = "lookup_name";
const RESOLVING_KEY = "mezan-profit-product-cost-link-resolving-v2";

function clean(value) {
  return String(value || "").trim();
}

function normalized(value) {
  return clean(value).toLowerCase();
}

export function buildProfitabilityProductCostHref({
  productId = "",
  sku = "",
  name = "",
} = {}) {
  const params = new URLSearchParams({ focus: "cost" });
  if (clean(productId)) {
    params.set("product", clean(productId));
  } else {
    if (clean(sku)) params.set(LOOKUP_SKU_PARAM, clean(sku));
    if (clean(name)) params.set(LOOKUP_NAME_PARAM, clean(name));
  }
  return `/products-v2?${params.toString()}`;
}

export function currentSnapchatAdsManagerRange(
  root = typeof document !== "undefined" ? document : null,
  locationLike = typeof window !== "undefined" ? window.location : null,
) {
  if (!root || !locationLike) return null;
  const pathname = clean(locationLike.pathname).replace(/\/+$/, "") || "/";
  const provider = new URLSearchParams(locationLike.search || "").get("provider");
  if (pathname !== "/ads-manager" || normalized(provider) !== "snapchat") return null;

  const workspace = root.querySelector('[data-testid="marketing-platform-workspace"]');
  const inputs = [...(workspace?.querySelectorAll('form input[type="date"]') || [])].slice(0, 2);
  const dateFrom = clean(inputs[0]?.value);
  const dateTo = clean(inputs[1]?.value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateFrom)) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateTo)) return null;
  return {
    dateFrom,
    dateTo,
    accountId: clean(workspace?.dataset?.snapchatSelectedAccount),
  };
}

export function rememberSnapchatRangeBeforeProductNavigation(
  root = typeof document !== "undefined" ? document : null,
  locationLike = typeof window !== "undefined" ? window.location : null,
) {
  const range = currentSnapchatAdsManagerRange(root, locationLike);
  return range ? rememberSnapchatAdsManagerReturnRange(range) : null;
}

function productSkus(product = {}) {
  const values = [product.sku];
  for (const variant of product.variants || []) {
    if (variant && typeof variant === "object") values.push(variant.sku);
  }
  return values.map(normalized).filter(Boolean);
}

export function resolveProductFromWorkspace(items = [], { sku = "", name = "" } = {}) {
  const rows = Array.isArray(items) ? items : [];
  const expectedSku = normalized(sku);
  const expectedName = normalized(name);
  if (expectedSku) {
    const bySku = rows.find((product) => productSkus(product).includes(expectedSku));
    if (bySku) return bySku;
  }
  if (expectedName) {
    const byName = rows.find((product) => normalized(product?.name) === expectedName);
    if (byName) return byName;
  }
  return rows.length === 1 ? rows[0] : null;
}

function productWorkspaceId(product = {}) {
  return clean(product.mezan_product_id || product.id || product.salla_product_id);
}

async function searchProduct(lookup) {
  const queries = [...new Set([lookup.sku, lookup.name].map(clean).filter(Boolean))];
  for (const query of queries) {
    const result = await listWorkspaceProducts({
      page: 1,
      perPage: 30,
      query,
      sort: "newest",
    });
    const product = resolveProductFromWorkspace(result?.items, lookup);
    if (product) return product;
  }
  return null;
}

export async function resolveProductCostDeepLink(locationLike = window.location) {
  const pathname = clean(locationLike?.pathname).replace(/\/+$/, "") || "/";
  if (pathname !== "/products-v2") return false;
  const params = new URLSearchParams(locationLike?.search || "");
  if (params.get("product")) return false;

  const lookup = {
    sku: clean(params.get(LOOKUP_SKU_PARAM)),
    name: clean(params.get(LOOKUP_NAME_PARAM)),
  };
  if (!lookup.sku && !lookup.name) return false;

  let product = await searchProduct(lookup);
  if (!product) {
    try {
      await syncRecentProductsV2({ force: true });
      product = await searchProduct(lookup);
    } catch {
      // Keep the product list reachable if refresh is temporarily unavailable.
    }
  }

  const productId = productWorkspaceId(product);
  if (!productId) return false;
  const next = new URL(locationLike.href);
  next.searchParams.delete(LOOKUP_SKU_PARAM);
  next.searchParams.delete(LOOKUP_NAME_PARAM);
  next.searchParams.set("product", productId);
  next.searchParams.set("focus", "cost");
  window.sessionStorage.removeItem(RESOLVING_KEY);
  window.location.replace(`${next.pathname}${next.search}${next.hash}`);
  return true;
}

export function installProductCostDeepLinkResolver() {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search || "");
  const hasLookup = params.has(LOOKUP_SKU_PARAM) || params.has(LOOKUP_NAME_PARAM);
  if (!hasLookup) return false;
  if (window.sessionStorage.getItem(RESOLVING_KEY) === window.location.search) return false;

  window.sessionStorage.setItem(RESOLVING_KEY, window.location.search);
  window.setTimeout(async () => {
    try {
      const resolved = await resolveProductCostDeepLink(window.location);
      if (!resolved) window.sessionStorage.removeItem(RESOLVING_KEY);
    } catch {
      window.sessionStorage.removeItem(RESOLVING_KEY);
    }
  }, 80);
  return true;
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  installProductCostDeepLinkResolver();
}

export const CAMPAIGN_PRODUCT_NAVIGATION_POLICY = Object.freeze({
  rendered_by_react: true,
  mutation_observer_used: false,
  preserves_snapchat_date_range: true,
  opens_cost_focus: true,
  provider_writes_allowed: false,
});
