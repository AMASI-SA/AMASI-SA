import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
  get: jest.fn(),
  post: jest.fn(),
}));

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
  __resetQoyodUnsentOrdersClientForTests();
  api.get.mockResolvedValue({
    data: {
      counts: {},
      orders: [],
      salla_status_counts: {},
    },
  });
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
    );
    expect(api.post).toHaveBeenNthCalledWith(
      2,
      "/integrations/qoyod/manual/send/275633528",
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
      .toContain("بقي لم يُرسل — يحتاج مراجعة");
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
