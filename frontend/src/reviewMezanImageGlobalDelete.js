import api from "./lib/api";


const ROOT_ID = "mezan-review-global-image-delete-root";

const text = (value) => String(value || "").trim();

export function mezanGlobalDeleteConfirmationText() {
  return [
    "حذف صورة ميزان؟",
    "",
    "سيتم فك ارتباطها تلقائيًا من جميع الطلبات التي تستخدمها،",
    "وستعود تلك الطلبات إلى صورة سلة الافتراضية.",
    "لن تتأثر صور سلة الأصلية.",
  ].join("\n");
}

export function mezanGlobalImageId(imageUrl) {
  return text(imageUrl)
    .split(/[?#]/, 1)[0]
    .split("/")
    .filter(Boolean)
    .pop() || "";
}

export function mezanGlobalDeletePath(orderNumber, orderItemId, imageId) {
  return `/order-reviews-v1/${encodeURIComponent(orderNumber)}`
    + `/items/${encodeURIComponent(orderItemId)}`
    + `/mezan-images/${encodeURIComponent(imageId)}/unlink-and-delete`;
}

export function isMezanGlobalDeleteButton(target) {
  const button = target?.closest?.("button");
  if (!button) return null;
  if (text(button.textContent) !== "حذف") return null;
  if (!button.closest("[data-mezan-image-tools]")) return null;
  return button;
}

function orderNumberFromPage() {
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || "";
}

function skuFromCard(card) {
  const candidate = [...(card?.querySelectorAll?.("span") || [])].find(
    (node) => node.textContent?.trim().startsWith("SKU:"),
  );
  return candidate?.textContent?.replace(/^SKU:\s*/, "").trim() || "";
}

export function itemForDeleteCard(detail, card, allCards = []) {
  const items = Array.isArray(detail?.items) ? detail.items : [];
  const sku = skuFromCard(card);
  const index = allCards.indexOf(card);
  const indexed = index >= 0 ? items[index] : null;
  if (indexed && (!sku || text(indexed.sku) === sku)) return indexed;
  return items.find((item) => text(item?.sku) === sku) || indexed || null;
}

function showToast(message, error = false) {
  const node = document.createElement("div");
  node.textContent = message;
  node.style.cssText = [
    "position:fixed",
    "z-index:10040",
    "bottom:24px",
    "left:24px",
    "max-width:460px",
    "padding:12px 16px",
    "border-radius:14px",
    "color:white",
    "font-weight:800",
    `background:${error ? "#be123c" : "#047857"}`,
    "box-shadow:0 12px 30px #0003",
  ].join(";");
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 4200);
}

function errorMessage(error) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return text(detail?.message) || text(error?.message);
}

async function handleGlobalDelete(event) {
  const button = isMezanGlobalDeleteButton(event.target);
  if (!button) return;

  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();

  if (button.dataset.globalDeleteBusy === "1") return;
  const box = button.parentElement;
  const imageUrl = box?.querySelector("img")?.getAttribute("src") || "";
  const imageId = mezanGlobalImageId(imageUrl);
  const orderNumber = orderNumberFromPage();
  const card = button.closest("[data-testid='order-review-product-card']");

  if (!imageId || !orderNumber || !card) {
    showToast("تعذر تحديد الصورة أو الطلب لحذف صورة ميزان.", true);
    return;
  }
  if (!window.confirm(mezanGlobalDeleteConfirmationText())) return;

  button.dataset.globalDeleteBusy = "1";
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "جارٍ الحذف…";

  try {
    const { data: detail } = await api.get(
      `/order-reviews-v1/${encodeURIComponent(orderNumber)}`,
    );
    const cards = [...document.querySelectorAll(
      "[data-testid='order-review-product-card']",
    )];
    const item = itemForDeleteCard(detail, card, cards);
    if (!item?.order_item_id) {
      throw new Error("تعذر تحديد المنتج المرتبط بصورة ميزان.");
    }

    await api.post(
      mezanGlobalDeletePath(orderNumber, item.order_item_id, imageId),
    );
    showToast(
      "تم حذف صورة ميزان وإرجاع جميع الطلبات المرتبطة إلى صورة سلة الافتراضية.",
    );
    window.setTimeout(() => window.location.reload(), 450);
  } catch (error) {
    showToast(
      errorMessage(error) || "تعذر حذف صورة ميزان من الطلبات المرتبطة.",
      true,
    );
    button.disabled = false;
    button.textContent = originalLabel;
    delete button.dataset.globalDeleteBusy;
  }
}

function start() {
  if (typeof document === "undefined" || !document?.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  document.addEventListener("click", handleGlobalDelete, true);
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
