import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProviderFeeSetup from "./ProviderFeeSetup";
import { useOptionalAuth } from "../../context/AuthContext";
import * as fees from "../../services/accountingOnboarding";

jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("../../services/accountingOnboarding", () => ({ listOnboardingFeePolicies: jest.fn(), listOnboardingFeeCandidates: jest.fn(), createOnboardingFeePolicy: jest.fn() }));
let root, host;
const input = key => host.querySelector(`[aria-label="${key}"]`);
async function set(key, value) {
    const el = input(key);
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(el, value);
        el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    useOptionalAuth.mockReturnValue({ user: { id: "owner", role: "owner" } });
    fees.listOnboardingFeePolicies.mockResolvedValue({ items: [] });
    fees.listOnboardingFeeCandidates.mockResolvedValue({ items: [{ provider: "salla", payment_method: "mada", label: "مدى", percentage: "1.25", fixed_amount: "1", vat_rate: "15" }] });
    fees.createOnboardingFeePolicy.mockImplementation(async payload => ({ ...payload, id: "saved" }));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

test("settings prefill cannot implicitly adopt an opening date, contract evidence or fee", async () => {
    await act(async () => root.render(<ProviderFeeSetup />));
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent.includes("1.25%")).click());
    expect(input("طريقة الدفع لدى سلة").value).toBe("mada");
    expect(input("تسري من").value).toBe("");
    expect(host.querySelector("form").checkValidity()).toBe(false);
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
    await set("تسري من", "2026-11-02"); await set("معالجة ضريبة العمولة", "exclusive");
    await set("مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها", "owner contract 31");
    expect(host.querySelector("form").checkValidity()).toBe(true);
    await act(async () => host.querySelector("button[type=submit]").click());
    expect(fees.createOnboardingFeePolicy).toHaveBeenCalledTimes(1);
    expect(fees.createOnboardingFeePolicy).toHaveBeenCalledWith(expect.objectContaining({ provider: "salla",
        payment_method: "mada", effective_from: "2026-11-02", vat_rate: "15", percentage: "1.25", fixed_amount: "1",
        refund_fee_treatment: null, cancellation_fee_treatment: null, currency: "SAR" }));
    expect(host.textContent).toContain("حُفظت التعرفة");
});
test("ambiguous aliases require contract review without prefill or approval", async () => {
    fees.listOnboardingFeeCandidates.mockResolvedValue({ items: [], conflicts: [
        {provider:"salla", payment_method:"credit_card", labels:["البطاقة الإئتمانية", "بطاقة ائتمانية"], reason:"duplicate_settings"},
        {provider:"emkan", payment_method:null, labels:["إمكان"], reason:"incomplete_settings"},
    ] });
    await act(async () => root.render(<ProviderFeeSetup />));
    expect(host.textContent).toContain("البطاقة الإئتمانية، بطاقة ائتمانية");
    expect(host.textContent).toContain("بيانات الرسم أو الضريبة غير مكتملة");
    expect(input("نسبة العمولة الإجمالية %").value).toBe("");
    expect(host.querySelector("form").checkValidity()).toBe(false);
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
});

test("non-owner never loads merchant settings or a confirmation form", async () => {
    useOptionalAuth.mockReturnValue({ user: { id: "staff", role: "employee" } });
    await act(async () => root.render(<ProviderFeeSetup />));
    expect(host.querySelector("form")).toBeNull();
    expect(fees.listOnboardingFeeCandidates).not.toHaveBeenCalled();
});

test("component VAT rounding is explicit while unverified event terms stay empty", async () => {
    await act(async () => root.render(<ProviderFeeSetup />));
    await set("المنصة", "tabby");
    await set("معرف خطة التقسيط إن خصّ العقد خطة بعينها", "fixture-plan");
    await set("نسبة العمولة الإجمالية %", "4.5");
    await set("الرسم الثابت الإجمالي", "2");
    await set("تسري من", "2026-11-01");
    await set("معالجة ضريبة العمولة", "exclusive");
    await set("نسبة ضريبة العمولة %", "15");
    await set("مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها", "synthetic component contract");
    await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "إضافة جزء مفصل للعمولة").click());
    await set("النسبة للجزء 1", "4.5");
    await set("المبلغ الثابت للجزء 1", "2");
    expect(input("تقريب ضريبة الجزء 1").value).toBe("half_up");
    await set("تقريب ضريبة الجزء 1", "half_even");
    expect(host.querySelector("form").checkValidity()).toBe(true);
    await act(async () => host.querySelector("button[type=submit]").click());
    expect(fees.createOnboardingFeePolicy).toHaveBeenCalledWith(expect.objectContaining({
        plan_id: "fixture-plan",
        components: [{ id: "1", percentage: "4.5", fixed_amount: "2", vat_treatment: "exclusive",
            vat_rate: "15", vat_rounding: "half_even", refund_fee_treatment: null,
            cancellation_fee_treatment: null }],
    }));
});
