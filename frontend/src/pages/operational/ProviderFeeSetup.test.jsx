import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProviderFeeSetup from "./ProviderFeeSetup";
import { useOptionalAuth } from "../../context/AuthContext";
import * as fees from "../../services/accountingOnboarding";
import { requestId } from "./api";

jest.mock("../../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));
jest.mock("../../services/accountingOnboarding", () => ({ listOnboardingFeePolicies: jest.fn(), listOnboardingProviderPlanScopes: jest.fn(), createOnboardingProviderPlanScope: jest.fn(), listOnboardingFeeCandidates: jest.fn(), createOnboardingFeePolicy: jest.fn(), updateOnboardingFeePolicy: jest.fn(), saveOnboardingFeeSourceMode: jest.fn(), previewOnboardingFeePolicy: jest.fn() }));
jest.mock("./api", () => ({ requestId: jest.fn() }));
let root, host;
const input = key => host.querySelector(`[aria-label="${key}"]`);
async function set(key, value) {
    const el = input(key);
    await act(async () => {
        Object.getOwnPropertyDescriptor(el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value").set.call(el, value);
        el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
}
async function click(label) {
    await act(async () => [...host.querySelectorAll("button")].find(button => button.textContent === label).click());
}
function policy(changes = {}) {
    return { id: "policy-opaque", version: 3, status: "active", provider: "tabby", event_type: "capture",
        plan_id: "fixture-plan", percentage: "4.5", fixed_amount: "2", currency: "SAR",
        vat_treatment: "exclusive", vat_rate: "15", effective_from: "2026-11-01", effective_to: null,
        evidence: "synthetic saved contract", refund_fee_treatment: null, cancellation_fee_treatment: null,
        ...changes };
}
function componentPolicy() {
    return policy({ components: [
        { id: "percentage-fee", percentage: "4.5", fixed_amount: "0", vat_treatment: "exclusive", vat_rate: "15",
            vat_rounding: "half_even", vat_basis: "unrounded_fee", refund_fee_treatment: "recalculate", cancellation_fee_treatment: "recalculate" },
        { id: "fixed-service", percentage: "0", fixed_amount: "1.5", vat_treatment: "inclusive", vat_rate: "10",
            vat_rounding: "half_up", vat_basis: "rounded_fee", refund_fee_treatment: "retain", cancellation_fee_treatment: "retain" },
        { id: "exempt-fee", percentage: "0", fixed_amount: "0.5", vat_treatment: "exempt", vat_rate: "0",
            vat_rounding: "half_up", vat_basis: "rounded_fee", refund_fee_treatment: "full_only", cancellation_fee_treatment: null },
    ] });
}
async function renderPolicies(items, sourceMode = "legacy_settings") {
    fees.listOnboardingFeePolicies.mockResolvedValue({ items, source_mode: sourceMode });
    await act(async () => root.render(<ProviderFeeSetup />));
}
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    useOptionalAuth.mockReturnValue({ user: { id: "owner", role: "owner" } });
    fees.listOnboardingFeePolicies.mockResolvedValue({ items: [] });
    fees.listOnboardingProviderPlanScopes.mockResolvedValue({ items: [], version: 4 });
    fees.createOnboardingProviderPlanScope.mockResolvedValue({ id: "scope-synthetic" });
    fees.listOnboardingFeeCandidates.mockResolvedValue({ items: [{ provider: "salla", payment_method: "mada", label: "مدى", percentage: "1.25", fixed_amount: "1", vat_rate: "15" }] });
    fees.createOnboardingFeePolicy.mockImplementation(async payload => ({ ...payload, id: "saved" }));
    fees.updateOnboardingFeePolicy.mockImplementation(async (id, payload) => ({ ...payload, id, version: payload.expected_version + 1 }));
    fees.saveOnboardingFeeSourceMode.mockResolvedValue({ source_mode: "mz2_policies" });
    fees.previewOnboardingFeePolicy.mockResolvedValue({ fee_net: "2.00", fee_vat: "0.30", fee_total: "2.30", net_amount: "97.70", currency: "SAR" });
    let nextComponent = 1;
    requestId.mockImplementation(() => `new-component-${nextComponent++}`);
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
        components: [{ id: "new-component-1", percentage: "4.5", fixed_amount: "2", vat_treatment: "exclusive",
            vat_rate: "15", vat_rounding: "half_even", vat_basis: "rounded_fee", refund_fee_treatment: null,
            cancellation_fee_treatment: null }],
    }));
});

test("the Mezan 2 fee table paginates thirteen rows and edits with the saved version", async () => {
    fees.listOnboardingFeePolicies.mockResolvedValue({ items: Array.from({ length: 14 }, (_, index) => ({
        id: `policy-${index}`, version: 2, status: "active", provider: "tabby", event_type: "payout",
        plan_id: `fixture-${index}`, percentage: "0", fixed_amount: "2", currency: "SAR",
        vat_treatment: "exclusive", vat_rate: "15", effective_from: "2026-11-01", evidence: "fixture policy",
        confirmed_at: "2026-11-01T00:00:00Z",
    })) });
    await act(async () => root.render(<ProviderFeeSetup />));
    expect(host.querySelectorAll("tbody tr")).toHaveLength(13);
    expect(host.querySelector("nav[aria-label='صفحات رسوم طرق الدفع']")).not.toBeNull();
    expect(host.querySelector("tbody").textContent).toContain("رسوم التحويل");
    await act(async () => host.querySelector("button[aria-label='الصفحة التالية — رسوم طرق الدفع']").click());
    expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(host.querySelector("tbody").textContent).toContain("fixture-13");
    await act(async () => host.querySelector("button[aria-label='الصفحة السابقة — رسوم طرق الدفع']").click());
    await act(async () => host.querySelector("tbody button").click());
    expect(input("نوع الرسم").value).toBe("payout");
    expect(input("نوع الرسم").disabled).toBe(true);
    expect(input("المنصة").disabled).toBe(true);
    await set("الرسم الثابت الإجمالي", "3");
    await act(async () => host.querySelector("button[type=submit]").click());
    expect(fees.updateOnboardingFeePolicy).toHaveBeenCalledWith("policy-0", expect.objectContaining({
        expected_version: 2, event_type: "payout", fixed_amount: "3", plan_id: "fixture-0",
    }));
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("حُفظت التعرفة");
});

test("saved component identities and mixed VAT round-trip without inheriting the aggregate tax", async () => {
    const saved = componentPolicy();
    await renderPolicies([saved]);
    expect(host.querySelector("tbody").textContent).toContain("حسب الأجزاء");
    await click("تعديل");
    expect(input("معالجة ضريبة الجزء 1").value).toBe("exclusive");
    expect(input("نسبة ضريبة الجزء 1 %").value).toBe("15");
    expect(input("معالجة ضريبة الجزء 2").value).toBe("inclusive");
    expect(input("نسبة ضريبة الجزء 2 %").value).toBe("10");
    expect(input("معالجة ضريبة الجزء 3").value).toBe("exempt");
    await set("نسبة ضريبة العمولة %", "20");
    await click("حفظ تعديل التعرفة");
    expect(fees.updateOnboardingFeePolicy).toHaveBeenCalledWith(saved.id, expect.objectContaining({
        expected_version: 3, vat_rate: "20", components: saved.components,
    }));
    expect(requestId).not.toHaveBeenCalled();
});

test("editing one component tax preserves the other source components", async () => {
    const saved = componentPolicy();
    await renderPolicies([saved]);
    await click("تعديل");
    await set("معالجة ضريبة الجزء 2", "exclusive");
    await set("نسبة ضريبة الجزء 2 %", "17");
    await click("حفظ تعديل التعرفة");
    expect(fees.updateOnboardingFeePolicy.mock.calls[0][1].components).toEqual([
        saved.components[0], { ...saved.components[1], vat_treatment: "exclusive", vat_rate: "17" }, saved.components[2],
    ]);
});

test("removing and adding a component preserves surviving IDs and assigns the new ID once", async () => {
    const saved = componentPolicy();
    await renderPolicies([saved]);
    await click("تعديل");
    await act(async () => input("النسبة للجزء 1").closest("fieldset").querySelector("button").click());
    await click("إضافة جزء مفصل للعمولة");
    await set("النسبة للجزء 3", "1");
    await set("المبلغ الثابت للجزء 3", "0");
    await set("نسبة العمولة الإجمالية %", "1");
    await click("حفظ تعديل التعرفة");
    const parts = fees.updateOnboardingFeePolicy.mock.calls[0][1].components;
    expect(parts.slice(0, 2)).toEqual(saved.components.slice(1));
    expect(parts[2].id).toBe("new-component-1");
    expect(requestId).toHaveBeenCalledTimes(1);
});

test.each([
    { minimum: "1.25", maximum: "100.50" }, { minimum: 0, maximum: null }, { minimum: null, maximum: "0" },
])("editing preserves the saved fee limits $minimum / $maximum", async limits => {
    const saved = policy(limits);
    await renderPolicies([saved]);
    await click("تعديل");
    await set("الرسم الثابت الإجمالي", "3");
    await click("حفظ تعديل التعرفة");
    expect(fees.updateOnboardingFeePolicy).toHaveBeenCalledWith(saved.id, expect.objectContaining({ ...limits, fixed_amount: "3" }));
});

test("fee source activation is explicit and uses the saved table", async () => {
    await renderPolicies([policy()]);
    expect(fees.saveOnboardingFeeSourceMode).not.toHaveBeenCalled();
    await click("ربط الأرصدة التشغيلية بهذا الجدول");
    expect(fees.saveOnboardingFeeSourceMode).toHaveBeenCalledTimes(1);
    expect(fees.saveOnboardingFeeSourceMode).toHaveBeenCalledWith("mz2_policies");
    expect(host.textContent).toContain("مصدر احتساب الرسوم المعتمد هو جدول ميزان 2");
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(fees.updateOnboardingFeePolicy).not.toHaveBeenCalled();
});

test("an empty table cannot activate and an already active table needs no activation request", async () => {
    await renderPolicies([]);
    expect([...host.querySelectorAll("button")].find(button => button.textContent === "ربط الأرصدة التشغيلية بهذا الجدول").disabled).toBe(true);
    await act(async () => root.render(null));
    await renderPolicies([policy()], "mz2_policies");
    expect(host.textContent).toContain("مصدر احتساب الرسوم المعتمد هو جدول ميزان 2");
    expect(host.textContent).not.toContain("ربط الأرصدة التشغيلية بهذا الجدول");
    expect(fees.saveOnboardingFeeSourceMode).not.toHaveBeenCalled();
});

test("failed source activation keeps the current source and permits a retry", async () => {
    fees.saveOnboardingFeeSourceMode.mockRejectedValueOnce(new Error("source unavailable"));
    await renderPolicies([policy()]);
    await click("ربط الأرصدة التشغيلية بهذا الجدول");
    expect(host.textContent).toContain("تعذر ربط الجدول");
    expect(host.textContent).toContain("المصدر الحالي هو إعدادات طرق الدفع القديمة");
    await click("ربط الأرصدة التشغيلية بهذا الجدول");
    expect(fees.saveOnboardingFeeSourceMode).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("مصدر احتساب الرسوم المعتمد هو جدول ميزان 2");
});

test("failed legacy suggestions do not block the central table, source mode or editing", async () => {
    const saved = componentPolicy();
    fees.listOnboardingFeeCandidates.mockRejectedValueOnce(new Error("legacy suggestions unavailable"));
    await renderPolicies([saved], "mz2_policies");
    expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(host.textContent).toContain("مصدر احتساب الرسوم المعتمد هو جدول ميزان 2");
    await click("تعديل");
    await set("مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها", "updated source evidence");
    await click("حفظ تعديل التعرفة");
    expect(fees.updateOnboardingFeePolicy).toHaveBeenCalledWith(saved.id, expect.objectContaining({
        expected_version: 3, components: saved.components, evidence: "updated source evidence",
    }));
    expect(fees.saveOnboardingFeeSourceMode).not.toHaveBeenCalled();
});

test("failure to read the central policies remains visible without asserting a source mode", async () => {
    fees.listOnboardingFeePolicies.mockRejectedValueOnce(new Error("policy source unavailable"));
    await act(async () => root.render(<ProviderFeeSetup />));
    expect(host.textContent).toContain("تعذرت قراءة التعرفات");
    expect(host.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(host.textContent).not.toContain("المصدر الحالي هو إعدادات طرق الدفع القديمة");
    expect(host.textContent).not.toContain("مصدر احتساب الرسوم المعتمد هو جدول ميزان 2");
    expect(fees.saveOnboardingFeeSourceMode).not.toHaveBeenCalled();
});

test.each(["capture", "payout", "refund", "cancellation"])("%s preview displays its fee and the applicable net only", async eventType => {
    const saved = policy({ event_type: eventType });
    await renderPolicies([saved]);
    await click("معاينة الرسم");
    await set("المبلغ لمعاينة الرسم", "100.00");
    await set("تاريخ العملية للمعاينة", "2026-11-02");
    await click("احسب المعاينة");
    expect(fees.previewOnboardingFeePolicy).toHaveBeenCalledWith(saved.id, { amount: "100.00", transaction_date: "2026-11-02" });
    expect(host.textContent).toContain("الرسم قبل الضريبة 2.00");
    expect(host.textContent).toContain("الضريبة 0.30");
    expect(host.textContent).toContain("إجمالي الرسم 2.30 SAR");
    expect(host.textContent.includes("صافي المبلغ 97.70")).toBe(["capture", "payout"].includes(eventType));
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(fees.updateOnboardingFeePolicy).not.toHaveBeenCalled();
    await set("المبلغ لمعاينة الرسم", "110.00");
    expect(host.textContent).not.toContain("إجمالي الرسم 2.30");
});

test("pending preview holds its inputs and a failed preview preserves them for correction", async () => {
    let reject;
    fees.previewOnboardingFeePolicy.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    await renderPolicies([policy()]);
    await click("معاينة الرسم");
    await set("المبلغ لمعاينة الرسم", "100.00");
    await click("احسب المعاينة");
    expect(input("المبلغ لمعاينة الرسم").disabled).toBe(true);
    expect(input("تاريخ العملية للمعاينة").disabled).toBe(true);
    await act(async () => reject(new Error("outside effective range")));
    expect(host.querySelector("[role=alert]").textContent).toContain("تعذرت المعاينة");
    expect(input("المبلغ لمعاينة الرسم").value).toBe("100.00");
    expect(input("المبلغ لمعاينة الرسم").disabled).toBe(false);
    expect(host.textContent).not.toContain("إجمالي الرسم 2.30");
});

test.each([
    ["provider_fee_policy_version_conflict", "تغيّرت التعرفة أثناء التعديل"],
    ["provider_fee_policy_overlap", "توجد تعرفة لهذه الطريقة والخطة"],
    ["validation_error", "تعذر حفظ التعرفة"],
])("%s save error preserves the edited metadata and the last saved table", async (code, message) => {
    const saved = componentPolicy();
    fees.updateOnboardingFeePolicy.mockRejectedValue({ response: { data: { detail: { code } } } });
    await renderPolicies([saved]);
    await click("تعديل");
    await set("مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها", "edited evidence");
    await click("حفظ تعديل التعرفة");
    expect(host.textContent).toContain(message);
    expect(input("مرجع العقد أو كشف المنصة المثبت للتعرفة وفترتها").value).toBe("edited evidence");
    expect(input("معالجة ضريبة الجزء 2").value).toBe("inclusive");
    expect(host.querySelector("tbody").textContent).toContain("synthetic saved contract");
    expect(host.querySelector("tbody").textContent).not.toContain("edited evidence");
    expect(fees.updateOnboardingFeePolicy.mock.calls[0][1].components).toEqual(saved.components);
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
});

test("event guidance distinguishes settlement transfer evidence from refund processing", async () => {
    await renderPolicies([]);
    await set("المنصة", "tabby");
    await set("نوع الرسم", "payout");
    expect(host.textContent).toContain("رسوم التحويل الفعلية تؤخذ من كشف التسوية");
    expect(host.textContent).toContain("للمعاينة والمطابقة");
    await set("نوع الرسم", "refund");
    expect(host.textContent).toContain("تُحتسب رسوم معالجة الاسترجاع عند ثبوت الاسترجاع");
    expect(host.textContent).not.toContain("رسوم التحويل الفعلية تؤخذ من كشف التسوية");
});

const scopedPolicy = (id, plan, patch = {}) => policy({ id, plan_id: plan, provider: "tabby", currency: "SAR", status: "active", fee_model_version: 2,
    confirmed_by: "owner", confirmed_at: "2026-11-01T00:00:00Z", ...patch });
test("dated Tabby scope explicitly selects existing contracts without changing tariffs or orders", async () => {
    await renderPolicies([scopedPolicy("tabby3", "3"), scopedPolicy("tabby4", "4"), scopedPolicy("long", "12", { status: "inactive" }), scopedPolicy("old", "3", { fee_model_version: 1 })]);
    expect(host.querySelectorAll('[aria-label^="تعرفة تابي "]')).toHaveLength(2);
    expect(input("تعرفة تابي tabby3").checked).toBe(false);
    expect(input("بداية نطاق تابي").value).toBe("");
    expect(fees.createOnboardingProviderPlanScope).not.toHaveBeenCalled();
    await act(async () => { input("تعرفة تابي tabby3").click(); input("تعرفة تابي tabby4").click(); });
    await set("بداية نطاق تابي", "2026-11-01");
    await set("دليل تفعيل خطط تابي وفترتها", "approved merchant availability for 3 and 4");
    await click("اعتماد نطاق تعرفات تابي");
    expect(fees.createOnboardingProviderPlanScope).toHaveBeenCalledTimes(1);
    expect(fees.createOnboardingProviderPlanScope).toHaveBeenCalledWith({ provider: "tabby", currency: "SAR", policy_ids: ["tabby3", "tabby4"], expected_version: 4,
        effective_from: "2026-11-01", effective_to: null, evidence: "approved merchant availability for 3 and 4" });
    expect(fees.createOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(fees.updateOnboardingFeePolicy).not.toHaveBeenCalled();
    expect(host.textContent).toContain("حُفظ نطاق التعرفات المفعلة");
});

test("scope denial blocks saving until explicitly reloaded and never retries uncertain writes", async () => {
    fees.listOnboardingProviderPlanScopes.mockRejectedValue(new Error("forbidden"));
    await renderPolicies([scopedPolicy("tabby3", "3")]);
    expect(host.querySelector('[aria-label="اعتماد نطاق تابي"] fieldset').disabled).toBe(true);
    expect(host.textContent).toContain("تعذرت قراءة نطاقات تابي");
    fees.listOnboardingProviderPlanScopes.mockResolvedValue({ items: [], version: 5 });
    await click("إعادة تحميل نطاقات تابي");
    await act(async () => input("تعرفة تابي tabby3").click());
    await set("بداية نطاق تابي", "2026-11-01"); await set("دليل تفعيل خطط تابي وفترتها", "approved scope");
    fees.createOnboardingProviderPlanScope.mockRejectedValue(new Error("response lost"));
    await click("اعتماد نطاق تعرفات تابي");
    expect(host.querySelector('[aria-label="اعتماد نطاق تابي"] fieldset').disabled).toBe(true);
    expect(fees.createOnboardingProviderPlanScope).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("أعد تحميل النطاقات قبل أي محاولة أخرى");
});

test("existing scopes display their exact dates and evidence; nonowner cannot read or write setup", async () => {
    fees.listOnboardingProviderPlanScopes.mockResolvedValue({ version: 8, items: [{ id: "saved-scope", status: "active", currency: "SAR", effective_from: "2026-11-01", effective_to: "2026-11-30", evidence: "saved availability", contracts: [{ policy_id: "tabby3" }] }] });
    await renderPolicies([scopedPolicy("tabby3", "3")]);
    const section = host.querySelector('[aria-label="نطاق تعرفات تابي المفعلة"]');
    expect(section.textContent).toContain("saved availability"); expect(section.textContent).toContain("2026-11-30");
    expect(section.textContent).toContain("tabby3");
    const reads = fees.listOnboardingProviderPlanScopes.mock.calls.length;
    useOptionalAuth.mockReturnValue({ user: { id: "viewer", role: "employee" } });
    await act(async () => root.render(<ProviderFeeSetup />));
    expect(host.querySelector('[aria-label="نطاق تعرفات تابي المفعلة"]')).toBeNull();
    expect(fees.listOnboardingProviderPlanScopes).toHaveBeenCalledTimes(reads);
    expect(fees.createOnboardingProviderPlanScope).not.toHaveBeenCalled();
});
