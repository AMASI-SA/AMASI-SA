const SALES_CELL_SELECTOR = '[data-mezan-sales-with-spend="true"]';
const FIXED_ATTRIBUTE = "data-mezan-sales-visibility-fixed";
const SALES_DISPLAY_ATTRIBUTE = "data-mezan-sales-display";

let frame = 0;

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function sectionName(cell) {
  return cell?.closest?.("thead,tbody,tfoot")?.tagName || "TBODY";
}

function salesLabel(cell) {
  const value = clean(cell?.textContent);
  if (value) return value;
  const section = sectionName(cell);
  if (section === "THEAD") {
    return cell?.closest?.('[data-testid="campaign-manager-table"]')
      ? "مبيعات سلة"
      : "المبيعات";
  }
  return "—";
}

function installSalesSortBridge(cell) {
  if (cell.dataset.mezanSalesSortBridge === "true") return;
  const button = cell.querySelector("button");
  if (!button) return;
  cell.dataset.mezanSalesSortBridge = "true";
  let forwarding = false;
  cell.addEventListener("click", (event) => {
    if (forwarding || event.target === button || button.contains(event.target)) return;
    const rect = cell.getBoundingClientRect();
    const spendWidth = Number.parseFloat(
      cell.style.getPropertyValue("--mezan-spend-width") || "145",
    ) || 145;
    const spendSegmentStart = rect.right - spendWidth;
    if (event.clientX >= spendSegmentStart) return;
    forwarding = true;
    try {
      button.click();
    } finally {
      forwarding = false;
    }
  }, true);
}

export function repairAdsSalesVisibility(root = document) {
  let repaired = 0;
  root.querySelectorAll?.(SALES_CELL_SELECTOR).forEach((cell) => {
    const display = salesLabel(cell);
    if (cell.dataset.mezanSalesDisplay !== display) {
      cell.dataset.mezanSalesDisplay = display;
    }
    cell.setAttribute(FIXED_ATTRIBUTE, "true");
    cell.setAttribute(SALES_DISPLAY_ATTRIBUTE, display);
    if (sectionName(cell) === "THEAD") installSalesSortBridge(cell);
    repaired += 1;
  });
  return repaired;
}

function scheduleRepair() {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  if (frame) window.cancelAnimationFrame(frame);
  frame = window.requestAnimationFrame(() => {
    frame = 0;
    repairAdsSalesVisibility(document);
  });
}

export function installAdsSalesVisibilityFix() {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (window.__mezanAdsSalesVisibilityFixInstalled) return false;
  window.__mezanAdsSalesVisibilityFixInstalled = true;

  const observer = new MutationObserver(scheduleRepair);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["data-mezan-sales-with-spend"],
  });
  window.addEventListener("popstate", scheduleRepair);
  scheduleRepair();
  return true;
}

if (
  typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  installAdsSalesVisibilityFix();
}
