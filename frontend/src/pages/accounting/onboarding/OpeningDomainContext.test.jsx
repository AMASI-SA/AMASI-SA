import React, { act } from "react";
import { createRoot } from "react-dom/client";
import OpeningDomainContext from "./OpeningDomainContext";

let node, root, transport;
const ready = (key, data) => ({ key, label: key, status: "ready", data });
const response = sources => ({ sources });
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    transport = { getOnboardingDomainContext: jest.fn() };
});
afterEach(() => { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
async function show(stage) { await act(async () => root.render(<OpeningDomainContext stage={stage} transport={transport} />)); }
async function choose(value) { await act(async () => { const select = node.querySelector("select"); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, value); select.dispatchEvent(new Event("change", { bubbles: true })); }); }
async function click(text) { await act(async () => [...node.querySelectorAll("button")].find(b => b.textContent === text).click()); }

test("a failed financial source does not hide available operational couriers or invent identity", async () => {
    transport.getOnboardingDomainContext.mockResolvedValue(response([
        ready("couriers", { items: [{ courier_key: "imile", display_name: "iMile", payment_mode: "deferred", verification_status: "missing" }] }),
        { key: "native", label: "MZ2", status: "error", httpStatus: 403 },
    ]));
    await show("courier_contracts");
    expect(node.textContent).toContain("iMile"); expect(node.textContent).toContain("صلاحية قراءة هذا المصدر غير متاحة");
    expect(node.textContent).toContain("لم يُثبت مصدر الهويات");
    expect(node.querySelectorAll("select option")).toHaveLength(1);
    expect(node.querySelector("select").disabled).toBe(true);
    expect(node.textContent).toContain("تعذر تحميل مصدر هويات الكشف");
    expect(transport.getOnboardingDomainContext).toHaveBeenCalledTimes(1);
});

test("missing native courier key cannot become a selectable identity from an unrelated id", async () => {
    transport.getOnboardingDomainContext.mockResolvedValue(response([
        ready("native", { couriers: [{ id: "document-only", name: "اسم بلا هوية كشف" }], contracts: [] }),
    ]));
    await show("courier_balances");
    expect(node.querySelectorAll("select option")).toHaveLength(1);
    expect(node.querySelector("select").disabled).toBe(true);
    expect(node.textContent).toContain("لا توجد جهة ذات هوية صالحة لقراءة الكشف");
    await click("قراءة كشف الجهة المختارة");
    expect(transport.getOnboardingDomainContext).toHaveBeenCalledTimes(1);
});

test("operational and native names never establish a binding without equal exact keys", async () => {
    transport.getOnboardingDomainContext.mockResolvedValue(response([
        ready("couriers", { items: [{ courier_key: "operational-a", display_name: "Same name" }] }),
        ready("native", { couriers: [{ courier_key: "native-b", name: "Same name", status: "active" }], contracts: [] }),
    ]));
    await show("courier_balances");
    expect(node.textContent).toContain("غير مرتبطة بهوية موثقة");
    expect(node.textContent).toContain("عقد معتمد غير موجود");
});

test("driver details require selection and explicit read, preserving independent COD/cash/POS", async () => {
    transport.getOnboardingDomainContext.mockResolvedValueOnce(response([ready("drivers", { items: [{ id: "driver-a", name: "مندوب أ", delivery_fee: "20.00" }, { id: "driver-b", name: "مندوب ب" }] })]))
        .mockResolvedValueOnce(response([
            ready("statement", { cod_receivable: "150.00", payable: "20.00", collections: "0.00", payments: "0.00" }),
            ready("cash", { totals: { confirmed_cash: "140.00", variance: "-10.00" }, items: [], coverage: { complete: false } }),
            ready("history", { items: [{ id: "review", status: "approved", payment_method: "card_terminal", amount: "150.00", destination: { display_name: "ذمة POS", currency: "SAR" } }] }),
        ]));
    await show("drivers"); expect(transport.getOnboardingDomainContext).toHaveBeenCalledTimes(1);
    await choose("driver-a"); expect(transport.getOnboardingDomainContext).toHaveBeenCalledTimes(1);
    await click("قراءة كشف الجهة المختارة");
    expect(transport.getOnboardingDomainContext).toHaveBeenLastCalledWith("drivers", { driverId: "driver-a" });
    for (const text of ["150.00 SAR", "20.00 SAR", "140.00", "-10.00", "ذمة POS", "غير مكتملة", "لا يعني وصول المبلغ للبنك"]) expect(node.textContent).toContain(text);
    expect(node.textContent).not.toContain("130.00");
});

test("late detail response for previous selection cannot overwrite selected driver", async () => {
    let finish;
    transport.getOnboardingDomainContext.mockResolvedValueOnce(response([ready("drivers", { items: [{ id: "a", name: "أ" }, { id: "b", name: "ب" }] })]))
        .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await show("drivers"); await choose("a"); await click("قراءة كشف الجهة المختارة"); await choose("b");
    await act(async () => finish(response([ready("statement", { cod_receivable: "98765.00" })])));
    expect(node.textContent).not.toContain("98765.00");
});

test("empty native sources stay pending rather than asserting zero; deposit contract stays explicit", async () => {
    transport.getOnboardingDomainContext.mockResolvedValue(response([ready("facts", { items: [] }), ready("recurring", { items: [] })]));
    await show("obligations");
    expect(node.textContent).toContain("هذا لا يثبت رصيدًا صفريًا"); expect(node.textContent).toContain("لا يوجد هنا عقد تصنيف مستقل مثبت");
    expect(node.textContent).not.toContain("0.00");
});

test("typed facts retain separate side and currency without netting or operational accrual copy", async () => {
    transport.getOnboardingDomainContext.mockResolvedValue(response([
        ready("facts", { items: [{ id: "tax-in", category: "input_vat", display_name: "ضريبة مدخلات", amount: "100.00", currency: "SAR" }, { id: "tax-out", category: "sales_vat_payable", display_name: "ضريبة مبيعات", amount: "60.00", currency: "USD" }] }),
        ready("recurring", { items: [{ id: "rent", title: "إيجار", accrued_to_today: 999999.99 }] }),
    ]));
    await show("obligations");
    for (const value of ["100.00 SAR", "60.00 USD", "لنا", "علينا", "إيجار"]) expect(node.textContent).toContain(value);
    expect(node.textContent).not.toContain("40.00"); expect(node.textContent).not.toContain("999999.99");
});
