const ROOT_ID = "mezan-review-dialog-edit-control-enhancer";
const STYLE_ID = "mezan-review-dialog-edit-control-style";
let scheduled = false;

const text = (value) => String(value || "").replace(/\s+/g, " ").trim();

export const REVIEW_DIALOG_EDIT_CONTROL_CSS = `
[data-review-edit-control] {
  position: relative !important;
  top: auto !important;
  left: auto !important;
  z-index: 35 !important;
  display: flex !important;
  justify-content: flex-start !important;
  width: 100% !important;
  padding: 12px 14px 0 !important;
  pointer-events: auto !important;
}
[data-review-edit-control] > button[data-review-edit-toggle] {
  display: inline-flex !important;
  min-width: max-content !important;
  width: auto !important;
  height: 40px !important;
  padding: 0 13px !important;
  gap: 7px !important;
}
[data-review-edit-control] [data-review-edit-visible-label] {
  display: inline !important;
  font-size: 12px !important;
  font-weight: 900 !important;
  white-space: nowrap !important;
}
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-review-image-dialog-overlay] button,
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-review-image-dialog-footer] button,
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  [data-review-image-dialog-close] {
  display: inline-flex !important;
}
[data-review-image-dialog-panel] {
  display: flex !important;
  min-height: 0 !important;
  flex-direction: column !important;
  overflow: hidden !important;
}
[data-review-image-dialog-header] {
  position: relative !important;
  z-index: 3 !important;
  flex: none !important;
  background: white !important;
}
[data-review-image-dialog-body] {
  min-height: 0 !important;
  flex: 1 1 auto !important;
  overflow-y: auto !important;
  overscroll-behavior: contain !important;
}
[data-review-image-dialog-original-actions] {
  display: none !important;
}
[data-review-image-dialog-footer] {
  position: relative !important;
  z-index: 4 !important;
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  flex: none !important;
  gap: 8px !important;
  border-top: 1px solid #e2e8f0 !important;
  background: white !important;
  padding: 12px !important;
  box-shadow: 0 -10px 24px rgba(15, 23, 42, 0.08) !important;
}
[data-review-image-dialog-footer] button {
  display: inline-flex !important;
  min-height: 44px !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 12px !important;
  padding: 8px 10px !important;
  font-size: 12px !important;
  font-weight: 900 !important;
  line-height: 1.35 !important;
}
[data-review-image-dialog-footer] button:disabled {
  cursor: not-allowed !important;
  opacity: 0.4 !important;
}
@media (max-width: 520px) {
  [data-review-image-dialog-footer] {
    grid-template-columns: minmax(0, 1fr) !important;
  }
}
`;

function injectStyle() {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = REVIEW_DIALOG_EDIT_CONTROL_CSS;
  document.head.appendChild(style);
}

export function enhanceReviewEditToggle(button) {
  if (!button) return null;
  const host = button.closest("[data-review-edit-control]");
  const editing = text(button.getAttribute("aria-label")).includes("حفظ وإغلاق")
    || text(button.textContent).includes("حفظ");
  if (host) host.dataset.reviewEditMode = editing ? "save" : "edit";

  if (editing) {
    button.title = "حفظ وإغلاق تعديلات المنتج";
    return button;
  }

  if (button.getAttribute("aria-label") !== "تعديل خيارات المنتج") {
    button.setAttribute("aria-label", "تعديل خيارات المنتج");
  }
  button.title = "تعديل الاسم والمقاس واللون وباقي خيارات المنتج";
  let label = button.querySelector("[data-review-edit-visible-label]");
  if (!label) {
    label = document.createElement("span");
    label.dataset.reviewEditVisibleLabel = "1";
    button.appendChild(label);
  }
  if (label.textContent !== "تعديل خيارات المنتج") {
    label.textContent = "تعديل خيارات المنتج";
  }
  return button;
}

function directChildOf(node, parent) {
  let current = node;
  while (current?.parentElement && current.parentElement !== parent) {
    current = current.parentElement;
  }
  return current?.parentElement === parent ? current : null;
}

function findButton(panel, phrase) {
  return [...panel.querySelectorAll("button")].find((button) =>
    !button.dataset.reviewImageProxy && text(button.textContent).includes(phrase),
  ) || null;
}

function proxyButton(label, tone, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.dataset.reviewImageProxy = tone;
  const tones = {
    order: "border:1px solid #cbd5e1;background:#fff;color:#334155",
    options: "border:0;background:#6d28d9;color:#fff",
    default: "border:0;background:#047857;color:#fff",
    cancel: "border:1px solid #fecdd3;background:#fff;color:#be123c",
  };
  button.style.cssText = tones[tone] || tones.order;
  button.onclick = onClick;
  return button;
}

function syncDialogFooter(panel, footer) {
  const originals = {
    order: findButton(panel, "حفظ لهذا الطلب فقط"),
    options: findButton(panel, "حفظ مع الخيارات المحددة"),
    default: findButton(panel, "حفظ كصورة رئيسية"),
  };
  Object.entries(originals).forEach(([mode, original]) => {
    const proxy = footer.querySelector(`[data-review-image-proxy="${mode}"]`);
    if (!proxy) return;
    const shouldDisable = !original || Boolean(original.disabled);
    if (proxy.disabled !== shouldDisable) proxy.disabled = shouldDisable;
    const visible = text(original?.textContent);
    const fallback = mode === "order"
      ? "حفظ لهذا الطلب فقط"
      : mode === "options"
        ? "حفظ مع الخيارات المحددة"
        : "حفظ كصورة رئيسية";
    if (proxy.textContent !== (visible || fallback)) proxy.textContent = visible || fallback;
  });
}

export function enhanceReviewImageDialog(heading) {
  if (!heading || text(heading.textContent) !== "حفظ صورة التجهيز") return null;
  const overlay = heading.closest(".fixed") || heading.parentElement?.parentElement?.parentElement;
  if (!overlay) return null;
  const panel = [...overlay.children].find((child) => child.contains(heading))
    || heading.parentElement?.parentElement;
  if (!panel) return null;

  overlay.dataset.reviewImageDialogOverlay = "1";
  panel.dataset.reviewImageDialogPanel = "1";
  const header = directChildOf(heading, panel);
  if (header) header.dataset.reviewImageDialogHeader = "1";
  const body = header?.nextElementSibling;
  if (body) body.dataset.reviewImageDialogBody = "1";

  const closeButton = header?.querySelector("button") || null;
  if (closeButton) closeButton.dataset.reviewImageDialogClose = "1";

  const orderButton = findButton(panel, "حفظ لهذا الطلب فقط");
  const optionsButton = findButton(panel, "حفظ مع الخيارات المحددة");
  const defaultButton = findButton(panel, "حفظ كصورة رئيسية");
  const originals = [orderButton, optionsButton, defaultButton].filter(Boolean);
  const originalHost = originals.length
    ? originals[0].parentElement
    : null;
  if (originalHost && originals.every((button) => button.parentElement === originalHost)) {
    originalHost.dataset.reviewImageDialogOriginalActions = "1";
  }

  let footer = panel.querySelector(":scope > [data-review-image-dialog-footer]");
  if (!footer) {
    footer = document.createElement("div");
    footer.dataset.reviewImageDialogFooter = "1";
    footer.append(
      proxyButton("حفظ لهذا الطلب فقط", "order", () => findButton(panel, "حفظ لهذا الطلب فقط")?.click()),
      proxyButton("حفظ مع الخيارات المحددة", "options", () => findButton(panel, "حفظ مع الخيارات المحددة")?.click()),
      proxyButton("حفظ كصورة رئيسية", "default", () => findButton(panel, "حفظ كصورة رئيسية")?.click()),
      proxyButton("إلغاء وإغلاق", "cancel", () => {
        const close = panel.querySelector("[data-review-image-dialog-close]");
        if (close) close.click();
        else overlay.remove();
      }),
    );
    panel.appendChild(footer);
  }
  syncDialogFooter(panel, footer);
  return { overlay, panel, header, body, footer };
}

function decorate() {
  scheduled = false;
  injectStyle();
  document.querySelectorAll('[data-review-edit-toggle="1"]').forEach(
    (button) => enhanceReviewEditToggle(button),
  );
  [...document.querySelectorAll("h2, h3")]
    .filter((heading) => text(heading.textContent) === "حفظ صورة التجهيز")
    .forEach((heading) => enhanceReviewImageDialog(heading));
}

function scheduleDecorate() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.requestAnimationFrame(decorate);
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
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["disabled", "aria-label"],
  });
  scheduleDecorate();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
