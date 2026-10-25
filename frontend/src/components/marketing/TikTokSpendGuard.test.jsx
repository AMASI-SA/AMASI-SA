import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokSpendGuard from "./TikTokSpendGuard";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn() } }));
const selection = { accountId: "70001", campaignId: "1111", accountName: "أماسي", name: "حملة الشتاء", currency: "SAR" };
const initial = { account_id: "70001", revision: 3, enabled: false, rules: [], currency: "SAR", timezone: "Asia/Riyadh" };
let container, root;
beforeEach(() => { api.get.mockReset(); api.put.mockReset(); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
async function render(value = initial) { api.get.mockResolvedValueOnce({ data: value }); await act(async () => root.render(<TikTokSpendGuard selection={selection} onClose={jest.fn()} />)); }
async function authorize() {
    await act(async () => container.querySelector('[aria-label="تفعيل حماية صرف الحملة"]').click());
    const input = container.querySelector('[aria-label="حد صرف حماية TikTok"]');
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "55.25"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => container.querySelector('[aria-label="اعتماد تأخر فحص الحماية"]').click());
}

test("opening monitoring does not enable it or submit owner authority", async () => {
    await render();
    expect(api.get).toHaveBeenCalledWith("/integrations-v2/tiktok_ads/spend-guard/policy", expect.objectContaining({ params: { account_id: "70001" } }));
    expect(api.put).not.toHaveBeenCalled();
    expect(container.querySelector('[aria-label="تفعيل حماية صرف الحملة"]').checked).toBe(false);
    expect(container.textContent).toContain("قاعدة الحملة المحفوظة غير مفعّلة");
});

test("saving explicit owner policy preserves other campaign rules and exact revision", async () => {
    const other = { campaign_id: "2222", pause_at_daily_spend_native: 20.0 };
    await render({ ...initial, enabled: true, rules: [other] });
    await authorize();
    expect(api.put).not.toHaveBeenCalled();
    expect(container.textContent).toContain("قاعدة الحملة المحفوظة غير مفعّلة");
    api.put.mockResolvedValueOnce({ data: { ...initial, revision: 4, enabled: true, rules: [other, { campaign_id: "1111", pause_at_daily_spend_native: 55.25 }] } });
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(api.put.mock.calls[0][1]).toEqual({ account_id: "70001", expected_revision: 3, enabled: true, rules: [other, { campaign_id: "1111", pause_at_daily_spend_native: 55.25 }], polling_and_report_delay_acknowledged: true });
    expect(container.textContent).toContain("قاعدة الحملة المحفوظة مفعّلة");
});

test("a stale owner revision shows the error without retrying configuration", async () => {
    await render(); await authorize();
    api.put.mockRejectedValueOnce({ response: { status: 409, data: { detail: { message: "تغيرت سياسة الحماية؛ حدّثها" } } } });
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(container.querySelector('[role="alert"]').textContent).toContain("تغيرت سياسة الحماية");
    expect(api.put).toHaveBeenCalledTimes(1);
    expect(button("تحديث سياسة الحماية").disabled).toBe(false);
});

test("the owner can disable the entire account without approving a new trigger", async () => {
    await render({ ...initial, enabled: true, rules: [{ campaign_id: "1111", pause_at_daily_spend_native: 55.25 }] });
    api.put.mockResolvedValueOnce({ data: { ...initial, revision: 4 } });
    await act(async () => button("تعطيل حماية الحساب بالكامل").click());
    expect(api.put.mock.calls[0][1]).toEqual({ account_id: "70001", expected_revision: 3, enabled: false, rules: [], polling_and_report_delay_acknowledged: false });
});
