import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import OnboardingWizardView from "./OnboardingWizardView";
import { ONBOARDING_STAGES, sectionIndicators, sectionPresentation } from "./onboardingStages";

let root, container;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const clickText = text => act(() => [...container.querySelectorAll("button")].find(b => b.textContent === text).click());
function Harness({ initial = {}, ...props }) {
    const [value, setValue] = useState(initial), [stage, setStage] = useState("cutover");
    return <OnboardingWizardView value={value} onChange={setValue} activeStage={stage} onStageChange={setStage} {...props} />;
}

test("renders the authoritative 16 stages in RTL and never exposes posting or activation", () => {
    act(() => root.render(<Harness />));
    expect(container.querySelector("main").dir).toBe("rtl");
    expect(container.querySelectorAll("nav button")).toHaveLength(16);
    expect(ONBOARDING_STAGES.map(s => s.id)).toEqual(["cutover", "banks", "providers", "employees", "suppliers", "external_persons", "courier_contracts", "courier_balances", "drivers", "inventory", "payment_fees", "advertising", "prepaid", "obligations", "review", "approval"]);
    for (let i = 0; i < 16; i++) {
        act(() => container.querySelectorAll("nav button")[i].click());
        expect(container.querySelector("h2").textContent).toBe(ONBOARDING_STAGES[i].label);
        expect([...container.querySelectorAll("button")].some(b => /^(ترحيل|تفعيل|Post|Activate)/i.test(b.textContent))).toBe(false);
    }
    expect(container.textContent).toContain("الاعتماد النهائي مقفل");
    expect(container.textContent).toContain("P02 — LOCKED");
});

test("unbound integration is explicit, disabled and does not write storage or call network", () => {
    const fetch = jest.spyOn(global, "fetch").mockImplementation(() => { throw new Error("unexpected network"); });
    const storage = jest.spyOn(Storage.prototype, "setItem");
    act(() => root.render(<Harness />));
    expect(container.textContent).toContain("التعديلات غير محفوظة على الخادم");
    expect([...container.querySelectorAll("button")].find(b => b.textContent === "حفظ القسم").disabled).toBe(true);
    clickText("التالي");
    clickText("اختيار جهة موجودة");
    expect(fetch).not.toHaveBeenCalled(); expect(storage).not.toHaveBeenCalled();
    fetch.mockRestore(); storage.mockRestore();
});

test("section progress and final review retain incomplete zero NA evidence and reconciliation separately", () => {
    const sections = { banks: { status: "complete", explicit_zero: true, evidence_ref: "fixture-bank" }, providers: { status: "not_applicable", not_applicable_reason: "لا توجد جهة", evidence_ref: "fixture-provider" }, employees: { status: "incomplete", evidence_missing: true, reconciliation_problem: true } };
    act(() => root.render(<Harness initial={{ sections }} />));
    expect(container.querySelector("progress").value).toBe(2);
    act(() => container.querySelectorAll("nav button")[14].click());
    const text = container.textContent;
    for (const label of ["صفر صريح", "لا ينطبق", "ناقص", "مكتمل", "دليل ناقص", "مشكلة مطابقة"]) expect(text).toContain(label);
    expect(sectionIndicators(sections.employees)).toEqual(["ناقص", "دليل ناقص", "مشكلة مطابقة"]);
});

test("only explicit section save calls the supplied integration seam; navigation has no autosave", () => {
    const save = jest.fn();
    act(() => root.render(<Harness onSaveSection={save} />));
    clickText("التالي"); expect(save).not.toHaveBeenCalled();
    clickText("حفظ القسم"); expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith("banks", {});
});

test("prepaid classification and fees stay unavailable without backend capabilities", () => {
    act(() => root.render(<Harness />));
    act(() => container.querySelectorAll("nav button")[12].click());
    expect(container.textContent).toContain("تصنيفات النواة غير متاحة");
    expect(container.textContent).toContain("لا ينفذ المعالج إطفاءً آليًا");
    act(() => container.querySelectorAll("nav button")[10].click());
    expect(container.textContent).toContain("إعداد رسوم المزود غير متاح");
    expect(container.querySelector('[aria-label="مزود الرسوم 1"]')).toBeNull();
});

test("NA does not complete a section without reason/evidence or for cutover; zero cannot cover nonzero rows", () => {
    expect(sectionPresentation({ status: "not_applicable" }, "banks").status).toBe("incomplete");
    const na = { status: "not_applicable", not_applicable_reason: "لا توجد أرصدة", evidence_ref: "fixture" };
    expect(sectionPresentation(na, "banks").status).toBe("not_applicable");
    expect(sectionPresentation(na, "cutover").status).toBe("incomplete");
    expect(sectionPresentation({ ...na, rows: [{ balance: "10" }] }, "banks").status).toBe("incomplete");
    const invalidZero = { status: "complete", explicit_zero: true, evidence_ref: "fixture", rows: [{ balance: "5" }] };
    expect(sectionPresentation(invalidZero, "banks").status).toBe("incomplete");
    expect(sectionIndicators(invalidZero)).not.toContain("صفر صريح");
    expect(sectionIndicators(invalidZero)).toContain("تعارض مع إثبات الصفر");
});
