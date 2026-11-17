import api from "./api";
import { sendQoyodManualBatch, sendQoyodManualJob } from "./qoyodManualSendJobs";

jest.mock("./api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));

const BASE = "/integrations/qoyod/manual";
const storageKey = (order) => `qoyod-manual-job:${order}`;
const job = (order, status = "queued") => ({ id: `job/${order}`, order_number: order, status, poll_after_seconds: 5 });
const completed = (order) => ({ ...job(order, "succeeded"), result: { ok: true, invoice_id: `invoice-${order}` } });
const settle = (promise) => promise.then((value) => ({ value }), (error) => ({ error }));
async function flush() { for (let i = 0; i < 6; i += 1) await Promise.resolve(); }
async function advance(ms) { jest.advanceTimersByTime(ms); await flush(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  jest.useFakeTimers();
  api.get.mockReset(); api.post.mockReset();
  window.sessionStorage.clear();
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

test("queued and running statuses poll one durable job before exposing success", async () => {
  const order = "981000001";
  api.post.mockResolvedValue({ data: { job: job(order) } });
  api.get.mockResolvedValueOnce({ data: { job: job(order, "running") } })
    .mockResolvedValueOnce({ data: { job: completed(order) } });
  const onProgress = jest.fn();
  const request = sendQoyodManualJob(order, { onProgress });
  await flush();
  expect(onProgress).toHaveBeenLastCalledWith(job(order));
  expect(api.get).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem(storageKey(order))).toBeTruthy();
  await advance(5000);
  expect(onProgress).toHaveBeenLastCalledWith(job(order, "running"));
  await advance(5000);
  await expect(request).resolves.toEqual(completed(order).result);
  expect(onProgress.mock.calls.map(([value]) => value.status)).toEqual(["queued", "running", "succeeded"]);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenNthCalledWith(1, `${BASE}/jobs/job%2F${order}`,
    expect.objectContaining({ timeout: 10000 }));
  expect(window.sessionStorage.getItem(storageKey(order))).toBeNull();
});

test.each([undefined, 503])("uncertain submission (%s) retains its key for the next explicit attempt", async (status) => {
  const order = status ? "981000003" : "981000002";
  api.post.mockRejectedValueOnce(status ? { response: { status } } : { code: "ECONNABORTED" })
    .mockResolvedValueOnce({ data: { job: job(order) } });
  await expect(sendQoyodManualJob(order)).rejects.toMatchObject({
    response: { data: { detail: { code: "manual_job_submission_unknown" } } },
  });
  const key = api.post.mock.calls[0][2].headers["Idempotency-Key"];
  expect(window.sessionStorage.getItem(storageKey(order))).toBe(key);
  expect(api.post).toHaveBeenCalledTimes(1);
  await expect(sendQoyodManualJob(order, { retry: true, maxWaitMs: 0 })).rejects.toMatchObject({
    response: { data: { detail: { code: "manual_job_status_pending" } } },
  });
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(key);
});

test("status read failure retains the accepted request key and never resubmits automatically", async () => {
  const order = "981000004";
  api.post.mockResolvedValue({ data: { job: job(order) } });
  api.get.mockRejectedValue(new Error("synthetic network loss"));
  const request = settle(sendQoyodManualJob(order));
  await flush();
  const key = window.sessionStorage.getItem(storageKey(order));
  await advance(5000);
  expect((await request).error.response.data.detail.code).toBe("manual_job_status_unavailable");
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(window.sessionStorage.getItem(storageKey(order))).toBe(key);
  api.post.mockResolvedValueOnce({ data: { job: completed(order) } });
  await expect(sendQoyodManualJob(order)).resolves.toEqual(completed(order).result);
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(key);
});

test("needs-review preserves the original request across explicit retry clicks", async () => {
  const order = "981000005";
  const held = { ...job(order, "needs_review"), error: { code: "manual_job_outcome_unknown", message: "تحتاج النتيجة مراجعة" } };
  api.post.mockResolvedValue({ data: { job: held } });
  await expect(sendQoyodManualJob(order)).rejects.toMatchObject({ response: { data: { detail: held.error } } });
  const key = window.sessionStorage.getItem(storageKey(order));
  await expect(sendQoyodManualJob(order, { retry: true })).rejects.toMatchObject({ response: { data: { detail: held.error } } });
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(key);
  expect(api.get).not.toHaveBeenCalled();
});

test("a known refusal permits a fresh key only on a later explicit request", async () => {
  const order = "981000006";
  api.post.mockResolvedValueOnce({ data: { job: { ...job(order, "refused"),
    error: { code: "not_qoyod_eligible_status", message: "الحالة غير مؤهلة" } } } })
    .mockResolvedValueOnce({ data: { job: completed(order) } });
  await expect(sendQoyodManualJob(order)).rejects.toThrow("الحالة غير مؤهلة");
  expect(window.sessionStorage.getItem(storageKey(order))).toBeNull();
  expect(api.post).toHaveBeenCalledTimes(1);
  await sendQoyodManualJob(order, { retry: true });
  expect(api.post.mock.calls[1][2].headers["Idempotency-Key"])
    .not.toBe(api.post.mock.calls[0][2].headers["Idempotency-Key"]);
});

test("poll deadline is an unknown outcome, without a late status call or new submission", async () => {
  const order = "981000007";
  api.post.mockResolvedValue({ data: { job: job(order) } });
  const request = settle(sendQoyodManualJob(order, { maxWaitMs: 1000 }));
  await flush();
  const key = window.sessionStorage.getItem(storageKey(order));
  await advance(1000);
  expect((await request).error.response.data.detail.code).toBe("manual_job_status_pending");
  expect(api.get).not.toHaveBeenCalled();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(window.sessionStorage.getItem(storageKey(order))).toBe(key);
});

test("aborting the polling delay stops reads and keeps the durable request identity", async () => {
  const order = "981000008";
  const controller = new AbortController();
  api.post.mockResolvedValue({ data: { job: job(order) } });
  const request = settle(sendQoyodManualJob(order, { signal: controller.signal }));
  await flush();
  const key = window.sessionStorage.getItem(storageKey(order));
  controller.abort();
  await advance(10000);
  expect((await request).error.name).toBe("AbortError");
  expect(api.get).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem(storageKey(order))).toBe(key);
});

test("a late POST acknowledgement after abort cannot clear the original key or announce success", async () => {
  const order = "981000009";
  const controller = new AbortController(), response = deferred(), progress = jest.fn();
  api.post.mockReturnValue(response.promise);
  const request = settle(sendQoyodManualJob(order, { signal: controller.signal, onProgress: progress }));
  const key = window.sessionStorage.getItem(storageKey(order));
  controller.abort();
  response.resolve({ data: { invoice_id: "late-legacy-success" } });
  expect((await request).error.name).toBe("AbortError");
  expect(progress).not.toHaveBeenCalled();
  expect(api.get).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem(storageKey(order))).toBe(key);
});

test("a late status response after abort cannot publish completion", async () => {
  const order = "981000010";
  const controller = new AbortController(), response = deferred(), progress = jest.fn();
  api.post.mockResolvedValue({ data: { job: job(order) } });
  api.get.mockReturnValue(response.promise);
  const request = settle(sendQoyodManualJob(order, { signal: controller.signal, onProgress: progress }));
  await flush();
  await advance(5000);
  controller.abort();
  response.resolve({ data: { job: completed(order) } });
  expect((await request).error.name).toBe("AbortError");
  expect(progress.mock.calls.map(([value]) => value.status)).toEqual(["queued"]);
  expect(window.sessionStorage.getItem(storageKey(order))).toBeTruthy();
});

test.each([null, { id: "unknown", status: "invented" }])("invalid job response fails closed and retains the key: %p", async (invalid) => {
  const order = invalid ? "981000012" : "981000011";
  api.post.mockResolvedValue({ data: { job: invalid } });
  await expect(sendQoyodManualJob(order)).rejects.toMatchObject({
    response: { data: { detail: { code: "manual_job_status_unavailable" } } },
  });
  expect(window.sessionStorage.getItem(storageKey(order))).toBeTruthy();
  expect(api.get).not.toHaveBeenCalled();
});

test("the rolling-update legacy completion does not poll", async () => {
  const order = "981000013";
  const legacy = { ok: true, invoice_id: "legacy-invoice", invoice_only: true };
  api.post.mockResolvedValue({ data: legacy });
  await expect(sendQoyodManualJob(order)).resolves.toEqual(legacy);
  expect(api.get).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem(storageKey(order))).toBeNull();
});

test("bulk caps at 100 distinct valid orders and keeps the per-order retry route", async () => {
  const orders = Array.from({ length: 105 }, (_, index) => String(982000000 + index));
  api.post.mockImplementation((url) => Promise.resolve({ data: { invoice_id: `invoice-${url.split("/").pop()}` } }));
  const onResult = jest.fn();
  const results = await sendQoyodManualBatch([orders[0], "", "invalid", ...orders], {
    retryOrderNumbers: [orders[1]], onResult,
  });
  expect(api.post).toHaveBeenCalledTimes(100);
  expect(api.post.mock.calls.map(([url]) => url)).toEqual(orders.slice(0, 100)
    .map((order, index) => `${BASE}/${index === 1 ? "retry-failed" : "send"}/${order}`));
  expect(results.map((result) => result.orderNumber)).toEqual(orders.slice(0, 100));
  expect(results.every((result) => result.status === "fulfilled")).toBe(true);
  expect(onResult).toHaveBeenCalledTimes(100);
  expect(api.get).not.toHaveBeenCalled();
});

test("bulk bounds pending jobs to three, replenishes completed slots and reports in selection order", async () => {
  const orders = ["982001001", "982001002", "982001003", "982001004", "982001005"];
  const responses = new Map(orders.map((order) => [order, deferred()]));
  let active = 0, peak = 0;
  api.post.mockImplementation((url) => {
    const order = url.split("/").pop();
    active += 1;
    peak = Math.max(peak, active);
    return responses.get(order).promise.finally(() => { active -= 1; });
  });
  const onResult = jest.fn();
  const request = sendQoyodManualBatch(orders, { onResult });
  await flush();
  expect(api.post).toHaveBeenCalledTimes(3);
  responses.get(orders[1]).resolve({ data: { invoice_id: "second" } });
  await flush(); await flush();
  expect(api.post).toHaveBeenCalledTimes(4);
  expect(onResult.mock.calls[0][1].map((result) => result.orderNumber)).toEqual([orders[1]]);
  responses.get(orders[0]).resolve({ data: { invoice_id: "first" } });
  await flush(); await flush();
  expect(api.post).toHaveBeenCalledTimes(5);
  expect(onResult.mock.calls[1][1].map((result) => result.orderNumber)).toEqual(orders.slice(0, 2));
  responses.get(orders[2]).resolve({ data: { job: { ...job(orders[2], "refused"),
    error: { code: "customer_name_missing", message: "اسم العميل مفقود" } } } });
  responses.get(orders[3]).resolve({ data: { invoice_id: "fourth" } });
  responses.get(orders[4]).resolve({ data: { invoice_id: "fifth" } });
  const results = await request;
  expect(peak).toBe(3);
  expect(results.map((result) => result.orderNumber)).toEqual(orders);
  expect(results.map((result) => result.status)).toEqual([
    "fulfilled", "fulfilled", "rejected", "fulfilled", "fulfilled",
  ]);
});

test("aborting bulk observation retains accepted keys and never submits the remaining orders", async () => {
  const orders = ["982002001", "982002002", "982002003", "982002004"];
  api.post.mockImplementation((url) => Promise.resolve({ data: { job: job(url.split("/").pop()) } }));
  const controller = new AbortController(), onResult = jest.fn();
  const request = settle(sendQoyodManualBatch(orders, { signal: controller.signal, onResult }));
  await flush();
  expect(api.post).toHaveBeenCalledTimes(3);
  controller.abort();
  await advance(10000);
  expect((await request).error.name).toBe("AbortError");
  expect(api.post).toHaveBeenCalledTimes(3);
  expect(api.get).not.toHaveBeenCalled();
  expect(onResult).not.toHaveBeenCalled();
  expect(orders.slice(0, 3).every((order) => window.sessionStorage.getItem(storageKey(order)))).toBe(true);
  expect(window.sessionStorage.getItem(storageKey(orders[3]))).toBeNull();
});

test("an unknown batch result retains its original job identity while other orders finish", async () => {
  const orders = ["982003001", "982003002", "982003003", "982003004"];
  api.post.mockImplementation((url) => {
    const order = url.split("/").pop();
    return order === orders[0]
      ? Promise.reject({ response: { status: 503 } })
      : Promise.resolve({ data: { invoice_id: `invoice-${order}` } });
  });
  const results = await sendQoyodManualBatch(orders);
  expect(results[0]).toMatchObject({ orderNumber: orders[0], status: "rejected",
    reason: { response: { data: { detail: { code: "manual_job_submission_unknown" } } } } });
  expect(results.slice(1).every((result) => result.status === "fulfilled")).toBe(true);
  const key = api.post.mock.calls[0][2].headers["Idempotency-Key"];
  expect(window.sessionStorage.getItem(storageKey(orders[0]))).toBe(key);
  api.post.mockResolvedValueOnce({ data: { job: completed(orders[0]) } });
  await sendQoyodManualJob(orders[0]);
  expect(api.post.mock.calls[4][2].headers["Idempotency-Key"]).toBe(key);
});

test("feeding the same sequential worker ahead removes browser polling gaps from a 100-order fixture", async () => {
  // The provider still processes one order each second. Only queue submission
  // changes; both variants retain the real client's five-second status polling.
  jest.setSystemTime(0);
  let workerAvailableAt = 0;
  const completionAt = new Map();
  api.post.mockImplementation((url) => {
    const order = url.split("/").pop();
    workerAvailableAt = Math.max(Date.now(), workerAvailableAt) + 1000;
    completionAt.set(order, workerAvailableAt);
    return Promise.resolve({ data: { job: job(order) } });
  });
  api.get.mockImplementation((url) => {
    const order = decodeURIComponent(url.split("/").pop()).split("/").pop();
    return Promise.resolve({ data: { job: Date.now() >= completionAt.get(order)
      ? completed(order) : job(order, "running") } });
  });
  const serialOrders = Array.from({ length: 100 }, (_, index) => String(982004000 + index));
  let serialFinishedAt;
  const serial = (async () => {
    for (const order of serialOrders) await sendQoyodManualJob(order);
    serialFinishedAt = Date.now();
  })();
  await flush();
  for (let step = 0; step < 100; step += 1) await advance(5000);
  await serial;
  expect(serialFinishedAt).toBeDefined();
  const serialElapsed = serialFinishedAt;

  const batchOrders = serialOrders.map((order) => String(Number(order) + 100));
  const start = Date.now();
  let batchFinishedAt;
  const batch = sendQoyodManualBatch(batchOrders).then((results) => {
    batchFinishedAt = Date.now();
    return results;
  });
  await flush();
  for (let step = 0; step < 100 && batchFinishedAt === undefined; step += 1) await advance(5000);
  expect(await batch).toHaveLength(100);
  expect(serialElapsed).toBe(500000);
  expect(batchFinishedAt - start).toBe(170000);
  expect(api.post).toHaveBeenCalledTimes(200);
});
