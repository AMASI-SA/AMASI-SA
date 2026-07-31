const ACTION_LABELS = new Map([
  ["إضافة منتج تشغيلي", "منتج"],
  ["تعليمات التجهيز", "تعليمات"],
  ["ملاحظة داخلية", "ملاحظة"],
  ["توجيه مباشر للتجهيز الداخلي", "توجيه للتجهيز"],
  ["إضافة صورة ميزان", "صورة ميزان"],
]);

const ROOT_ID = "mezan-review-compact-action-labels-root";
const TOOLTIP_ID = "mezan-review-action-label-tooltip";
let scheduled = false;

export function compactReviewActionLabel(value) {
  const label = String(value || "").trim();
  return ACTION_LABELS.get(label) || label;
}

function replaceLabelText(node, fullLabel, compactLabel) {
  if (typeof document === "undefined" || !document) return false;
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  let changed = false;
  while (walker.nextNode()) {
    const current = walker.currentNode;
    const value = current.nodeValue || "";
    if (!value.includes(fullLabel)) continue;
    current.nodeValue = value.replace(fullLabel, compactLabel);
    changed = true;
  }
  return changed;
}

function removeTooltip() {
  document.getElementById(TOOLTIP_ID)?.remove();
}

function showTooltip(node, fullLabel) {
  if (typeof document === "undefined" || !document?.body) return;
  removeTooltip();
  const tooltip = document.createElement("div");
  tooltip.id = TOOLTIP_ID;
  tooltip.dataset.reviewLongPressTooltip = "1";
  tooltip.textContent = fullLabel;
  tooltip.style.cssText = [
    "position:fixed",
    "z-index:10050",
    "max-width:min(320px,calc(100vw - 24px))",
    "padding:8px 12px",
    "border-radius:10px",
    "background:#0f172a",
    "color:#fff",
    "font-size:13px",
    "font-weight:800",
    "line-height:1.5",
    "text-align:center",
    "box-shadow:0 10px 28px rgba(15,23,42,.28)",
    "pointer-events:none",
    "direction:rtl",
  ].join(";");
  document.body.appendChild(tooltip);

  const rect = node.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const left = Math.max(
    12,
    Math.min(
      window.innerWidth - tooltipRect.width - 12,
      rect.left + (rect.width - tooltipRect.width) / 2,
    ),
  );
  const preferredTop = rect.top - tooltipRect.height - 10;
  const top = preferredTop >= 10
    ? preferredTop
    : Math.min(window.innerHeight - tooltipRect.height - 10, rect.bottom + 10);
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${Math.max(10, top)}px`;
  window.setTimeout(removeTooltip, 1700);
}

function bindLongPress(node, fullLabel) {
  if (node.dataset.reviewLongPressBound === "1") return;
  node.dataset.reviewLongPressBound = "1";

  let pressTimer = null;
  let suppressNextClick = false;

  const clearPressTimer = () => {
    if (pressTimer !== null) window.clearTimeout(pressTimer);
    pressTimer = null;
  };

  const start = (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    clearPressTimer();
    suppressNextClick = false;
    pressTimer = window.setTimeout(() => {
      pressTimer = null;
      suppressNextClick = true;
      showTooltip(node, fullLabel);
    }, 550);
  };

  const finish = () => {
    clearPressTimer();
    if (suppressNextClick) window.setTimeout(removeTooltip, 500);
  };

  node.addEventListener("pointerdown", start, { passive: true });
  node.addEventListener("pointerup", finish, { passive: true });
  node.addEventListener("pointercancel", finish, { passive: true });
  node.addEventListener("pointerleave", finish, { passive: true });
  node.addEventListener("click", (event) => {
    if (!suppressNextClick) return;
    event.preventDefault();
    event.stopPropagation();
    suppressNextClick = false;
  }, true);
  node.addEventListener("contextmenu", (event) => {
    if (suppressNextClick) event.preventDefault();
  });
}

export function compactReviewActionElement(node) {
  if (!node) return false;
  const existingFullLabel = node.dataset.reviewFullLabel;
  const entry = existingFullLabel
    ? [existingFullLabel, ACTION_LABELS.get(existingFullLabel)]
    : [...ACTION_LABELS.entries()].find(([fullLabel]) =>
      String(node.textContent || "").includes(fullLabel));
  if (!entry?.[1]) return false;

  const [fullLabel, compactLabel] = entry;
  node.dataset.reviewFullLabel = fullLabel;
  node.title = fullLabel;
  node.setAttribute("aria-label", fullLabel);
  node.style.width = "auto";
  node.style.minWidth = "0";
  node.style.maxWidth = "100%";
  node.style.paddingInline = "9px";
  node.style.whiteSpace = "nowrap";
  node.style.touchAction = "manipulation";
  replaceLabelText(node, fullLabel, compactLabel);
  bindLongPress(node, fullLabel);
  return true;
}

function compactAll() {
  scheduled = false;
  if (typeof document === "undefined" || !document) return;
  document.querySelectorAll(
    "[data-testid='order-review-product-card'] button, [data-testid='order-review-product-card'] label",
  ).forEach(compactReviewActionElement);
}

function scheduleCompact() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.requestAnimationFrame(compactAll);
}

function start() {
  if (typeof document === "undefined" || !document?.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);
  const observer = new MutationObserver(scheduleCompact);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  scheduleCompact();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
