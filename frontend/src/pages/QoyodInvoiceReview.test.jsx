import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("../lib/api", () => ({
  get: jest.fn(),
  post: jest.fn(),
}));
jest.mock("../lib/dates", () => ({
  todaySA: () => "2026-08-12",
}));
jest.mock("sonner", () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

import api from "../lib/api";
import QoyodInvoiceReview from "./QoyodInvoiceReview";

const payload = {
  page: 1,
  page_size: 15,
  total: 1,
  pages: 1,
  last_sync_at: "2026-08-12T12:09:47Z",
  summary: {
    eligible_salla_orders: 1373,
    eligible_with_qoyod_invoice: 1090,
    eligible_without_qoyod_invoice: 283,
    qoyod_invoices: 1160,
    qoyod_distinct_references: 1159,
    qoyod_outside_eligible: 69,
  },
  items: [{
    qoyod_invoice_id: "1363",
    invoice_number: "1363",
    reference: "277274465",
    customer_name: "saud abuhabsha",
    issue_date: "2026-08-12",
    due_date: "2026-08-12",
    currency: "SAR",
    total: 170.83,
    paid_amount: 170.83,
    remaining: 0,
    status: "paid",
    exact_reference_match: true,
    salla_status: "تم التنفيذ",
  }],
};

async function renderPage() {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<QoyodInvoiceReview />); });
  await act(async () => { await Promise.resolve(); });
  return { container, root };
}

async function cleanup(container, root) {
  await act(async () => root.unmount());
  container.remove();
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}

beforeEach(() => {
  jest.clearAllMocks();
  api.get.mockResolvedValue({ data: payload });
  api.post.mockResolvedValue({
    data: { sync_summary: {
      ok: true,
      ran: true,
      fetched: 1279,
      in_scope: 1160,
      finished_at: "2026-08-12T12:15:00Z",
    } },
  });
});

test("loads the local Qoyod mirror from 2026-07-01 and renders the requested report", async () => {
  const { container, root } = await renderPage();
  try {
    expect(api.get).toHaveBeenCalledWith(
      "/integrations/qoyod/invoice-review",
      { params: {
        from_date: "2026-07-01",
        to_date: "2026-08-12",
        search: undefined,
        page: 1,
        page_size: 15,
      } },
    );
    expect(container.textContent).toContain("طلبات سلة المؤهلة");
    expect(container.textContent).toContain("1373");
    expect(container.textContent).toContain("الموجودة في قيود");
    expect(container.querySelector('[data-testid="qoyod-summary-found-value"]').textContent).toBe("1090");
    expect(container.textContent).toContain("غير الموجودة في قيود");
    expect(container.querySelector('[data-testid="qoyod-summary-missing-value"]').textContent).toBe("283");
    expect(container.textContent).toContain("فواتير قيود");
    expect(container.textContent).toContain("1160");
    expect(container.textContent).toContain("خارج النطاق المؤهل: 69");
    expect(container.textContent).toContain("المراجع الفريدة: 1159");
    expect(container.textContent).toContain("ناتج مطابقة فعلية، وليس طرح إجمالي الفواتير");
    expect(container.textContent).toContain("277274465");
    expect(container.textContent).toContain("170.83 SAR");
    expect(container.textContent).toContain("مطابق برقم الطلب");
    expect(container.textContent).toContain("لا تدخل ضمن شروط الإرسال");
  } finally {
    await cleanup(container, root);
  }
});

test("does not invent comparable counts from legacy raw totals", async () => {
  api.get.mockResolvedValueOnce({
    data: {
      ...payload,
      summary: {
        eligible_salla_orders: 1373,
        qoyod_invoices: 1160,
      },
    },
  });
  const { container, root } = await renderPage();
  try {
    expect(container.querySelector('[data-testid="qoyod-summary-eligible-value"]').textContent).toBe("1373");
    expect(container.querySelector('[data-testid="qoyod-summary-invoices-value"]').textContent).toBe("1160");
    expect(container.querySelector('[data-testid="qoyod-summary-found-value"]').textContent).toBe("—");
    expect(container.querySelector('[data-testid="qoyod-summary-missing-value"]').textContent).toBe("—");
    expect(container.textContent).toContain("ليس طرح إجمالي الفواتير من إجمالي الطلبات");
  } finally {
    await cleanup(container, root);
  }
});

test("refresh uses the read-only Qoyod sync endpoint and never exposes send controls", async () => {
  const { container, root } = await renderPage();
  try {
    const refresh = container.querySelector('[data-testid="qoyod-invoice-sync"]');
    await act(async () => refresh.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(api.post).toHaveBeenCalledWith("/integrations/qoyod/invoice-review/sync");
    expect(container.textContent).toContain("تم جلب 1279 فاتورة");
    expect(container.textContent).toContain("1160 منذ 2026-07-01");
    expect(container.textContent).not.toContain("إرسال الدفعة");
    expect(container.textContent).not.toContain("إرسال إلى قيود");
  } finally {
    await cleanup(container, root);
  }
});

test("clears stale rows when a filtered request fails", async () => {
  const { container, root } = await renderPage();
  try {
    expect(container.textContent).toContain("277274465");
    api.get.mockRejectedValueOnce(new Error("network unavailable"));
    const search = container.querySelector('[data-testid="qoyod-invoice-search"]');
    await act(async () => {
      search.value = "999999999";
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const submit = container.querySelector('[data-testid="qoyod-invoice-search-submit"]');
    await act(async () => submit.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.textContent).not.toContain("277274465");
    expect(container.textContent).toContain("network unavailable");
    expect(container.textContent).not.toContain("لا توجد فاتورة مطابقة");
  } finally {
    await cleanup(container, root);
  }
});

test("does not infer zero or SAR when Qoyod omitted accounting fields", async () => {
  api.get.mockResolvedValueOnce({
    data: {
      ...payload,
      items: [{
        ...payload.items[0],
        total: null,
        paid_amount: null,
        remaining: null,
        currency: null,
      }],
    },
  });
  const { container, root } = await renderPage();
  try {
    const row = container.querySelector('[data-testid="qoyod-invoice-row-1363"]');
    expect(row.textContent).not.toContain("0.00");
    expect(row.textContent).not.toContain("SAR");
  } finally {
    await cleanup(container, root);
  }
});

test("exports the selected date range through the isolated Excel endpoint", async () => {
  const originalCreateObjectURL = window.URL.createObjectURL;
  const originalRevokeObjectURL = window.URL.revokeObjectURL;
  window.URL.createObjectURL = jest.fn(() => "blob:qoyod-invoices");
  window.URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  api.get.mockImplementation((url) => Promise.resolve({
    data: url.endsWith("/export.xlsx") ? "xlsx-bytes" : payload,
  }));

  const { container, root } = await renderPage();
  try {
    const exportButton = container.querySelector('[data-testid="qoyod-invoice-export"]');
    await act(async () => exportButton.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(api.get).toHaveBeenCalledWith(
      "/integrations/qoyod/invoice-review/export.xlsx",
      {
        params: {
          from_date: "2026-07-01",
          to_date: "2026-08-12",
          search: undefined,
        },
        responseType: "blob",
      },
    );
    expect(window.URL.createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
  } finally {
    await cleanup(container, root);
    click.mockRestore();
    window.URL.createObjectURL = originalCreateObjectURL;
    window.URL.revokeObjectURL = originalRevokeObjectURL;
  }
});

test("source is isolated from Qoyod write and manual-send routes", () => {
  const fs = require("fs");
  const source = fs.readFileSync(__dirname + "/QoyodInvoiceReview.jsx", "utf8");
  expect(source).not.toMatch(/manual\/send|pipeline\/|auto_send|repair-recon/);
  expect(source).toContain("`${QOYOD_BASE}/sync`");
  expect(source).toContain("`${QOYOD_BASE}/export.xlsx`");
});
