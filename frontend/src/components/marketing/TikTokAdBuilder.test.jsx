import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokAdBuilder from "./TikTokAdBuilder";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { post: jest.fn(), get: jest.fn() } }));
const selection = { kind: "video_asset", accountId: "70001", accountName: "أماسي" };
const preview = { kind: "video_asset", account_id: "70001", proposal_id: "proposal1", status: "previewed", confirmation_digest: "a".repeat(64), plan: { input: { name: "فيديو الشتاء", reason: "تجربة فيديو هدية الشتاء", video_url: "https://media.example.test/winter.mp4" }, proof: { currency: "SAR" } } };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset();
    Object.defineProperty(window, "crypto", { configurable: true, value: { randomUUID: () => "00000000-0000-4000-8000-000000000001" } });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (text) => [...container.querySelectorAll("button")].find((node) => node.textContent === text);
async function prepare() {
    await act(async () => root.render(<TikTokAdBuilder selection={selection} onClose={jest.fn()} />));
    for (const [label, value] of [["الاسم", "فيديو الشتاء"], ["سبب الإنشاء", "تجربة فيديو هدية الشتاء"], ["رابط الفيديو", "https://media.example.test/winter.mp4"]]) {
        const field = [...container.querySelectorAll("input")].find((node) => node.getAttribute("aria-label") === label);
        await act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(field, value);
            field.dispatchEvent(new Event("input", { bubbles: true }));
        });
    }
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}
test("video URL preview does not execute before explicit owner approval", async () => {
    api.post.mockResolvedValueOnce({ data: preview });
    await prepare();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toMatchObject({ kind: "video_asset", account_id: "70001", video_url: "https://media.example.test/winter.mp4" });
    expect(button("اعتماد الإنشاء").disabled).toBe(true);
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("video_file");
});
test("uncertain submission never automatically retries or re-enables execution", async () => {
    api.post.mockResolvedValueOnce({ data: preview }).mockRejectedValueOnce({ response: { status: 502 } });
    await prepare();
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    await act(async () => button("اعتماد الإنشاء").click());
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(button("اعتماد الإنشاء")).toBeUndefined();
    expect(button("تحديث حالة العملية").disabled).toBe(false);
});

test("Spark preview links the existing post and retains its original caption without importing video", async () => {
    api.post.mockResolvedValueOnce({ data: { ...preview, kind: "ad", plan: { input: { name: "Spark winter", reason: "Owner selected original post", tiktok_item_id: "1234567891234567891", identity_id: "identity1", identity_type: "TT_USER", landing_page_url: "https://amasi-sa.com/ar/p123" }, proof: { currency: "SAR", spark_post: { item_id: "1234567891234567891", caption: "نص المنشور الأصلي" } }, body: { call_to_action: "SHOP_NOW" } } } });
    await act(async () => root.render(<TikTokAdBuilder selection={{ kind: "ad", accountId: "70001", campaignId: "1111", adgroupId: "2222" }} onClose={jest.fn()} />));
    const source = container.querySelector('[aria-label="مصدر إعلان TikTok"]');
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(source, "spark"); source.dispatchEvent(new Event("change", { bubbles: true })); });
    for (const [label, value] of [["الاسم", "Spark winter"], ["سبب الإنشاء", "Owner selected original post"], ["معرّف منشور TikTok العام", "1234567891234567891"], ["معرّف هوية الإعلان", "identity1"], ["رابط المنتج", "https://amasi-sa.com/ar/p123"]]) {
        const field = container.querySelector(`[aria-label="${label}"]`);
        await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(field, value); field.dispatchEvent(new Event("input", { bubbles: true })); });
    }
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toMatchObject({ kind: "ad", tiktok_item_id: "1234567891234567891", identity_type: "TT_USER" });
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("video_id");
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("ad_text");
    expect(container.textContent).toContain("نص المنشور الأصلي");
    expect(button("اعتماد الإنشاء").disabled).toBe(true);
});
