const ROOT_ID = "mezan-review-spec-row-layout-fix-root";
const STYLE_ID = "mezan-review-spec-row-layout-fix-style";
let scheduled = false;

export const REVIEW_SPEC_ROW_LAYOUT_CSS = `
[data-review-spec-layout="1"] {
  grid-template-columns: minmax(110px, 36%) minmax(0, 1fr) !important;
  align-items: start !important;
  column-gap: 12px !important;
  row-gap: 8px !important;
}
[data-review-spec-label="1"],
[data-review-spec-value="1"] {
  min-width: 0 !important;
  max-width: none !important;
  line-height: 1.75 !important;
  white-space: pre-wrap !important;
  word-break: normal !important;
  overflow-wrap: break-word !important;
}
[data-review-spec-label="1"] {
  grid-column: 1 !important;
}
[data-review-spec-value="1"] {
  grid-column: 2 !important;
  width: 100% !important;
}
[data-review-spec-actions="1"] {
  grid-column: 1 / -1 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 8px !important;
  flex-wrap: wrap !important;
  width: 100% !important;
  margin-top: 3px !important;
}
[data-review-spec-actions="1"] > [data-export-spec-action] {
  display: inline-flex !important;
  flex: 0 0 auto !important;
  align-items: center !important;
  justify-content: center !important;
}
[data-review-spec-actions="1"] > [data-spec-replacement-tools] {
  display: flex !important;
  flex: 1 1 220px !important;
  align-items: center !important;
  justify-content: flex-end !important;
  gap: 8px !important;
  margin: 0 !important;
  min-width: 0 !important;
}
[data-review-spec-actions="1"] [data-spec-replacement-display] {
  min-width: 0 !important;
  overflow-wrap: break-word !important;
  word-break: normal !important;
}
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  button[data-spec-replacement-action],
[data-testid="order-review-product-card"][data-review-product-editing="false"]
  button[data-export-spec-action] {
  display: inline-flex !important;
}
[data-review-spec-layout="1"] > [data-export-hidden-badge] {
  grid-column: 1 / -1 !important;
}
@media (max-width: 520px) {
  [data-review-spec-layout="1"] {
    grid-template-columns: minmax(0, 1fr) !important;
  }
  [data-review-spec-label="1"],
  [data-review-spec-value="1"] {
    grid-column: 1 !important;
  }
  [data-review-spec-actions="1"] {
    grid-column: 1 !important;
  }
}
`;

function injectStyle() {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = REVIEW_SPEC_ROW_LAYOUT_CSS;
  document.head.appendChild(style);
}

export function arrangeReviewSpecRow(row) {
  if (!row) return null;
  const directSpans = [...row.children].filter((node) => node.tagName === "SPAN");
  const label = directSpans[0] || null;
  const value = directSpans[1] || null;
  if (!label || !value) return null;

  row.dataset.reviewSpecLayout = "1";
  label.dataset.reviewSpecLabel = "1";
  value.dataset.reviewSpecValue = "1";

  const exportAction = row.querySelector("[data-export-spec-action]");
  const replacementTools = row.querySelector("[data-spec-replacement-tools]");
  if (!exportAction && !replacementTools) return { row, label, value, actions: null };

  let actions = row.querySelector(":scope > [data-review-spec-actions]");
  if (!actions) {
    actions = document.createElement("div");
    actions.dataset.reviewSpecActions = "1";
    row.appendChild(actions);
  }
  if (exportAction && exportAction.parentElement !== actions) actions.appendChild(exportAction);
  if (replacementTools && replacementTools.parentElement !== actions) actions.appendChild(replacementTools);

  return { row, label, value, actions, exportAction, replacementTools };
}

function decorate() {
  scheduled = false;
  injectStyle();
  document
    .querySelectorAll('[data-testid="order-review-product-specs"] > div')
    .forEach((row) => arrangeReviewSpecRow(row));
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
  observer.observe(document.body, { childList: true, subtree: true });
  scheduleDecorate();
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}
