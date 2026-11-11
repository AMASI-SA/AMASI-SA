import api from "./api";
import { sendQoyodManualJob } from "./qoyodManualSendJobs";

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
