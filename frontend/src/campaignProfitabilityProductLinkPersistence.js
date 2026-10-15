import {
  buildProfitabilityProductCostHref,
  rememberSnapchatRangeBeforeProductNavigation,
} from "./campaignProfitabilityProductCostLink";

const DIALOG_SELECTOR = '[data-testid="campaign-profitability-dialog"]';
const PERSISTENT_ATTRIBUTE = "data-mezan-profit-product-persistent-link";
const HREF_ATTRIBUTE = "data-mezan-profit-product-persistent-href";
const LABEL_ATTRIBUTE = "data-mezan-profit-product-persistent-label";
const LEGACY_LINK_SELECTOR = '[data-mezan-profit-product-link]';

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function directCells(row) {
  return [...(row?.children || [])].filter((cell) => cell.tagName === "TD");
}

export function profitabilityProductIdentity(cell) {
  if (!cell) return { sku: "", name: "" };
  const skuNode = cell.querySelector(".font-mono");
  const nameNode = cell.querySelector('[title]:not([title=""])')
    || [...cell.querySelectorAll("div,span")].find((node) => (
      node !== skuNode
      && /font-(?:black|extrabold|bold)/.test(String(node.className || ""))
      && clean(node.textContent)
    ));
  return {
    sku: clean(skuNode?.textContent),
    name: clean(nameNode?.textContent),
  };
}

function productCostMissing(costCell) {
  const value = clean(costCell?.textContent);
  return !value || value === "—" || /غير (?:مكتملة|محسوم)/.test(value);
}

export function persistProfitabilityProductLinks(root = document) {
  const dialog = root.querySelector?.(DIALOG_SELECTOR);
  if (!dialog) return 0;

  let enhanced = 0;
  dialog.querySelectorAll("tbody tr").forEach((row) => {
    const cells = directCells(row);
    if (cells.length < 4) return;
    const productCell = cells[0];

    if (productCell.querySelector(LEGACY_LINK_SELECTOR)) {
      productCell.removeAttribute(PERSISTENT_ATTRIBUTE);
      productCell.removeAttribute(HREF_ATTRIBUTE);
      productCell.removeAttribute(LABEL_ATTRIBUTE);
      return;
    }

    const identity = profitabilityProductIdentity(productCell);
    if (!identity.sku && !identity.name) return;

    const href = buildProfitabilityProductCostHref(identity);
    const label = productCostMissing(cells[3])
      ? "فتح المنتج وإضافة التكلفة"
      : "فتح المنتج";

    productCell.setAttribute(PERSISTENT_ATTRIBUTE, "true");
    productCell.setAttribute(HREF_ATTRIBUTE, href);
    productCell.setAttribute(LABEL_ATTRIBUTE, label);
    productCell.setAttribute("role", "link");
    productCell.setAttribute("tabindex", "0");
    productCell.setAttribute("aria-label", `${label}: ${identity.name || identity.sku}`);
    enhanced += 1;
  });
  return enhanced;
}

function hrefFromTarget(target) {
  const cell = target?.closest?.(`[${PERSISTENT_ATTRIBUTE}="true"]`);
  return clean(cell?.getAttribute(HREF_ATTRIBUTE));
}

function navigate(href) {
  if (!href) return;
  rememberSnapchatRangeBeforeProductNavigation(document, window.location);
  window.location.assign(href);
}

function handleClick(event) {
  if (
    event.defaultPrevented
    || event.button !== 0
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
    || event.target?.closest?.("a,button,input,select,textarea")
  ) return;
  navigate(hrefFromTarget(event.target));
}

function handleKeyboard(event) {
  if (!["Enter", " "].includes(event.key)) return;
  const href = hrefFromTarget(event.target);
  if (!href) return;
  event.preventDefault();
  navigate(href);
}

let frame = 0;
function schedule() {
  if (frame) window.cancelAnimationFrame(frame);
  frame = window.requestAnimationFrame(() => {
    frame = 0;
    persistProfitabilityProductLinks(document);
  });
}

export function installCampaignProfitabilityProductLinkPersistence() {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (window.__mezanCampaignProfitabilityProductLinkPersistenceInstalled) return false;
  window.__mezanCampaignProfitabilityProductLinkPersistenceInstalled = true;

  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("click", handleClick);
  document.addEventListener("keydown", handleKeyboard);
  window.addEventListener("popstate", schedule);
  schedule();
  return true;
}

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installCampaignProfitabilityProductLinkPersistence();
}

export const PRODUCT_LINK_PERSISTENCE_POLICY = Object.freeze({
  rendered_with_css_pseudo_button: true,
  whole_product_cell_clickable: true,
  preserves_ads_manager_return_range: true,
  provider_writes_allowed: false,
  accounting_writes_allowed: false,
});
