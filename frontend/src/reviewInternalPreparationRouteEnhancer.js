import { toast } from "sonner";

import api from "./lib/api";


const ROOT_ID = "mezan-review-internal-preparation-route-root";
const OPERATIONAL_FULL_LABEL = "إضافة منتج تشغيلي";
let activeOrderNumber = null;
let activeDetail = null;
let controlsByItemId = new Map();
let loading = false;
let scheduled = false;

const text = (value) => String(value || "").trim();

export function findOperationalProductButton(card) {
  if (!card) return null;
  return [...card.querySelectorAll("button")].find((node) => {
    const fullLabel = text(node.dataset.reviewFullLabel);
    const ariaLabel = text(node.getAttribute("aria-label"));
    const visibleLabel = text(node.textContent);
    return fullLabel === OPERATIONAL_FULL_LABEL
      || ariaLabel === OPERATIONAL_FULL_LABEL
      || visibleLabel.includes(OPERATIONAL_FULL_LABEL);
  }) || null;
}

export function isInternalPreparationControl(control) {
  return control?.preparation_route === "internal_preparation"
    || control?.supplier_export === false;
}

export function internalPreparationActionLabel(control) {
  return isInternalPreparationControl(control)
    ? "إرجاع للملف"
    : "توجيه للتجهيز";
}

function orderNumberFromPage() {
  if (typeof document === "undefined" || !document) return null;
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}

function normalizeControl(control, orderItemId) {
  return {
    order_item_id: orderItemId,
    preparation_route: "supplier_file",
    supplier_export: true,
    preparation_status: "pending_file",
    ...(control || {}),
  };
}

async function loadContext(orderNumber) {
  if (!orderNumber || loading) return;
  loading = true;
  try {
    const [detailResponse, controlsResponse] = await Promise.all([
      api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}`),
      api.get(`/order-review-export-controls-v1/${encodeURIComponent(orderNumber)}`),
    ]);
    activeOrderNumber = orderNumber;
    activeDetail = detailResponse.data;
    controlsByItemId = new Map(
      (controlsResponse.data?.items || []).map((row) => {
        const orderItemId = text(row.order_item_id);
        return [orderItemId, normalizeControl(row, orderItemId)];
      }),
    );
  } catch (error) {
    console.warn("Internal preparation route unavailable", error);
  } finally {
    loading = false;
  }
}

async function patchRoute(orderNumber, orderItemId, preparationRoute) {
  const { data } = await api.patch(
    `/order-review-export-controls-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`,
    { preparation_route: preparationRoute },
  );
  const next = normalizeControl(data, orderItemId);
  controlsByItemId.set(orderItemId, next);
  return next;
}

function actionStyle(internal) {
  return [
    "border-radius:10px",
    "padding:5px 9px",
    "font-size:11px",
    "font-weight:900",
    "cursor:pointer",
    "white-space:nowrap",
    internal ? "border:1px solid #10b981" : "border:1px solid #f59e0b",
    internal ? "background:white" : "background:#fffbeb",
    internal ? "color:#047857" : "color:#92400e",
  ].join(";");
}

function renderRouteAction(card, item, control) {
  const operationalButton = findOperationalProductButton(card);
  const host = operationalButton?.parentElement;
  if (!host) return false;

  const internal = isInternalPreparationControl(control);
  card.style.borderColor = internal ? "#f59e0b" : "#e2e8f0";
  card.style.boxShadow = internal ? "0 0 0 2px rgba(245,158,11,.13)" : "";

  let button = host.querySelector("[data-item-route-action]");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.dataset.itemRouteAction = "1";
    host.appendChild(button);
  }

  const fullLabel = internal
    ? "إرجاع المنتج لملف التجهيز"
    : "توجيه مباشر للتجهيز الداخلي";
  button.textContent = internalPreparationActionLabel(control);
  button.title = fullLabel;
  button.setAttribute("aria-label", fullLabel);
  if (!internal) button.dataset.reviewFullLabel = fullLabel;
  else delete button.dataset.reviewFullLabel;
  button.style.cssText = actionStyle(internal);

  let banner = card.querySelector("[data-item-route-banner]");
  if (internal && !banner) {
    banner = document.createElement("div");
    banner.dataset.itemRouteBanner = "1";
    banner.style.cssText = "margin:0 16px 12px;border:1px solid #f59e0b;border-radius:12px;background:#fffbeb;color:#92400e;padding:10px 12px;font-size:12px;font-weight:900;line-height:1.7";
    const footer = host.closest(".border-t") || host.parentElement;
    footer?.parentElement?.insertBefore(banner, footer);
  }
  if (banner) {
    banner.textContent = "تجهيز داخلي — لن يظهر هذا المنتج في ملف المورد، وسيبدأ مباشرة بحالة قيد التجهيز داخل ميزان.";
    banner.hidden = !internal;
  }

  button.onclick = async () => {
    const nextRoute = internal ? "supplier_file" : "internal_preparation";
    if (!internal && !window.confirm(
      "سيتم استبعاد المنتج كاملًا من ملف التجهيز وتوجيهه مباشرة إلى قيد التنفيذ داخل ميزان. متابعة؟",
    )) return;

    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = "جارٍ الحفظ…";
    try {
      const next = await patchRoute(
        activeOrderNumber,
        text(item.order_item_id),
        nextRoute,
      );
      toast.success(
        nextRoute === "internal_preparation"
          ? "تم توجيه المنتج مباشرة للتجهيز الداخلي."
          : "تمت إعادة المنتج إلى ملف التجهيز.",
      );
      renderRouteAction(card, item, next);
    } catch (error) {
      toast.error(
        error?.response?.data?.detail?.message
        || error.message
        || "تعذّر تغيير مسار المنتج.",
      );
      button.disabled = false;
      button.textContent = originalLabel;
    }
  };
  button.disabled = false;
  return true;
}

async function enhance() {
  scheduled = false;
  if (typeof document === "undefined" || !document) return;
  const orderNumber = orderNumberFromPage();
  if (!orderNumber) return;
  if (orderNumber !== activeOrderNumber || !activeDetail) {
    await loadContext(orderNumber);
  }
  if (orderNumber !== activeOrderNumber || !activeDetail) return;

  const cards = [...document.querySelectorAll("[data-testid='order-review-product-card']")];
  const items = Array.isArray(activeDetail.items) ? activeDetail.items : [];
  cards.forEach((card, index) => {
    const item = items[index];
    const orderItemId = text(item?.order_item_id);
    if (!orderItemId) return;
    const control = controlsByItemId.get(orderItemId)
      || normalizeControl(null, orderItemId);
    renderRouteAction(card, item, control);
  });
}

function scheduleEnhance() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.setTimeout(enhance, 35);
}

function start() {
  if (typeof document === "undefined" || !document?.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  const observer = new MutationObserver(scheduleEnhance);
  observer.observe(document.body, { childList: true, subtree: true });
  scheduleEnhance();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
