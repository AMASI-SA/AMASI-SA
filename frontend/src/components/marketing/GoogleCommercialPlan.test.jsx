import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleCommercialPlan from "./GoogleCommercialPlan";
import { getGoogleCommercialPlan, saveGoogleCommercialPlan } from "../../services/googleCommercialPlan";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn() } }));
const fields = { instruction: "ركز على هدايا الشتاء", season: "الشتاء", starts_on: "2026-11-01", ends_on: "2026-12-31", market: "SA",
    audience: "رجال يبحثون عن هدية", message: "دفء الشتاء", category_url: "https://amasi-sa.com/shawls", category_ids: [],
    pinned_product_ids: ["123"], excluded_product_ids: [], exclusions: [], selection_mode: "pinned_only", calendar: "gregorian", limits: null };
const saved = { plan_id: "seasonal-focus", revision: 3, fields, execution_authorized: false };
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset();
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
    expect(container.textContent).toContain("الأدلة لم تُجمع بعد");
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
    await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
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
