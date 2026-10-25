import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokChildControls from "./TikTokChildControls";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { post: jest.fn(), get: jest.fn() } }));
const selection = { kind: "ad", accountId: "70001", accountName: "أماسي", campaignId: "1111", adgroupId: "2222", adId: "3333", name: "تصميم الشتاء" };
const preview = { kind: "ad", action: "pause", account_id: "70001", entity_id: "3333", proposal_id: "proposal1", status: "previewed", currency: "SAR", confirmation_digest: "a".repeat(64), before: { operation_status: "ENABLE", ad_name: "إعلان الشتاء" }, changes: { operation_status: "DISABLE" }, reason: "إيقاف إعلان الشتاء" };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset();
    Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => "00000000-0000-4000-8000-000000000001" } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
async function prepare() {
    await act(async () => root.render(<TikTokChildControls selection={selection} onClose={jest.fn()} />));
    const reason = container.querySelector('[aria-label="سبب إدارة عنصر TikTok"]');
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(reason, "إيقاف إعلان الشتاء"); reason.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("child preview identifies the native ad and never writes before exact approval", async () => {
    api.post.mockResolvedValueOnce({ data: preview });
    await prepare();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toMatchObject({ kind: "ad", action: "pause", account_id: "70001", campaign_id: "1111", adgroup_id: "2222", ad_id: "3333" });
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("amount_native");
    expect(button("اعتماد تعديل العنصر").disabled).toBe(true);
    expect(container.textContent).toContain("3333");
    expect(container.textContent).toContain("إعلان الشتاء");
});

test("uncertain transmission cannot be retried even if a later read still says previewed", async () => {
    api.post.mockResolvedValueOnce({ data: preview }).mockRejectedValueOnce({ response: { status: 502 } });
    await prepare();
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    await act(async () => button("اعتماد تعديل العنصر").click());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(button("اعتماد تعديل العنصر")).toBeUndefined();
    api.get.mockResolvedValueOnce({ data: preview });
    await act(async () => button("تحديث حالة تعديل العنصر").click());
    expect(button("اعتماد تعديل العنصر")).toBeUndefined();
    expect(api.post).toHaveBeenCalledTimes(2);
});

test("history shows the actual proposal account,kind and name", async () => {
    await act(async () => root.render(<TikTokChildControls selection={selection} onClose={jest.fn()} />));
    const saved = { ...preview, kind: "adgroup", account_id: "70002", entity_id: "9999", before: { adgroup_name: "المجموعة المحفوظة" } };
    api.get.mockResolvedValueOnce({ data: { items: [saved] } }).mockResolvedValueOnce({ data: saved });
    await act(async () => button("سجل تعديل المجموعات والإعلانات").click());
    await act(async () => button("9999 · راجع المعاينة قبل الاعتماد").click());
    expect(container.textContent).toContain("إدارة مجموعة Smart+");
    expect(container.textContent).toContain("70002");
    expect(container.textContent).toContain("المجموعة المحفوظة");
    expect(container.textContent).not.toContain("تصميم الشتاء");
});
