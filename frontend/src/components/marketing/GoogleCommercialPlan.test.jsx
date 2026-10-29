import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleCommercialPlan from "./GoogleCommercialPlan";
import { getGoogleCommercialPlan, saveGoogleCommercialPlan } from "../../services/googleCommercialPlan";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const fields = { instruction: "ركز على هدايا الشتاء", season: "الشتاء", starts_on: "2026-11-01", ends_on: "2026-12-31", market: "SA",
    audience: "رجال يبحثون عن هدية", message: "دفء الشتاء", category_url: "https://amasi-sa.com/shawls", category_ids: [],
    pinned_product_ids: ["123"], excluded_product_ids: [], exclusions: [], selection_mode: "pinned_only", calendar: "gregorian", limits: null };
const saved = { plan_id: "seasonal-focus", revision: 3, fields, execution_authorized: false };
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockResolvedValue({ data: saved }); api.put.mockResolvedValue({ data: { ...saved, revision: 4 } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleCommercialPlan isOwner {...props} />)); }
function element(label) { return container.querySelector(`[aria-label="${label}"]`); }
function button(text) { return [...container.querySelectorAll("button")].find((item) => item.textContent === text); }
async function input(label, value) {
    const item = element(label);
    await act(async () => {
        const proto = item.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(item, value);
        item.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function acknowledge() { await act(async () => element("راجعت حقول خطة الموسم").click()); }
async function save() { await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }

test("loads saved owner context without default budgets or granting execution", async () => {
    await render();
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/commercial-plans/seasonal-focus");
    expect(element("حد الميزانية اليومي الاختياري").value).toBe("");
    expect(element("هدف العائد الاختياري ROAS").value).toBe("");
    expect(element("توجيه المالك").value).toBe(fields.instruction);
    expect(container.textContent).toContain("لا يمنح تفويض تشغيل أو إنفاق");
    expect(container.textContent).toContain("اكتمالها غير مثبت");
    expect(container.textContent).toContain("المشاهدات الجزئية لا تثبت صافي المبيعات أو نموها");
    expect(container.textContent).toContain("الربحية غير مثبتة عند نقص التكاليف");
    expect(api.put).not.toHaveBeenCalled();
});

test("catalog review uses saved revision and is hidden when the owner edits the plan", async () => {
    await render();
    api.get.mockResolvedValueOnce({ data: { plan_id: "seasonal-focus", plan_revision: 3,
        provider_writes: 0, execution_authorized: false, source_verification: "catalog_row_only",
        pinned_product_ids_reviewed: ["123"], catalog: { products: [{ product_id: "123", status: "unknown", verification: "verified" }],
            missing_or_unverified_product_ids: [], category_membership_verified: false, variant_availability_verified: false,
            source_writes: 0, execution_authorized: false } } });
    await act(async () => button("قراءة منتجات الخطة المحفوظة").click());
    expect(api.get.mock.calls[1][1].params).toEqual({ expected_revision: 3 });
    expect(container.textContent).toContain("التوفر القابل للشراء غير مثبت");
    await input("الموسم", "موسم جديد");
    expect(container.textContent).toContain("تعديلات غير محفوظة");
    expect(container.textContent).not.toContain("المنتج 123");
});

test.each([false, undefined])("non-owner sees no form and makes no request %s", async (isOwner) => {
    await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

const suggestionInstruction = "ركز على الشتاء بالسعودية واستهدف الرجال الباحثين عن هدية";
const suggestionResponse = () => ({ instruction: suggestionInstruction,
    fields: { season: "الشتاء", starts_on: null, ends_on: null, market: "SA", audience: "الرجال الباحثين عن هدية", message: null, category_url: null },
    evidence: { season: "الشتاء", market: "السعودية", audience: "الرجال الباحثين عن هدية" },
    missing_fields: ["starts_on", "ends_on", "message", "category_url"],
    manual_fields: ["category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions", "limits"],
    status: "unreviewed_suggestion", persisted: false, execution_authorized: false, provider_writes: 0 });
async function suggest() { await act(async () => button("اقتراح حقول من التوجيه").click()); }

test("suggestions require explicit request and apply only to empty fields without saving or reviewing", async () => {
    api.get.mockRejectedValue({ response: { status: 404, data: { detail: { code: "google_commercial_plan_not_found" } } } });
    api.post.mockResolvedValue({ data: suggestionResponse() });
    await render();
    expect(api.post).not.toHaveBeenCalled();
    expect(container.textContent).toContain("احفظ خطة ومعرفات منتجات سلة الرقمية أولًا");
    await input("توجيه المالك", suggestionInstruction); await input("الموسم", "توجيه سابق");
    await input("عملة الحدود مثل SAR", "SAR"); await input("حد الميزانية اليومي الاختياري", "75");
    await input("معرفات المنتجات المثبتة", "123"); await acknowledge(); await suggest();
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.put).not.toHaveBeenCalled();
    expect(element("السوق · رمز الدولة مثل SA").value).toBe("");
    expect(container.textContent).toContain("من توجيهك: «السعودية»");
    await act(async () => button("تعبئة الحقول الفارغة فقط").click());
    expect(element("الموسم").value).toBe("توجيه سابق"); expect(element("السوق · رمز الدولة مثل SA").value).toBe("SA");
    expect(element("الجمهور المستهدف").value).toBe("الرجال الباحثين عن هدية");
    expect(element("حد الميزانية اليومي الاختياري").value).toBe("75"); expect(element("معرفات المنتجات المثبتة").value).toBe("123");
    expect(element("بداية الموسم").value).toBe(""); expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
    expect(api.put).not.toHaveBeenCalled();
});

test.each(["توجيه المالك", "الموسم", "حد الميزانية اليومي الاختياري"])("editing %s aborts and discards a late suggestion", async (label) => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await input("توجيه المالك", suggestionInstruction); await suggest();
    const signal = api.post.mock.calls[0][2].signal;
    await input(label, label === "حد الميزانية اليومي الاختياري" ? "99" : "تعديل المالك"); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() }));
    expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined(); expect(api.put).not.toHaveBeenCalled();
    expect(element(label).value).toBe(label === "حد الميزانية اليومي الاختياري" ? "99" : "تعديل المالك");
});

test("changing review acknowledgement aborts and discards a late suggestion", async () => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await input("توجيه المالك", suggestionInstruction); await suggest();
    const signal = api.post.mock.calls[0][2].signal;
    await acknowledge(); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() }));
    expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined(); expect(api.put).not.toHaveBeenCalled();
});

test("reloading the saved plan cancels extraction and cannot apply the previous form's result", async () => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await input("توجيه المالك", suggestionInstruction); await suggest();
    const signal = api.post.mock.calls[0][2].signal;
    await act(async () => button("إعادة تحميل خطة الموسم").click()); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() }));
    expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined(); expect(element("توجيه المالك").value).toBe(fields.instruction);
    expect(api.put).not.toHaveBeenCalled();
});

test("saving the plan cancels a pending suggestion and late result cannot replace saved form", async () => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await input("توجيه المالك", suggestionInstruction); await acknowledge(); await suggest();
    const signal = api.post.mock.calls[0][2].signal;
    await save(); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() }));
    expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined(); expect(api.put).toHaveBeenCalledTimes(1);
    expect(element("توجيه المالك").value).toBe(fields.instruction);
});

test("plan or owner session change aborts a pending suggestion", async () => {
    let finish; api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await input("توجيه المالك", suggestionInstruction); await suggest();
    const signal = api.post.mock.calls[0][2].signal;
    await render({ planId: "other-season" }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() }));
    expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined();
    await input("توجيه المالك", suggestionInstruction); await suggest(); const nextSignal = api.post.mock.calls[1][2].signal;
    await render({ isOwner: false }); expect(nextSignal.aborted).toBe(true);
    await act(async () => finish({ data: suggestionResponse() })); expect(container.textContent).toBe("");
});

test("malformed suggestion is surfaced without changing a field", async () => {
    api.post.mockResolvedValue({ data: { ...suggestionResponse(), fields: { ...suggestionResponse().fields, limits: { daily_budget_native: "500" } } } });
    await render(); await input("توجيه المالك", suggestionInstruction); await suggest();
    expect(container.querySelector('[role="alert"]')).not.toBe(null); expect(button("تعبئة الحقول الفارغة فقط")).toBeUndefined();
    expect(element("حد الميزانية اليومي الاختياري").value).toBe(""); expect(api.put).not.toHaveBeenCalled();
});

test("typed edits reset review and explicit save sends current revision with optional limits", async () => {
    await render(); await save(); expect(api.put).not.toHaveBeenCalled();
    await acknowledge(); await input("معرفات المنتجات المثبتة", "123،456");
    expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
    await input("عملة الحدود مثل SAR", "sar"); await input("حد الميزانية اليومي الاختياري", "50");
    await acknowledge(); await save();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put.mock.calls[0][1]).toMatchObject({ expected_revision: 3, acknowledge_review: true, pinned_product_ids: ["123", "456"],
        instruction: fields.instruction, limits: { currency_code: "SAR", daily_budget_native: "50", total_budget_native: null, minimum_margin_percent: null, target_roas: null } });
    expect(api.put.mock.calls[0][1].enabled).toBeUndefined();
    expect(container.textContent).toContain("لم يبدأ تشغيل أي حملة");
});

test("only explicit not-found code permits first save at revision zero", async () => {
    api.get.mockRejectedValue({ response: { status: 404, data: { detail: { code: "google_commercial_plan_not_found" } } } });
    await render(); expect(container.textContent).toContain("النسخة المحفوظة: 0");
    expect(element("حد الميزانية اليومي الاختياري").value).toBe("");
    await input("توجيه المالك", "ركز على الشالات"); await input("الموسم", "الشتاء"); await acknowledge(); await save();
    expect(api.put.mock.calls[0][1]).toMatchObject({ expected_revision: 0, limits: null });
});

test("generic 404 does not create an empty editable plan", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } }); await render();
    expect(container.querySelector("form")).toBe(null); expect(container.querySelector('[role="alert"]')).not.toBe(null);
    expect(api.put).not.toHaveBeenCalled();
});

test("conflict preserves edits and blocks retries until owner reloads", async () => {
    api.put.mockRejectedValue({ response: { status: 409 } }); await render();
    await input("الرسالة التسويقية", "هدية مخصصة"); await acknowledge(); await save();
    expect(element("الرسالة التسويقية").value).toBe("هدية مخصصة"); expect(container.querySelector("fieldset").disabled).toBe(true);
    await save(); expect(api.put).toHaveBeenCalledTimes(1);
    api.get.mockResolvedValue({ data: { ...saved, revision: 8 } });
    await act(async () => button("إعادة تحميل خطة الموسم").click());
    expect(container.textContent).toContain("النسخة المحفوظة: 8"); expect(element("راجعت حقول خطة الموسم").checked).toBe(false);
});

test("plan switch aborts late mutation responses", async () => {
    let finish;
    api.put.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await acknowledge(); await save(); const signal = api.put.mock.calls[0][2].signal;
    api.get.mockResolvedValue({ data: { ...saved, plan_id: "summer", revision: 9, fields: { ...fields, season: "الصيف" } } });
    await render({ planId: "summer" }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: { ...saved, revision: 4 } }));
    expect(element("الموسم").value).toBe("الصيف"); expect(container.textContent).toContain("النسخة المحفوظة: 9");
    expect(container.textContent).not.toContain("حُفظت خطة الموسم");
});

test("services reject invalid ids and implicit review without writes", async () => {
    await expect(getGoogleCommercialPlan("bad.id")).rejects.toThrow();
    await expect(saveGoogleCommercialPlan("seasonal-focus", { acknowledge_review: 1 })).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});


test("plan switch aborts late read before it can overwrite the new form", async () => {
    let finish;
    api.get.mockImplementation((url) => url.endsWith("seasonal-focus") ? new Promise((resolve) => { finish = resolve; })
        : Promise.resolve({ data: { ...saved, revision: 9, fields: { ...fields, season: "الصيف" } } }));
    await render(); const signal = api.get.mock.calls[0][1].signal;
    await render({ planId: "summer" }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: saved }));
    expect(element("الموسم").value).toBe("الصيف"); expect(container.textContent).toContain("النسخة المحفوظة: 9");
});

function observedStoreResponse(revision = 3) {
    const windows = { last_14_days: ["2026-10-15", "2026-10-28"], previous_14_days: ["2026-10-01", "2026-10-14"], last_30_days: ["2026-09-29", "2026-10-28"], last_90_days: ["2026-07-31", "2026-10-28"] };
    return { plan_id: saved.plan_id, plan_revision: revision, product_ids_reviewed: ["123"], catalog_missing_or_unverified_ids: [], provider_writes: 0, execution_authorized: false,
        observations: { mode: "stored_product_line_observations", metric_basis: "lower_bound_on_qualifying_saved_gross_line_observations_not_sales", as_of: "2026-10-29T12:00:00+00:00",
            windows: Object.fromEntries(Object.entries(windows).map(([key, [from, to]]) => [key, { from, to, timezone: "Asia/Riyadh" }])),
            coverage_complete: false, refunds_reconciled: false, snapshot_consistent: false, source_snapshot_immutable: false, platform_conversions_added: false,
            execution_authorized: false, provider_writes: 0, source_writes: 0, truncated: false,
            products: [{ product_id: "123", currency_groups: [], sales_native: null, profitability: null, growth_14_days_percent: null, success_classification: "not_established" }] } };
}

test("actual saved plan exposes explicit observations read beside catalog review without automatic requests", async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/store-observations") ? observedStoreResponse() : saved }));
    await render(); expect(api.get).toHaveBeenCalledTimes(1);
    expect(button("قراءة منتجات الخطة المحفوظة")).not.toBeUndefined();
    await act(async () => button("قراءة مشاهدات منتجات الخطة").click());
    expect(api.get.mock.calls[1][0]).toBe("/integrations-v2/google_ads/commercial-plans/seasonal-focus/store-observations");
    expect(api.get.mock.calls[1][1].params).toEqual({ expected_revision: 3 });
    expect(container.textContent).toContain("لا يعني ذلك عدم وجود مبيعات");
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test("editing actual parent form aborts and removes late saved-plan observations", async () => {
    let finish; api.get.mockImplementation((url) => url.endsWith("/store-observations") ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve({ data: saved }));
    await render(); await act(async () => button("قراءة مشاهدات منتجات الخطة").click());
    const signal = api.get.mock.calls[1][1].signal;
    await input("الموسم", "الصيف"); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: observedStoreResponse() }));
    expect(button("قراءة مشاهدات منتجات الخطة")).toBeUndefined();
    expect(container.textContent).not.toContain("وقت القراءة الثابت");
    expect(api.put).not.toHaveBeenCalled();
});
