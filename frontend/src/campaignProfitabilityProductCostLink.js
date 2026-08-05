import {
  listWorkspaceProducts,
  syncRecentProductsV2,
} from "./services/mezanProductsV2";
import { rememberSnapchatAdsManagerReturnRange } from "./marketingCampaignResultSource";

const DIALOG_SELECTOR = '[data-testid="campaign-profitability-dialog"]';
const WORKSPACE_SELECTOR = '[data-testid="marketing-platform-workspace"]';
const LINK_ATTRIBUTE = "data-mezan-profit-product-link";
const CELL_ATTRIBUTE = "data-mezan-profit-product-cell";
const CELL_HREF_ATTRIBUTE = "data-mezan-profit-product-href";
const LOOKUP_SKU_PARAM = "lookup_sku";
const LOOKUP_NAME_PARAM = "lookup_name";
const RESOLVING_KEY = "mezan-profit-product-cost-link-resolving";

function normalized(value) {
  return String(value || "").trim().toLowerCase();
}

function clean(value) {
  return String(value || "").trim();
}

function isSnapchatAdsManagerLocation(locationLike = window.location) {
  try {
    const pathname = String(locationLike?.pathname || "").replace(/\/+$/, "") || "/";
    if (pathname !== "/ads-manager") return false;
    const provider = new URLSearchParams(locationLike?.search || "").get("provider");
    return normalized(provider) === "snapchat";
  } catch {
    return false;
  }
}

export function buildProfitabilityProductCostHref({ sku = "", name = "" } = {}) {
  const params = new URLSearchParams({ focus: "cost" });
  if (clean(sku)) params.set(LOOKUP_SKU_PARAM, clean(sku));
  if (clean(name)) params.set(LOOKUP_NAME_PARAM, clean(name));
  return `/products-v2?${params.toString()}`;
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

export function productWorkspaceId(product = {}) {
  return clean(
    product.mezan_product_id
    || product.id
    || product.salla_product_id,
  );
}

function textFromProductCell(cell) {
  const skuNode = cell.querySelector(".font-mono");
  const nameNode = [...cell.querySelectorAll("div")].find((node) => (
    node !== skuNode
    && node.className.includes("font-black")
    && clean(node.textContent)
  ));
  return {
    sku: clean(skuNode?.textContent),
    name: clean(nameNode?.textContent),
  };
}

function missingCostFromRow(row) {
  const cells = row.querySelectorAll("td");
  if (cells.length < 4) return false;
  const value = clean(cells[3]?.textContent);
  return value === "—" || value.includes("غير مكتملة") || value.includes("غير محسوم");
}

export function productHrefFromTarget(target) {
  const cell = target?.closest?.(`[${CELL_ATTRIBUTE}]`);
  return clean(cell?.getAttribute(CELL_HREF_ATTRIBUTE));
}

export function snapchatAdsManagerRangeFromPage(
  root = document,
  locationLike = window.location,
) {
  if (!isSnapchatAdsManagerLocation(locationLike)) return null;
  const workspace = root.querySelector(WORKSPACE_SELECTOR);
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
  root = document,
  locationLike = window.location,
) {
  const range = snapchatAdsManagerRangeFromPage(root, locationLike);
  if (!range) return null;
  return rememberSnapchatAdsManagerReturnRange(range);
}

export function enhanceProfitabilityProductRows(root = document) {
  const dialog = root.querySelector(DIALOG_SELECTOR);
  if (!dialog) return 0;
  let enhanced = 0;
  for (const row of dialog.querySelectorAll("tbody tr")) {
    const cells = row.querySelectorAll("td");
    if (cells.length < 7 || row.querySelector(`[${LINK_ATTRIBUTE}]`)) continue;
    const productCell = cells[0];
    const identity = textFromProductCell(productCell);
    if (!identity.sku && !identity.name) continue;

    const missingCost = missingCostFromRow(row);
    const href = buildProfitabilityProductCostHref(identity);
    productCell.setAttribute(CELL_ATTRIBUTE, "true");
    productCell.setAttribute(CELL_HREF_ATTRIBUTE, href);
    productCell.setAttribute("role", "link");
    productCell.setAttribute("tabindex", "0");
    productCell.classList.add("cursor-pointer");
    productCell.setAttribute(
      "aria-label",
      `فتح المنتج: ${identity.name || identity.sku}`,
    );

    const link = document.createElement("a");
    link.setAttribute(LINK_ATTRIBUTE, "true");
    link.href = href;
    link.className = [
      "mt-2 inline-flex items-center rounded-lg border px-3 py-1.5 text-xs font-black transition",
      missingCost
        ? "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100"
        : "border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100",
    ].join(" ");
    link.textContent = missingCost
      ? "فتح المنتج وإضافة التكلفة"
      : "فتح المنتج";
    link.setAttribute(
      "aria-label",
      `${link.textContent}: ${identity.name || identity.sku}`,
    );
    productCell.appendChild(link);
    enhanced += 1;
  }
  return enhanced;
}

async function searchProduct(lookup) {
  const query = lookup.sku || lookup.name;
  if (!query) return null;
  const result = await listWorkspaceProducts({
    page: 1,
    perPage: 30,
    query,
    sort: "newest",
  });
  return resolveProductFromWorkspace(result?.items, lookup);
}

export async function resolveProductCostDeepLink(locationLike = window.location) {
  const pathname = String(locationLike?.pathname || "").replace(/\/+$/, "") || "/";
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
      // Keep the product list reachable even when a refresh is temporarily unavailable.
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

let frame = 0;
function scheduleEnhancement() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    enhanceProfitabilityProductRows(document);
  });
}

function rememberProductLinkReturnState(event) {
  if (
    event.defaultPrevented
    || event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  ) return;
  const link = event.target?.closest?.(`[${LINK_ATTRIBUTE}]`);
  if (link) rememberSnapchatRangeBeforeProductNavigation(document, window.location);
}

function navigateFromProductCell(event) {
  if (event.target?.closest?.("a,button,input,select,textarea")) return;
  const href = productHrefFromTarget(event.target);
  if (!href) return;
  rememberSnapchatRangeBeforeProductNavigation(document, window.location);
  window.location.assign(href);
}

function navigateFromProductCellKeyboard(event) {
  if (!["Enter", " "].includes(event.key)) return;
  const href = productHrefFromTarget(event.target);
  if (!href) return;
  event.preventDefault();
  rememberSnapchatRangeBeforeProductNavigation(document, window.location);
  window.location.assign(href);
}

export function installCampaignProfitabilityProductCostLinks() {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (window.__mezanCampaignProfitabilityProductCostLinksInstalled) return false;
  window.__mezanCampaignProfitabilityProductCostLinksInstalled = true;

  const observer = new MutationObserver(scheduleEnhancement);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("click", rememberProductLinkReturnState, true);
  document.addEventListener("click", navigateFromProductCell);
  document.addEventListener("keydown", navigateFromProductCellKeyboard);
  scheduleEnhancement();

  const params = new URLSearchParams(window.location.search || "");
  const hasLookup = params.has(LOOKUP_SKU_PARAM) || params.has(LOOKUP_NAME_PARAM);
  if (hasLookup && window.sessionStorage.getItem(RESOLVING_KEY) !== window.location.search) {
    window.sessionStorage.setItem(RESOLVING_KEY, window.location.search);
    window.setTimeout(async () => {
      try {
        const resolved = await resolveProductCostDeepLink(window.location);
        if (!resolved) window.sessionStorage.removeItem(RESOLVING_KEY);
      } catch {
        window.sessionStorage.removeItem(RESOLVING_KEY);
      }
    }, 80);
  }
  return true;
}

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installCampaignProfitabilityProductCostLinks();
}
