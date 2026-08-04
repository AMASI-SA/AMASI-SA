import { CAMPAIGN_REPORT_UPDATED_EVENT } from "./marketingCampaignResultSource";

const TABLE_SELECTOR = '[data-testid="campaign-manager-table"]';
const CELL_SIGNATURE = "mezanSnapchatDeliverySignature";

function isSnapchatAdsManager() {
  if (typeof window === "undefined") return false;
  if (window.location.pathname !== "/ads-manager") return false;
  try {
    return new URLSearchParams(window.location.search || "").get("provider") === "snapchat";
  } catch {
    return false;
  }
}

function normalizedText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function deliveryState(text) {
  const value = normalizedText(text);
  if (value.includes("يتم التسليم")) return "delivering";
  if (value.includes("قيد التحقق")) return "pending";
  if (
    value.includes("لا تسليم")
    || value.includes("خارج الميزانية")
    || value.includes("لا توجد مجموعة")
    || value.includes("لا يوجد إعلان")
  ) return "blocked";
  if (value.includes("غير نشط")) return "inactive";
  return "unknown";
}

function deliveryColumnIndex(table) {
  return [...table.querySelectorAll("thead th")].findIndex(
    (cell) => normalizedText(cell.textContent).includes("حالة التسليم"),
  );
}

function detailNode(cell) {
  return [...cell.querySelectorAll("div")].find((node) => (
    typeof node.className === "string"
    && node.className.includes("text-[10px]")
  )) || null;
}

function paintDot(dot, state) {
  if (!dot) return;
  dot.classList.remove(
    "bg-emerald-500",
    "ring-emerald-100",
    "bg-amber-500",
    "ring-amber-100",
    "bg-slate-300",
    "ring-2",
  );
  if (state === "delivering") {
    dot.classList.add("bg-emerald-500", "ring-2", "ring-emerald-100");
  } else if (state === "blocked" || state === "pending") {
    dot.classList.add("bg-amber-500", "ring-2", "ring-amber-100");
  } else {
    dot.classList.add("bg-slate-300");
  }
}

function paintDetail(node, state) {
  if (!node) return;
  if (state === "delivering") {
    node.hidden = false;
    if (!normalizedText(node.textContent)) {
      node.textContent = "قد تكون في مرحلة التعلم";
    }
    return;
  }
  if (state === "blocked") {
    node.hidden = false;
    node.textContent = "الحملة مفعلة، لكنها لا تسلّم حاليًا";
    return;
  }
  if (state === "pending") {
    node.hidden = false;
    node.textContent = "بانتظار تأكيد حالة التسليم من Snapchat";
    return;
  }
  if (state === "unknown") {
    node.hidden = false;
    node.textContent = "لم تُرجع Snapchat حالة تسليم مؤكدة";
    return;
  }
  node.hidden = true;
}

export function enhanceSnapchatDeliveryStates(root = document) {
  if (!isSnapchatAdsManager()) return false;
  const section = root.querySelector(TABLE_SELECTOR);
  const table = section?.querySelector("table");
  if (!table) return false;
  const columnIndex = deliveryColumnIndex(table);
  if (columnIndex < 0) return false;

  [...table.querySelectorAll("tbody tr")].forEach((row) => {
    const cells = [...row.querySelectorAll(":scope > td")];
    const cell = cells[columnIndex];
    if (!cell) return;
    const text = normalizedText(cell.textContent);
    const state = deliveryState(text);
    const signature = `${state}:${text}`;
    if (cell.dataset[CELL_SIGNATURE] === signature) return;

    const dot = cell.querySelector("span.rounded-full");
    paintDot(dot, state);
    paintDetail(detailNode(cell), state);
    cell.dataset[CELL_SIGNATURE] = signature;
    cell.dataset.mezanSnapchatDeliveryState = state;
  });
  return true;
}

let frame = 0;
function scheduleEnhancement() {
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    frame = 0;
    enhanceSnapchatDeliveryStates(document);
  });
}

const canAutoEnhance = typeof window !== "undefined"
  && typeof document !== "undefined"
  && process.env.NODE_ENV !== "test";

if (canAutoEnhance) {
  const observer = new MutationObserver(scheduleEnhancement);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("popstate", scheduleEnhancement);
  window.addEventListener(CAMPAIGN_REPORT_UPDATED_EVENT, scheduleEnhancement);
  scheduleEnhancement();
}
