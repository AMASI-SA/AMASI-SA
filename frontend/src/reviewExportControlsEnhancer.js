import { toast } from "sonner";

import api from "./lib/api";


const ROOT_ID = "mezan-review-export-controls-root";
let activeOrderNumber = null;
let activeDetail = null;
let controlsByItemId = new Map();
let loading = false;
let scheduled = false;

const text = (value) => String(value || "").trim();

export function canonicalReviewSpecKey(value) {
  const normalized = text(value)
    .toLocaleLowerCase("ar")
    .replace(/[ـ:：\s_-]+/g, " ");
  if (["لون", "اللون", "لون المنتج", "اللون المنتج"].includes(normalized)) return "color";
  if (["مقاس", "المقاس", "مقاس المنتج", "المقاس المنتج"].includes(normalized)) return "size";
  return normalized;
}

export function nextManualHiddenKeys(currentKeys, specKey) {
  const normalizedKey = canonicalReviewSpecKey(specKey);
  const values = new Set(
    (currentKeys || []).map(canonicalReviewSpecKey).filter(Boolean),
  );
  if (values.has(normalizedKey)) values.delete(normalizedKey);
  else if (normalizedKey) values.add(normalizedKey);
  return [...values].sort();
}

export function isInternalPreparationRoute(control) {
  return control?.preparation_route === "internal_preparation"
    || control?.supplier_export === false;
}

function orderNumberFromPage() {
  if (typeof document === "undefined" || !document) return null;
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || null;
}

function defaultControl(orderItemId) {
  return {
    order_item_id: orderItemId,
    manual_hidden_spec_keys: [],
    operational_hidden_spec_keys: [],
    hidden_spec_keys: [],
    preparation_route: "supplier_file",
    supplier_export: true,
    preparation_status: "pending_file",
  };
}

function normalizeControl(control, orderItemId) {
  return {
    ...defaultControl(orderItemId),
    ...(control || {}),
    order_item_id: orderItemId,
    manual_hidden_spec_keys: (control?.manual_hidden_spec_keys || [])
      .map(canonicalReviewSpecKey)
      .filter(Boolean),
    operational_hidden_spec_keys: (control?.operational_hidden_spec_keys || [])
      .map(canonicalReviewSpecKey)
      .filter(Boolean),
    hidden_spec_keys: (control?.hidden_spec_keys || [])
      .map(canonicalReviewSpecKey)
      .filter(Boolean),
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
      (controlsResponse.data?.items || []).map((row) => [
        text(row.order_item_id),
        normalizeControl(row, text(row.order_item_id)),
      ]),
    );
  } catch (error) {
    console.warn("Review export controls unavailable", error);
  } finally {
    loading = false;
  }
}

async function patchControl(orderNumber, orderItemId, payload) {
  const { data } = await api.patch(
    `/order-review-export-controls-v1/${encodeURIComponent(orderNumber)}/items/${encodeURIComponent(orderItemId)}`,
    payload,
  );
  const next = normalizeControl(data, orderItemId);
  controlsByItemId.set(orderItemId, next);
  return next;
}

function buttonBase() {
  return "border-radius:10px;padding:5px 9px;font-size:11px;font-weight:900;cursor:pointer;white-space:nowrap";
}

function applySpecRowPresentation(row, control, specKey, rerender) {
  const manual = new Set(control.manual_hidden_spec_keys || []);
  const operational = new Set(control.operational_hidden_spec_keys || []);
  const hidden = manual.has(specKey) || operational.has(specKey);
  const locked = operational.has(specKey);

  row.style.gridTemplateColumns = "auto minmax(0,1fr) auto";
  row.style.border = hidden ? "1px dashed #fb7185" : "1px solid transparent";
  row.style.background = hidden ? "#fff1f2" : "#f5f3ff";
  row.style.opacity = hidden ? "0.72" : "1";

  [...row.children].slice(0, 2).forEach((node) => {
    node.style.textDecoration = hidden ? "line-through" : "none";
  });

  let action = row.querySelector("[data-export-spec-action]");
  if (!action) {
    action = document.createElement("button");
    action.type = "button";
    action.dataset.exportSpecAction = "1";
    row.appendChild(action);
  }

  if (locked) {
    action.disabled = true;
    action.textContent = "منقول لمنتج تشغيلي";
    action.style.cssText = `${buttonBase()};border:1px solid #f59e0b;background:#fffbeb;color:#92400e;cursor:not-allowed`;
  } else if (manual.has(specKey)) {
    action.disabled = false;
    action.textContent = "إظهار في الملف";
    action.style.cssText = `${buttonBase()};border:1px solid #10b981;background:white;color:#047857`;
  } else {
    action.disabled = false;
    action.textContent = "إخفاء من الملف";
    action.style.cssText = `${buttonBase()};border:1px solid #f43f5e;background:white;color:#be123c`;
  }

  action.title = hidden
    ? "هذا الحقل لن يظهر في ملف التجهيز"
    : "إخفاء هذا الحقل من ملف التجهيز فقط";

  action.onclick = locked ? null : async () => {
    action.disabled = true;
    const original = action.textContent;
    action.textContent = "جارٍ الحفظ…";
    try {
      const nextKeys = nextManualHiddenKeys(
        control.manual_hidden_spec_keys,
        specKey,
      );
      const next = await patchControl(
        activeOrderNumber,
        control.order_item_id,
        { manual_hidden_spec_keys: nextKeys },
      );
      toast.success(
        next.manual_hidden_spec_keys.includes(specKey)
          ? "تم إخفاء الحقل من ملف التجهيز."
          : "سيظهر الحقل في ملف التجهيز.",
      );
      rerender(next);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.message || error.message || "تعذّر حفظ إعداد الحقل.");
      action.disabled = false;
      action.textContent = original;
    }
  };

  let badge = row.querySelector("[data-export-hidden-badge]");
  if (hidden && !badge) {
    badge = document.createElement("span");
    badge.dataset.exportHiddenBadge = "1";
    badge.textContent = locked ? "مخفي — منقول داخليًا" : "مخفي من ملف التجهيز";
    badge.style.cssText = "grid-column:1/-1;color:#be123c;font-size:11px;font-weight:900;margin-top:2px";
    row.appendChild(badge);
  } else if (hidden && badge) {
    badge.textContent = locked ? "مخفي — منقول داخليًا" : "مخفي من ملف التجهيز";
  } else if (!hidden && badge) {
    badge.remove();
  }
}

function routeControlsHost(card) {
  const operationalButton = [...card.querySelectorAll("button")].find(
    (node) => node.textContent?.includes("إضافة منتج تشغيلي"),
  );
  return operationalButton?.parentElement || null;
}

function applyItemRoutePresentation(card, control, rerender) {
  const host = routeControlsHost(card);
  if (!host) return;
  const internal = isInternalPreparationRoute(control);

  card.style.borderColor = internal ? "#f59e0b" : "#e2e8f0";
  card.style.boxShadow = internal ? "0 0 0 2px rgba(245,158,11,.13)" : "";

  let button = host.querySelector("[data-item-route-action]");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.dataset.itemRouteAction = "1";
    host.appendChild(button);
  }
  button.textContent = internal
    ? "إرجاع المنتج لملف التجهيز"
    : "توجيه مباشر للتجهيز الداخلي";
  button.style.cssText = internal
    ? `${buttonBase()};border:1px solid #10b981;background:white;color:#047857`
    : `${buttonBase()};border:1px solid #f59e0b;background:#fffbeb;color:#92400e`;

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
    const original = button.textContent;
    button.textContent = "جارٍ الحفظ…";
    try {
      const next = await patchControl(
        activeOrderNumber,
        control.order_item_id,
        { preparation_route: nextRoute },
      );
      toast.success(
        nextRoute === "internal_preparation"
          ? "تم توجيه المنتج مباشرة للتجهيز الداخلي."
          : "تمت إعادة المنتج إلى ملف التجهيز.",
      );
      rerender(next);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.message || error.message || "تعذّر تغيير مسار المنتج.");
      button.disabled = false;
      button.textContent = original;
    }
  };
}

function enhanceCard(card, item) {
  const orderItemId = text(item?.order_item_id);
  if (!orderItemId) return;
  const control = controlsByItemId.get(orderItemId) || defaultControl(orderItemId);

  const renderWith = (nextControl) => {
    controlsByItemId.set(orderItemId, nextControl);
    enhanceCard(card, item);
  };

  const specsContainer = card.querySelector("[data-testid='order-review-product-specs']");
  if (specsContainer) {
    [...specsContainer.children].forEach((row) => {
      const label = text(row.querySelector("span")?.textContent).replace(/[:：]\s*$/, "");
      const specKey = canonicalReviewSpecKey(label);
      if (specKey) applySpecRowPresentation(row, control, specKey, renderWith);
    });
  }
  applyItemRoutePresentation(card, control, renderWith);
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
  cards.forEach((card, index) => enhanceCard(card, items[index]));
}

function scheduleEnhance() {
  if (scheduled || typeof document === "undefined" || !document) return;
  scheduled = true;
  window.setTimeout(enhance, 30);
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
