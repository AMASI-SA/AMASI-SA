const ROOT_ID = "mezan-review-auto-advance-root";
const ADVANCE_TIMEOUT_MS = 12_000;
let pendingAdvance = null;
let retryTimer = null;

const text = (value) => String(value || "").trim();

export function reviewOrderNumberFromHeading(root = document) {
  const heading = [...root.querySelectorAll("h2")].find((node) =>
    text(node.textContent).includes("مراجعة الطلب #"),
  );
  return heading?.textContent?.match(/#(\d+)/)?.[1] || "";
}

export function isVisibleReviewQueueButton(button) {
  if (!button || button.hidden || button.disabled) return false;
  if (button.closest('[data-review-queue-hidden="true"]')) return false;
  const section = button.closest("[data-review-queue-section]");
  if (section?.hidden || section?.dataset.reviewQueueHidden === "true") {
    return false;
  }
  return true;
}

export function pendingReviewOrderRows(root = document) {
  return [...root.querySelectorAll("button")]
    .filter(isVisibleReviewQueueButton)
    .map((button) => {
      const orderNumberNode = [...button.querySelectorAll("span")].find((node) =>
        /^#\d+$/.test(text(node.textContent)),
      );
      const orderNumber = text(orderNumberNode?.textContent).replace(/^#/, "");
      return orderNumber ? { orderNumber, button } : null;
    })
    .filter(Boolean);
}

export function orderedNextReviewNumbers(orderNumbers, completedOrderNumber) {
  const normalizedCompleted = text(completedOrderNumber);
  const unique = [...new Set((orderNumbers || []).map(text).filter(Boolean))];
  const currentIndex = unique.indexOf(normalizedCompleted);
  if (currentIndex < 0) {
    return unique.filter((value) => value !== normalizedCompleted);
  }
  return [
    ...unique.slice(currentIndex + 1),
    ...unique.slice(0, currentIndex),
  ].filter((value) => value !== normalizedCompleted);
}

function clearRetryTimer() {
  if (retryTimer !== null && typeof window !== "undefined") {
    window.clearTimeout(retryTimer);
  }
  retryTimer = null;
}

export function clearPendingReviewAdvance() {
  pendingAdvance = null;
  clearRetryTimer();
}

export function armReviewAutoAdvance(
  completedOrderNumber,
  rows = pendingReviewOrderRows(),
) {
  const completed = text(completedOrderNumber);
  if (!completed) return null;
  pendingAdvance = {
    completedOrderNumber: completed,
    preferredOrderNumbers: orderedNextReviewNumbers(
      rows.map((row) => row.orderNumber),
      completed,
    ),
    startedAt: Date.now(),
  };
  scheduleAdvanceAttempt();
  return pendingAdvance;
}

function rowByOrderNumber(rows, orderNumber) {
  return rows.find((row) => row.orderNumber === orderNumber) || null;
}

export function attemptReviewAutoAdvance(root = document) {
  if (!pendingAdvance) return false;

  const elapsed = Date.now() - pendingAdvance.startedAt;
  if (elapsed > ADVANCE_TIMEOUT_MS) {
    clearPendingReviewAdvance();
    return false;
  }

  const drawerOrderNumber = reviewOrderNumberFromHeading(root);
  if (drawerOrderNumber === pendingAdvance.completedOrderNumber) {
    scheduleAdvanceAttempt();
    return false;
  }
  if (
    drawerOrderNumber
    && drawerOrderNumber !== pendingAdvance.completedOrderNumber
  ) {
    clearPendingReviewAdvance();
    return true;
  }

  const rows = pendingReviewOrderRows(root);
  const completedStillVisible = rows.some(
    (row) => row.orderNumber === pendingAdvance.completedOrderNumber,
  );
  if (completedStillVisible) {
    scheduleAdvanceAttempt();
    return false;
  }

  const preferredRow = pendingAdvance.preferredOrderNumbers
    .map((orderNumber) => rowByOrderNumber(rows, orderNumber))
    .find(Boolean);
  const nextRow = preferredRow || rows.find(
    (row) => row.orderNumber !== pendingAdvance.completedOrderNumber,
  );

  if (!nextRow) {
    scheduleAdvanceAttempt();
    return false;
  }

  clearPendingReviewAdvance();
  nextRow.button.click();
  return true;
}

function scheduleAdvanceAttempt() {
  if (!pendingAdvance || retryTimer !== null || typeof window === "undefined") {
    return;
  }
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    attemptReviewAutoAdvance();
  }, 120);
}

function isCompleteReviewButton(button) {
  if (!button) return false;
  const label = text(button.textContent).replace(/\s+/g, " ");
  return label.includes("تمت المراجعة")
    && Boolean(button.closest("section"));
}

function captureCompletionIntent(event) {
  const button = event.target?.closest?.("button");
  if (!isCompleteReviewButton(button)) return;
  const orderNumber = reviewOrderNumberFromHeading();
  if (!orderNumber) return;
  armReviewAutoAdvance(orderNumber);
}

function start() {
  if (typeof document === "undefined" || !document.body) return;
  if (document.getElementById(ROOT_ID)) return;
  const marker = document.createElement("div");
  marker.id = ROOT_ID;
  marker.hidden = true;
  document.body.appendChild(marker);

  document.addEventListener("click", captureCompletionIntent, true);
  const observer = new MutationObserver(() => {
    if (pendingAdvance) attemptReviewAutoAdvance();
  });
  observer.observe(document.body, { childList: true, subtree: true });
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
