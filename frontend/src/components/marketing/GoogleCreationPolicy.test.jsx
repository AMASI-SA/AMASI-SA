import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleCreationPolicy from "./GoogleCreationPolicy";
import { activateGoogleCreationPolicy, saveGoogleCreationPolicy } from "../../services/googleCreationPolicy";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const product = { product_id: "1", name: "هودي شتوي بالاسم", product_short_name: "هودي شتوي بالاسم", sku: "AMS11549", final_url: "https://amasi-sa.com/p1" };
const saved = { account_id: "123", configured: true, revision: 3, enabled: false, executor_available: false, effective_enabled: false,
    currency_code: "SAR", time_zone: "Asia/Riyadh", allowed_domain: "amasi-sa.com", owner_generation: 1,
    product_scopes: [{ product_id: "1", final_url: product.final_url }], product_review: [product],
    template: { template_id: "winter", version: 1, campaign_name: "هدية | {product_short_name}", group_name: "هدايا",
        headlines: ["{product_short_name}", "هدية شتوية بالاسم", "اختاري تفاصيل هديتك"],
        descriptions: ["اختاري تفاصيل منتجك قبل الطلب.", "راجعي خيارات المتجر المتاحة."], keywords: [{ match_type: "PHRASE", text: "هودي بالاسم" }] },
    budget_native: "15", max_account_daily_budget_native: "55", geo_target_ids: ["2682"], geo_review: [{ id: "2682", name: "Saudi Arabia" }],
    max_paused_campaigns: 2, max_daily_creations: 1, max_total_creations: 3, catalog_max_age_seconds: 3600, auto_enable_new: false,
    expires_at: "2026-10-27T09:00:00+00:00", configuration_blockers: [] };
let root, container;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/products") ? { items: [product] } : saved }));
    api.put.mockResolvedValue({ data: { ...saved, revision: 4 } }); api.post.mockResolvedValue({ data: { ...saved, revision: 4 } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleCreationPolicy accountId="123" isOwner {...props} />)); }
const button = (text) => [...container.querySelectorAll("button")].find((element) => element.textContent === text);
async function input(label, value) {
    const field = container.querySelector(`[aria-label="${label}"]`);
    await act(async () => {
        Object.getOwnPropertyDescriptor(field.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, "value").set.call(field, value);
        field.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function save() { await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))); }
async function clickAck(label) { await act(async () => container.querySelector(`[aria-label="${label}"]`).click()); }

test("owner can review products and policy but loading makes no changes", async () => {
    await render();
    expect(container.textContent).toContain("تبدأ الحملة موقوفة"); expect(container.textContent).toContain("التشغيل غير متاح حاليًا");
    expect(api.get.mock.calls.some(([url]) => url.endsWith("/products"))).toBe(true);
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
    expect(button("حفظ قالب وسياسة الإنشاء معطّلين").disabled).toBe(true);
    expect(button("تفعيل تفويض الإنشاء ضمن هذه الحدود").disabled).toBe(true);
});

test.each([false, undefined])("non-owner sees no configuration and makes no reads: %s", async (isOwner) => {
    await render({ isOwner }); expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
});

test("default configuration does not invent products, budget, copy or generation", async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/products") ? { items: [product] } : { account_id: "123", revision: 0, configured: false, product_scopes: [] } }));
    await render();
    expect(container.querySelector('[aria-label="ميزانية حملة الإنشاء"]').value).toBe("");
    expect(container.querySelector('[aria-label="جيل الإنشاء"]').value).toBe("");
    expect(container.querySelector('[aria-label="عناوين RSA · 3 إلى 15"]').value).toBe("");
    expect([...container.querySelectorAll('input[type="checkbox"]')].every((field) => !field.checked)).toBe(true);
    expect(api.put).not.toHaveBeenCalled();
});

test("save requires explicit creative review and sends only owner inputs plus current revision", async () => {
    await render(); await save(); expect(api.put).not.toHaveBeenCalled();
    await input("ميزانية حملة الإنشاء", "14.123456");
    await clickAck("اعتماد قالب الإنشاء للحفظ"); await save();
    expect(api.put).toHaveBeenCalledTimes(1);
    const [url, payload] = api.put.mock.calls[0];
    expect(url).toBe("/integrations-v2/google_ads/automation/creation-policy/123");
    expect(payload).toMatchObject({ expected_revision: 3, enabled: false, owner_generation: 1, product_ids: ["1"],
        budget_native: "14.123456", max_account_daily_budget_native: "55", acknowledge_template: true, auto_enable_new: false });
    expect(payload.template.keywords).toEqual([{ text: "هودي بالاسم", match_type: "PHRASE" }]);
    expect(payload).not.toHaveProperty("allowed_domain"); expect(payload).not.toHaveProperty("user_id");
    expect(payload.template).not.toHaveProperty("approved_by"); expect(api.post).not.toHaveBeenCalled();
    expect(container.textContent).toContain("لم تُنشأ أي حملة");
});

test("saved creative review renders only simple verified product substitutions", async () => {
    await render();
    const review = container.querySelector('[aria-label="مراجعة تفويض إنشاء جوجل"]');
    expect(review.textContent).toContain(product.name); expect(review.textContent).toContain(product.final_url);
    expect(review.textContent).toContain("55 SAR");
    expect(review.textContent).not.toContain("{product_short_name}");
});

test("edits invalidate creative acknowledgement and old activation review", async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/products") ? { items: [product] } : { ...saved, executor_available: true } }));
    await render(); await clickAck("اعتماد قالب الإنشاء للحفظ"); await clickAck("اعتماد إنشاء الحملات تلقائيًا");
    await input("ميزانية حملة الإنشاء", "14");
    expect(container.querySelector('[aria-label="اعتماد قالب الإنشاء للحفظ"]').checked).toBe(false);
    expect(container.querySelector('[aria-label="اعتماد إنشاء الحملات تلقائيًا"]').checked).toBe(false);
    expect(button("تفعيل تفويض الإنشاء ضمن هذه الحدود").disabled).toBe(true);
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test("activation requires independent creation and creative acknowledgements and current revision", async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/products") ? { items: [product] } : { ...saved, executor_available: true } }));
    await render(); await clickAck("اعتماد إنشاء الحملات تلقائيًا");
    expect(button("تفعيل تفويض الإنشاء ضمن هذه الحدود").disabled).toBe(true);
    await clickAck("اعتماد النصوص والمنتجات المحفوظة");
    await act(async () => button("تفعيل تفويض الإنشاء ضمن هذه الحدود").click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/creation-policy/123/activate");
    expect(api.post.mock.calls[0][1]).toEqual({ expected_revision: 3, acknowledge_creation: true, acknowledge_creative: true, acknowledge_auto_enable: false });
    expect(api.put).not.toHaveBeenCalled();
});

test("auto-enable permission needs an additional explicit acknowledgement", async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/products") ? { items: [product] } : { ...saved, executor_available: true, auto_enable_new: true } }));
    await render(); await clickAck("اعتماد إنشاء الحملات تلقائيًا"); await clickAck("اعتماد النصوص والمنتجات المحفوظة");
    expect(button("تفعيل تفويض الإنشاء ضمن هذه الحدود").disabled).toBe(true);
    await clickAck("اعتماد التفعيل اللاحق ضمن التمويل المثبت");
    await act(async () => button("تفعيل تفويض الإنشاء ضمن هذه الحدود").click());
    expect(api.post.mock.calls[0][1].acknowledge_auto_enable).toBe(true);
});

test("conflict keeps edited copy but blocks another write until explicit reload", async () => {
    api.put.mockRejectedValue({ response: { status: 409 } });
    await render(); await input("ميزانية حملة الإنشاء", "14"); await clickAck("اعتماد قالب الإنشاء للحفظ"); await save();
    expect(container.querySelector('[aria-label="ميزانية حملة الإنشاء"]').value).toBe("14");
    expect(container.textContent).toContain("بقيت مدخلاتك ظاهرة");
    await save(); expect(api.put).toHaveBeenCalledTimes(1);
    await act(async () => button("إعادة تحميل سياسة الإنشاء").click());
    expect(container.querySelector('[aria-label="ميزانية حملة الإنشاء"]').value).toBe("15");
});

test("network failures keep fields without echoing private provider messages", async () => {
    api.put.mockRejectedValue(new Error("private provider response"));
    await render(); await input("ميزانية حملة الإنشاء", "14"); await clickAck("اعتماد قالب الإنشاء للحفظ"); await save();
    expect(container.querySelector('[aria-label="ميزانية حملة الإنشاء"]').value).toBe("14");
    expect(container.textContent).not.toContain("private provider response");
});

test("account switch aborts old load and cannot show old policy or products", async () => {
    let finish;
    api.get.mockImplementation((url) => url.endsWith("/123") ? new Promise((resolve) => { finish = resolve; })
        : Promise.resolve({ data: url.endsWith("/products") ? { items: [] } : { ...saved, account_id: "222", revision: 9, product_review: [], product_scopes: [] } }));
    await render(); const signal = api.get.mock.calls.find(([url]) => url.endsWith("/123"))[1].signal;
    await render({ accountId: "222" }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: saved }));
    expect(container.textContent).toContain("النسخة 9"); expect(container.textContent).not.toContain(product.name);
    expect(api.put).not.toHaveBeenCalled();
});

test("revocation sends scoped CAS and reports already authorized work may finish", async () => {
    api.post.mockResolvedValue({ data: { ...saved, revision: 4, in_flight_authorized: true } });
    await render(); await act(async () => button("إلغاء تفويض الإنشاء").click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/creation-policy/123/revoke");
    expect(api.post.mock.calls[0][1]).toEqual({ expected_revision: 3 }); expect(api.put).not.toHaveBeenCalled();
    expect(container.textContent).toContain("قد تكتمل عملية سبق تفويضها");
});

test("service save cannot enable configuration and activation cannot coerce acknowledgements", async () => {
    await saveGoogleCreationPolicy("123", { enabled: true }); expect(api.put.mock.calls[0][1].enabled).toBe(false);
    await expect(activateGoogleCreationPolicy("123", 3, { creation: 1, creative: true, autoEnable: false })).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
});
