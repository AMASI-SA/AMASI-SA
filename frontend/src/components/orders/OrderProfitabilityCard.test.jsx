import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import OrderProfitabilityCard from "./OrderProfitabilityCard";

jest.mock("../../lib/api", () => ({ get: jest.fn() }));
let host, root;
const data = { product_cost_sar: 24.2, known_product_cost_sar: 24.2, shipping_cost_sar: 17.25,
    payment_fees_sar: 3.55, direct_cost_sar: 45, known_direct_cost_sar: 45,
    profit_before_advertising_sar: 55, advertising_cost_sar: null, reasons: [] };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset();
    host = document.createElement("div"); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); delete global.IntersectionObserver; });
const render = async (number = "123") => act(async () => { root.render(<OrderProfitabilityCard orderNumber={number} />); });

test("missing ad allocation is explicit and Qoyod is never requested", async () => {
    api.get.mockResolvedValue({ data });
    await render();
    expect(host.textContent).toContain("تكلفة الطلب قبل الترويج");
    expect(host.textContent).toContain("55.00 ر.س");
    expect(host.textContent).toContain("لم تُفترض صفرًا");
    expect(host.textContent).not.toContain("قيود");
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/orders-v2/123/profitability");
});

test("incomplete choices show known costs without a fabricated profit", async () => {
    api.get.mockResolvedValue({ data: { ...data, product_cost_sar: null, direct_cost_sar: null,
        profit_before_advertising_sar: null, reasons: ["option_selection_unverified"] } });
    await render();
    expect(host.textContent).toContain("المعروف 45.00 ر.س — الإجمالي غير مكتمل");
    expect(host.textContent).toContain("اختيار عميل غير متحقق");
    expect(host.textContent).not.toContain("55.00");
});

test("saved promotion allocation shows loss after promotion", async () => {
    api.get.mockResolvedValue({ data: { ...data, advertising_cost_sar: 80, total_cost_sar: 125, profit_sar: -25 } });
    await render();
    expect(host.textContent).toContain("تكلفة الطلب مع الترويج");
    expect(host.textContent).toContain("125.00 ر.س");
    expect(host.textContent).toContain("-25.00 ر.س");
    expect(host.textContent).toContain("توزيع محفوظ على الطلب");
});

test("failure allows a manual retry", async () => {
    api.get.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data });
    await render();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    await act(async () => host.querySelector("button").click());
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.textContent).toContain("55.00 ر.س");
});

test("a late response for a previous order cannot replace the current order", async () => {
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
        .mockResolvedValueOnce({ data: { ...data, profit_before_advertising_sar: 77 } });
    await render("old");
    const signal = api.get.mock.calls[0][1].signal;
    await render("new");
    await act(async () => resolveOld({ data }));
    expect(signal.aborted).toBe(true);
    expect(host.textContent).toContain("77.00 ر.س");
    expect(host.textContent).not.toContain("55.00 ر.س");
});

test("loading waits until the card approaches the viewport", async () => {
    let observe;
    global.IntersectionObserver = class {
        constructor(callback) { observe = callback; }
        observe() {} disconnect() {}
    };
    api.get.mockResolvedValue({ data });
    await render();
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => observe([{ isIntersecting: true }]));
    expect(api.get).toHaveBeenCalledTimes(1);
});
