import { toast } from "sonner";


const ROOT_ID = "mezan-review-product-edit-mode-root";
const STYLE_ID = "mezan-review-product-edit-mode-style";
const editingKeys = new Set();
let scheduled = false;

const text = (value) => String(value || "").trim();
const wait = (milliseconds) => new Promise((resolve) => {
  window.setTimeout(resolve, milliseconds);
});

export const REVIEW_PRODUCT_EDIT_MODE_CSS = `
[data-testid="order-review-product-card"] {
  position: relative;
}
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  button:not([data-review-edit-toggle]) {
  display: none !important;
}
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-review-edit-only],
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-mezan-image-tools] {
  display: none !important;
}
[data-review-product-subsave] {
  display: none !important;
}
[data-review-edit-control] {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 30;
  pointer-events: none;
}
[data-review-edit-control] > button {
  pointer-events: auto;
}
`;

function orderNumberFromPage() {
  if (typeof document === "undefined") return "";
  const heading = [...document.querySelectorAll("h2")].find(
    (node) => node.textContent?.includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || "";
}

function skuFromCard(card) {
  const candidate = [...card.querySelectorAll("span")].find(
    (node) => text(node.textContent).startsWith("SKU:"),
  );
  return text(candidate?.textContent).replace(/^SKU:\s*/, "");
}

function productNameFromCard(card) {
  return text(card.querySelector("h3")?.textContent);
}

export function reviewProductCardKey(card, index = 0, orderNumber = "") {
  return [
    text(orderNumber),
    skuFromCard(card),
    productNameFromCard(card),
    String(index),
  ].join("|");
}

export function reviewProductQuantityValue(card) {
  const quantity = [...card.querySelectorAll("span")].find(
    (node) => text(node.textContent).startsWith("الكمية:"),
  );
  return text(quantity?.querySelector("b")?.textContent);
}

export function decorateReviewProductQuantity(card) {
  const quantity = [...card.querySelectorAll("span")].find(
    (node) => text(node.textContent).startsWith("الكمية:"),
  );
  const value = text(quantity?.querySelector("b")?.textContent);
  if (!quantity || !value) return null;

  quantity.dataset.reviewProductQuantity = "1";
  quantity.setAttribute("aria-label", `الكمية ${value}`);
  quantity.title = "الكمية";
  quantity.style.cssText = [
    "display:inline-flex",
    "align-items:center",
    "justify-content:center",
    "min-width:36px",
    "height:34px",
    "padding:0 10px",
    "border-radius:9999px",
    "background:#059669",
    "color:white",
    "font-size:0",
    "font-weight:900",
    "box-shadow:0 5px 14px rgba(5,150,105,.28)",
  ].join(";");
  const number = quantity.querySelector("b");
  if (number) {
    number.style.cssText = [
      "font-size:17px",
      "line-height:1",
      "font-weight:950",
      "color:white",
      "font-variant-numeric:tabular-nums",
    ].join(";");
  }
  return quantity;
}

function operationalButton(card) {
  return [...card.querySelectorAll("button")].find((button) => {
    const fullLabel = text(button.dataset.reviewFullLabel);
    const ariaLabel = text(button.getAttribute("aria-label"));
    const visible = text(button.textContent);
    return fullLabel === "إضافة منتج تشغيلي"
      || ariaLabel === "إضافة منتج تشغيلي"
      || visible.includes("إضافة منتج تشغيلي")
      || visible === "منتج";
  }) || null;
}

export function markReviewProductEditOnlyRegions(card) {
  const imageChoice = [...card.querySelectorAll("button")].find(
    (button) => text(button.getAttribute("aria-label")).startsWith(
      "اختيار صورة التجهيز رقم",
    ),
  );
  const gallery = imageChoice?.closest(".mt-4")
    || imageChoice?.parentElement?.parentElement;
  if (gallery) gallery.dataset.reviewEditOnly = "image-gallery";

  const actionButton = operationalButton(card);
  const actionSection = actionButton?.closest(".border-t");
  if (actionSection) actionSection.dataset.reviewEditOnly = "product-actions";

  const notesSave = [...card.querySelectorAll("button")].find(
    (button) => text(button.textContent).includes("حفظ الملاحظات"),
  );
  if (notesSave) notesSave.dataset.reviewProductSubsave = "notes";

  return { gallery, actionSection, notesSave };
}

export function isReviewProductCardEditing(card) {
  return card?.dataset.reviewProductEditing === "true";
}

function pencilIcon() {
  return `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round"
      stroke-linejoin="round" aria-hidden="true">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" />
    </svg>`;
}

function saveIcon() {
  return `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2" stroke-linecap="round"
      stroke-linejoin="round" aria-hidden="true">
      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z" />
      <path d="M17 21v-8H7v8" />
      <path d="M7 3v5h8" />
    </svg>`;
}

async function waitUntilEnabled(button, timeout = 12000) {
  const startedAt = Date.now();
  while (
    button?.isConnected
    && button.disabled
    && Date.now() - startedAt < timeout
  ) {
    await wait(80);
  }
}

export async function persistOpenReviewProductNotes(card) {
  const notesSave = card.querySelector(
    '[data-review-product-subsave="notes"]',
  );
  if (!notesSave) return false;

  await waitUntilEnabled(notesSave, 4000);
  if (!notesSave.isConnected || notesSave.disabled) return false;
  notesSave.click();
  // Give React one tick to enter its busy state, then wait for the request.
  await wait(80);
  await waitUntilEnabled(notesSave);
  return true;
}

function injectStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = REVIEW_PRODUCT_EDIT_MODE_CSS;
  document.head.appendChild(style);
}

function editControl(card) {
  let host = card.querySelector("[data-review-edit-control]");
  if (!host) {
    host = document.createElement("div");
    host.dataset.reviewEditControl = "1";
    card.prepend(host);
  }
  let button = host.querySelector("button");
  if (!button) {
    button = document.createElement("button");
    button.type = "button";
    button.dataset.reviewEditToggle = "1";
    host.appendChild(button);
  }
  return { host, button };
}

function renderEditControl(card, key) {
  const editing = editingKeys.has(key);
  card.dataset.reviewProductEditing = editing ? "true" : "false";
  card.style.borderColor = editing ? "#8b5cf6" : "";
  card.style.boxShadow = editing
    ? "0 0 0 2px rgba(139,92,246,.14),0 10px 28px rgba(15,23,42,.08)"
    : "";

  const { host, button } = editControl(card);
  const signature = editing ? "save" : "edit";
  if (host.dataset.reviewEditSignature !== signature) {
    host.dataset.reviewEditSignature = signature;
    button.disabled = false;
    if (editing) {
      button.innerHTML = `${saveIcon()}<span>حفظ</span>`;
      button.setAttribute("aria-label", "حفظ وإغلاق تعديلات المنتج");
      button.title = "حفظ وإغلاق";
      button.style.cssText = [
        "display:inline-flex",
        "align-items:center",
        "justify-content:center",
        "gap:6px",
        "border:0",
        "border-radius:12px",
        "padding:9px 13px",
        "background:#047857",
        "color:white",
        "font-size:12px",
        "font-weight:900",
        "box-shadow:0 8px 18px rgba(4,120,87,.25)",
        "cursor:pointer",
      ].join(";");
    } else {
      button.innerHTML = pencilIcon();
      button.setAttribute("aria-label", "تعديل المنتج");
      button.title = "تعديل المنتج";
      button.style.cssText = [
        "display:inline-flex",
        "align-items:center",
        "justify-content:center",
        "width:40px",
        "height:40px",
        "border:1px solid #c4b5fd",
        "border-radius:12px",
        "background:white",
        "color:#6d28d9",
        "box-shadow:0 7px 18px rgba(15,23,42,.14)",
        "cursor:pointer",
      ].join(";");
    }
  }

  button.onclick = async () => {
    if (!editingKeys.has(key)) {
      editingKeys.add(key);
      markReviewProductEditOnlyRegions(card);
      renderEditControl(card, key);
      return;
    }

    button.disabled = true;
    button.innerHTML = `${saveIcon()}<span>جارٍ الحفظ…</span>`;
    try {
      await persistOpenReviewProductNotes(card);
      editingKeys.delete(key);
      markReviewProductEditOnlyRegions(card);
      renderEditControl(card, key);
      toast.success("تم حفظ تعديلات المنتج وإغلاقها.");
    } catch (error) {
      button.disabled = false;
      toast.error(error?.message || "تعذر إغلاق وضع تعديل المنتج.");
      renderEditControl(card, key);
    }
  };
}

export function decorateReviewProductCard(
  card,
  { key = "product-card", editing = false } = {},
) {
  injectStyle();
  if (editing) editingKeys.add(key);
  else if (!card.dataset.reviewProductEditing) editingKeys.delete(key);
  card.dataset.reviewProductEditKey = key;
  decorateReviewProductQuantity(card);
  markReviewProductEditOnlyRegions(card);
  renderEditControl(card, key);
  return card;
}

function decorateCards() {
  scheduled = false;
  if (typeof document === "undefined") return;
  const orderNumber = orderNumberFromPage();
  const cards = [
    ...document.querySelectorAll(
      '[data-testid="order-review-product-card"]',
    ),
  ];
  cards.forEach((card, index) => {
    const key = reviewProductCardKey(card, index, orderNumber);
    decorateReviewProductCard(card, {
      key,
      editing: editingKeys.has(key),
    });
  });
}

function scheduleDecorate() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.requestAnimationFrame(decorateCards);
}

function start() {
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  injectStyle();
  const observer = new MutationObserver(scheduleDecorate);
  observer.observe(document.body, { childList: true, subtree: true });
  scheduleDecorate();
}

if (
  typeof window !== "undefined"
  && process.env.NODE_ENV !== "test"
) {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
