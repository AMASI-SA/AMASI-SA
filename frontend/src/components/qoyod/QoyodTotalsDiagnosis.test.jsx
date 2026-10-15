import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import QoyodTotalsDiagnosis from "./QoyodTotalsDiagnosis";

jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));

const blocked = {
  ok: true, order_number: "100000001", within_tolerance: false,
  salla_total: 230, expected_qoyod_total: 225, difference: -5,
  canonical_summary: { cod_fee_amount: 5 },
  settings_used: { default_cod_fee_product_id: null },
  breakdown: { cod_fee: { included: false, salla_declared_amount: 5,
    reason: "لم يُربط بند رسوم الدفع عند الاستلام" } },
};

beforeEach(() => { jest.clearAllMocks(); });

async function render(orderNumber = "100000001") {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<QoyodTotalsDiagnosis orderNumber={orderNumber} onClose={() => {}} />));
  return { container, root };
}

async function cleanup(container, root) {
  await act(async () => root.unmount());
  container.remove();
  globalThis.IS_REACT_ACT_ENVIRONMENT = false;
}

test("explains a missing five-riyal fee using a read-only request", async () => {
  api.get.mockResolvedValue({ data: blocked });
  const { container, root } = await render();
  try {
    expect(api.get).toHaveBeenCalledWith("/integrations/qoyod/manual/diagnose/100000001");
    expect(container.textContent).toContain("230.00");
    expect(container.textContent).toContain("225.00");
    expect(container.textContent).toContain("-5.00");
    expect(container.textContent).toContain("لم يُربط بند رسوم الدفع عند الاستلام");
    expect(container.textContent).toContain("غير مربوط");
    expect(api.post).not.toHaveBeenCalled();
    expect(api.put).not.toHaveBeenCalled();
  } finally { await cleanup(container, root); }
});

test("a passing calculation does not claim invoice delivery or unlock a send", async () => {
  api.get.mockResolvedValue({ data: { ...blocked, within_tolerance: true,
    expected_qoyod_total: 230, difference: 0,
    settings_used: { default_cod_fee_product_id: "700" },
    breakdown: { cod_fee: { included: true, salla_declared_amount: 5,
      qoyod_gross_after_tax: 5 } } } });
  const { container, root } = await render();
  try {
    expect(container.textContent).toContain("المبلغ مطابق");
    expect(container.textContent).toContain("قراءة فقط");
    expect(container.textContent).not.toContain("تم الإرسال");
    expect(container.querySelectorAll("button")).toHaveLength(1);
    expect(api.post).not.toHaveBeenCalled();
  } finally { await cleanup(container, root); }
});

test("failed diagnosis never renders missing monetary evidence as zero", async () => {
  api.get.mockRejectedValue({ response: { data: { detail: { message: "تعذر قراءة التشخيص" } } } });
  const { container, root } = await render();
  try {
    expect(container.textContent).toContain("تعذر قراءة التشخيص");
    expect(container.textContent).not.toContain("0.00");
    expect(container.querySelector("table")).toBeNull();
  } finally { await cleanup(container, root); }
});

test("late results from a previous order cannot overwrite the selected order", async () => {
  let finishFirst;
  api.get.mockImplementation((url) => url.endsWith("100000001")
    ? new Promise((resolve) => { finishFirst = resolve; })
    : Promise.resolve({ data: { ...blocked, order_number: "100000002", salla_total: 90 } }));
  const { container, root } = await render();
  try {
    await act(async () => root.render(<QoyodTotalsDiagnosis orderNumber="100000002" onClose={() => {}} />));
    await act(async () => finishFirst({ data: blocked }));
    expect(container.textContent).toContain("100000002");
    expect(container.textContent).toContain("90.00");
    expect(container.textContent).not.toContain("230.00");
  } finally { await cleanup(container, root); }
});
