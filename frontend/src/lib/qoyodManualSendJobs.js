import api from "./api";

const BASE = "/integrations/qoyod/manual";
export const QOYOD_MANUAL_BATCH_LIMIT = 100;
const ACTIVE_BATCH_JOBS = 3;
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

// Keep a small queue ahead of the server's sequential accounting worker. The
// next submission need not wait for the browser to observe every prior result.
// Each order retains the same durable job, refusal and unknown-outcome rules.
export async function sendQoyodManualBatch(orderNumbers, {
  signal, onProgress, onResult, maxWaitMs, retryOrderNumbers = [],
} = {}) {
  if (signal?.aborted) throw abortError();
  const orders = Array.from(new Set(orderNumbers
    .map((order) => String(order || "").trim())
    .filter((order) => /^\d+$/.test(order))))
    .slice(0, QOYOD_MANUAL_BATCH_LIMIT);
  const results = new Array(orders.length);
  const retryOrders = new Set(retryOrderNumbers.map((order) => String(order).trim()));
  let nextIndex = 0;
  async function observeNext() {
    while (!signal?.aborted && nextIndex < orders.length) {
      const index = nextIndex++;
      const orderNumber = orders[index];
      let result;
      try {
        const value = await sendQoyodManualJob(orderNumber, {
          retry: retryOrders.has(orderNumber), signal, onProgress, maxWaitMs,
        });
        result = { orderNumber, status: "fulfilled", value };
      } catch (reason) {
        if (signal?.aborted) return;
        result = { orderNumber, status: "rejected", reason };
      }
      if (signal?.aborted) return;
      results[index] = result;
      onResult?.(result, results.filter(Boolean));
    }
  }
  await Promise.all(Array.from({ length: Math.min(ACTIVE_BATCH_JOBS, orders.length) }, observeNext));
  if (signal?.aborted) throw abortError();
  return results;
}
