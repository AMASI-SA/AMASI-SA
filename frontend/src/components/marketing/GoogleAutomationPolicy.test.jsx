import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleAutomationPolicy from "./GoogleAutomationPolicy";
import { activateGoogleAutomationPolicy, saveGoogleAutomationPolicy } from "../../services/googleAutomationPolicy";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));
const saved = { account_id: "123", revision: 3, enabled: false, executor_available: false, effective_enabled: false,
    currency_code: "SAR", time_zone: "Asia/Riyadh", campaign_ids: ["456"], allowed_actions: ["pause"],
    max_campaign_daily_budget_native: "40", max_account_daily_budget_native: "55", max_budget_change_percent: "10",
    cooldown_seconds: 18000, max_actions_per_day: 1, expires_at: null };
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockResolvedValue({ data: saved });
    api.put.mockResolvedValue({ data: { ...saved, revision: 4 } });
    api.post.mockResolvedValue({ data: { ...saved, revision: 4, enabled: false } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleAutomationPolicy accountId="123" isOwner {...props} />)); }
function button(text) { return [...container.querySelectorAll("button")].find((element) => element.textContent === text); }
async function input(label, value) {
    const element = container.querySelector(`[aria-label="${label}"]`);
    await act(async () => {
        const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
        element.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function save() {
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("owner sees preparation only and loading never changes policy or campaigns", async () => {
    await render();
    expect(container.textContent).toContain("التنفيذ الآلي غير متاح حاليًا");
    expect(container.textContent).toContain("ليست سقفًا مضمونًا للصرف الفعلي");
    expect(container.querySelector('input[type="checkbox"]').disabled).toBe(true);
    expect(container.querySelector('input[type="checkbox"]').checked).toBe(false);
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/policy/123");
    expect(api.put).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
});

test.each([false, undefined])("non-owner cannot see policy or make requests: %s", async (isOwner) => {
    await render({ isOwner });
    expect(container.textContent).toBe(""); expect(api.get).not.toHaveBeenCalled();
});

test("explicit save preserves native decimal strings and revision while keeping policy disabled", async () => {
    await render();
    await input("حد متوسط ميزانية الحملة", "39.123456");
    await input("معرفات الحملات المسموح بها", "456\n789");
    await save();
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/policy/123");
    expect(api.put.mock.calls[0][1]).toEqual({ expected_revision: 3, enabled: false, campaign_ids: ["456", "789"],
        allowed_actions: ["pause"], max_campaign_daily_budget_native: "39.123456", max_account_daily_budget_native: "55",
        max_budget_change_percent: "10", cooldown_seconds: 18000, max_actions_per_day: 1, expires_at: null });
    expect(container.textContent).toContain("حُفظت الحدود والسياسة معطّلة");
    expect(api.post).not.toHaveBeenCalled();
});

test("save service cannot forward an enabled flag to policy API", async () => {
    await saveGoogleAutomationPolicy("123", { ...saved, enabled: true });
    expect(api.put.mock.calls[0][1].enabled).toBe(false);
});

test("first save uses revision zero and no preselected campaigns or actions", async () => {
    api.get.mockResolvedValue({ data: { account_id: "123", revision: 0, enabled: false, campaign_ids: [], allowed_actions: [] } });
    await render();
    expect(container.querySelector('[aria-label="معرفات الحملات المسموح بها"]').value).toBe("");
    expect([...container.querySelectorAll('input[type="checkbox"]')].every((checkbox) => !checkbox.checked)).toBe(true);
    await input("حد متوسط ميزانية الحملة", "40"); await input("حد مجموع متوسط الميزانيات", "55"); await input("أقصى تغيير في الميزانية", "10");
    await save();
    expect(api.put.mock.calls[0][1]).toMatchObject({ expected_revision: 0, enabled: false, campaign_ids: [], allowed_actions: [] });
});

test("conflict preserves edits and requires explicit reload before another save", async () => {
    api.put.mockRejectedValue({ response: { status: 409 } });
    await render(); await input("حد متوسط ميزانية الحملة", "39"); await save();
    expect(container.querySelector('[aria-label="حد متوسط ميزانية الحملة"]').value).toBe("39");
    expect(container.textContent).toContain("بقيت مدخلاتك ظاهرة للمراجعة");
    expect(container.querySelector("fieldset").disabled).toBe(true);
    await save(); expect(api.put).toHaveBeenCalledTimes(1);
    api.get.mockResolvedValue({ data: { ...saved, revision: 5 } });
    await act(async () => button("إعادة تحميل السياسة").click());
    expect(container.querySelector("fieldset").disabled).toBe(false);
    expect(container.querySelector('[aria-label="حد متوسط ميزانية الحملة"]').value).toBe("40");
    expect(api.put).toHaveBeenCalledTimes(1);
});

test("ordinary save failure keeps inputs and exposes a safe status", async () => {
    api.put.mockRejectedValue({ response: { status: 503, data: { private_body: "do not display" } } });
    await render(); await input("حد متوسط ميزانية الحملة", "39"); await save();
    expect(container.querySelector('[aria-label="حد متوسط ميزانية الحملة"]').value).toBe("39");
    expect(container.textContent).toContain("بقيت مدخلاتك كما هي");
    expect(container.textContent).not.toContain("do not display");
});

test("revocation sends only current revision to the scoped endpoint", async () => {
    api.get.mockResolvedValue({ data: { ...saved, enabled: true } });
    await render();
    expect(container.querySelector('input[type="checkbox"]').disabled).toBe(true);
    expect(container.textContent).toContain("تفويض محفوظ؛ التنفيذ الآلي غير متاح");
    await act(async () => button("إلغاء التفويض المحفوظ").click());
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/policy/123/revoke");
    expect(api.post.mock.calls[0][1]).toEqual({ expected_revision: 3 });
    expect(api.put).not.toHaveBeenCalled();
});

test("account switch aborts old read and cannot show its policy", async () => {
    let finish;
    api.get.mockImplementation((url) => url.endsWith("/123") ? new Promise((resolve) => { finish = resolve; })
        : Promise.resolve({ data: { ...saved, account_id: "222", revision: 8, max_campaign_daily_budget_native: "22" } }));
    await render(); const firstSignal = api.get.mock.calls[0][1].signal;
    await render({ accountId: "222" });
    expect(firstSignal.aborted).toBe(true);
    await act(async () => finish({ data: saved }));
    expect(container.textContent).toContain("النسخة المحفوظة: 8");
    expect(container.querySelector('[aria-label="حد متوسط ميزانية الحملة"]').value).toBe("22");
    expect(api.put).not.toHaveBeenCalled();
});

test("late mutation result from previous account cannot replace new policy", async () => {
    let finish;
    api.put.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render(); await save(); const oldSignal = api.put.mock.calls[0][2].signal;
    api.get.mockResolvedValue({ data: { ...saved, account_id: "222", revision: 9 } });
    await render({ accountId: "222" });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => finish({ data: { ...saved, revision: 4 } }));
    expect(container.textContent).toContain("النسخة المحفوظة: 9");
    expect(container.textContent).not.toContain("حُفظت الحدود");
});

test("activation is unavailable with runtime off and never happens during loading or saving", async () => {
    await render();
    expect(button("تفعيل الإدارة الآلية ضمن هذه الحدود").disabled).toBe(true);
    expect(container.querySelector('[aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود"]').disabled).toBe(true);
    await save();
    expect(api.put.mock.calls[0][1].enabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
});

test("owner reviews saved account limits then explicitly acknowledges one activation revision", async () => {
    api.get.mockResolvedValue({ data: { ...saved, executor_available: true } });
    api.post.mockResolvedValue({ data: { ...saved, revision: 4, enabled: true, executor_available: true, effective_enabled: true, activation_revision: 4 } });
    await render();
    const review = container.querySelector('[aria-label="مراجعة تفويض الإدارة الآلية"]');
    expect(review.textContent).toContain("123"); expect(review.textContent).toContain("456");
    expect(review.textContent).toContain("40"); expect(review.textContent).toContain("55");
    expect(button("تفعيل الإدارة الآلية ضمن هذه الحدود").disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
    await act(async () => container.querySelector('[aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود"]').click());
    expect(button("تفعيل الإدارة الآلية ضمن هذه الحدود").disabled).toBe(false);
    await act(async () => button("تفعيل الإدارة الآلية ضمن هذه الحدود").click());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][0]).toBe("/integrations-v2/google_ads/automation/policy/123/activate");
    expect(api.post.mock.calls[0][1]).toEqual({ expected_revision: 3, acknowledge_execution: true });
    expect(api.put).not.toHaveBeenCalled();
    expect(container.textContent).toContain("الإدارة الآلية مفعّلة ضمن الحدود المعتمدة");
});

test("unsaved edits reset acknowledgement and cannot activate old reviewed limits", async () => {
    api.get.mockResolvedValue({ data: { ...saved, executor_available: true } });
    await render();
    await act(async () => container.querySelector('[aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود"]').click());
    await input("حد متوسط ميزانية الحملة", "39");
    expect(container.querySelector('[aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود"]').checked).toBe(false);
    expect(button("تفعيل الإدارة الآلية ضمن هذه الحدود").disabled).toBe(true);
    expect(container.textContent).toContain("لديك تعديلات غير محفوظة");
    expect(api.post).not.toHaveBeenCalled();
});

test("activation service rejects implicit acknowledgement", async () => {
    await expect(activateGoogleAutomationPolicy("123", 3, 1)).rejects.toThrow();
    expect(api.post).not.toHaveBeenCalled();
});

test("revocation reports a previously authorized operation may finish without disclosing claims", async () => {
    api.post.mockResolvedValue({ data: { ...saved, revision: 4, in_flight_authorized: true } });
    await render();
    await act(async () => button("إلغاء التفويض المحفوظ").click());
    expect(container.textContent).toContain("قد تكتمل عملية مُنحت إذن الإرسال قبل الإلغاء");
    expect(api.post.mock.calls[0][1]).toEqual({ expected_revision: 3 });
});

test("late activation response cannot mark another account active", async () => {
    let finish;
    api.get.mockResolvedValue({ data: { ...saved, executor_available: true } });
    api.post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await render();
    await act(async () => container.querySelector('[aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود"]').click());
    await act(async () => button("تفعيل الإدارة الآلية ضمن هذه الحدود").click());
    const oldSignal = api.post.mock.calls[0][2].signal;
    api.get.mockResolvedValue({ data: { ...saved, account_id: "222", revision: 9, executor_available: true } });
    await render({ accountId: "222" });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => finish({ data: { ...saved, revision: 4, effective_enabled: true } }));
    expect(container.textContent).toContain("النسخة المحفوظة: 9");
    expect(container.textContent).not.toContain("اعتُمد التفويض لهذا الإصدار");
    expect(container.textContent).not.toContain("الإدارة الآلية مفعّلة ضمن الحدود المعتمدة");
});
