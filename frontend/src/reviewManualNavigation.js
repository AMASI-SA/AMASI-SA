import {
  clearPendingReviewAdvance,
  pendingReviewOrderRows,
  reviewOrderNumberFromHeading,
} from "./reviewAutoAdvance";


const ROOT_ID = "mezan-review-manual-navigation-root";
const HOST_ATTRIBUTE = "data-review-manual-navigation";
let scheduled = false;

const text = (value) => String(value || "").trim();

export function adjacentReviewOrder(
  rows,
  currentOrderNumber,
  direction,
) {
  const normalizedCurrent = text(currentOrderNumber);
  const normalizedRows = (rows || []).filter((row) => text(row?.orderNumber));
  const currentIndex = normalizedRows.findIndex(
    (row) => text(row.orderNumber) === normalizedCurrent,
  );
  if (currentIndex < 0) return null;

  const offset = direction === "previous" ? -1 : direction === "next" ? 1 : 0;
  if (!offset) return null;
  return normalizedRows[currentIndex + offset] || null;
}

export function reviewManualNavigationState(root = document) {
  const currentOrderNumber = reviewOrderNumberFromHeading(root);
  const rows = pendingReviewOrderRows(root);
  const currentIndex = rows.findIndex(
    (row) => text(row.orderNumber) === text(currentOrderNumber),
  );
  return {
    currentOrderNumber,
    rows,
    currentIndex,
    previous: adjacentReviewOrder(rows, currentOrderNumber, "previous"),
    next: adjacentReviewOrder(rows, currentOrderNumber, "next"),
  };
}

export function navigateReviewManually(direction, root = document) {
  const state = reviewManualNavigationState(root);
  const target = direction === "previous" ? state.previous : state.next;
  if (!target?.button) return null;

  clearPendingReviewAdvance();
  target.button.click();
  return target.orderNumber;
}

function arrowIcon(direction) {
  const path = direction === "previous"
    ? "m9 18 6-6-6-6"
    : "m15 18-6-6 6-6";
  return `
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
      stroke-linejoin="round" aria-hidden="true">
      <path d="${path}" />
    </svg>`;
}

function createNavigationButton(direction) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.reviewNavigationDirection = direction;
  button.innerHTML = arrowIcon(direction);
  button.style.cssText = [
    "display:inline-flex",
    "align-items:center",
    "justify-content:center",
    "width:40px",
    "height:40px",
    "border:1px solid #cbd5e1",
    "border-radius:12px",
    "background:white",
    "color:#334155",
    "box-shadow:0 4px 12px rgba(15,23,42,.08)",
    "cursor:pointer",
    "transition:opacity .15s,border-color .15s,color .15s",
  ].join(";");
  return button;
}

function drawerHeader(root = document) {
  const heading = [...root.querySelectorAll("h2")].find((node) =>
    text(node.textContent).includes("مراجعة الطلب #"),
  );
  return heading?.closest("header") || null;
}

function navigationHost(header) {
  let host = header.querySelector(`[${HOST_ATTRIBUTE}]`);
  if (host) return host;

  host = document.createElement("div");
  host.setAttribute(HOST_ATTRIBUTE, "1");
  host.style.cssText = [
    "display:flex",
    "align-items:center",
    "gap:7px",
    "margin-inline-start:auto",
    "margin-inline-end:8px",
  ].join(";");

  const previous = createNavigationButton("previous");
  const next = createNavigationButton("next");
  host.append(previous, next);

  const directCloseButton = [...header.children].find(
    (node) => node.tagName === "BUTTON",
  );
  if (directCloseButton) header.insertBefore(host, directCloseButton);
  else header.appendChild(host);
  return host;
}

function updateButton(button, target, label) {
  const available = Boolean(target?.button);
  button.disabled = !available;
  button.setAttribute("aria-label", label);
  button.title = available
    ? `${label} #${target.orderNumber}`
    : `${label} — غير متاح`;
  button.dataset.targetOrderNumber = target?.orderNumber || "";
  button.style.opacity = available ? "1" : ".32";
  button.style.cursor = available ? "pointer" : "not-allowed";
  button.style.borderColor = available ? "#a78bfa" : "#cbd5e1";
  button.style.color = available ? "#6d28d9" : "#64748b";
}

export function decorateReviewManualNavigation(root = document) {
  const header = drawerHeader(root);
  const currentOrderNumber = reviewOrderNumberFromHeading(root);
  if (!header || !currentOrderNumber) return null;

  const host = navigationHost(header);
  const state = reviewManualNavigationState(root);
  const previousButton = host.querySelector(
    '[data-review-navigation-direction="previous"]',
  );
  const nextButton = host.querySelector(
    '[data-review-navigation-direction="next"]',
  );

  updateButton(previousButton, state.previous, "الطلب السابق");
  updateButton(nextButton, state.next, "الطلب التالي");

  previousButton.onclick = () => navigateReviewManually("previous", root);
  nextButton.onclick = () => navigateReviewManually("next", root);
  host.dataset.currentOrderNumber = currentOrderNumber;
  host.dataset.currentPosition = state.currentIndex >= 0
    ? String(state.currentIndex + 1)
    : "";
  host.dataset.totalOrders = String(state.rows.length);
  return host;
}

function scheduleDecorate() {
  if (scheduled || typeof window === "undefined") return;
  scheduled = true;
  window.requestAnimationFrame(() => {
    scheduled = false;
    decorateReviewManualNavigation();
  });
}

function start() {
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById(ROOT_ID)) return;

  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);

  const observer = new MutationObserver(scheduleDecorate);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
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
