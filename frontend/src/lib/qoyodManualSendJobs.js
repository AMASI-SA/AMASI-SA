import api from "./api";

const BASE = "/integrations/qoyod/manual";
const pendingRequests = new Map();
const keyFor = (order) => `qoyod-manual-job:${order}`;

function requestKey(order) {
  const key = keyFor(order);
  let saved;
  try { saved = window.sessionStorage.getItem(key); } catch (_) { /* memory fallback */ }
  saved ||= pendingRequests.get(key);
  if (!saved) saved = globalThis.crypto?.randomUUID?.()
    || `manual-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  pendingRequests.set(key, saved);
  try { window.sessionStorage.setItem(key, saved); } catch (_) { /* memory fallback */ }
  return saved;
}

function clearKey(order) {
  pendingRequests.delete(keyFor(order));
  try { window.sessionStorage.removeItem(keyFor(order)); } catch (_) { /* memory fallback */ }
}

function abortError() {
  const error = new Error("متابعة المهمة متوقفة؛ تستمر المهمة المحفوظة في الخلفية");
  error.name = "AbortError";
  return error;
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abortError()); return; }
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function failure(detail) {
  const error = new Error(detail.message);
  error.response = { data: { detail } };
  return error;
}

function checkedJob(job, expectedId) {
  if (!job || typeof job.id !== "string" || !job.id
      || (expectedId && job.id !== expectedId)
      || !["queued", "running", "succeeded", "refused", "needs_review"].includes(job.status)
      || (job.status === "succeeded" && (!job.result || typeof job.result !== "object"))) {
    throw failure({ code: "manual_job_status_unavailable",
      message: "تعذّر التحقق من نتيجة المهمة. تابع نفس الطلب قبل أي إعادة إرسال." });
  }
  return job;
}

function pendingFailure() {
  return failure({ code: "manual_job_status_pending",
    message: "المهمة محفوظة وما زالت معلّقة. تابع نتيجتها قبل أي إعادة إرسال." });
}

export async function sendQoyodManualJob(order, {
  retry = false, signal, onProgress, maxWaitMs = 16 * 60 * 1000,
} = {}) {
  if (signal?.aborted) throw abortError();
  let data;
  try {
    ({ data } = await api.post(
      `${BASE}/${retry ? "retry-failed" : "send"}/${encodeURIComponent(order)}`,
      undefined,
      { headers: { "Idempotency-Key": requestKey(order) }, signal, timeout: 15000 },
    ));
  } catch (error) {
    if (signal?.aborted) throw abortError();
    if (!error?.response || error.response.status >= 500) {
      // A timeout is not proof that enqueue failed. Keep the original key so
      // the next explicit attempt can only observe the same durable request.
      throw failure({ code: "manual_job_submission_unknown",
        message: "تعذّر تأكيد استلام طلب الإرسال. قد يكون محفوظًا؛ إعادة فتح نفس الإجراء تتابع الطلب نفسه." });
    }
    throw error;
  }
  if (signal?.aborted) throw abortError();
  // Accept a completed response from the prior backend during a rolling update.
  if (!data?.job && data && (data.ok === true || data.invoice_id || data.invoice_number)) {
    clearKey(order);
    return data;
  }
  let job = checkedJob(data?.job);
  const deadline = Date.now() + maxWaitMs;
  while (true) {
    if (signal?.aborted) throw abortError();
    onProgress?.(job);
    if (job.status === "succeeded") { clearKey(order); return job.result; }
    if (["refused", "needs_review"].includes(job.status)) {
      if (job.status === "refused") clearKey(order);
      throw failure(job.error || { code: "manual_job_needs_review",
        message: "تحتاج نتيجة الإرسال إلى مراجعة قبل إعادة المحاولة" });
    }
    if (Date.now() >= deadline) throw pendingFailure();
    await wait(Math.min(Math.max(5, Math.min(30, job.poll_after_seconds || 5)) * 1000,
      deadline - Date.now()), signal);
    if (Date.now() >= deadline) throw pendingFailure();
    try {
      const next = await api.get(`${BASE}/jobs/${encodeURIComponent(job.id)}`,
        { signal, timeout: 10000 });
      job = checkedJob(next.data?.job, job.id);
    } catch (error) {
      if (signal?.aborted) throw abortError();
      throw failure({ code: "manual_job_status_unavailable",
        message: "حُفظت مهمة الإرسال، لكن تعذرت متابعة نتيجتها. إعادة فتح نفس الإجراء تتابع المهمة المحفوظة." });
    }
  }
}
