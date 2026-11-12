import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PendingOrdersDialog, { distinctPendingOrders } from "./PendingOrdersDialog";

global.IS_REACT_ACT_ENVIRONMENT = true;
let host, root;
beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const details = count => Array.from({length: count}, (_, i) => ({order_id: `id-${i}`, order_number: `100${i}`,
    reasons: [{code: "date_missing", message: `تاريخ التوصيل غير مؤكد للطلب ${i}`}] }));
const click = async text => act(async () => [...document.querySelectorAll("button")].find(node => node.textContent.includes(text)).click());

test("clicking seven pending orders shows seven order numbers and reasons without hiding the known balance", async () => {
    await act(async () => root.render(<><span>الرصيد المحتسب 930.00</span><PendingOrdersDialog count={7} details={details(7)} name="المندوب" /></>));
    await click("7");
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(7);
    for (let i = 0; i < 7; i++) {
        expect(dialog.textContent).toContain(`100${i}`);
        expect(dialog.textContent).toContain(`تاريخ التوصيل غير مؤكد للطلب ${i}`);
    }
    expect(host.textContent).toContain("930.00");
    await click("إغلاق");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("duplicate reasons count one order and every page remains reachable", async () => {
    const all = details(21);
    all.push({...all[0], reasons: [{code: "fee_missing", message: "التعرفة غير مثبتة"}]});
    expect(distinctPendingOrders(all)).toHaveLength(21);
    await act(async () => root.render(<PendingOrdersDialog count={21} details={all} name="شركة الشحن" />));
    await click("21");
    let dialog = document.querySelector('[role="dialog"]');
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(10);
    expect(dialog.textContent).toContain("التعرفة غير مثبتة");
    await act(async () => dialog.querySelector('[aria-label="الصفحة التالية — الطلبات غير المؤكدة"]').click());
    await act(async () => dialog.querySelector('[aria-label="الصفحة التالية — الطلبات غير المؤكدة"]').click());
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(dialog.textContent).toContain("10020");
});

test("an unavailable order number remains an explicitly labeled identity", async () => {
    await act(async () => root.render(<PendingOrdersDialog count={1} details={[{order_id: "source-id", reasons: [{code: "identity", message: "رقم الطلب يحتاج مطابقة"}]}]} />));
    await click("1");
    expect(document.querySelector('[role="dialog"]').textContent).toContain("معرف الطلب: source-id");
});
