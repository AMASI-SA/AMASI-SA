import React, { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TikTokCampaignControls from "./TikTokCampaignControls";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
const selection = { mode: "create", accountId: "70001", accountName: "Account", currency: "SAR" };
const preview = { proposal_id: "proposal-1", action: "create", account_id: "70001", account_name: "Account", currency: "SAR", campaign_name: "Product launch", status: "previewed", confirmation_digest: "a".repeat(64), planned: { campaign_name: "Product launch", operation_status: "DISABLE", budget: 100, budget_mode: "BUDGET_MODE_DAY" }, before: {}, reason: "Product test" };
let container, root;
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const button = (name) => [...container.querySelectorAll("button")].find((element) => element.textContent === name);
async function input(label, value) {
    const element = container.querySelector(`[aria-label="${label}"]`);
    const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    await act(async () => { Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value); element.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function prepare() {
    await input("اسم حملة TikTok", "Product launch"); await input("ميزانية حملة TikTok", "100"); await input("سبب إدارة حملة TikTok", "Product test");
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("previews owner inputs and cannot execute without separate exact approval", async () => {
    api.post.mockResolvedValueOnce({ data: preview });
    await act(async () => root.render(<StrictMode><TikTokCampaignControls selection={selection} /></StrictMode>));
    await prepare();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0][1]).toMatchObject({ action: "create", account_id: "70001", campaign_name: "Product launch", budget_native: 100, budget_mode: "BUDGET_MODE_DAY", objective_type: "WEB_CONVERSIONS", reason: "Product test" });
    expect(button("الموافقة والتنفيذ في TikTok").disabled).toBe(true);
    expect(container.querySelectorAll("video,img,input[type=file]")).toHaveLength(0);
});

test("double click executes the confirmed proposal once and shows verified paused identity", async () => {
    let finish;
    const completed = jest.fn();
    api.post.mockResolvedValueOnce({ data: preview }).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => root.render(<TikTokCampaignControls selection={selection} onCompleted={completed} />)); await prepare();
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    const execute = button("الموافقة والتنفيذ في TikTok");
    await act(async () => { execute.click(); execute.click(); });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual({ confirmation_digest: "a".repeat(64) });
    expect(api.post.mock.calls[1][2].timeout).toBe(60000);
    await act(async () => finish({ data: { ...preview, status: "completed", verified: true, created_campaign_id: "2222" } }));
    expect(completed).toHaveBeenCalledTimes(1); expect(container.textContent).toContain("2222"); expect(container.textContent).toContain("مكتمل ومثبت");
});

test("lost execution response requires a status read and never offers another write", async () => {
    api.post.mockResolvedValueOnce({ data: preview }).mockRejectedValueOnce(new Error("timeout"));
    api.get.mockResolvedValueOnce({ data: { ...preview, status: "uncertain", provider_write_reached: true } });
    await act(async () => root.render(<TikTokCampaignControls selection={selection} />)); await prepare();
    await act(async () => container.querySelector('input[type="checkbox"]').click());
    await act(async () => button("الموافقة والتنفيذ في TikTok").click());
    expect(button("الموافقة والتنفيذ في TikTok")).toBeUndefined();
    await act(async () => button("تحديث حالة الاقتراح").click());
    expect(api.post).toHaveBeenCalledTimes(2); expect(api.get).toHaveBeenCalledTimes(1); expect(container.textContent).toContain("نتيجة غير مؤكدة");
});

test("unmount aborts preview and late results cannot call the completion callback", async () => {
    let finish;
    const completed = jest.fn(); api.post.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => root.render(<TikTokCampaignControls selection={selection} onCompleted={completed} />)); await prepare();
    const signal = api.post.mock.calls[0][2].signal;
    await act(async () => root.render(<div>New selection</div>)); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: { ...preview, status: "completed", verified: true } }));
    expect(completed).not.toHaveBeenCalled(); expect(container.textContent).toBe("New selection");
});

test("history is loaded only on demand and is bounded to twelve proposals", async () => {
    api.get.mockResolvedValueOnce({ data: { items: [preview] } }).mockResolvedValueOnce({ data: preview });
    await act(async () => root.render(<TikTokCampaignControls selection={selection} />)); expect(api.get).not.toHaveBeenCalled();
    await act(async () => button("سجل عمليات TikTok").click());
    expect(api.get.mock.calls[0][1].params).toEqual({ limit: 12 });
    await act(async () => [...container.querySelectorAll("button")].find((element) => element.textContent.includes("Product launch")).click());
    expect(api.post).not.toHaveBeenCalled(); expect(button("الموافقة والتنفيذ في TikTok").disabled).toBe(true);
});
