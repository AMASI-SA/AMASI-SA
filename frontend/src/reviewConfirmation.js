import api from "./lib/api";

export const REVIEW_CONFIRMATION_MESSAGE = "جارٍ تأكيد تحديث الحالة في سلة. لا تحتاج إلى الضغط مرة أخرى.";
export const isReviewConfirmationPending = (result) =>
  result?.confirmation_pending === true || result?.salla_status_sync === "pending";

// Observe the existing operation only. Never retry completion or provider POST.
export function watchReviewConfirmation(orderNumber, onUpdate, isActive = () => true) {
  let stopped = false;
  let timer;
  async function read() {
    if (stopped || !isActive()) return;
    let terminal = false;
    try {
      const { data } = await api.get(`/order-reviews-v1/${encodeURIComponent(orderNumber)}/completion-operation`);
      if (stopped || !isActive()) return;
      const operation = data?.operation;
      if (operation) {
        onUpdate(operation);
        terminal = ["completed", "requires_review", "failed"].includes(operation.state);
      }
    } catch {
      // An unavailable status read is not evidence of failure or completion.
    }
    if (!stopped && !terminal && isActive()) timer = window.setTimeout(read, 15000);
  }
  read();
  return () => { stopped = true; window.clearTimeout(timer); };
}
