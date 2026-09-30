import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OpeningCourierEditor, { newCourierDraft, validateCourierDraft } from "./OpeningCourierEditor";

const banks = [{ id: "bank-1", name: "البنك الموثوق" }];
const couriers = [{ id: "courier-a", name: "شركة أ" }, { id: "courier-b", name: "شركة ب" }];
const validDraft = () => ({ ...newCourierDraft(), shipping_cost: "0", shipping_vat_percent: "0", shipping_cost_vat_inclusive: false, commission_vat_percent: "0", commission_vat_inclusive: false, payment_mode: "postpaid", effective_from: "2026-10-01T00:00", settlement_bank_id: "bank-1", opening_cod_receivable: "0", opening_payable: "0", evidence_ref: "fixture-evidence", cod_fee_tiers: [{min_amount: "0", max_amount: "", min_inclusive: true, max_inclusive: false, commission_percent: "0", fixed_fee: "0"}] });
let container, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const field = label => container.querySelector(`[aria-label="${label}"]`);
function change(label, value) {
    const target = field(label);
    act(() => {
        Object.getOwnPropertyDescriptor(target.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(target, value);
        target.dispatchEvent(new Event(target.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
function Harness({ initial = {}, onSave }) {
    const [value, setValue] = useState(initial);
    return <OpeningCourierEditor value={value} onChange={setValue} couriers={couriers} banks={banks} onSave={onSave} />;
}

test("switching couriers preserves independent cost, bank, COD and tier drafts", () => {
    act(() => root.render(<Harness />));
    change("شركة الشحن", "courier-a");
    change("تكلفة الشحن", "20");
    change("بنك التسوية", "bank-1");
    change("COD افتتاحي لنا", "150");
    act(() => [...container.querySelectorAll("button")].find(b => b.textContent === "إضافة شريحة").click());
    change("العمولة الثابتة 1", "5");
    change("شركة الشحن", "courier-b");
    expect(field("تكلفة الشحن").value).toBe("");
    expect(field("بنك التسوية").value).toBe("");
    expect(field("COD افتتاحي لنا").value).toBe("");
    expect(field("العمولة الثابتة 1")).toBeNull();
    change("تكلفة الشحن", "35");
    change("شركة الشحن", "courier-a");
    expect(field("تكلفة الشحن").value).toBe("20");
    expect(field("بنك التسوية").value).toBe("bank-1");
    expect(field("COD افتتاحي لنا").value).toBe("150");
    expect(field("العمولة الثابتة 1").value).toBe("5");
    change("شركة الشحن", "courier-b");
    expect(field("تكلفة الشحن").value).toBe("35");
});

test("COD tiers require explicit fees and reject overlap or halala gaps", () => {
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [] }, banks).join(" ")).toContain("شريحة COD صريحة");
    const tier = { min_amount: "0", max_amount: "100", min_inclusive: true, max_inclusive: false, commission_percent: "0", fixed_fee: "0" };
    const next = { ...tier, min_amount: "100", max_amount: "" };
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [tier, next] }, banks)).toEqual([]);
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [{ ...tier, fixed_fee: "" }] }, banks).length).toBeGreaterThan(0);
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [{ ...tier, max_inclusive: true }, next] }, banks).join(" ")).toContain("متداخلة");
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [tier, { ...next, min_amount: "100.01" }] }, banks).join(" ")).toContain("فجوة");
});

test("explicit zeros validate, while missing amounts, evidence and settlement bank do not", () => {
    expect(validateCourierDraft(validDraft(), banks)).toEqual([]);
    expect(validateCourierDraft({ ...validDraft(), opening_cod_receivable: "" }, banks).join(" ")).toContain("الصفر");
    expect(validateCourierDraft({ ...validDraft(), settlement_bank_id: "" }, banks).join(" ")).toContain("بنك");
    expect(validateCourierDraft({ ...validDraft(), evidence_ref: "" }, banks).join(" ")).toContain("دليل");
    expect(validateCourierDraft({ ...validDraft(), cod_fee_tiers: [{ min_amount: "0", max_amount: "100", commission_percent: "0.01", fixed_fee: "0" }] }, banks)).toEqual([]);
});

test("saving invokes draft callback for chosen company only; invalid draft cannot save and P02 stays locked", async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    act(() => root.render(<Harness initial={{ "courier-a": validDraft() }} onSave={onSave} />));
    expect(container.querySelector("section").dir).toBe("rtl");
    expect(container.textContent).toContain("P02 — LOCKED");
    change("شركة الشحن", "courier-b");
    await act(async () => [...container.querySelectorAll("button")].find(b => b.textContent === "حفظ مسودة الشركة").click());
    expect(onSave).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]').textContent).toContain("الصفر");
    change("شركة الشحن", "courier-a");
    await act(async () => [...container.querySelectorAll("button")].find(b => b.textContent === "حفظ مسودة الشركة").click());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith("courier-a", validDraft());
    expect([...container.querySelectorAll("button")].every(b => b.type === "button" && !/Post|تفعيل|ترحيل|اعتماد/i.test(b.textContent))).toBe(true);
});
