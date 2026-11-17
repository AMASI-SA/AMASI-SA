import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
  get: jest.fn(),
  post: jest.fn(),
}));
// The recovery child has its own real DOM/API-boundary test. Keep these
// legacy order-table request assertions scoped to the order-table component.
jest.mock("../components/qoyod/Qoyod404Recovery", () => () => null);

import api from "../lib/api";
import { __resetQoyodUnsentOrdersClientForTests } from "../lib/qoyodUnsentOrdersClient";
import QoyodUnsentOrders from "./QoyodUnsentOrders";

async function renderRecoveryPage(path = "/", onRender) {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.history.pushState({}, "", path);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(onRender
    ? <React.Profiler id="qoyod-unsent-orders" onRender={onRender}><QoyodUnsentOrders /></React.Profiler>
    : <QoyodUnsentOrders />); });
  await act(async () => { await Promise.resolve(); });
  return { container, root };
}

async function cleanup(container, root) {
  await act(async () => root.unmount());
  container.remove();
  window.history.pushState({}, "", "/");
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}

beforeEach(() => {
  jest.clearAllMocks();
  api.get.mockReset();
  api.post.mockReset();
  __resetQoyodUnsentOrdersClientForTests();
  api.get.mockResolvedValue({
    data: {
      counts: {},
      orders: [],
      salla_status_counts: {},
    },
  });
});

test("an unsent unified order has a direct send action without a separate payment check", async () => {
  const order = "990009001";
  api.get.mockResolvedValue({ data: { source_authority: "unified_orders",
    counts: { "لم يُرسل": 1 }, salla_status_counts: {},
    orders: [{ order_number: order, status: "لم يُرسل", salla_status: "تم التنفيذ" }] } });
  api.post.mockResolvedValue({ data: { invoice_id: "synthetic-invoice" } });
  const { container, root } = await renderRecoveryPage();
  try {
    const send = container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`);
    expect(send).not.toBeNull();
    expect(send.textContent).toContain("إرسال إلى قيود");
    await act(async () => { send.click(); send.click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe(`/integrations/qoyod/manual/send/${order}`);
    expect(container.querySelector(`[data-testid="qoyod-send-result-${order}"]`).textContent)
      .toContain("تم الإرسال");
  } finally { await cleanup(container, root); }
});

test("bulk keeps three pending jobs ahead without waiting for the first terminal status", async () => {
  api.get.mockResolvedValue({ data: { source_authority: "unified_orders",
    counts: { "لم يُرسل": 6 }, salla_status_counts: {},
    orders: Array.from({ length: 6 }, (_, index) => ({
      order_number: String(990009100 + index), status: "لم يُرسل",
    })) } });
  const signals = [];
  api.post.mockImplementation((_url, _body, { signal }) => new Promise((_resolve, reject) => {
    signals.push(signal);
    signal.addEventListener("abort", () => reject({ code: "ERR_CANCELED" }), { once: true });
  }));
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const send = container.querySelector('[data-testid="qoyod-recovery-send"]');
    expect(send).not.toBeNull();
    await act(async () => { send.click(); send.click(); });
    expect(api.post).toHaveBeenCalledTimes(3);
  } finally { await cleanup(container, root); }
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(api.post).toHaveBeenCalledTimes(3);
});

test("only unsent and explicitly retryable failed rows expose send actions", async () => {
  api.get.mockResolvedValue({ data: { counts: {}, salla_status_counts: {}, orders: [
    { order_number: "990009301", status: "لم يُرسل", retry_allowed: true },
    { order_number: "990009302", status: "أُرسل", retry_allowed: true },
    { order_number: "990009303", status: "مكرر", retry_allowed: true },
    { order_number: "990009304", status: "فشل", retry_allowed: true },
    { order_number: "990009305", status: "فشل", retry_allowed: false },
  ] } });
  api.post.mockResolvedValue({ data: { invoice_id: "synthetic-recovered" } });
  const { container, root } = await renderRecoveryPage();
  try {
    await act(async () => container.querySelector('[data-testid="unsent-tab-الكل"]').click());
    expect(container.querySelector('[data-testid="qoyod-failed-retry-990009304"]')).not.toBeNull();
    for (const order of ["990009302", "990009303", "990009305"]) {
      expect(container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`)).toBeNull();
      expect(container.querySelector(`[data-testid="qoyod-failed-retry-${order}"]`)).toBeNull();
    }
    await act(async () => container.querySelector('[data-testid="qoyod-manual-send-990009301"]').click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/integrations/qoyod/manual/retry-failed/990009301");
  } finally { await cleanup(container, root); }
});

test("the bulk button sends at most 100 and leaves the next batch for another explicit click", async () => {
  const orders = Array.from({ length: 101 }, (_, index) => ({
    order_number: String(990010000 + index), status: "لم يُرسل", retry_allowed: index === 2,
  }));
  api.get.mockResolvedValue({ data: { source_authority: "unified_orders",
    counts: { "لم يُرسل": 101 }, salla_status_counts: {}, orders } });
  api.post.mockImplementation((url) => Promise.resolve({ data: { invoice_id: `invoice-${url.split("/").pop()}` } }));
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const send = () => container.querySelector('[data-testid="qoyod-recovery-send"]');
    expect(send().textContent).toContain("تأكيد وإرسال 100 طلب");
    expect(container.querySelector('[data-testid="qoyod-recovery-selection-count"]').textContent).toContain("الحد 100");
    await act(async () => send().click());
    expect(api.post).toHaveBeenCalledTimes(100);
    expect(api.post.mock.calls[2][0]).toBe("/integrations/qoyod/manual/retry-failed/990010002");
    expect(send().textContent).toContain("تأكيد وإرسال 1 طلب");
    expect(container.querySelector('[data-testid="qoyod-manual-send-990010000"]').disabled).toBe(true);
    await act(async () => send().click());
    expect(api.post).toHaveBeenCalledTimes(101);
    expect(api.post.mock.calls[100][0]).toBe("/integrations/qoyod/manual/send/990010100");
    expect(send().disabled).toBe(true);
  } finally { await cleanup(container, root); }
});

test("100 refused orders do not starve order 101, and correction retries remain explicit per row", async () => {
  const orders = Array.from({ length: 101 }, (_, index) => ({
    order_number: String(990011000 + index), status: "لم يُرسل",
  }));
  api.get.mockResolvedValue({ data: { source_authority: "unified_orders",
    counts: { "لم يُرسل": 101 }, salla_status_counts: {}, orders } });
  const attempted = new Set();
  api.post.mockImplementation((url) => {
    const order = url.split("/").pop();
    const firstAttempt = !attempted.has(order);
    attempted.add(order);
    return Promise.resolve({ data: firstAttempt && order !== "990011100"
      ? { job: { id: `refused-${order}`, order_number: order, status: "refused",
        error: { code: "customer_name_missing", message: "اسم العميل مفقود" } } }
      : { invoice_id: `invoice-${order}` } });
  });
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const bulk = () => container.querySelector('[data-testid="qoyod-recovery-send"]');
    await act(async () => bulk().click());
    expect(api.post).toHaveBeenCalledTimes(100);
    expect(bulk().textContent).toContain("تأكيد وإرسال 1 طلب");
    await act(async () => bulk().click());
    expect(api.post).toHaveBeenCalledTimes(101);
    expect(api.post.mock.calls[100][0]).toBe("/integrations/qoyod/manual/send/990011100");
    expect(bulk().disabled).toBe(true);
    const retry = () => container.querySelector('[data-testid="qoyod-manual-send-990011000"]');
    expect(retry().disabled).toBe(false);
    await act(async () => retry().click());
    expect(api.post).toHaveBeenCalledTimes(102);
    expect(api.post.mock.calls[101][0]).toBe("/integrations/qoyod/manual/send/990011000");
    expect(api.post.mock.calls[101][2].headers["Idempotency-Key"])
      .not.toBe(api.post.mock.calls[0][2].headers["Idempotency-Key"]);
    expect(retry().disabled).toBe(true);
    expect(container.querySelector('[data-testid="qoyod-send-result-990011000"]').textContent)
      .toContain("تم الإرسال");
  } finally { await cleanup(container, root); }
});

test("unknown bulk results stay out of the next batch and require explicit same-key row followup", async () => {
  const orders = ["990012001", "990012002", "990012003"];
  api.get.mockResolvedValue({ data: { counts: {}, salla_status_counts: {},
    orders: orders.map((order) => ({ order_number: order, status: "لم يُرسل" })) } });
  api.post.mockRejectedValueOnce({ response: { status: 503 } })
    .mockResolvedValueOnce({ data: { job: { id: "held-job", order_number: orders[1], status: "needs_review",
      error: { code: "manual_job_outcome_unknown", message: "يلزم التحقق من النتيجة" } } } })
    .mockResolvedValueOnce({ data: { invoice_id: "third-invoice" } });
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const bulk = () => container.querySelector('[data-testid="qoyod-recovery-send"]');
    await act(async () => bulk().click());
    expect(api.post).toHaveBeenCalledTimes(3);
    expect(bulk().disabled).toBe(true);
    await act(async () => bulk().click());
    expect(api.post).toHaveBeenCalledTimes(3);
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]').textContent)
      .toContain("لا تدخل الدفعة التالية تلقائيًا");
    for (const order of orders.slice(0, 2)) {
      expect(container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`).textContent)
        .toContain("متابعة نتيجة الإرسال");
    }
    api.post.mockResolvedValueOnce({ data: { invoice_id: "confirmed-first" } });
    await act(async () => container.querySelector(`[data-testid="qoyod-manual-send-${orders[0]}"]`).click());
    expect(api.post).toHaveBeenCalledTimes(4);
    expect(api.post.mock.calls[3][2].headers["Idempotency-Key"])
      .toBe(api.post.mock.calls[0][2].headers["Idempotency-Key"]);
    expect(window.sessionStorage.getItem(`qoyod-manual-job:${orders[1]}`))
      .toBe(api.post.mock.calls[1][2].headers["Idempotency-Key"]);
    expect(bulk().disabled).toBe(true);
  } finally { await cleanup(container, root); }
});

test("an uncertain single result shows the row explanation and resumes the same durable request", async () => {
  const order = "990009401";
  api.get.mockResolvedValue({ data: { counts: {}, salla_status_counts: {},
    orders: [{ order_number: order, status: "لم يُرسل" }] } });
  api.post.mockRejectedValueOnce({ response: { status: 503 } })
    .mockResolvedValueOnce({ data: { invoice_id: "confirmed-later" } });
  const { container, root } = await renderRecoveryPage();
  try {
    const send = () => container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`);
    await act(async () => send().click());
    expect(send().textContent).toContain("متابعة نتيجة الإرسال");
    expect(container.querySelector(`[data-testid="qoyod-send-result-${order}"]`).textContent)
      .toContain("قد يكون محفوظًا");
    expect(api.post).toHaveBeenCalledTimes(1);
    await act(async () => send().click());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][2].headers["Idempotency-Key"])
      .toBe(api.post.mock.calls[0][2].headers["Idempotency-Key"]);
  } finally { await cleanup(container, root); }
});

test.each(["bulk", "single"])("one synchronous send gate rejects rapid clicks and overlap when %s starts first", async (first) => {
  api.get.mockResolvedValue({ data: { counts: { "لم يُرسل": 2 }, salla_status_counts: {},
    orders: [
      { order_number: "SYN-OVERLAP-1", status: "فشل", retry_allowed: true },
      { order_number: "990000001", status: "لم يُرسل" },
      { order_number: "990000002", status: "لم يُرسل" },
    ] } });
  const signals = [];
  api.post.mockImplementation((_url, _body, { signal }) => new Promise((_resolve, reject) => {
    signals.push(signal);
    signal.addEventListener("abort", () => reject({ code: "ERR_CANCELED" }), { once: true });
  }));
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    // Open a row confirmation before either send starts; both handlers are
    // invoked before React can render their disabled state.
    const failedTab = container.querySelector('[data-testid="unsent-tab-فشل"]');
    await act(async () => failedTab.click());
    await act(async () => container.querySelector('[data-testid="qoyod-failed-retry-SYN-OVERLAP-1"]').click());
    const confirm = container.querySelector('[data-testid="qoyod-failed-retry-confirm-SYN-OVERLAP-1"]');
    const bulk = container.querySelector('[data-testid="qoyod-recovery-send"]');
    await act(async () => {
      if (first === "bulk") {
        bulk.click();
        bulk.click();
        confirm.click();
      } else {
        confirm.click();
        confirm.click();
        bulk.click();
      }
    });
    expect(api.post).toHaveBeenCalledTimes(first === "bulk" ? 2 : 1);
    expect(confirm.disabled).toBe(true);
    expect(signals).toHaveLength(first === "bulk" ? 2 : 1);
  } finally {
    await cleanup(container, root);
  }
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

test("accepted single send displays pending and running status before confirmed success", async () => {
  jest.useFakeTimers();
  const order = "990001001";
  const queued = { id: "status-job", order_number: order, status: "queued", poll_after_seconds: 5 };
  let polls = 0;
  api.get.mockImplementation((url) => Promise.resolve({ data: url.includes("/jobs/")
    ? { job: ++polls === 1 ? { ...queued, status: "running" }
      : { ...queued, status: "succeeded", result: { invoice_id: "confirmed-invoice" } } }
    : { counts: { "لم يُرسل": 1 }, salla_status_counts: {},
      orders: [{ order_number: order, status: "لم يُرسل", retry_allowed: true }] } }));
  api.post.mockResolvedValue({ data: { ok: true, job: queued } });
  const { container, root } = await renderRecoveryPage();
  try {
    await act(async () => container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`).click());
    const notice = () => container.querySelector('[data-testid="qoyod-failed-retry-notice"]');
    expect(notice().getAttribute("role")).toBe("status");
    expect(notice().textContent).toContain("بانتظار التنفيذ");
    expect(notice().className).toContain("bg-sky-50");
    expect(container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`).disabled).toBe(true);
    expect(container.querySelector(`[data-testid="qoyod-send-result-${order}"]`).textContent).toContain("بانتظار التنفيذ");
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(notice().textContent).toContain("جارٍ التحقق والإرسال في الخلفية");
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(notice().textContent).toContain("إلى قيود بنجاح");
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(polls).toBe(2);
  } finally {
    await cleanup(container, root);
    jest.clearAllTimers();
    jest.useRealTimers();
  }
});

test.each(["single", "bulk"])("unchanged %s progress polls do not keep committing page renders", async (mode) => {
  jest.useFakeTimers();
  const orders = mode === "bulk" ? ["990001051", "990001052", "990001053"] : ["990001050"];
  let status = "queued";
  let polls = 0;
  const job = (order) => ({ id: `render-job-${order}`, order_number: order,
    status, poll_after_seconds: 5 });
  api.get.mockImplementation((url) => {
    if (url.includes("/jobs/")) {
      polls += 1;
      return Promise.resolve({ data: { job: job(url.split("render-job-")[1]) } });
    }
    return Promise.resolve({ data: { source_authority: "unified_orders",
      counts: { "لم يُرسل": orders.length }, salla_status_counts: {},
      orders: orders.map((order) => ({ order_number: order, status: "لم يُرسل" })) } });
  });
  api.post.mockImplementation((url) => Promise.resolve({ data: { job: job(url.split("/").pop()) } }));
  const commits = jest.fn();
  const { container, root } = await renderRecoveryPage("/?recovery=1", commits);
  try {
    const selector = mode === "bulk" ? '[data-testid="qoyod-recovery-send"]'
      : `[data-testid="qoyod-manual-send-${orders[0]}"]`;
    await act(async () => container.querySelector(selector).click());
    const progress = () => container.querySelector(`[data-testid="qoyod-send-result-${orders[0]}"]`).textContent;
    expect(progress()).toContain("بانتظار التنفيذ");
    // React may perform one bailout render after a real state transition.
    // Continued unchanged polling must not keep committing the whole page.
    await act(async () => { jest.advanceTimersByTime(5000); });
    const queuedCommits = commits.mock.calls.length;
    for (let repeat = 0; repeat < 2; repeat += 1) {
      await act(async () => { jest.advanceTimersByTime(5000); });
    }
    expect(polls).toBe(3 * orders.length);
    expect(commits).toHaveBeenCalledTimes(queuedCommits);

    status = "running";
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(progress()).toContain("جارٍ التحقق والإرسال في الخلفية");
    expect(commits.mock.calls.length).toBeGreaterThan(queuedCommits);
    await act(async () => { jest.advanceTimersByTime(5000); });
    const runningCommits = commits.mock.calls.length;
    for (let repeat = 0; repeat < 2; repeat += 1) {
      await act(async () => { jest.advanceTimersByTime(5000); });
    }
    expect(polls).toBe(7 * orders.length);
    expect(commits).toHaveBeenCalledTimes(runningCommits);
    expect(api.post).toHaveBeenCalledTimes(orders.length);
  } finally {
    await cleanup(container, root);
    jest.clearAllTimers();
    jest.useRealTimers();
  }
});

test("unmount aborts an in-flight bulk submission and a late acknowledgement cannot send the next order", async () => {
  const order = "990001101";
  api.get.mockResolvedValue({ data: { counts: { "لم يُرسل": 4 }, salla_status_counts: {},
    orders: Array.from({ length: 4 }, (_, index) => ({ order_number: String(Number(order) + index), status: "لم يُرسل" })) } });
  const acknowledgements = [];
  api.post.mockImplementation(() => new Promise((resolve) => { acknowledgements.push(resolve); }));
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  await act(async () => container.querySelector('[data-testid="qoyod-recovery-send"]').click());
  expect(api.post).toHaveBeenCalledTimes(3);
  const request = api.post.mock.calls[0][2];
  await cleanup(container, root);
  expect(request.signal.aborted).toBe(true);
  await act(async () => { acknowledgements.forEach((acknowledge) => acknowledge({ data: { invoice_id: "late-invoice" } })); });
  expect(api.post).toHaveBeenCalledTimes(3);
  expect(window.sessionStorage.getItem(`qoyod-manual-job:${order}`)).toBe(request.headers["Idempotency-Key"]);
});

test("known refusal releases the page gate but a new send waits for a new explicit click", async () => {
  const order = "990001201";
  api.get.mockResolvedValue({ data: { counts: { "لم يُرسل": 1 }, salla_status_counts: {},
    orders: [{ order_number: order, status: "لم يُرسل", retry_allowed: true }] } });
  api.post.mockResolvedValueOnce({ data: { job: { id: "refused-job", order_number: order, status: "refused",
    error: { code: "not_qoyod_eligible_status", message: "الحالة غير مؤهلة" } } } })
    .mockResolvedValueOnce({ data: { invoice_id: "later-invoice" } });
  const { container, root } = await renderRecoveryPage();
  try {
    await act(async () => container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`).click());
    expect(api.post).toHaveBeenCalledTimes(1);
    const confirm = container.querySelector(`[data-testid="qoyod-manual-send-${order}"]`);
    expect(confirm.disabled).toBe(false);
    expect(container.querySelector('[data-testid="qoyod-failed-retry-notice"]').textContent).toContain("الحالة غير مؤهلة");
    await act(async () => confirm.click());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][2].headers["Idempotency-Key"])
      .not.toBe(api.post.mock.calls[0][2].headers["Idempotency-Key"]);
  } finally { await cleanup(container, root); }
});

test("stored provider failure distinguishes the operation without a retry or fresh probe", async () => {
  api.get.mockResolvedValue({ data: {
    source_authority: "unified_orders", counts: { "لم يُرسل": 1 },
    salla_status_counts: {},
    orders: [{ order_number: "100000099", status: "لم يُرسل", retry_allowed: true,
      reason: "استجابة غير ناجحة من قيود (404)",
      provider_failure: { operation: "البحث عن العميل", endpoint: "GET /customers",
        status_code: 404, observed_at: "2026-09-16T12:00:00+00:00" } }],
  } });
  const { container, root } = await renderRecoveryPage();
  try {
    const details = container.querySelector('[data-testid="qoyod-provider-failure-100000099"]');
    await act(async () => details.querySelector("summary").click());
    expect(details.textContent).toContain("البحث عن العميل");
    expect(details.textContent).toContain("GET /customers");
    expect(details.textContent).toContain("HTTP 404");
    expect(details.textContent).toContain("2026-09-16T12:00:00+00:00");
    expect(details.textContent).toContain("هذه نتيجة محفوظة");
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="qoyod-failed-retry-confirm-100000099"]')).toBeNull();
  } finally { await cleanup(container, root); }
});

test("recovery panel documents the closed three-status live gate", async () => {
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const panel = container.querySelector('[data-testid="qoyod-recovery-panel"]');
    expect(panel).not.toBeNull();
    expect(panel.textContent).toContain("تم التنفيذ");
    expect(panel.textContent).toContain("جاري التوصيل");
    expect(panel.textContent).toContain("تم التوصيل");
    expect(panel.textContent).toContain("تم التجهيز");
    expect(panel.textContent).toContain("completed");
    expect(panel.textContent).not.toContain("ولا يقبل إلا «تم التنفيذ»،");
  } finally {
    await cleanup(container, root);
  }
});

test("a quarantined order can be diagnosed without sending or checking payment", async () => {
  api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/diagnose/100000001")
    ? { ok: true, order_number: "100000001", within_tolerance: false,
      salla_total: 230, expected_qoyod_total: 225, difference: -5,
      canonical_summary: { cod_fee_amount: 5 },
      settings_used: { default_cod_fee_product_id: null },
      breakdown: { cod_fee: { included: false, reason: "بند الرسوم غير مربوط" } } }
    : { source_authority: "unified_orders", counts: { "لم يُرسل": 1 },
      orders: [{ order_number: "100000001", status: "لم يُرسل", retry_allowed: true }],
      salla_status_counts: {} } }));
  const { container, root } = await renderRecoveryPage();
  try {
    await act(async () => container.querySelector('[data-testid="qoyod-totals-diagnose-100000001"]').click());
    expect(container.querySelector('[data-testid="qoyod-totals-diagnosis"]').textContent).toContain("بند الرسوم غير مربوط");
    expect(api.get).toHaveBeenCalledWith("/integrations/qoyod/manual/diagnose/100000001");
    expect(api.post).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="qoyod-failed-retry-confirm-100000001"]')).toBeNull();
  } finally { await cleanup(container, root); }
});

test("visible recovery button opens the automatic bulk resend panel", async () => {
  const { container, root } = await renderRecoveryPage();
  try {
    const toggle = container.querySelector('[data-testid="qoyod-recovery-toggle"]');
    expect(toggle).not.toBeNull();
    expect(toggle.textContent).toContain("إرسال جماعي (0)");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).toBeNull();

    await act(async () => { toggle.click(); });

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).not.toBeNull();
    expect(toggle.textContent).toContain("إغلاق الإرسال الجماعي");
  } finally {
    await cleanup(container, root);
  }
});

test("bulk resend automatically selects every unsent order and requires no pasted numbers", async () => {
  api.get.mockResolvedValue({
    data: {
      counts: { "لم يُرسل": 2, "أُرسل": 1 },
      orders: [
        { order_number: "275623757", status: "لم يُرسل", salla_status: "تم التوصيل" },
        { order_number: "275633528", status: "لم يُرسل", salla_status: "تم التنفيذ" },
        { order_number: "275600000", status: "أُرسل", salla_status: "تم التنفيذ" },
      ],
      salla_status_counts: {},
    },
  });
  api.post
    .mockResolvedValueOnce({ data: { invoice_id: "q-1" } })
    .mockResolvedValueOnce({ data: { invoice_id: "q-2" } });

  const { container, root } = await renderRecoveryPage();
  try {
    const toggle = container.querySelector('[data-testid="qoyod-recovery-toggle"]');
    expect(toggle.textContent).toContain("إرسال جماعي (2)");

    await act(async () => { toggle.click(); });

    expect(container.querySelector('[data-testid="qoyod-recovery-orders"]')).toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-confirmation"]')).toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-selection-count"]')?.textContent)
      .toContain("سيتم إرسال 2 طلب إلى قيود تلقائيًا");

    const send = container.querySelector('[data-testid="qoyod-recovery-send"]');
    expect(send.textContent).toContain("تأكيد وإرسال 2 طلب");

    await act(async () => {
      send.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post).toHaveBeenNthCalledWith(
      1,
      "/integrations/qoyod/manual/send/275623757",
      undefined,
      expect.objectContaining({ headers: { "Idempotency-Key": expect.any(String) } }),
    );
    expect(api.post).toHaveBeenNthCalledWith(
      2,
      "/integrations/qoyod/manual/send/275633528",
      undefined,
      expect.objectContaining({ headers: { "Idempotency-Key": expect.any(String) } }),
    );
  } finally {
    await cleanup(container, root);
  }
});

test("one failed order does not stop the remaining automatic batch", async () => {
  api.get.mockResolvedValue({
    data: {
      counts: { "لم يُرسل": 2 },
      orders: [
        { order_number: "1001", status: "لم يُرسل", salla_status: "تم التنفيذ" },
        { order_number: "1002", status: "لم يُرسل", salla_status: "تم التنفيذ" },
      ],
      salla_status_counts: {},
    },
  });
  api.post
    .mockRejectedValueOnce({
      response: { data: { detail: { code: "unexpected_error", message: "temporary" } } },
    })
    .mockResolvedValueOnce({ data: { invoice_id: "q-1002" } });

  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    const send = container.querySelector('[data-testid="qoyod-recovery-send"]');
    await act(async () => {
      send.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.post).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-testid="qoyod-recovery-results"]')?.textContent)
      .toContain("نتيجة الإرسال تحتاج مراجعة");
    expect(container.querySelector('[data-testid="qoyod-recovery-results"]')?.textContent)
      .toContain("تم الإرسال");
  } finally {
    await cleanup(container, root);
  }
});

test("unified backlog exposes single and bulk sending without a preliminary payment-only pass", async () => {
  api.get.mockResolvedValue({
    data: {
      source_authority: "unified_orders",
      from_date: "2026-08-15",
      to_date: "2026-08-22",
      counts: { "لم يُرسل": 3, "فشل": 0, "مكرر": 0, "أُرسل": 0 },
      orders: [{
        order_number: "990009201",
        order_date: "2026-08-22",
        status: "لم يُرسل",
        salla_status: "تم التنفيذ",
        reason: "مؤهل من الطلبات الموحدة",
      }],
      salla_status_counts: { "تم التنفيذ": 3 },
    },
  });

  const { container, root } = await renderRecoveryPage("/?recovery=1");
  try {
    expect(container.querySelector('[data-testid="unsent-count-لم يُرسل"]')?.textContent)
      .toContain("3");
    expect(container.querySelector('[data-testid="unsent-row-990009201"]')?.textContent)
      .toContain("2026-08-22");
    expect(container.querySelector('[data-testid="unsent-unified-source-note"]'))
      .not.toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-toggle"]'))
      .not.toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]'))
      .not.toBeNull();
    expect(container.querySelector('[data-testid="qoyod-manual-send-990009201"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-selection-count"]').textContent)
      .toContain("سيتم إرسال 1 طلب");
    expect(api.post).not.toHaveBeenCalled();
  } finally {
    await cleanup(container, root);
  }
});

test("payment-only bulk check never calls a Qoyod send endpoint", async () => {
  api.get.mockResolvedValue({
    data: {
      source_authority: "unified_orders",
      counts: { "لم يُرسل": 2 },
      orders: [
        { order_number: "278100001", status: "لم يُرسل" },
        { order_number: "278100002", status: "فشل" },
      ],
      salla_status_counts: {},
    },
  });
  api.post.mockResolvedValue({
    data: {
      read_only: true,
      invoice_sent_count: 0,
      results: [{
        order_number: "278100001",
        outcome: "ready",
        message: "مؤهل ولم يتم الإرسال",
      }],
    },
  });

  const { container, root } = await renderRecoveryPage();
  try {
    const check = container.querySelector('[data-testid="qoyod-payment-recheck-bulk"]');
    expect(check.textContent).toContain("فحص الدفع فقط (2)");
    await act(async () => {
      check.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(
      "/integrations/qoyod/manual/recheck-payment-bulk",
      { order_numbers: ["278100001", "278100002"] },
    );
    expect(api.post.mock.calls[0][0]).not.toContain("/send/");
    expect(container.querySelector('[data-testid="qoyod-payment-recheck-results"]')?.textContent)
      .toContain("لم تُرسل أي فاتورة");
  } finally {
    await cleanup(container, root);
  }
});

test('missing snapshot shows unknown counts and never claims there are no pending orders', async () => {
  api.get.mockResolvedValueOnce({ data: { snapshot_available: false, orders: [], counts: { 'لم يُرسل': 0 } } });
  const { container, root } = await renderRecoveryPage();
  try {
    expect(container.querySelector('[data-testid="unsent-count-لم يُرسل"]').textContent).toContain('—');
    expect(container.querySelector('[data-testid="unsent-empty"]').textContent).toContain('لا يمكن تأكيد');
    expect(container.textContent).not.toContain('✅ لا توجد طلبات');
  } finally { await cleanup(container, root); }
});

test('failed initial read never renders successful empty results', async () => {
  api.get.mockRejectedValueOnce({ response: { data: { detail: 'qoyod_security_owner_required' } } });
  const { container, root } = await renderRecoveryPage();
  try {
    expect(container.querySelector('[data-testid="unsent-error"]').textContent).toContain('ربط مالك التكامل');
    expect(container.querySelector('[data-testid="unsent-count-لم يُرسل"]').textContent).toContain('—');
    expect(container.textContent).not.toContain('✅ لا توجد طلبات');
  } finally { await cleanup(container, root); }
});
