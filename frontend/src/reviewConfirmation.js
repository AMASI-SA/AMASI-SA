import api from "./lib/api";

export const REVIEW_CONFIRMATION_MESSAGE = "توجد عملية مراجعة سابقة مرتبطة بسلة. يلزم التحقق من نتيجتها قبل إعادة الاعتماد.";
export const LOCAL_REVIEW_COMPLETION_MODE = "mezan_local_v1";
export const REVIEW_COMPLETION_UNVERIFIED_MESSAGE = "لم يتأكد اكتمال المراجعة داخل ميزان. بقي الطلب في هذه المرحلة؛ حدّث بياناته للتحقق.";
export const LEGACY_REVIEW_RESOLUTION_MESSAGE = "توجد عملية مراجعة سابقة مرتبطة بسلة وتحتاج إلى فحص قبل اعتماد الطلب داخل ميزان. لم تُعد إرسال العملية.";

export function isReviewCompletionComplete(result) {
  if (result?.ok !== true || result.confirmation_pending === true) return false;
  if (result.completion_mode === LOCAL_REVIEW_COMPLETION_MODE) {
    return result.state === "completed" && result.salla_status_sync === "not_requested";
  }
  if (result.completion_mode) return false;
  if (result.state && result.state !== "completed") return false;
  return result.salla_status_sync === "sent" || result.already_reviewed === true;
}

export function isReviewOperationCompleted(operation) {
  return operation?.state === "completed"
    && (!operation.completion_mode || operation.completion_mode === LOCAL_REVIEW_COMPLETION_MODE);
}

export function isReviewConfirmationPending(result) {
  if (isReviewCompletionComplete(result)) return false;
  if (result?.completion_mode === LOCAL_REVIEW_COMPLETION_MODE) {
    return ["prepared", "finalizing"].includes(result.state);
  }
  if (result?.completion_mode) return false;
  return result?.confirmation_pending === true || result?.salla_status_sync === "pending"
    || ["prepared", "syncing", "provider_confirmed"].includes(result?.state);
}

export function reviewConfirmationMessage(operation) {
  return operation?.completion_mode === LOCAL_REVIEW_COMPLETION_MODE
    ? "جارٍ إكمال المراجعة داخل ميزان. لا تحتاج إلى الضغط مرة أخرى."
    : REVIEW_CONFIRMATION_MESSAGE;
}

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
      } else {
        // New local reviews complete in the request. No operation means there
        // is nothing to observe; a pending completion response starts a watch.
        terminal = true;
      }
    } catch {
      // An unavailable status read is not evidence of failure or completion.
    }
    if (!stopped && !terminal && isActive()) timer = window.setTimeout(read, 15000);
  }
  read();
  return () => { stopped = true; window.clearTimeout(timer); };
}
