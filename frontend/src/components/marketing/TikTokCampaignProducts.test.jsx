import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import TikTokCampaignProducts from "./TikTokCampaignProducts";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
let container, root;
beforeEach(() => {
    jest.clearAllMocks(); container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "request-123" } });
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (text) => [...container.querySelectorAll("button")].find(b => b.textContent === text);
const click = async (text) => act(async () => button(text).click());
const product = { id: "123", name: "هودي", url: "https://amasi-sa.com/ar/hoodie/p123" };
async function search() {
    const input = container.querySelector("input");
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "هودي");
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("بحث المنتجات");
}
test("search then preview then explicit save never requests AI or media", async () => {
    api.get.mockResolvedValue({ data: { items: [{ salla_product_id: "123", name: "هودي" }] } });
    api.post.mockResolvedValueOnce({ data: { status: "preview", product } })
        .mockResolvedValueOnce({ data: { status: "linked", product } });
    await act(async () => root.render(<TikTokCampaignProducts accountId="account" campaignId="campaign" />));
    expect(api.post).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
    await search(); await click("هودي");
    expect(api.post.mock.calls[0][1]).toEqual({ account_id: "account", campaign_id: "campaign", product_id: "123" });
    await click("تأكيد ربط المنتج");
    expect(api.post.mock.calls[1][1]).toEqual({ account_id: "account", campaign_id: "campaign", product_id: "123", confirm: true, idempotency_key: "request-123" });
    expect(container.textContent).toContain("التحليل المعروض سابق للربط");
    expect(api.post.mock.calls.every(([url]) => url.endsWith("/campaign-product"))).toBe(true);
    expect(container.querySelectorAll("img,video,iframe")).toHaveLength(0);
});
test("failed confirmation retains same retry key", async () => {
    api.get.mockResolvedValue({ data: { items: [{ salla_product_id: "123", name: "هودي" }] } });
    api.post.mockResolvedValueOnce({ data: { status: "preview", product } })
        .mockRejectedValueOnce(new Error("timeout")).mockResolvedValueOnce({ data: { status: "linked", product } });
    await act(async () => root.render(<TikTokCampaignProducts accountId="account" campaignId="campaign" />));
    await search(); await click("هودي"); await click("تأكيد ربط المنتج"); await click("تأكيد ربط المنتج");
    expect(api.post.mock.calls[1][1]).toEqual(api.post.mock.calls[2][1]);
});
