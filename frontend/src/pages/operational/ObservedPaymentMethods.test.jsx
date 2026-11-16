import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import ObservedPaymentMethods from "./ObservedPaymentMethods";
import { saveObservedPaymentMethodCommission } from "../../services/accountingOnboarding";

jest.mock("../../services/accountingOnboarding", () => ({ saveObservedPaymentMethodCommission: jest.fn() }));
const pending = { key: "paypal-hash", name: "PayPal / دولي", status: "needs_commission",
    commission_percent: null, fixed_fee: null, vat_percent: null };
const second = { key: "another-hash", name: "طريقة أخرى", status: "needs_commission",
    commission_percent: null, fixed_fee: null, vat_percent: null };
const directory = (items = [pending]) => ({ items, complete: true,
    pending_count: items.filter(method => method.status === "needs_commission").length, revision: "a".repeat(64) });
let host, root;
function Page({ setup }) {
    const [current, setCurrent] = useState(setup);
    return <ObservedPaymentMethods setup={current} onSaved={setCurrent} />;
}
const input = label => host.querySelector(`input[aria-label="${label}"]`);
const render = async setup => act(async () => root.render(<Page setup={setup} />));
const click = async label => act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === label).click());
async function set(label, value) {
    const field = input(label);
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(field, value);
        field.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function fill(percentage = "0", fixed = "0", vat = "0") {
    await set("نسبة العمولة %", percentage); await set("الرسم الثابت بالريال", fixed); await set("ضريبة العمولة %", vat);
}
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    window.history.replaceState({}, "", "/operational-balances/provider-fees");
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); window.history.replaceState({}, "", "/"); });

test("pending methods are automatically listed without inventing zero fees or making a write", async () => {
    await render(directory());
    expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(host.textContent).toContain(pending.name);
    expect(host.textContent).toContain("العمولة غير محددة");
    expect([...host.querySelectorAll("tbody td")].slice(1, 4).map(cell => cell.textContent)).toEqual(["—", "—", "—"]);
    expect(host.querySelector("form")).toBeNull();
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test("the dashboard deep link opens and focuses the exact raw method with blank inputs", async () => {
    window.history.replaceState({}, "", `/operational-balances/provider-fees?method=${encodeURIComponent(pending.name)}`);
    await render(directory([second, pending]));
    expect(host.querySelector("form").getAttribute("aria-label")).toBe(`عمولة ${pending.name}`);
    expect(document.activeElement).toBe(input("نسبة العمولة %"));
    for (const label of ["نسبة العمولة %", "الرسم الثابت بالريال", "ضريبة العمولة %"]) expect(input(label).value).toBe("");
    expect(host.querySelector("form").checkValidity()).toBe(false);
    await click("حفظ العمولة");
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test("an ambiguous method stays locked while an unrelated PayPal deep link can save explicit fees", async () => {
    const ambiguous = { ...second, name: "Visa Card", editable: false };
    const paypal = { ...pending, name: "paypal", editable: true };
    const updated = { ...directory([ambiguous, { ...paypal, status: "configured",
        commission_percent: 2.5, fixed_fee: 1.25, vat_percent: 15 }]), revision: "b".repeat(64) };
    let confirmSave;
    saveObservedPaymentMethodCommission.mockImplementation(() => new Promise(resolve => { confirmSave = resolve; }));
    window.history.replaceState({}, "", "/operational-balances/provider-fees?method=paypal");
    await render(directory([ambiguous, paypal]));
    const ambiguousRow = host.querySelectorAll("tbody tr")[0];
    expect(ambiguousRow.textContent).toContain("تحتاج مراجعة إعداداتها");
    expect(ambiguousRow.querySelector("button")).toBeNull();
    expect([...ambiguousRow.querySelectorAll("td")].slice(1, 4).map(cell => cell.textContent)).toEqual(["—", "—", "—"]);
    expect(host.querySelector("[role=alert]")).toBeNull();
    expect(host.querySelector("form").getAttribute("aria-label")).toBe("عمولة paypal");
    expect(document.activeElement).toBe(input("نسبة العمولة %"));
    for (const label of ["نسبة العمولة %", "الرسم الثابت بالريال", "ضريبة العمولة %"]) expect(input(label).value).toBe("");
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();

    await fill("2.5", "1.25", "15");
    await click("حفظ العمولة");
    expect(saveObservedPaymentMethodCommission).toHaveBeenCalledTimes(1);
    expect(saveObservedPaymentMethodCommission).toHaveBeenCalledWith({ name: "paypal",
        commission_percent: 2.5, fixed_fee: 1.25, vat_percent: 15, expected_revision: "a".repeat(64) });
    expect(host.querySelectorAll("tbody tr")[1].textContent).toContain("العمولة غير محددة");
    expect(host.querySelector("[role=status]")).toBeNull();
    await act(async () => confirmSave({ payment_method_setup: updated }));

    const rows = host.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("تحتاج مراجعة إعداداتها");
    expect(rows[0].querySelector("button")).toBeNull();
    expect([...rows[0].querySelectorAll("td")].slice(1, 4).map(cell => cell.textContent)).toEqual(["—", "—", "—"]);
    expect(rows[1].textContent).toContain("مكتملة");
    expect([...rows[1].querySelectorAll("td")].slice(1, 4).map(cell => cell.textContent)).toEqual(["2.5", "1.25", "15"]);
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector("[role=status]").textContent).toContain("حُفظت عمولة paypal");
});

test("explicit zero is saved once and authoritative results retain the configured row and other pending methods", async () => {
    const updated = { ...directory([{ ...pending, status: "configured", commission_percent: 0, fixed_fee: 0, vat_percent: 0 }, second]), revision: "b".repeat(64) };
    saveObservedPaymentMethodCommission.mockResolvedValue({ payment_method_setup: updated });
    await render(directory([pending, second]));
    await click("إضافة العمولة"); await fill();
    expect(host.querySelector("form").checkValidity()).toBe(true);
    await click("حفظ العمولة");
    expect(saveObservedPaymentMethodCommission).toHaveBeenCalledTimes(1);
    expect(saveObservedPaymentMethodCommission).toHaveBeenCalledWith({ name: pending.name,
        commission_percent: 0, fixed_fee: 0, vat_percent: 0, expected_revision: "a".repeat(64) });
    const rows = host.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("مكتملة");
    expect(rows[1].textContent).toContain("العمولة غير محددة");
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector("[role=status]").textContent).toContain(`حُفظت عمولة ${pending.name}`);
});

test("a failed save keeps the pending row and all entered values", async () => {
    saveObservedPaymentMethodCommission.mockRejectedValue(new Error("private server detail"));
    await render(directory()); await click("إضافة العمولة"); await fill("2.5", "1", "15");
    await click("حفظ العمولة");
    expect(host.querySelector("tbody").textContent).toContain("العمولة غير محددة");
    expect(input("نسبة العمولة %").value).toBe("2.5");
    expect(input("الرسم الثابت بالريال").value).toBe("1");
    expect(input("ضريبة العمولة %").value).toBe("15");
    expect(host.querySelector("[role=alert]").textContent).toContain("احتفظنا بمدخلاتك");
    expect(host.textContent).not.toContain("private server detail");
    expect(host.querySelector("button[type=submit]").disabled).toBe(false);
});

test("a conflicting revision cannot silently adopt newer fees", async () => {
    saveObservedPaymentMethodCommission.mockRejectedValue({ response: { status: 409 } });
    await render(directory()); await click("إضافة العمولة"); await fill("3", "1", "15");
    await click("حفظ العمولة");
    expect(host.querySelector("[role=alert]").textContent).toContain("تغيّرت إعدادات طرق الدفع");
    expect(input("نسبة العمولة %").value).toBe("3");
    expect(host.querySelector("tbody").textContent).toContain("العمولة غير محددة");
});

test("an unchanged or malformed save response does not clear the pending requirement", async () => {
    saveObservedPaymentMethodCommission.mockResolvedValue({ payment_method_setup: directory() });
    await render(directory()); await click("إضافة العمولة"); await fill(); await click("حفظ العمولة");
    expect(host.querySelector("[role=alert]")).not.toBeNull();
    expect(host.querySelector("tbody").textContent).toContain("العمولة غير محددة");
    expect(host.querySelector("form")).not.toBeNull();
});

test("an incomplete directory is visible and cannot authorize editing", async () => {
    await render({ ...directory(), complete: false });
    expect(host.querySelector("[role=alert]").textContent).toContain("تعذر التحقق");
    expect(host.querySelector("tbody button").disabled).toBe(true);
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test("configured values including zero can be reviewed without another read or save", async () => {
    await render(directory([{ ...pending, status: "configured", commission_percent: 2.5, fixed_fee: 0, vat_percent: 15 }]));
    await click("تعديل العمولة");
    expect(input("نسبة العمولة %").value).toBe("2.5");
    expect(input("الرسم الثابت بالريال").value).toBe("0");
    expect(input("ضريبة العمولة %").value).toBe("15");
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test.each([
    ["configured", "تستخدم العمولة المحفوظة"],
    ["needs_commission", "تحتاج مراجعة إعداداتها"],
])("immutable %s methods remain visible without edit controls or a deep-link form", async (status, message) => {
    window.history.replaceState({}, "", `/operational-balances/provider-fees?method=${encodeURIComponent(pending.name)}`);
    await render(directory([{ ...pending, status, editable: false }]));
    expect(host.querySelector("tbody").textContent).toContain(pending.name);
    expect(host.querySelector("tbody").textContent).toContain(message);
    expect(host.querySelector("tbody button")).toBeNull();
    expect(host.querySelector("form")).toBeNull();
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test("a fresh immutable method response removes an already open editor", async () => {
    await act(async () => root.render(<ObservedPaymentMethods setup={directory([{ ...pending, editable: true }])} onSaved={jest.fn()} />));
    await click("إضافة العمولة");
    expect(host.querySelector("form")).not.toBeNull();
    await act(async () => root.render(<ObservedPaymentMethods setup={directory([{ ...pending, editable: false }])} onSaved={jest.fn()} />));
    expect(host.querySelector("form")).toBeNull();
    expect(host.textContent).toContain("تحتاج مراجعة إعداداتها");
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
});

test("fixed SAR fees require whole halalas while percentage precision remains available", async () => {
    await render(directory()); await click("إضافة العمولة"); await fill("2.555", "0.001", "15.125");
    expect(input("الرسم الثابت بالريال").step).toBe(".01");
    expect(input("نسبة العمولة %").step).toBe("any");
    expect(input("ضريبة العمولة %").step).toBe("any");
    expect(host.querySelector("form").checkValidity()).toBe(false);
    await click("حفظ العمولة");
    expect(saveObservedPaymentMethodCommission).not.toHaveBeenCalled();
    await set("الرسم الثابت بالريال", "0.01");
    expect(host.querySelector("form").checkValidity()).toBe(true);
});
