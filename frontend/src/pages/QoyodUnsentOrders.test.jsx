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

async function renderRecoveryPage(path = "/") {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.history.pushState({}, "", path);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<QoyodUnsentOrders />); });
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
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(confirm.disabled).toBe(true);
    expect(signals).toHaveLength(1);
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
    await act(async () => container.querySelector(`[data-testid="qoyod-failed-retry-${order}"]`).click());
    await act(async () => container.querySelector(`[data-testid="qoyod-failed-retry-confirm-${order}"]`).click());
    const notice = () => container.querySelector('[data-testid="qoyod-failed-retry-notice"]');
    expect(notice().getAttribute("role")).toBe("status");
    expect(notice().textContent).toContain("بانتظار التنفيذ");
    expect(notice().className).toContain("bg-sky-50");
    expect(container.querySelector(`[data-testid="qoyod-failed-retry-confirm-${order}"]`).disabled).toBe(true);
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

test("unmount aborts an in-flight bulk submission and a late acknowledgement cannot send the next order", async () => {
  const order = "990001101";
  api.get.mockResolvedValue({ data: { counts: { "لم يُرسل": 2 }, salla_status_counts: {},
    orders: [{ order_number: order, status: "لم يُرسل" }, { order_number: "990001102", status: "لم يُرسل" }] } });
  let acknowledge;
  api.post.mockImplementation(() => new Promise((resolve) => { acknowledge = resolve; }));
  const { container, root } = await renderRecoveryPage("/?recovery=1");
  await act(async () => container.querySelector('[data-testid="qoyod-recovery-send"]').click());
  expect(api.post).toHaveBeenCalledTimes(1);
  const request = api.post.mock.calls[0][2];
  await cleanup(container, root);
  expect(request.signal.aborted).toBe(true);
  await act(async () => { acknowledge({ data: { invoice_id: "late-invoice" } }); });
  expect(api.post).toHaveBeenCalledTimes(1);
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
    await act(async () => container.querySelector(`[data-testid="qoyod-failed-retry-${order}"]`).click());
    await act(async () => container.querySelector(`[data-testid="qoyod-failed-retry-confirm-${order}"]`).click());
    expect(api.post).toHaveBeenCalledTimes(1);
    const confirm = container.querySelector(`[data-testid="qoyod-failed-retry-confirm-${order}"]`);
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
    expect(toggle.textContent).toContain("إعادة إرسال الكل (0)");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).toBeNull();

    await act(async () => { toggle.click(); });

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]')).not.toBeNull();
    expect(toggle.textContent).toContain("إغلاق إعادة الإرسال");
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
    expect(toggle.textContent).toContain("إعادة إرسال الكل (2)");

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

test("unified backlog stays visible while bulk recovery is hidden", async () => {
  api.get.mockResolvedValue({
    data: {
      source_authority: "unified_orders",
      from_date: "2026-08-15",
      to_date: "2026-08-22",
      counts: { "لم يُرسل": 3, "فشل": 0, "مكرر": 0, "أُرسل": 0 },
      orders: [{
        order_number: "SYNTHETIC-UNSENT-001",
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
    expect(container.querySelector('[data-testid="unsent-row-SYNTHETIC-UNSENT-001"]')?.textContent)
      .toContain("2026-08-22");
    expect(container.querySelector('[data-testid="unsent-unified-readonly-note"]'))
      .not.toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-toggle"]'))
      .toBeNull();
    expect(container.querySelector('[data-testid="qoyod-recovery-panel"]'))
      .toBeNull();
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
